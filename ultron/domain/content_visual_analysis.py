from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .content import ContentRecord
from .content_visual_intelligence import VisualVideoAnalysis


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,255}$")
_OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_MODEL_DIGEST_RE = re.compile(r"^(?:sha256:)?[a-f0-9]{64}$")


class VisualAnalysisState(StrEnum):
    QUEUED = "queued"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    FAILED = "failed"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class VisualAnalysisRequest(ContentRecord):
    analysis_id: str
    owner_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    asset_id: str = Field(min_length=1, max_length=128)
    asset_sha256: str
    idempotency_key: str = Field(min_length=1, max_length=200)
    provider_id: str = Field(min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=256)
    model_version: str = Field(min_length=1, max_length=256)
    prompt_version: str = Field(min_length=1, max_length=128)
    schema_version: str = Field(min_length=1, max_length=128)
    sampler_version: str = Field(min_length=1, max_length=128)
    timestamps_seconds: tuple[float, ...] = Field(min_length=1, max_length=24)
    context_tokens: int = Field(default=1024, ge=256, le=8192)
    output_tokens: int = Field(default=384, ge=16, le=2048)
    image_dimension_limit: int = Field(default=1024, gt=0, le=1024)

    @field_validator("analysis_id")
    @classmethod
    def require_uuid_analysis_id(cls, value: str) -> str:
        try:
            parsed = UUID(value)
        except ValueError as exc:
            raise ValueError("Visual analysis ID must be a UUID.") from exc
        if str(parsed) != value:
            raise ValueError("Visual analysis ID must use canonical lowercase UUID form.")
        return value

    @field_validator("owner_id")
    @classmethod
    def require_safe_owner_id(cls, value: str) -> str:
        if not _OWNER_RE.fullmatch(value):
            raise ValueError("Visual analysis owner ID is malformed.")
        return value

    @field_validator(
        "project_id",
        "asset_id",
        "provider_id",
        "model_id",
        "model_version",
        "prompt_version",
        "schema_version",
        "sampler_version",
    )
    @classmethod
    def require_safe_identifiers(cls, value: str) -> str:
        if not _IDENTIFIER_RE.fullmatch(value):
            raise ValueError("Visual analysis identifiers are malformed.")
        return value

    @field_validator("model_version")
    @classmethod
    def require_immutable_model_digest(cls, value: str) -> str:
        if not _MODEL_DIGEST_RE.fullmatch(value):
            raise ValueError(
                "Visual analysis model version must be an immutable SHA-256 digest."
            )
        return value

    @field_validator("asset_sha256")
    @classmethod
    def require_asset_sha256(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("Visual analysis asset digest must be lowercase SHA-256.")
        return value

    @field_validator("timestamps_seconds")
    @classmethod
    def validate_timestamps(cls, values: tuple[float, ...]) -> tuple[float, ...]:
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("Visual analysis timestamps must be finite and non-negative.")
        if any(left >= right for left, right in zip(values, values[1:])):
            raise ValueError("Visual analysis timestamps must be strictly increasing.")
        return values

    @model_validator(mode="after")
    def require_idempotency_key(self) -> VisualAnalysisRequest:
        if not self.idempotency_key.strip():
            raise ValueError("Visual analysis idempotency key must not be blank.")
        return self

    def cache_fingerprint(self) -> str:
        payload = self.model_dump(
            mode="json",
            exclude={"analysis_id", "idempotency_key"},
        )
        canonical = json.dumps(
            {"purpose": "visual-analysis-cache-v1", "request": payload},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def request_fingerprint(self) -> str:
        payload = self.model_dump(mode="json", exclude={"analysis_id"})
        canonical = json.dumps(
            {"purpose": "visual-analysis-idempotency-v1", "request": payload},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ContentVisualAnalysis(ContentRecord):
    analysis_id: str
    owner_id: str
    project_id: str
    asset_id: str
    asset_sha256: str
    idempotency_key: str
    request_fingerprint: str
    cache_fingerprint: str
    request: VisualAnalysisRequest
    state: VisualAnalysisState
    result: VisualVideoAnalysis | None = None
    cache_source_analysis_id: str | None = None
    failure_code: str | None = Field(default=None, max_length=100)
    failure_detail: str | None = Field(default=None, max_length=1000)
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None

    @field_validator("request_fingerprint", "cache_fingerprint", "asset_sha256")
    @classmethod
    def validate_hashes(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("Visual analysis digests must be lowercase SHA-256.")
        return value

    @model_validator(mode="after")
    def validate_consistency(self) -> ContentVisualAnalysis:
        if (
            self.request.analysis_id != self.analysis_id
            or self.request.owner_id != self.owner_id
            or self.request.project_id != self.project_id
            or self.request.asset_id != self.asset_id
            or self.request.asset_sha256 != self.asset_sha256
            or self.request.idempotency_key != self.idempotency_key
            or self.request.cache_fingerprint() != self.cache_fingerprint
            or self.request.request_fingerprint() != self.request_fingerprint
        ):
            raise ValueError("Visual analysis request identity does not match its record.")
        if self.state == VisualAnalysisState.COMPLETED:
            if self.result is None or self.completed_at is None:
                raise ValueError("Completed visual analyses require a result and completion time.")
            if (
                self.result.asset_id != self.asset_id
                or self.result.asset_sha256 != self.asset_sha256
            ):
                raise ValueError("Visual analysis result does not match its source asset.")
        elif self.result is not None or self.completed_at is not None:
            raise ValueError("Only completed visual analyses may contain a result.")
        if self.cache_source_analysis_id is not None and (
            self.state != VisualAnalysisState.COMPLETED
            or self.cache_source_analysis_id == self.analysis_id
        ):
            raise ValueError("Only cached completed analyses may reference a source run.")
        if self.state == VisualAnalysisState.FAILED:
            if not self.failure_code or not self.failure_detail:
                raise ValueError("Failed visual analyses require a failure code and detail.")
        elif self.failure_code is not None or self.failure_detail is not None:
            raise ValueError("Failure details are only valid for failed analyses.")
        return self


class ContentVisualAnalysisEvent(ContentRecord):
    sequence: int = Field(gt=0)
    analysis_id: str
    owner_id: str
    from_state: VisualAnalysisState | None = None
    to_state: VisualAnalysisState
    actor: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=200)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class VisualAnalysisRetentionPolicy(ContentRecord):
    owner_id: str = Field(min_length=1, max_length=128)
    completed_before: datetime
    failed_before: datetime
    reconciliation_required_before: datetime

    @field_validator(
        "completed_before",
        "failed_before",
        "reconciliation_required_before",
    )
    @classmethod
    def require_timezone_aware_cutoffs(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Visual analysis retention cutoffs must be timezone-aware.")
        return value.astimezone(UTC)


class VisualAnalysisRetentionPlan(ContentRecord):
    owner_id: str
    candidate_analysis_ids: tuple[str, ...] = ()
    audit_protected_analysis_ids: tuple[str, ...] = ()
    cache_referenced_analysis_ids: tuple[str, ...] = ()
    active_analysis_ids: tuple[str, ...] = ()
    dry_run: bool = True
