from __future__ import annotations

import hashlib
import math
import subprocess
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from ultron.application.content.visual_intelligence import (
    OllamaVisionProvider,
    VisualFrameInput,
    VideoFrameSampler,
    _ProviderFrameResponse,
    select_frame_timestamps,
)
from ultron.domain.content_visual_intelligence import (
    VisualCapability,
    VisualConfidence,
    VisualConfidenceSemantics,
    VisualFrameEvidence,
    VisualInferenceDevice,
    VisualInferenceResources,
    VisualModelCapabilities,
)


def _frame_evidence(image: bytes = b"synthetic-image-bytes") -> VisualFrameEvidence:
    return VisualFrameEvidence(
        frame_id="frame-0001",
        asset_id="synthetic-asset",
        asset_sha256="a" * 64,
        timestamp_seconds=0.25,
        frame_sha256=hashlib.sha256(image).hexdigest(),
        width=384,
        height=256,
    )


def test_visual_provider_rejects_non_loopback_and_non_http_endpoints():
    for endpoint in (
        "https://127.0.0.1:11434",
        "http://example.test:11434",
        "http://127.0.0.1:11434/api",
        "http://user:password@127.0.0.1:11434",
    ):
        with pytest.raises(ValueError, match="loopback"):
            OllamaVisionProvider("local-model", base_url=endpoint)


def test_visual_provider_never_accepts_a_memory_floor_below_two_gibibytes():
    with pytest.raises(ValueError, match="limits"):
        OllamaVisionProvider(
            "local-model",
            minimum_available_memory_bytes=2 * 1024**3 - 1,
        )


def test_visual_frame_input_requires_exact_sha256_binding():
    image = b"synthetic-image-bytes"
    evidence = _frame_evidence(image)

    frame = VisualFrameInput(evidence=evidence, image_bytes=image)

    assert frame.evidence.frame_sha256 == hashlib.sha256(image).hexdigest()
    with pytest.raises(ValueError, match="hash does not match"):
        VisualFrameInput(evidence=evidence, image_bytes=b"different-image")


def test_visual_frame_input_rejects_empty_or_oversized_images():
    evidence = _frame_evidence()
    with pytest.raises(ValueError, match="non-empty"):
        VisualFrameInput(evidence=evidence, image_bytes=b"")
    with pytest.raises(ValueError, match="byte limit"):
        VisualFrameInput(
            evidence=evidence,
            image_bytes=b"x" * (8 * 1024 * 1024 + 1),
        )


def test_provider_response_contract_rejects_free_text_and_incomplete_json():
    with pytest.raises(ValidationError):
        _ProviderFrameResponse.model_validate_json(
            '"A stick figure stands to the left of a table."'
        )
    with pytest.raises(ValidationError):
        _ProviderFrameResponse.model_validate_json(
            '{"objects":[{"label":"table"}]}'
        )


def test_single_image_provider_cannot_advertise_cross_frame_reasoning():
    with pytest.raises(ValidationError, match="multiple frames"):
        VisualModelCapabilities(
            provider_id="test-provider",
            model_id="test-model",
            model_version="v1",
            capabilities=(
                VisualCapability.IMAGE_UNDERSTANDING,
                VisualCapability.ORDERED_FRAME_ANALYSIS,
            ),
            max_sequence_frames=1,
        )


def test_confidence_requires_explicit_semantics_and_calibration_identity():
    with pytest.raises(ValidationError, match="explicit semantics"):
        VisualConfidence(value=0.7)
    with pytest.raises(ValidationError, match="calibration identifier"):
        VisualConfidence(
            value=0.7,
            semantics=VisualConfidenceSemantics.CALIBRATED,
        )
    confidence = VisualConfidence(
        value=0.7,
        semantics=VisualConfidenceSemantics.CALIBRATED,
        calibration_id="eval-set-v1",
    )
    assert confidence.value == 0.7


def test_visual_resources_do_not_claim_unknown_backend_without_evidence():
    resources = VisualInferenceResources(
        duration_ms=10,
        device=VisualInferenceDevice.UNKNOWN,
    )
    assert resources.device == VisualInferenceDevice.UNKNOWN
    with pytest.raises(ValidationError, match="supporting evidence"):
        VisualInferenceResources(
            duration_ms=10,
            device=VisualInferenceDevice.CPU,
        )


def test_non_finite_visual_frame_timestamp_is_rejected():
    with pytest.raises(ValidationError, match="finite"):
        VisualFrameEvidence(
            frame_id="frame",
            asset_id="asset",
            asset_sha256="a" * 64,
            timestamp_seconds=math.inf,
            frame_sha256="b" * 64,
            width=100,
            height=100,
        )


def test_frame_timestamp_selection_is_ordered_bounded_and_scene_aware():
    timestamps = select_frame_timestamps(
        10,
        max_frames=6,
        scene_boundaries_seconds=(4.0,),
    )

    assert len(timestamps) == 6
    assert tuple(sorted(set(timestamps))) == timestamps
    assert all(0 <= timestamp < 10 for timestamp in timestamps)
    assert any(abs(timestamp - 4.0) < 0.001 for timestamp in timestamps)
    with pytest.raises(ValueError, match="configuration"):
        select_frame_timestamps(
            10,
            scene_boundaries_seconds=(6.0, 4.0),
        )


def test_video_frame_sampler_preserves_timestamp_and_hash_provenance():
    with tempfile.TemporaryDirectory(prefix="cre-synthetic-video-test-") as directory:
        video_path = Path(directory) / "synthetic.mp4"
        result = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=320x240:rate=5:duration=2",
                "-an",
                "-c:v",
                "mpeg4",
                "-threads",
                "1",
                "-pix_fmt",
                "yuv420p",
                "-y",
                str(video_path),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
        source_bytes = video_path.read_bytes()

    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    sampled = VideoFrameSampler().sample_video_frames(
        source_bytes,
        asset_id="synthetic-video",
        expected_asset_sha256=source_sha256,
        timestamps_seconds=(0.25, 1.25),
    )

    assert sampled.asset_id == "synthetic-video"
    assert sampled.asset_sha256 == source_sha256
    assert 1.9 <= sampled.duration_seconds <= 2.1
    assert tuple(
        frame.evidence.timestamp_seconds for frame in sampled.frames
    ) == (0.25, 1.25)
    for frame in sampled.frames:
        assert frame.image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        assert frame.evidence.asset_sha256 == source_sha256
        assert frame.evidence.width == 320
        assert frame.evidence.height == 240
        assert hashlib.sha256(frame.image_bytes).hexdigest() == frame.evidence.frame_sha256


def test_video_frame_sampler_rejects_mismatched_source_digest_before_ffmpeg():
    sampler = VideoFrameSampler(ffmpeg="missing-ffmpeg", ffprobe="missing-ffprobe")

    with pytest.raises(ValueError, match="registered source digest"):
        sampler.sample_video_frames(
            b"not a video",
            asset_id="synthetic-video",
            expected_asset_sha256="a" * 64,
            timestamps_seconds=(0.5,),
        )
