from __future__ import annotations

import math
import re
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from .content import ContentRecord


_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$")


class VisualCapability(StrEnum):
    IMAGE_UNDERSTANDING = "image_understanding"
    SCENE_DESCRIPTION = "scene_description"
    OBJECT_RECOGNITION = "object_recognition"
    ACTION_OBSERVATION = "action_observation"
    STRUCTURED_OUTPUT = "structured_output"
    ORDERED_FRAME_ANALYSIS = "ordered_frame_analysis"
    CROSS_FRAME_REASONING = "cross_frame_reasoning"
    NATIVE_VIDEO_INPUT = "native_video_input"
    SPATIAL_RELATIONSHIPS = "spatial_relationships"


class VisualConfidenceSemantics(StrEnum):
    UNAVAILABLE = "unavailable"
    MODEL_REPORTED_UNCALIBRATED = "model_reported_uncalibrated"
    CALIBRATED = "calibrated"


class VisualInferenceDevice(StrEnum):
    CPU = "cpu"
    GPU = "gpu"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class VisualModelCapabilities(ContentRecord):
    provider_id: str = Field(min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=256)
    model_version: str = Field(min_length=1, max_length=256)
    capabilities: tuple[VisualCapability, ...] = Field(min_length=1, max_length=32)
    max_image_dimension: int = Field(default=1024, gt=0, le=8192)
    max_sequence_frames: int = Field(default=1, gt=0, le=64)

    @field_validator("provider_id", "model_id", "model_version")
    @classmethod
    def require_safe_identifier(cls, value: str) -> str:
        if not _IDENTIFIER_RE.fullmatch(value):
            raise ValueError("Visual provider and model identifiers are malformed.")
        return value

    @model_validator(mode="after")
    def validate_capabilities(self) -> VisualModelCapabilities:
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("Visual capabilities must be unique.")
        if (
            VisualCapability.ORDERED_FRAME_ANALYSIS in self.capabilities
            and self.max_sequence_frames < 2
        ):
            raise ValueError(
                "Ordered frame analysis requires capacity for multiple frames."
            )
        return self


