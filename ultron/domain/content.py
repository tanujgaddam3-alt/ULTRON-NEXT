from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_TERRITORY_RE = re.compile(r"^[A-Z]{2}$")
_SAFE_STORAGE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ContentRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ContentPlatform(StrEnum):
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"


class PublishingProvider(StrEnum):
    YOUTUBE = "youtube"


class UploadSecretPurpose(StrEnum):
    RESUMABLE_UPLOAD_SESSION = "resumable_upload_session"



class AssetCategory(StrEnum):
    FOOTAGE = "footage"
    MUSIC = "music"
    IMAGE = "image"
    VOICE = "voice"
    OTHER = "other"


class AcquisitionMethod(StrEnum):
    OWNER_CREATED = "owner_created"
    OWNER_UPLOAD = "owner_upload"
    DIRECT_LICENSE = "direct_license"
    PUBLIC_DOMAIN = "public_domain"
    AUTHORIZED_PROVIDER = "authorized_provider"
    OTHER_AUTHORIZED = "other_authorized"


class AssetState(StrEnum):
    ACTIVE = "active"
    QUARANTINED = "quarantined"
    ARCHIVED = "archived"


class RightsState(StrEnum):
    UNKNOWN = "unknown"
    REVIEW_REQUIRED = "review_required"
    CLEARED = "cleared"
    BLOCKED = "blocked"
    EXPIRED = "expired"
    REVOKED = "revoked"


class EvidenceDecision(StrEnum):
    VERIFIED = "verified"
    REJECTED = "rejected"
    AMBIGUOUS = "ambiguous"


class OwnershipClaimState(StrEnum):
    REVIEW_REQUIRED = "review_required"
    VERIFIED = "verified"
    REJECTED = "rejected"
    AMBIGUOUS = "ambiguous"


class RightsAction(StrEnum):
    ANALYZE = "analyze"
    QUOTE = "quote"
    EDIT = "edit"
    CREATE_DERIVATIVE = "create_derivative"
    PUBLISH = "publish"
    COMMERCIAL_USE = "commercial_use"
    MONETIZE = "monetize"


class PlatformDecisionState(StrEnum):
    UNKNOWN = "unknown"
    COMPLIANT = "compliant"
    NON_COMPLIANT = "non_compliant"
    NOT_AUTHORIZED = "not_authorized"


class ReviewState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


class PublicationState(StrEnum):
    NOT_PUBLISHED = "not_published"
    QUEUED = "queued"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    UNCERTAIN = "uncertain"
    FAILED = "failed"
    TAKEDOWN_REQUIRED = "takedown_required"


class ContentTargetContext(StrEnum):
    YOUTUBE_SHORTS = "youtube_shorts"
    YOUTUBE_KIDS_AUDIENCE = "youtube_kids_audience"
    INSTAGRAM_REELS = "instagram_reels"
    SNAPCHAT_SPOTLIGHT = "snapchat_spotlight"


class ContentLifecycleState(StrEnum):
    REGISTERED = "registered"
    RIGHTS_PENDING = "rights_pending"
    RIGHTS_CLEARED = "rights_cleared"
    READY_FOR_ANALYSIS = "ready_for_analysis"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    EXPIRED = "expired"
    REVOKED = "revoked"
    ARCHIVED = "archived"


class ProcessingJobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNCERTAIN = "uncertain"


class PrivateRenderOperationState(StrEnum):
    QUEUED = "queued"
    RENDERING = "rendering"
    COMPLETED = "completed"
    FAILED = "failed"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class ContentPrivateRenderOperation(ContentRecord):
    """Durable private-project render operation and pinned execution inputs."""

    operation_id: str
    owner_id: str
    project_id: str
    idempotency_key: str
    request_fingerprint: str
    input_snapshot_json: str
    state: PrivateRenderOperationState
    artifact_key: str | None = None
    artifact_sha256: str | None = None
    artifact_byte_count: int | None = None
    result_json: str | None = None
    failure_code: str | None = None
    failure_detail: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class ContentPrivateRenderOperationEvent(ContentRecord):
    sequence: int
    operation_id: str
    from_state: PrivateRenderOperationState | None = None
    to_state: PrivateRenderOperationState
    actor: str
    reason: str
    created_at: datetime


class SourceHashStatus(StrEnum):
    UNVERIFIED = "unverified"


class SourceAccessPermissionState(StrEnum):
    UNKNOWN = "unknown"
    CLAIMED_AUTHORIZED = "claimed_authorized"
    NOT_REQUIRED = "not_required"
    DENIED = "denied"
    EXPIRED = "expired"
    REVOKED = "revoked"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    PENDING_REVIEW = "pending_review"
    VERIFIED = "verified"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


class AgreementStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    AMENDED = "amended"
    SUSPENDED = "suspended"
    EXPIRED = "expired"
    REVOKED = "revoked"


