from __future__ import annotations

import math
import re
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from .content import ContentRecord
from .content_visual_intelligence import VisualConfidence


_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


class HighlightEvidenceKind(StrEnum):
    VISUAL_OBSERVATION = "visual_observation"
    SCENE_TRANSITION = "scene_transition"
    TRANSCRIPT = "transcript"
    AUDIO_EVENT = "audio_event"
    NARRATIVE_CONTEXT = "narrative_context"
    EVENT_IMPORTANCE = "event_importance"


class HighlightEvidence(ContentRecord):
    evidence_id: str = Field(min_length=1, max_length=256)
    asset_id: str = Field(min_length=1, max_length=128)
    asset_sha256: str
    kind: HighlightEvidenceKind
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    description: str = Field(min_length=1, max_length=2000)
    source_sha256: str
    confidence: VisualConfidence = Field(default_factory=VisualConfidence)
    importance_score: float | None = Field(default=None, ge=0, le=1)
    importance_rubric_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("start_seconds", "end_seconds")
    @classmethod
    def require_finite_timestamp(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Highlight evidence timestamps must be finite.")
        return value

    @field_validator("description")
    @classmethod
    def require_nonblank_description(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Highlight evidence description must not be blank.")
        return value.strip()

    @field_validator("source_sha256")
    @classmethod
    def require_source_sha256(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("Highlight evidence source hash must be lowercase SHA-256.")
        return value

    @field_validator("asset_sha256")
    @classmethod
    def require_asset_sha256(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("Highlight evidence asset hash must be lowercase SHA-256.")
        return value

    @model_validator(mode="after")
    def validate_interval_and_importance(self) -> HighlightEvidence:
        if self.end_seconds < self.start_seconds:
            raise ValueError("Highlight evidence interval is reversed.")
        is_importance = self.kind == HighlightEvidenceKind.EVENT_IMPORTANCE
        if is_importance != (
            self.importance_score is not None
            and self.importance_rubric_id is not None
        ) or (
            not is_importance
            and (
                self.importance_score is not None
                or self.importance_rubric_id is not None
            )
        ):
            raise ValueError(
                "Event-importance evidence requires a score and rubric identifier."
            )
        return self


class RankedHighlightCandidate(ContentRecord):
    candidate_id: str = Field(min_length=1, max_length=128)
    asset_id: str = Field(min_length=1, max_length=128)
    asset_sha256: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    rank: int = Field(gt=0)
    ranking_score: float = Field(ge=0, le=1)
    score_semantics: str = "deterministic_heuristic_not_probability"
    component_scores: dict[str, float]
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=500)
    reasons: tuple[str, ...] = Field(min_length=1, max_length=64)
    confidence_evidence: tuple[VisualConfidence, ...] = Field(default=(), max_length=128)

    @field_validator("asset_sha256")
    @classmethod
    def require_asset_sha256(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("Highlight candidate asset hash must be lowercase SHA-256.")
        return value

    @field_validator("start_seconds", "end_seconds", "ranking_score")
    @classmethod
    def require_finite_numbers(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Highlight candidate values must be finite.")
        return value

    @model_validator(mode="after")
    def validate_candidate(self) -> RankedHighlightCandidate:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Highlight candidate interval must have positive duration.")
        if self.score_semantics != "deterministic_heuristic_not_probability":
            raise ValueError("Highlight scores must declare heuristic semantics.")
        if any(
            not math.isfinite(value) or not 0 <= value <= 1
            for value in self.component_scores.values()
        ):
            raise ValueError("Highlight component scores must be finite values from 0 to 1.")
        return self


class HighlightAnalysis(ContentRecord):
    asset_id: str = Field(min_length=1, max_length=128)
    asset_sha256: str
    duration_seconds: float = Field(gt=0)
    available_evidence_kinds: tuple[HighlightEvidenceKind, ...] = Field(
        min_length=1,
        max_length=len(HighlightEvidenceKind),
    )
    candidates: tuple[RankedHighlightCandidate, ...] = Field(max_length=50)
    scoring_version: str = Field(min_length=1, max_length=128)

    @field_validator("asset_sha256")
    @classmethod
    def require_asset_sha256(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("Highlight analysis asset hash must be lowercase SHA-256.")
        return value

    @field_validator("duration_seconds")
    @classmethod
    def require_finite_duration(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Highlight analysis duration must be finite.")
        return value

    @model_validator(mode="after")
    def validate_analysis(self) -> HighlightAnalysis:
        if len(set(self.available_evidence_kinds)) != len(
            self.available_evidence_kinds
        ):
            raise ValueError("Available highlight evidence kinds must be unique.")
        previous_rank = 0
        for candidate in self.candidates:
            if (
                candidate.asset_id != self.asset_id
                or candidate.asset_sha256 != self.asset_sha256
                or candidate.end_seconds > self.duration_seconds
                or candidate.rank <= previous_rank
            ):
                raise ValueError(
                    "Highlight candidates must match the source and be in rank order."
                )
            previous_rank = candidate.rank
        return self
