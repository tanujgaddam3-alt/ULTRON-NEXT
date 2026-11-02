from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from typing import Any

from pydantic import Field, field_validator, model_validator

from .content import ContentRecord, ContentTargetContext


_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


class TranscriptionConfig(ContentRecord):
    language: str | None = Field(default=None, min_length=2, max_length=16)
    max_duration_seconds: float = Field(default=900.0, gt=0, le=900)
    max_input_bytes: int = Field(default=32 * 1024 * 1024, gt=0, le=32 * 1024 * 1024)
    max_runtime_seconds: float = Field(default=120.0, gt=0, le=600)
    max_segments: int = Field(default=5000, gt=0, le=5000)
    max_text_characters: int = Field(default=500_000, gt=0, le=500_000)
    max_segment_characters: int = Field(default=10_000, gt=0, le=10_000)


class AudioMetadata(ContentRecord):
    duration_seconds: float = Field(gt=0, le=900)
    sample_rate: int = Field(gt=0, le=48_000)
    channels: int = Field(gt=0, le=2)
    sample_width_bytes: int = Field(gt=0, le=4)
    codec: str = "pcm"
    byte_size: int = Field(gt=0, le=32 * 1024 * 1024)
    content_sha256: str

    @field_validator("duration_seconds")
    @classmethod
    def require_finite_duration(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Audio duration must be finite.")
        return value

    @field_validator("content_sha256")
    @classmethod
    def require_sha256(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("Audio content hash must be a lowercase SHA-256.")
        return value


class EngineTranscriptSegment(ContentRecord):
    start_seconds: float
    end_seconds: float
    text: str
    confidence: float | None = None
    speaker_id: str | None = None


class EngineTranscript(ContentRecord):
    segments: tuple[EngineTranscriptSegment, ...]
    language: str | None = None
    raw_output_sha256: str

    @field_validator("raw_output_sha256")
    @classmethod
    def require_output_sha256(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("Engine output hash must be a lowercase SHA-256.")
        return value


class TranscriptSegment(ContentRecord):
    segment_id: str
    start_seconds: float
    end_seconds: float
    text: str = Field(min_length=1, max_length=10_000)
    confidence: float | None = Field(default=None, ge=0, le=1)
    speaker_id: str | None = Field(default=None, max_length=128)

    @field_validator("start_seconds", "end_seconds")
    @classmethod
    def require_finite_timestamp(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Transcript timestamps must be finite.")
        return value

    @field_validator("confidence")
    @classmethod
    def require_finite_confidence(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("Transcript confidence must be finite.")
        return value

    @field_validator("text")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Transcript segment text must not be blank.")
        return value

    @model_validator(mode="after")
    def validate_interval(self) -> TranscriptSegment:
        if self.start_seconds < 0 or self.end_seconds <= self.start_seconds:
            raise ValueError("Transcript segment timestamps must form a positive interval.")
        return self


class TranscriptRecord(ContentRecord):
    transcript_id: str
    job_id: str
    asset_id: str
    asset_sha256: str
    target_context: ContentTargetContext
    duration_seconds: float = Field(gt=0, le=900)
    engine_id: str = Field(min_length=1, max_length=128)
    engine_version: str = Field(min_length=1, max_length=128)
    language: str | None = Field(default=None, max_length=16)
    raw_output_sha256: str
    validation_version: str = "transcript-validation-v1"
    segments: tuple[TranscriptSegment, ...]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("asset_sha256", "raw_output_sha256")
    @classmethod
    def require_hash(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("Transcript hashes must be lowercase SHA-256 values.")
        return value

    @model_validator(mode="after")
    def validate_segments(self) -> TranscriptRecord:
        if not math.isfinite(self.duration_seconds):
            raise ValueError("Transcript duration must be finite.")
        if len(self.segments) > 5000:
            raise ValueError("Transcript contains too many segments.")
        previous_start = -1.0
        total_characters = 0
        for segment in self.segments:
            if segment.start_seconds < previous_start:
                raise ValueError("Transcript segments must retain chronological engine order.")
            if segment.end_seconds > self.duration_seconds:
                raise ValueError("Transcript segment extends beyond the media duration.")
            previous_start = segment.start_seconds
            total_characters += len(segment.text)
        if total_characters > 500_000:
            raise ValueError("Transcript text exceeds the configured total limit.")
        return self

    def fingerprint(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json", exclude={"created_at"}),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class HighlightConfig(ContentRecord):
    min_duration_seconds: float = Field(default=15.0, gt=0, le=60)
    max_duration_seconds: float = Field(default=60.0, gt=0, le=60)
    max_candidates: int = Field(default=10, gt=0, le=50)
    min_score: float = Field(default=0.38, ge=0, le=1)
    max_windows_evaluated: int = Field(default=20_000, gt=0, le=50_000)
    scene_boundary_seconds: tuple[float, ...] = Field(default=(), max_length=18_000)
    scene_analysis_performed: bool = False

    @field_validator("min_duration_seconds", "max_duration_seconds", "min_score")
    @classmethod
    def require_finite_config(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Highlight configuration values must be finite.")
        return value

    @field_validator("scene_boundary_seconds")
    @classmethod
    def require_finite_scene_boundaries(
        cls,
        values: tuple[float, ...],
    ) -> tuple[float, ...]:
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("Scene boundary timestamps must be finite and non-negative.")
        if any(left >= right for left, right in zip(values, values[1:])):
            raise ValueError("Scene boundary timestamps must be strictly increasing.")
        return values

    @model_validator(mode="after")
    def validate_duration_range(self) -> HighlightConfig:
        if self.min_duration_seconds > self.max_duration_seconds:
            raise ValueError("Minimum candidate duration exceeds maximum duration.")
        return self


class HighlightCandidate(ContentRecord):
    candidate_id: str
    run_id: str
    transcript_id: str
    asset_id: str
    asset_sha256: str
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    rank: int = Field(gt=0)
    score: float = Field(ge=0, le=1)
    scores: dict[str, float]
    evidence: dict[str, tuple[str, ...]]
    transcript_segment_ids: tuple[str, ...]
    explanation: str
    warnings: tuple[str, ...] = ()
    scoring_version: str
    created_at: datetime

    @model_validator(mode="after")
    def validate_candidate_interval(self) -> HighlightCandidate:
        if not all(
            math.isfinite(value)
            for value in (self.start_seconds, self.end_seconds, self.duration_seconds, self.score)
        ):
            raise ValueError("Candidate timing and score values must be finite.")
        if (
            self.start_seconds < 0
            or self.end_seconds <= self.start_seconds
            or abs((self.end_seconds - self.start_seconds) - self.duration_seconds) > 0.001
        ):
            raise ValueError("Candidate timestamps and duration are inconsistent.")
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in self.scores.values()):
            raise ValueError("Candidate component scores must be finite values from 0 to 1.")
        return self


class HighlightRun(ContentRecord):
    run_id: str
    transcript_id: str
    transcript_fingerprint: str
    config: HighlightConfig
    scoring_version: str
    candidates: tuple[HighlightCandidate, ...]
    created_at: datetime


def transcript_payload_fingerprint(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