class GrantorIdentity(ContentRecord):
    grantor_id: str
    owner_id: str
    claimed_identity: str
    verification_method: str
    evidence_reference: str
    evidence_sha256: str
    status: VerificationStatus = VerificationStatus.UNVERIFIED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator(
        "grantor_id", "owner_id", "claimed_identity", "verification_method",
        "evidence_reference",
    )
    @classmethod
    def validate_grantor_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 2048:
            raise ValueError("Grantor identity fields must be bounded and non-empty.")
        return normalized

    @field_validator("evidence_sha256")
    @classmethod
    def validate_grantor_evidence_hash(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("Grantor evidence must have a lowercase SHA-256 hash.")
        return value


class GrantorIdentityReview(ContentRecord):
    review_id: str
    grantor_id: str
    decision: EvidenceDecision
    evidence_sha256: str
    reviewer_principal: str
    reason: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GrantorAuthority(ContentRecord):
    authority_id: str
    grantor_id: str
    asset_id: str
    rights_holder: str
    relationship: str
    scope_description: str
    evidence_reference: str
    evidence_sha256: str
    status: VerificationStatus = VerificationStatus.UNVERIFIED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator(
        "authority_id", "grantor_id", "asset_id", "rights_holder",
        "relationship", "scope_description", "evidence_reference",
    )
    @classmethod
    def validate_authority_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 4000:
            raise ValueError("Grantor authority fields must be bounded and non-empty.")
        return normalized

    @field_validator("evidence_sha256")
    @classmethod
    def validate_authority_evidence_hash(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("Authority evidence must have a lowercase SHA-256 hash.")
        return value


class GrantorAuthorityReview(ContentRecord):
    review_id: str
    authority_id: str
    decision: EvidenceDecision
    evidence_sha256: str
    reviewer_principal: str
    reason: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RightsAgreementReview(ContentRecord):
    review_id: str
    agreement_id: str
    agreement_version: int
    decision: EvidenceDecision
    agreement_sha256: str
    evidence_sha256: str
    reviewer_principal: str
    reason: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RightsAgreementEvent(ContentRecord):
    sequence: int
    agreement_id: str
    agreement_version: int
    status: AgreementStatus
    actor: str
    reason: str
    created_at: datetime


def _validate_https_reference(value: str) -> str:
    normalized = value.strip()
    if (
        len(normalized) > 2048
        or any(ord(char) < 0x20 or char.isspace() for char in normalized)
        or "\\" in normalized
    ):
        raise ValueError("Reference must be a bounded, well-formed HTTPS URL.")
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Reference must be a well-formed HTTPS URL.") from exc
    hostname = parsed.hostname
    credential_parameters = {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "client_secret",
        "credential",
        "password",
        "secret",
        "sig",
        "signature",
        "token",
        "x-amz-signature",
        "x-goog-signature",
        "x-amz-credential",
        "x-goog-credential",
    }
    has_query_credentials = any(
        key.lower() in credential_parameters
        for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
    )
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or hostname is None
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or has_query_credentials
        or (port is not None and not 1 <= port <= 65535)
        or hostname.startswith(".")
        or hostname.endswith(".")
        or ".." in hostname
    ):
        raise ValueError("Reference must be a credential-free HTTPS URL.")
    return normalized


class SourceProvenance(ContentRecord):
    acquisition_method: AcquisitionMethod
    acquired_at: datetime
    source_reference: str | None = None
    provider: str | None = None
    provider_record_id: str | None = None
    notes: str = ""

    @field_validator("acquired_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Provenance timestamps must include a timezone.")
        return value.astimezone(UTC)

    @field_validator("source_reference")
    @classmethod
    def reject_local_file_references(cls, value: str | None) -> str | None:
        if value is not None:
            normalized = value.strip()
            if not normalized or normalized.lower().startswith(("file:", "/", "\\")):
                raise ValueError(
                    "Source references must not be local filesystem paths."
                )
            return normalized
        return value


class SourceAsset(ContentRecord):
    asset_id: str
    owner_id: str
    category: AssetCategory
    content_sha256: str
    provenance: SourceProvenance
    storage_key: str | None = None
    state: AssetState = AssetState.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("asset_id", "owner_id")
    @classmethod
    def require_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 200:
            raise ValueError("Identifiers must be non-empty and at most 200 characters.")
        return normalized

    @field_validator("content_sha256")
    @classmethod
    def validate_asset_hash(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("Asset content hash must be a lowercase SHA-256 digest.")
        return value

    @field_validator("storage_key")
    @classmethod
    def validate_storage_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if (
            not _SAFE_STORAGE_KEY_RE.fullmatch(value)
            or ".." in value
            or value in {".", ".."}
        ):
            raise ValueError("Storage keys must be opaque, path-safe identifiers.")
        return value


class ContentEditSegment(ContentRecord):
    """Owner-scoped edit decision referencing one registered source asset."""

    source_asset_id: str
    source_in_seconds: float
    source_out_seconds: float
    timeline_order: int

    @field_validator("source_asset_id")
    @classmethod
    def validate_segment_asset_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 200:
            raise ValueError("Segment source asset ID must be bounded and non-empty.")
        return normalized

    @model_validator(mode="after")
    def validate_segment_bounds(self) -> "ContentEditSegment":
        if (
            not math.isfinite(self.source_in_seconds)
            or not math.isfinite(self.source_out_seconds)
            or self.source_in_seconds < 0
            or self.source_out_seconds <= self.source_in_seconds
            or not 1 <= self.timeline_order <= 100
        ):
            raise ValueError("Edit segment bounds or order are invalid.")
        return self


class ContentEditPlanSource(ContentRecord):
    """Verified, factual media evidence used by a creative plan."""

    source_asset_id: str
    content_sha256: str
    duration_seconds: float
    scene_boundary_seconds: tuple[float, ...] = ()

    @field_validator("source_asset_id")
    @classmethod
    def validate_plan_source_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 200:
            raise ValueError("Plan source asset ID must be bounded and non-empty.")
        return normalized

    @field_validator("content_sha256")
    @classmethod
    def validate_plan_source_hash(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("Plan source hash must be a lowercase SHA-256 digest.")
        return value

    @model_validator(mode="after")
    def validate_source_evidence(self) -> "ContentEditPlanSource":
        if not math.isfinite(self.duration_seconds) or self.duration_seconds <= 0:
            raise ValueError("Plan source duration must be finite and positive.")
        previous = 0.0
        for boundary in self.scene_boundary_seconds:
            if (
                not math.isfinite(boundary)
                or boundary <= previous
                or boundary <= 0
                or boundary >= self.duration_seconds
            ):
                raise ValueError("Plan scene boundaries must be ordered within the source.")
            previous = boundary
        return self


class ContentEditPlanSegment(ContentRecord):
    """A planned source interval with factual selection evidence."""

    source_asset_id: str
    source_in_seconds: float
    source_out_seconds: float
    timeline_order: int
    selection_method: Literal[
        "nearest_detected_scene_cut",
        "evenly_spaced_sample",
        "distinct_detected_scene_interval",
        "repeated_detected_scene_interval",
        "insufficient_scene_duration_fallback",
        "repeated_source_interval_fallback",
    ]
    # For scene-interval methods, this identifies the interval's opening cut;
    # None identifies the first interval beginning at source time zero.
    selected_scene_cut_seconds: float | None = None
    transition_before: Literal["start", "hard_cut"]

    @model_validator(mode="after")
    def validate_plan_segment(self) -> "ContentEditPlanSegment":
        if (
            not self.source_asset_id.strip()
            or len(self.source_asset_id) > 200
            or not math.isfinite(self.source_in_seconds)
            or not math.isfinite(self.source_out_seconds)
            or self.source_in_seconds < 0
            or self.source_out_seconds <= self.source_in_seconds
            or not 1 <= self.timeline_order <= 100
        ):
            raise ValueError("Creative plan segment identity, bounds, or order is invalid.")
        if self.selection_method == "nearest_detected_scene_cut":
            if (
                self.selected_scene_cut_seconds is None
                or not math.isfinite(self.selected_scene_cut_seconds)
                or not self.source_in_seconds
                <= self.selected_scene_cut_seconds
                <= self.source_out_seconds
            ):
                raise ValueError("Scene-cut selection requires an in-range observed cut.")
        elif self.selection_method in {
            "distinct_detected_scene_interval",
            "repeated_detected_scene_interval",
        }:
            if (
                self.selected_scene_cut_seconds is not None
                and (
                    not math.isfinite(self.selected_scene_cut_seconds)
                    or self.selected_scene_cut_seconds <= 0
                )
            ):
                raise ValueError("Detected scene intervals require a positive opening cut.")
        elif self.selected_scene_cut_seconds is not None:
            raise ValueError("Evenly spaced selection cannot claim a scene-cut decision.")
        return self


class ContentEditPlanOutputConstraints(ContentRecord):
    """Only output constraints currently honored by the private CRE renderer."""

    frame_rate_fps: Literal[30] = 30
    max_width: Literal[1280] = 1280
    max_height: Literal[1280] = 1280
    max_duration_seconds: float = Field(default=60.0, gt=0, le=60)
    audio_sample_rate_hz: Literal[48_000] = 48_000


class ContentCreativeEditPlan(ContentRecord):
    """Versioned, immutable record of a planner's substantiated decisions."""

    schema_version: Literal["cre-creative-plan-v1"] = "cre-creative-plan-v1"
    project_id: str
    revision: int = Field(ge=1)
    creative_direction: str = Field(max_length=20_000)
    sources: tuple[ContentEditPlanSource, ...] = Field(min_length=1, max_length=12)
    segments: tuple[ContentEditPlanSegment, ...] = Field(min_length=2, max_length=100)
    audio_policy: Literal["preserve_segment_source_audio"] = (
        "preserve_segment_source_audio"
    )
    output_constraints: ContentEditPlanOutputConstraints = Field(
        default_factory=ContentEditPlanOutputConstraints
    )

    @field_validator("project_id")
    @classmethod
    def validate_plan_project_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 200:
            raise ValueError("Plan project ID must be bounded and non-empty.")
        return normalized

    @field_validator("creative_direction")
    @classmethod
    def validate_creative_direction(cls, value: str) -> str:
        if any(ord(character) < 32 and character not in "\t\n\r" for character in value):
            raise ValueError("Creative direction contains unsupported control characters.")
        return value

    @model_validator(mode="after")
    def validate_plan_consistency(self) -> "ContentCreativeEditPlan":
        source_ids = [source.source_asset_id for source in self.sources]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("Creative plan sources must be unique.")
        source_by_id = {source.source_asset_id: source for source in self.sources}
        total_duration = 0.0
        distinct_scene_starts: dict[str, set[float]] = {
            source_id: set() for source_id in source_ids
        }
        selected_segments: list[ContentEditPlanSegment] = []
        for expected_order, segment in enumerate(self.segments, start=1):
            source = source_by_id.get(segment.source_asset_id)
            if source is None:
                raise ValueError("Creative plan segment references an undeclared source.")
            if segment.timeline_order != expected_order:
                raise ValueError("Creative plan segments must have contiguous timeline order.")
            if segment.source_out_seconds > source.duration_seconds:
                raise ValueError("Creative plan segment exceeds its verified source duration.")
            if segment.transition_before != (
                "start" if expected_order == 1 else "hard_cut"
            ):
                raise ValueError("Creative plan only supports a start and subsequent hard cuts.")
            if segment.selected_scene_cut_seconds is not None and (
                segment.selected_scene_cut_seconds
                not in source.scene_boundary_seconds
            ):
                raise ValueError("Selected scene cut is absent from observed source evidence.")
            if segment.selection_method in {
                "distinct_detected_scene_interval",
                "repeated_detected_scene_interval",
                "insufficient_scene_duration_fallback",
                "repeated_source_interval_fallback",
            }:
                scene_start = segment.selected_scene_cut_seconds or 0.0
                if scene_start not in (0.0, *source.scene_boundary_seconds):
                    raise ValueError("Selected scene interval has no observed opening boundary.")
                scene_end = next(
                    (
                        boundary
                        for boundary in source.scene_boundary_seconds
                        if boundary > scene_start
                    ),
                    source.duration_seconds,
                )
                segment_duration = (
                    segment.source_out_seconds - segment.source_in_seconds
                )
                eligible_scene_starts = {
                    start
                    for start, end in zip(
                        (0.0, *source.scene_boundary_seconds),
                        (*source.scene_boundary_seconds, source.duration_seconds),
                    )
                    if end - start + 1e-6 >= segment_duration
                }
                if segment.selection_method in {
                    "distinct_detected_scene_interval",
                    "repeated_detected_scene_interval",
                }:
                    if (
                        segment.source_in_seconds < scene_start - 1e-6
                        or segment.source_out_seconds > scene_end + 1e-6
                    ):
                        raise ValueError(
                            "Selected interval exceeds its detected scene boundaries."
                        )
                    if segment.selection_method == "distinct_detected_scene_interval":
                        if scene_start in distinct_scene_starts[source.source_asset_id]:
                            raise ValueError(
                                "A detected scene interval cannot be selected as distinct twice."
                            )
                        distinct_scene_starts[source.source_asset_id].add(scene_start)
                    else:
                        if (
                            scene_start
                            not in distinct_scene_starts[source.source_asset_id]
                            or not eligible_scene_starts.issubset(
                                distinct_scene_starts[source.source_asset_id]
                            )
                            or not any(
                                prior.source_asset_id == segment.source_asset_id
                                and (
                                    prior.selected_scene_cut_seconds or 0.0
                                )
                                == scene_start
                                and prior.selection_method
                                == "distinct_detected_scene_interval"
                                for prior in selected_segments
                            )
                        ):
                            raise ValueError(
                                "Scene repetition is allowed only after all usable "
                                "distinct scene intervals have been selected."
                            )
                else:
                    if eligible_scene_starts:
                        raise ValueError(
                            "Scene-duration fallback is invalid while a usable "
                            "detected scene interval exists."
                        )
                    overlaps_prior = any(
                        prior.source_asset_id == segment.source_asset_id
                        and min(
                            segment.source_out_seconds,
                            prior.source_out_seconds,
                        )
                        - max(
                            segment.source_in_seconds,
                            prior.source_in_seconds,
                        )
                        >= 0.5
                        * min(
                            segment_duration,
                            prior.source_out_seconds - prior.source_in_seconds,
                        )
                        for prior in selected_segments
                    )
                    if (
                        segment.selection_method
                        == "repeated_source_interval_fallback"
                    ) != overlaps_prior:
                        raise ValueError(
                            "Insufficient-scene fallback must explicitly identify "
                            "repeated source intervals."
                        )
            selected_segments.append(segment)
            total_duration += segment.source_out_seconds - segment.source_in_seconds
        if total_duration > self.output_constraints.max_duration_seconds:
            raise ValueError("Creative plan exceeds its supported output duration.")
        return self


class ContentEditPlanAudio(ContentRecord):
    """One explicit, single-source audio decision for a video segment."""

    mode: Literal["source_audio", "silence"]
    source_asset_id: str | None
    source_in_seconds: float | None
    source_out_seconds: float | None
    gain_db: float
    timing_policy: Literal["trim_pad_to_video_segment"]

    @model_validator(mode="after")
    def validate_audio_decision(self) -> "ContentEditPlanAudio":
        if not math.isfinite(self.gain_db) or not -24.0 <= self.gain_db <= 6.0:
            raise ValueError("Segment audio gain must be finite and between -24 and +6 dB.")
        if self.mode == "silence":
            if (
                self.source_asset_id is not None
                or self.source_in_seconds is not None
                or self.source_out_seconds is not None
                or self.gain_db != 0.0
            ):
                raise ValueError(
                    "Intentional silence requires null source bounds and zero gain."
                )
        elif (
            self.source_asset_id is None
            or not self.source_asset_id.strip()
            or len(self.source_asset_id) > 200
            or self.source_in_seconds is None
            or self.source_out_seconds is None
            or not math.isfinite(self.source_in_seconds)
            or not math.isfinite(self.source_out_seconds)
            or self.source_in_seconds < 0
            or self.source_out_seconds <= self.source_in_seconds
        ):
            raise ValueError("Source audio requires an identity and valid source bounds.")
        return self


class ContentCreativeEditPlanSegmentV2(ContentRecord):
    """A video interval with an explicit, independently selected audio interval."""

    source_asset_id: str
    source_in_seconds: float
    source_out_seconds: float
    timeline_order: int
    selection_method: Literal[
        "nearest_detected_scene_cut",
        "evenly_spaced_sample",
        "distinct_detected_scene_interval",
        "repeated_detected_scene_interval",
        "insufficient_scene_duration_fallback",
        "repeated_source_interval_fallback",
    ]
    selected_scene_cut_seconds: float | None = None
    transition_before: Literal["start", "hard_cut"]
    audio: ContentEditPlanAudio

    @model_validator(mode="after")
    def validate_video_segment(self) -> "ContentCreativeEditPlanSegmentV2":
        ContentEditPlanSegment.model_validate(
            self.model_dump(mode="json", exclude={"audio"})
        )
        return self


class ContentCreativeEditPlanV2(ContentRecord):
    """Versioned edit plan with explicit audio decisions and peak protection."""

    schema_version: Literal["cre-creative-plan-v2"] = "cre-creative-plan-v2"
    project_id: str
    revision: int = Field(ge=1)
    creative_direction: str = Field(max_length=20_000)
    sources: tuple[ContentEditPlanSource, ...] = Field(min_length=1, max_length=12)
    segments: tuple[ContentCreativeEditPlanSegmentV2, ...] = Field(
        min_length=2,
        max_length=100,
    )
    audio_policy: Literal["explicit_segment_audio"] = "explicit_segment_audio"
    peak_protection_policy: Literal[
        "sample_peak_limit_minus_1_5_dbfs"
    ] = "sample_peak_limit_minus_1_5_dbfs"
    output_constraints: ContentEditPlanOutputConstraints = Field(
        default_factory=ContentEditPlanOutputConstraints
    )

    @field_validator("project_id")
    @classmethod
    def validate_plan_project_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 200:
            raise ValueError("Plan project ID must be bounded and non-empty.")
        return normalized

    @field_validator("creative_direction")
    @classmethod
    def validate_creative_direction(cls, value: str) -> str:
        if any(ord(character) < 32 and character not in "\t\n\r" for character in value):
            raise ValueError("Creative direction contains unsupported control characters.")
        return value

    @model_validator(mode="after")
    def validate_plan_consistency(self) -> "ContentCreativeEditPlanV2":
        source_ids = [source.source_asset_id for source in self.sources]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("Creative plan sources must be unique.")
        source_by_id = {source.source_asset_id: source for source in self.sources}
        total_duration = 0.0
        ContentCreativeEditPlan.model_validate(
            {
                "project_id": self.project_id,
                "revision": self.revision,
                "creative_direction": self.creative_direction,
                "sources": self.sources,
                "segments": tuple(
                ContentEditPlanSegment.model_validate(
                    segment.model_dump(mode="json", exclude={"audio"})
                )
                for segment in self.segments
                ),
                "audio_policy": "preserve_segment_source_audio",
                "output_constraints": self.output_constraints,
            }
        )
        for expected_order, segment in enumerate(self.segments, start=1):
            source = source_by_id.get(segment.source_asset_id)
            if source is None or segment.timeline_order != expected_order:
                raise ValueError(
                    "Creative plan segment identity or timeline order is invalid."
                )
            if segment.source_out_seconds > source.duration_seconds:
                raise ValueError("Creative plan segment exceeds its verified source duration.")
            if segment.transition_before != (
                "start" if expected_order == 1 else "hard_cut"
            ):
                raise ValueError("Creative plan only supports a start and subsequent hard cuts.")
            audio = segment.audio
            if audio.mode == "source_audio":
                audio_source = source_by_id.get(audio.source_asset_id or "")
                if (
                    audio_source is None
                    or audio.source_in_seconds is None
                    or audio.source_out_seconds is None
                    or audio.source_out_seconds > audio_source.duration_seconds
                ):
                    raise ValueError(
                        "Audio source must be a declared plan source with in-range bounds."
                    )
                if abs(
                    (audio.source_out_seconds - audio.source_in_seconds)
                    - (segment.source_out_seconds - segment.source_in_seconds)
                ) > 1e-6:
                    raise ValueError(
                        "Audio source interval duration must match its video segment."
                    )
            total_duration += segment.source_out_seconds - segment.source_in_seconds
        if total_duration > self.output_constraints.max_duration_seconds:
            raise ValueError("Creative plan exceeds its supported output duration.")
        return self


ContentCreativeEditPlanRecord = ContentCreativeEditPlan | ContentCreativeEditPlanV2


class ContentEditProject(ContentRecord):
    project_id: str
    owner_id: str
    source_asset_ids: tuple[str, ...]
    script: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("project_id", "owner_id")
    @classmethod
    def validate_project_ids(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 200:
            raise ValueError("Project and owner IDs must be bounded and non-empty.")
        return normalized

    @field_validator("source_asset_ids")
    @classmethod
    def validate_source_assets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not 1 <= len(value) <= 12:
            raise ValueError("A CRE project must contain 1 to 12 source assets.")
        normalized = tuple(dict.fromkeys(asset_id.strip() for asset_id in value))
        if not normalized or any(not asset_id or len(asset_id) > 200 for asset_id in normalized):
            raise ValueError("Project source asset IDs are invalid.")
        return normalized

    @field_validator("script")
    @classmethod
    def validate_project_script(cls, value: str) -> str:
        if not value.strip() or len(value) > 20_000:
            raise ValueError("Project script must contain 1 to 20,000 Unicode characters.")
        if any(ord(character) < 32 and character not in "\t\n\r" for character in value):
            raise ValueError("Project script contains unsupported control characters.")
        return value


class OwnershipClaim(ContentRecord):
    claim_id: str
    asset_id: str
    claimant_id: str
    rights_holder: str
    authority_description: str
    state: OwnershipClaimState = OwnershipClaimState.REVIEW_REQUIRED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("claim_id", "asset_id", "claimant_id", "rights_holder")
    @classmethod
    def require_nonempty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 200:
            raise ValueError("Claim identifiers and rights holder must be bounded text.")
        return normalized

    @field_validator("authority_description")
    @classmethod
    def require_authority_description(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 4000:
            raise ValueError("An explicit, bounded authority description is required.")
        return normalized


class RightsTerms(ContentRecord):
    platform: ContentPlatform
    account_scope: str
    territories: tuple[str, ...]
    permitted_actions: frozenset[RightsAction]
    valid_from: datetime
    valid_until: datetime
    attribution_required: bool
    attribution_text: str | None = None
    max_duration_seconds: int

    @field_validator("account_scope")
    @classmethod
    def require_explicit_account_scope(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 200:
            raise ValueError("Account scope must be an exact ID or explicit '*'.")
        return normalized

    @field_validator("territories")
    @classmethod
    def require_explicit_territories(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({value.strip().upper() for value in values}))
        if not normalized or any(
            value != "*" and not _TERRITORY_RE.fullmatch(value)
            for value in normalized
        ):
            raise ValueError("Territories must contain ISO-style codes or explicit '*'.")
        return normalized

    @field_validator("valid_from", "valid_until")
    @classmethod
    def normalize_validity_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Rights validity timestamps must include a timezone.")
        return value.astimezone(UTC)

    @field_validator("permitted_actions")
    @classmethod
    def require_explicit_actions(
        cls,
        value: frozenset[RightsAction],
    ) -> frozenset[RightsAction]:
        if not value:
            raise ValueError("A rights grant must explicitly list permitted actions.")
        return value

    @field_validator("max_duration_seconds")
    @classmethod
    def require_duration_limit(cls, value: int) -> int:
        if isinstance(value, bool) or not 1 <= value <= 86_400:
            raise ValueError("A grant must explicitly limit use duration.")
        return value

    @field_validator("attribution_text")
    @classmethod
    def validate_attribution_text(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            if not value or len(value) > 1000:
                raise ValueError("Attribution text must be bounded non-empty text.")
        return value

    @model_validator(mode="after")
    def validate_terms(self) -> RightsTerms:
        if self.valid_until <= self.valid_from:
            raise ValueError("Rights grant end must be later than its start.")
        if self.attribution_required != (self.attribution_text is not None):
            raise ValueError(
                "Attribution text must be supplied exactly when attribution is required."
            )
        return self

    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json")
        payload["permitted_actions"] = sorted(
            action.value for action in self.permitted_actions
        )
        payload["territories"] = sorted(self.territories)
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class RightsAgreement(ContentRecord):
    agreement_id: str
    version: int
    grantor_id: str
    authority_id: str
    rights_holder: str
    grantee_owner_id: str
    asset_ids: tuple[str, ...]
    terms: RightsTerms
    evidence_reference: str
    evidence_sha256: str
    status: AgreementStatus = AgreementStatus.PENDING_REVIEW
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator(
        "agreement_id", "grantor_id", "authority_id", "rights_holder",
        "grantee_owner_id", "evidence_reference",
    )
    @classmethod
    def validate_agreement_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 2048:
            raise ValueError("Agreement fields must be bounded and non-empty.")
        return normalized

    @field_validator("version")
    @classmethod
    def validate_agreement_version(cls, value: int) -> int:
        if isinstance(value, bool) or value < 1:
            raise ValueError("Agreement versions start at one.")
        return value

    @field_validator("asset_ids")
    @classmethod
    def validate_agreement_assets(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({value.strip() for value in values if value.strip()}))
        if not normalized or len(normalized) != len(values):
            raise ValueError("Agreement must have unique, explicit asset scope.")
        return normalized

    @field_validator("evidence_sha256")
    @classmethod
    def validate_agreement_evidence_hash(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("Agreement evidence must have a lowercase SHA-256 hash.")
        return value

    def fingerprint(self) -> str:
        payload = self.model_dump(
            mode="json",
            exclude={"status", "created_at"},
        )
        payload["asset_ids"] = sorted(self.asset_ids)
        terms = self.terms.model_dump(mode="json")
        terms["permitted_actions"] = sorted(
            action.value for action in self.terms.permitted_actions
        )
        terms["territories"] = sorted(self.terms.territories)
        payload["terms"] = terms
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class RightsEvidence(ContentRecord):
    evidence_id: str
    asset_id: str
    claim_id: str
    submitted_by: str
    evidence_kind: str
    evidence_bytes: bytes
    evidence_sha256: str
    grantor_identity: str
    grantor_authority: str
    claimed_terms: RightsTerms
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator(
        "evidence_id",
        "asset_id",
        "claim_id",
        "submitted_by",
        "evidence_kind",
        "grantor_identity",
        "grantor_authority",
    )
    @classmethod
    def require_evidence_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 2000:
            raise ValueError("Evidence identity and description fields are required.")
        return normalized

    @field_validator("evidence_bytes")
    @classmethod
    def require_evidence_content(cls, value: bytes) -> bytes:
        if not value or len(value) > 2_000_000:
            raise ValueError("Evidence must contain between 1 byte and 2 MB.")
        return value

    @field_validator("evidence_sha256")
    @classmethod
    def validate_evidence_hash(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("Evidence hash must be a lowercase SHA-256 digest.")
        return value

    @model_validator(mode="after")
    def ensure_evidence_hash_matches(self) -> RightsEvidence:
        if hashlib.sha256(self.evidence_bytes).hexdigest() != self.evidence_sha256:
            raise ValueError("Evidence bytes do not match their SHA-256 digest.")
        return self


class RightsEvidenceReview(ContentRecord):
    review_id: str
    evidence_id: str
    decision: EvidenceDecision
    reviewer_principal: str
    evidence_sha256: str
    verified_terms_sha256: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RightsGrant(ContentRecord):
    grant_id: str
    asset_id: str
    claim_id: str
    evidence_id: str
    grantor_identity: str
    grantor_authority: str
    grantee_owner_id: str
    terms: RightsTerms
    revoked: bool = False
    revoked_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    agreement_id: str | None = None
    agreement_version: int | None = None

    @field_validator(
        "grant_id",
        "asset_id",
        "claim_id",
        "evidence_id",
        "grantor_identity",
        "grantor_authority",
        "grantee_owner_id",
    )
    @classmethod
    def require_grant_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 2000:
            raise ValueError("Rights grant identifiers and authority are required.")
        return normalized

    @model_validator(mode="after")
    def require_agreement_reference_pair(self) -> RightsGrant:
        if (self.agreement_id is None) != (self.agreement_version is None):
            raise ValueError("Rights grant agreement ID and version must be paired.")
        if self.agreement_version is not None and self.agreement_version < 1:
            raise ValueError("Rights grant agreement version must be positive.")
        return self


class RightsUse(ContentRecord):
    asset_id: str
    expected_asset_sha256: str
    platform: ContentPlatform
    account_id: str
    territory: str
    actions: frozenset[RightsAction]
    commercial_intent: bool
    monetization_intent: bool
    duration_seconds: int
    attribution_text: str | None = None
    platform_policy_facts: dict[str, bool] = Field(default_factory=dict)

    @field_validator("expected_asset_sha256")
    @classmethod
    def validate_expected_asset_hash(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("Expected asset hash must be a lowercase SHA-256 digest.")
        return value

    @field_validator("account_id")
    @classmethod
    def require_account_id(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 200:
            raise ValueError("A specific platform account/channel ID is required.")
        return value

    @field_validator("territory")
    @classmethod
    def validate_territory(cls, value: str) -> str:
        value = value.strip().upper()
        if not _TERRITORY_RE.fullmatch(value):
            raise ValueError("Use territory must be an ISO-style country code.")
        return value

    @field_validator("actions")
    @classmethod
    def require_use_actions(
        cls,
        value: frozenset[RightsAction],
    ) -> frozenset[RightsAction]:
        if not value:
            raise ValueError("The intended rights actions must be explicit.")
        return value

    @field_validator("duration_seconds")
    @classmethod
    def validate_use_duration(cls, value: int) -> int:
        if isinstance(value, bool) or value < 1:
            raise ValueError("Intended use duration must be a positive number of seconds.")
        return value

    @model_validator(mode="after")
    def require_intent_rights(self) -> RightsUse:
        if self.commercial_intent and RightsAction.COMMERCIAL_USE not in self.actions:
            raise ValueError("Commercial intent requires commercial-use permission.")
        if self.monetization_intent and (
            not self.commercial_intent
            or RightsAction.MONETIZE not in self.actions
        ):
            raise ValueError(
                "Monetization intent requires explicit commercial and monetization rights."
            )
        return self


class SourceReferenceRecord(ContentRecord):
    intake_id: str
    asset_id: str
    owner_id: str
    source_reference: str
    acquisition_method: AcquisitionMethod
    provenance: SourceProvenance
    evidence_reference: str | None = None
    claimed_source_sha256: str | None = None
    source_hash_status: SourceHashStatus = SourceHashStatus.UNVERIFIED
    access_permission_state: SourceAccessPermissionState = (
        SourceAccessPermissionState.UNKNOWN
    )
    target_context: ContentTargetContext
    rights_use: RightsUse | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("intake_id", "asset_id", "owner_id")
    @classmethod
    def require_intake_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 200:
            raise ValueError("Source intake identifiers must be bounded and non-empty.")
        return normalized

    @field_validator("source_reference", "evidence_reference")
    @classmethod
    def validate_reference(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_https_reference(value)

    @field_validator("claimed_source_sha256")
    @classmethod
    def validate_claimed_hash(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256_RE.fullmatch(value):
            raise ValueError("Claimed source digest must be a lowercase SHA-256 value.")
        return value

    @model_validator(mode="after")
    def validate_intake_scope(self) -> SourceReferenceRecord:
        if self.acquisition_method != self.provenance.acquisition_method:
            raise ValueError("Intake and provenance acquisition methods must match.")
        if self.provenance.source_reference != self.source_reference:
            raise ValueError(
                "Provenance must explicitly identify the registered source reference."
            )
        expected_platform = {
            ContentTargetContext.YOUTUBE_SHORTS: ContentPlatform.YOUTUBE,
            ContentTargetContext.YOUTUBE_KIDS_AUDIENCE: ContentPlatform.YOUTUBE,
            ContentTargetContext.INSTAGRAM_REELS: ContentPlatform.INSTAGRAM,
            ContentTargetContext.SNAPCHAT_SPOTLIGHT: None,
        }[self.target_context]
        if expected_platform is None:
            if self.rights_use is not None:
                raise ValueError(
                    "Snapchat rights cannot be evaluated by the configured policy."
                )
        elif self.rights_use is None or self.rights_use.platform != expected_platform:
            raise ValueError("Source intake requires a matching platform rights scope.")
        elif (
            self.rights_use.asset_id != self.asset_id
            or self.rights_use.actions != frozenset({RightsAction.ANALYZE})
            or self.rights_use.commercial_intent
            or self.rights_use.monetization_intent
        ):
            raise ValueError(
                "Lifecycle readiness requires an exact, non-commercial analyze use."
            )
        return self


class ContentLifecycleRecord(ContentRecord):
    intake_id: str
    asset_id: str
    asset_sha256: str
    target_context: ContentTargetContext
    state: ContentLifecycleState = ContentLifecycleState.REGISTERED
    rights_decision_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ContentLifecycleEvent(ContentRecord):
    sequence: int
    intake_id: str
    from_state: ContentLifecycleState | None = None
    to_state: ContentLifecycleState
    actor: str
    reason: str
    rights_decision_id: str | None = None
    created_at: datetime


class ContentProcessingJob(ContentRecord):
    job_id: str
    owner_id: str
    session_id: str
    command_id: str
    asset_id: str
    asset_sha256: str
    target_context: ContentTargetContext
    rights_use: RightsUse
    state: ProcessingJobState
    created_at: datetime
    updated_at: datetime


class ContentProcessingOutput(ContentRecord):
    job_id: str
    output_key: str
    content_sha256: str
    byte_size: int
    created_at: datetime


class ContentClipRender(ContentRecord):
    render_id: str
    owner_id: str
    command_id: str
    candidate_id: str
    transcript_id: str
    asset_id: str
    asset_sha256: str
    target_context: ContentTargetContext
    rights_use: RightsUse
    start_seconds: float
    end_seconds: float
    source_duration_seconds: float | None = None
    output_duration_seconds: float | None = None
    has_video: bool | None = None
    has_audio: bool | None = None
    state: ProcessingJobState
    output_key: str | None = None
    output_sha256: str | None = None
    output_byte_size: int | None = None
    quality_output_sha256: str | None = None
    quality_status: str | None = None
    quality_black_frames: int | None = None
    quality_frozen_frames: int | None = None
    quality_black_ranges_json: str | None = None
    quality_frozen_ranges_json: str | None = None
    quality_revision_count: int | None = None
    created_at: datetime
    updated_at: datetime


class ContentClipRenderEvent(ContentRecord):
    sequence: int
    render_id: str
    from_state: ProcessingJobState | None = None
    to_state: ProcessingJobState
    actor: str
    reason: str
    created_at: datetime


class ContentProcessingEvent(ContentRecord):
    sequence: int
    job_id: str
    from_state: ProcessingJobState | None = None
    to_state: ProcessingJobState
    actor: str
    reason: str
    created_at: datetime


class RightsEvaluation(ContentRecord):
    decision_id: str
    asset_id: str
    use: RightsUse
    copyright_state: RightsState
    platform_rights_state: RightsState
    platform_compliance_state: PlatformDecisionState = PlatformDecisionState.UNKNOWN
    publishing_authorization_state: PlatformDecisionState = (
        PlatformDecisionState.NOT_AUTHORIZED
    )
    monetization_permission: bool = False
    monetization_eligibility_state: PlatformDecisionState = (
        PlatformDecisionState.UNKNOWN
    )
    applicable_grant_ids: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    policy_version: str
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ContentAssetUse(ContentRecord):
    use: RightsUse


class ContentPackage(ContentRecord):
    package_id: str
    owner_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ContentPackageVersion(ContentRecord):
    package_id: str
    version: int
    platform: ContentPlatform
    account_id: str
    artifact_sha256: str
    metadata_sha256: str
    metadata: dict[str, Any]
    asset_uses: tuple[ContentAssetUse, ...]
    review_state: ReviewState = ReviewState.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: int) -> int:
        if isinstance(value, bool) or value < 1:
            raise ValueError("Content package versions start at one.")
        return value

    @field_validator("artifact_sha256", "metadata_sha256")
    @classmethod
    def validate_package_hash(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("Package artifact and metadata hashes must be SHA-256.")
        return value

    @model_validator(mode="after")
    def validate_version_content(self) -> ContentPackageVersion:
        if not self.asset_uses:
            raise ValueError("A content package version must reference source assets.")
        asset_ids = [item.use.asset_id for item in self.asset_uses]
        if len(set(asset_ids)) != len(asset_ids):
            raise ValueError(
                "Combine each source asset's intended actions into one package use."
            )
        if any(RightsAction.PUBLISH not in item.use.actions for item in self.asset_uses):
            raise ValueError(
                "Every package source use must explicitly cover the intended publish action."
            )
        if not self.account_id.strip():
            raise ValueError("A package version must target a specific account/channel.")
        if any(
            item.use.platform != self.platform
            or item.use.account_id != self.account_id
            for item in self.asset_uses
        ):
            raise ValueError(
                "Every source use must match the package platform and account."
            )
        calculated_metadata_hash = hashlib.sha256(
            json.dumps(
                self.metadata,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        if calculated_metadata_hash != self.metadata_sha256:
            raise ValueError("Package metadata hash does not match its content.")
        return self


class ReviewApproval(ContentRecord):
    approval_id: str
    package_id: str
    package_version: int
    artifact_sha256: str
    metadata_sha256: str
    platform: ContentPlatform
    account_id: str
    permitted_action: RightsAction
    principal: str
    session_id: str
    rights_snapshot_sha256: str
    expires_at: datetime
    nonce: str
    review_state: ReviewState
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ContentPublicationRecord(ContentRecord):
    package_id: str
    package_version: int
    platform: ContentPlatform
    account_id: str
    state: PublicationState = PublicationState.NOT_PUBLISHED


class PublishingVisibility(StrEnum):
    PRIVATE = "private"
    UNLISTED = "unlisted"
    PUBLIC = "public"


class PublishingJobState(StrEnum):
    AWAITING_APPROVAL = "awaiting_approval"
    QUEUED = "queued"
    DISPATCHING = "dispatching"
    MOCK_ACCEPTED = "mock_accepted"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class PublishingApprovalState(StrEnum):
    APPROVED = "approved"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


class PublishingJob(ContentRecord):
    job_id: str
    owner_id: str
    package_id: str
    package_version: int
    render_id: str
    output_sha256: str
    metadata_sha256: str
    platform: ContentPlatform
    account_id: str
    visibility: PublishingVisibility
    intended_uses: tuple[RightsUse, ...]
    synthetic_media_disclosed: bool
    rights_decision_ids: tuple[str, ...]
    rights_snapshot_sha256: str
    idempotency_key: str
    request_fingerprint: str
    state: PublishingJobState
    created_at: datetime
    updated_at: datetime

    @field_validator(
        "output_sha256",
        "metadata_sha256",
        "rights_snapshot_sha256",
        "request_fingerprint",
    )
    @classmethod
    def validate_publishing_hash(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("Publishing fingerprints must be lowercase SHA-256.")
        return value

    @field_validator("idempotency_key")
    @classmethod
    def validate_publishing_idempotency_key(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 200:
            raise ValueError("A bounded publishing idempotency key is required.")
        return value

    @field_validator("package_version")
    @classmethod
    def validate_publishing_package_version(cls, value: int) -> int:
        if isinstance(value, bool) or value < 1:
            raise ValueError("Publishing package versions must be positive.")
        return value

    @field_validator(
        "job_id",
        "owner_id",
        "package_id",
        "render_id",
        "account_id",
    )
    @classmethod
    def validate_publishing_identity(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Publishing identities must not be empty.")
        return value

    @model_validator(mode="after")
    def validate_publishing_binding(self) -> PublishingJob:
        if not self.intended_uses or not self.rights_decision_ids:
            raise ValueError("A publishing job requires verified source-use decisions.")
        if len(self.intended_uses) != len(self.rights_decision_ids):
            raise ValueError("Each publishing source use requires one rights decision.")
        if len(set(self.rights_decision_ids)) != len(self.rights_decision_ids):
            raise ValueError("Publishing rights decisions must be unique.")
        for use in self.intended_uses:
            if (
                use.platform != self.platform
                or use.account_id != self.account_id
                or RightsAction.PUBLISH not in use.actions
                or RightsAction.CREATE_DERIVATIVE not in use.actions
            ):
                raise ValueError("Publishing use does not match the approved target.")
        return self


class PublishingReleaseApproval(ContentRecord):
    approval_id: str
    job_id: str
    request_fingerprint: str
    package_id: str
    package_version: int
    output_sha256: str
    metadata_sha256: str
    platform: ContentPlatform
    account_id: str
    visibility: PublishingVisibility
    intended_uses: tuple[RightsUse, ...]
    synthetic_media_disclosed: bool
    rights_decision_ids: tuple[str, ...]
    rights_snapshot_sha256: str
    request_principal: str
    approved_by: str
    expires_at: datetime
    state: PublishingApprovalState
    created_at: datetime

    @field_validator(
        "request_fingerprint",
        "output_sha256",
        "metadata_sha256",
        "rights_snapshot_sha256",
    )
    @classmethod
    def validate_release_approval_hash(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("Release approval bindings must be lowercase SHA-256.")
        return value

    @field_validator("package_version")
    @classmethod
    def validate_release_package_version(cls, value: int) -> int:
        if isinstance(value, bool) or value < 1:
            raise ValueError("Release approval package versions must be positive.")
        return value

    @field_validator(
        "approval_id",
        "job_id",
        "package_id",
        "account_id",
        "request_principal",
        "approved_by",
    )
    @classmethod
    def validate_release_identity(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Release approval identities must not be empty.")
        return value

    @model_validator(mode="after")
    def validate_independent_release_approval(self) -> PublishingReleaseApproval:
        if self.approved_by == self.request_principal:
            raise ValueError("Release approval must be independent of the requester.")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("Release approval expiry must include a timezone.")
        if (
            not self.intended_uses
            or len(self.intended_uses) != len(self.rights_decision_ids)
            or len(set(self.rights_decision_ids)) != len(self.rights_decision_ids)
        ):
            raise ValueError("Release approval requires unique decisions for each source.")
        for use in self.intended_uses:
            if (
                use.platform != self.platform
                or use.account_id != self.account_id
                or RightsAction.PUBLISH not in use.actions
                or RightsAction.CREATE_DERIVATIVE not in use.actions
            ):
                raise ValueError("Release approval use does not match its exact target.")
        return self


class PublishingAttemptIdentity(ContentRecord):
    attempt_id: str
    attempt_number: int
    owner_id: str
    job_id: str
    account_id: str
    provider: PublishingProvider
    release_identity: str
    package_id: str
    package_version: int
    output_sha256: str

    @field_validator("attempt_id")
    @classmethod
    def validate_attempt_id(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            value,
        ):
            raise ValueError("Publishing attempt ID must be a UUID.")
        return value

    @field_validator("attempt_number", "package_version")
    @classmethod
    def validate_attempt_number(cls, value: int) -> int:
        if isinstance(value, bool) or value < 1:
            raise ValueError("Publishing attempt and package versions must be positive.")
        return value

    @field_validator("release_identity", "output_sha256")
    @classmethod
    def validate_attempt_hash(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("Publishing attempt bindings require lowercase SHA-256.")
        return value

    @field_validator("owner_id", "job_id", "account_id", "package_id")
    @classmethod
    def validate_attempt_identity(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Publishing attempt identities must not be empty.")
        return value


class PublishingAttemptState(StrEnum):
    CREATED = "created"
    INITIATION_INTENT = "initiation_intent"
    SESSION_READY = "session_ready"
    UPLOADING = "uploading"
    INITIATION_UNCERTAIN = "initiation_uncertain"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    REMOTE_UPLOADED = "remote_uploaded"
    PROCESSING = "processing"
    PUBLISHED = "published"
    AVAILABLE_PRIVATE = "available_private"
    FAILED = "failed"
    BLOCKED = "blocked"
    SESSION_EXPIRED = "session_expired"
    OPERATOR_ACTION_REQUIRED = "operator_action_required"


class PublishingAttemptCode(StrEnum):
    ATTEMPT_CREATED = "attempt_created"
    INITIATION_AUTHORIZED = "initiation_authorized"
    SESSION_STORED = "session_stored"
    UPLOAD_STARTED = "upload_started"
    INITIATION_OUTCOME_UNKNOWN = "initiation_outcome_unknown"
    RECONCILIATION_STARTED = "reconciliation_started"
    REMOTE_UPLOAD_CONFIRMED = "remote_upload_confirmed"
    PROCESSING_STARTED = "processing_started"
    PUBLICATION_CONFIRMED = "publication_confirmed"
    PRIVATE_AVAILABILITY_CONFIRMED = "private_availability_confirmed"
    PROVIDER_REJECTED = "provider_rejected"
    RIGHTS_BLOCKED = "rights_blocked"
    AUTHORIZATION_BLOCKED = "authorization_blocked"
    SECRET_STORE_UNAVAILABLE = "secret_store_unavailable"
    SECRET_BINDING_MISMATCH = "secret_binding_mismatch"
    SESSION_EXPIRED_CONFIRMED = "session_expired_confirmed"
    REMOTE_OUTCOME_UNKNOWN = "remote_outcome_unknown"
    RECONCILIATION_MISMATCH = "reconciliation_mismatch"
    PROCESSING_FAILED_CONFIRMED = "processing_failed_confirmed"
    OFFSET_CONFIRMED = "offset_confirmed"
    SECRET_REFERENCE_PREPARED = "secret_reference_prepared"
    SECRET_REFERENCE_ACTIVATED = "secret_reference_activated"
    SECRET_DELETION_PENDING = "secret_deletion_pending"
    SECRET_INVALIDATED = "secret_invalidated"
    OPERATOR_RECONCILED = "operator_reconciled"
    OPERATOR_SAFE_TO_REPLACE = "operator_safe_to_replace"
    INTERNAL_ERROR = "internal_error"


class PublishingAttemptSecretState(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    INVALIDATED = "invalidated"
    DELETION_PENDING = "deletion_pending"
    DELETION_FAILED = "deletion_failed"
    DELETED = "deleted"


class PublishingAttempt(ContentRecord):
    identity: PublishingAttemptIdentity
    state: PublishingAttemptState
    confirmed_offset: int
    total_bytes: int
    revision: int
    error_code: PublishingAttemptCode | None = None
    operator_resolution_code: str | None = None
    operator_resolved_by: str | None = None
    operator_resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("confirmed_offset")
    @classmethod
    def validate_confirmed_offset(cls, value: int) -> int:
        if isinstance(value, bool) or value < 0:
            raise ValueError("Confirmed publishing offset must not be negative.")
        return value

    @field_validator("total_bytes", "revision")
    @classmethod
    def validate_positive_attempt_value(cls, value: int) -> int:
        if isinstance(value, bool) or value < 1:
            raise ValueError("Publishing attempt size and revision must be positive.")
        return value

    @model_validator(mode="after")
    def validate_attempt_progress(self) -> PublishingAttempt:
        if self.confirmed_offset > self.total_bytes:
            raise ValueError("Confirmed publishing offset exceeds total content size.")
        if (
            self.operator_resolution_code is None
            and (
                self.operator_resolved_by is not None
                or self.operator_resolved_at is not None
            )
        ):
            raise ValueError("Operator resolution metadata must be complete.")
        if (
            self.operator_resolution_code is not None
            and (
                self.operator_resolution_code != "safe_to_replace"
                or self.state != PublishingAttemptState.BLOCKED
                or not self.operator_resolved_by
                or self.operator_resolved_at is None
            )
        ):
            raise ValueError("Publishing attempt operator resolution is invalid.")
        return self


class PublishingAttemptEvent(ContentRecord):
    sequence: int
    attempt_id: str
    revision: int
    event_type: str
    from_state: PublishingAttemptState | None = None
    to_state: PublishingAttemptState
    code: PublishingAttemptCode
    actor: str
    command_id: str
    reference_id: str | None = None
    created_at: datetime

    @field_validator("event_type")
    @classmethod
    def validate_attempt_event_type(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z_]{1,48}", value):
            raise ValueError("Publishing attempt event type is invalid.")
        return value

    @field_validator("actor")
    @classmethod
    def validate_attempt_actor(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}", value):
            raise ValueError("Publishing attempt actor is invalid.")
        return value

    @field_validator("command_id")
    @classmethod
    def validate_attempt_command_id(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", value):
            raise ValueError("Publishing attempt command ID is invalid.")
        return value


class UploadSecretBinding(ContentRecord):
    attempt: PublishingAttemptIdentity
    purpose: UploadSecretPurpose
    schema_version: int

    @field_validator("schema_version")
    @classmethod
    def validate_secret_schema_version(cls, value: int) -> int:
        if isinstance(value, bool) or value < 1:
            raise ValueError("Secret schema version must be positive.")
        return value


class OpaqueSecretReference(ContentRecord):
    reference_id: str
    schema_version: int

    @field_validator("reference_id")
    @classmethod
    def validate_reference_id(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            value,
        ):
            raise ValueError("Opaque secret reference must be a UUID.")
        return value

    @field_validator("schema_version")
    @classmethod
    def validate_reference_schema_version(cls, value: int) -> int:
        if isinstance(value, bool) or value < 1:
            raise ValueError("Secret reference schema version must be positive.")
        return value


class SecretGeneration(ContentRecord):
    value: int

    @field_validator("value")
    @classmethod
    def validate_generation(cls, value: int) -> int:
        if isinstance(value, bool) or value < 1:
            raise ValueError("Secret generation must be positive.")
        return value


class PublishingAttemptSecretReference(ContentRecord):
    attempt_id: str
    purpose: UploadSecretPurpose
    reference: OpaqueSecretReference
    generation: SecretGeneration
    status: PublishingAttemptSecretState
    created_at: datetime
    updated_at: datetime


class UploadSessionSecretHandle(ContentRecord):
    binding: UploadSecretBinding
    reference: OpaqueSecretReference
    generation: SecretGeneration

    @model_validator(mode="after")
    def validate_handle_schema(self) -> UploadSessionSecretHandle:
        if self.binding.schema_version != self.reference.schema_version:
            raise ValueError("Secret binding and reference schema versions must match.")
        return self


class OAuthCredentialBinding(ContentRecord):
    credential_id: str
    owner_id: str
    account_id: str
    provider: PublishingProvider
    schema_version: int

    @field_validator("credential_id")
    @classmethod
    def validate_credential_id(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            value,
        ):
            raise ValueError("Credential ID must be a UUID.")
        return value

    @field_validator("owner_id", "account_id")
    @classmethod
    def validate_credential_identity(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Credential identities must not be empty.")
        return value

    @field_validator("schema_version")
    @classmethod
    def validate_credential_schema_version(cls, value: int) -> int:
        if isinstance(value, bool) or value < 1:
            raise ValueError("Credential schema version must be positive.")
        return value


class PublishingJobEvent(ContentRecord):
    sequence: int
    job_id: str
    from_state: PublishingJobState | None = None
    to_state: PublishingJobState
    actor: str
    reason: str
    created_at: datetime