class VisualConfidence(ContentRecord):
    value: float | None = Field(default=None, ge=0, le=1)
    semantics: VisualConfidenceSemantics = VisualConfidenceSemantics.UNAVAILABLE
    calibration_id: str | None = Field(default=None, max_length=128)

    @field_validator("value")
    @classmethod
    def require_finite_confidence(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("Visual confidence must be finite.")
        return value

    @model_validator(mode="after")
    def validate_semantics(self) -> VisualConfidence:
        if self.value is None and self.semantics != VisualConfidenceSemantics.UNAVAILABLE:
            raise ValueError("Confidence semantics require a supplied confidence value.")
        if self.value is not None and self.semantics == VisualConfidenceSemantics.UNAVAILABLE:
            raise ValueError("A supplied confidence value requires explicit semantics.")
        if (
            self.semantics == VisualConfidenceSemantics.CALIBRATED
            and not self.calibration_id
        ):
            raise ValueError("Calibrated confidence requires a calibration identifier.")
        if (
            self.semantics != VisualConfidenceSemantics.CALIBRATED
            and self.calibration_id is not None
        ):
            raise ValueError("Calibration identifiers are only valid for calibrated scores.")
        return self


class VisualFrameEvidence(ContentRecord):
    frame_id: str = Field(min_length=1, max_length=128)
    asset_id: str = Field(min_length=1, max_length=128)
    asset_sha256: str
    timestamp_seconds: float = Field(ge=0)
    frame_sha256: str
    width: int = Field(gt=0, le=8192)
    height: int = Field(gt=0, le=8192)

    @field_validator("asset_sha256", "frame_sha256")
    @classmethod
    def require_sha256(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("Visual evidence hashes must be lowercase SHA-256.")
        return value

    @field_validator("timestamp_seconds")
    @classmethod
    def require_finite_timestamp(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Visual frame timestamp must be finite.")
        return value


class VisualObjectObservation(ContentRecord):
    label: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    spatial_relation: str | None = Field(default=None, max_length=256)
    confidence: VisualConfidence = Field(default_factory=VisualConfidence)

    @field_validator("label", "description", "spatial_relation")
    @classmethod
    def reject_blank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip() and value != "":
            raise ValueError("Visual observation text must not be blank.")
        if value == "":
            return value
        return value.strip() if value is not None else None


class VisualEventObservation(ContentRecord):
    description: str = Field(min_length=1, max_length=2000)
    event_type: str = Field(default="action_observation", min_length=1, max_length=64)
    confidence: VisualConfidence = Field(default_factory=VisualConfidence)

    @field_validator("description", "event_type")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Visual event text must not be blank.")
        return value.strip()


class VisualInferenceResources(ContentRecord):
    duration_ms: int = Field(ge=0)
    device: VisualInferenceDevice = VisualInferenceDevice.UNKNOWN
    device_evidence: str | None = Field(default=None, max_length=512)
    mem_available_before_bytes: int | None = Field(default=None, ge=0)
    mem_available_min_bytes: int | None = Field(default=None, ge=0)
    mem_available_after_bytes: int | None = Field(default=None, ge=0)
    swap_free_before_bytes: int | None = Field(default=None, ge=0)
    swap_free_after_bytes: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_memory_samples(self) -> VisualInferenceResources:
        samples = (
            self.mem_available_before_bytes,
            self.mem_available_min_bytes,
            self.mem_available_after_bytes,
        )
        if all(value is not None for value in samples):
            before, minimum, after = samples
            if minimum > before or minimum > after:
                raise ValueError("Minimum available memory exceeds an endpoint sample.")
        if (self.device == VisualInferenceDevice.UNKNOWN) != (
            self.device_evidence is None
        ):
            raise ValueError("Inference device classification requires supporting evidence.")
        return self


class VisualFrameAnalysis(ContentRecord):
    evidence: VisualFrameEvidence
    scene_description: str = Field(min_length=1, max_length=8000)
    raw_output_sha256: str
    objects: tuple[VisualObjectObservation, ...] = Field(default=(), max_length=64)
    actions: tuple[VisualEventObservation, ...] = Field(default=(), max_length=64)
    events: tuple[VisualEventObservation, ...] = Field(default=(), max_length=64)
    uncertainties: tuple[str, ...] = Field(default=(), max_length=64)
    provider_id: str = Field(min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=256)
    model_version: str = Field(min_length=1, max_length=256)
    resources: VisualInferenceResources

    @field_validator("scene_description")
    @classmethod
    def require_nonblank_description(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Visual scene description must not be blank.")
        return value.strip()

    @field_validator("raw_output_sha256")
    @classmethod
    def require_output_sha256(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("Visual model output hash must be lowercase SHA-256.")
        return value

    @field_validator("uncertainties")
    @classmethod
    def require_nonblank_uncertainties(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("Visual uncertainties must not be blank.")
        return tuple(value.strip() for value in values)


class VisualVideoAnalysis(ContentRecord):
    asset_id: str = Field(min_length=1, max_length=128)
    asset_sha256: str
    duration_seconds: float = Field(gt=0, le=900)
    frames: tuple[VisualFrameAnalysis, ...] = Field(min_length=1, max_length=64)
    temporal_method: str = Field(
        default="ordered_timestamped_frame_observations_v1",
        min_length=1,
        max_length=128,
    )
    cross_frame_reasoning_performed: bool = False

    @field_validator("asset_sha256")
    @classmethod
    def require_asset_sha256(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("Visual video asset hash must be lowercase SHA-256.")
        return value

    @field_validator("duration_seconds")
    @classmethod
    def require_finite_duration(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Visual video duration must be finite.")
        return value

    @model_validator(mode="after")
    def validate_frame_sequence(self) -> VisualVideoAnalysis:
        previous_timestamp = -1.0
        seen_frame_ids: set[str] = set()
        for frame in self.frames:
            evidence = frame.evidence
            if evidence.asset_id != self.asset_id or evidence.asset_sha256 != self.asset_sha256:
                raise ValueError("Frame evidence does not match the analyzed video asset.")
            if evidence.timestamp_seconds >= self.duration_seconds:
                raise ValueError("Frame evidence lies outside the source duration.")
            if evidence.timestamp_seconds <= previous_timestamp:
                raise ValueError("Video frame analyses must be in strict timestamp order.")
            if evidence.frame_id in seen_frame_ids:
                raise ValueError("Video frame identifiers must be unique.")
            seen_frame_ids.add(evidence.frame_id)
            previous_timestamp = evidence.timestamp_seconds
        if self.cross_frame_reasoning_performed and len(self.frames) < 2:
            raise ValueError("Cross-frame reasoning requires multiple frame observations.")
        return self
