from __future__ import annotations

import hashlib

import pytest

from ultron.application.content.highlights import analyze_highlights
from ultron.domain.content import ContentTargetContext
from ultron.domain.content_highlights import (
    HighlightEvidence,
    HighlightEvidenceKind,
)
from ultron.domain.content_intelligence import TranscriptRecord, TranscriptSegment
from ultron.domain.content_visual_intelligence import (
    VisualConfidenceSemantics,
)


_ASSET_SHA256 = hashlib.sha256(b"synthetic-video").hexdigest()


def _transcript(*, asset_id: str = "asset-1") -> TranscriptRecord:
    return TranscriptRecord(
        transcript_id="transcript-1",
        job_id="job-1",
        asset_id=asset_id,
        asset_sha256=_ASSET_SHA256,
        target_context=ContentTargetContext.YOUTUBE_SHORTS,
        duration_seconds=60,
        engine_id="test-transcriber",
        engine_version="test-v1",
        raw_output_sha256="b" * 64,
        segments=(
            TranscriptSegment(
                segment_id="segment-1",
                start_seconds=9,
                end_seconds=12,
                text="A synthetic transcript segment for deterministic ranking.",
                confidence=0.82,
            ),
        ),
    )


def test_highlight_ranking_combines_present_evidence_and_preserves_provenance():
    extra_evidence = (
        HighlightEvidence(
            evidence_id="scene-cut-1",
            asset_id="asset-1",
            asset_sha256=_ASSET_SHA256,
            kind=HighlightEvidenceKind.SCENE_TRANSITION,
            start_seconds=10,
            end_seconds=10,
            description="A scene transition was detected.",
            source_sha256="c" * 64,
        ),
        HighlightEvidence(
            evidence_id="audio-event-1",
            asset_id="asset-1",
            asset_sha256=_ASSET_SHA256,
            kind=HighlightEvidenceKind.AUDIO_EVENT,
            start_seconds=10.2,
            end_seconds=10.8,
            description="A short synthetic audio event.",
            source_sha256="d" * 64,
        ),
        HighlightEvidence(
            evidence_id="narrative-1",
            asset_id="asset-1",
            asset_sha256=_ASSET_SHA256,
            kind=HighlightEvidenceKind.NARRATIVE_CONTEXT,
            start_seconds=9,
            end_seconds=13,
            description="Operator-supplied synthetic narrative context.",
            source_sha256="e" * 64,
        ),
        HighlightEvidence(
            evidence_id="importance-1",
            asset_id="asset-1",
            asset_sha256=_ASSET_SHA256,
            kind=HighlightEvidenceKind.EVENT_IMPORTANCE,
            start_seconds=10,
            end_seconds=11,
            description="Synthetic event importance assessment.",
            source_sha256="f" * 64,
            importance_score=0.9,
            importance_rubric_id="synthetic-rubric-v1",
        ),
    )

    analysis = analyze_highlights(
        asset_id="asset-1",
        asset_sha256=_ASSET_SHA256,
        duration_seconds=60,
        transcript=_transcript(),
        additional_evidence=extra_evidence,
    )

    assert len(analysis.candidates) == 1
    candidate = analysis.candidates[0]
    assert candidate.rank == 1
    assert candidate.start_seconds <= 10
    assert candidate.end_seconds >= 12
    assert candidate.ranking_score == pytest.approx(0.5538461538461538)
    assert candidate.score_semantics == "deterministic_heuristic_not_probability"
    assert set(candidate.evidence_ids) == {
        "segment-1",
        "scene-cut-1",
        "audio-event-1",
        "narrative-1",
        "importance-1",
    }
    assert candidate.confidence_evidence[0].semantics == (
        VisualConfidenceSemantics.MODEL_REPORTED_UNCALIBRATED
    )
    assert len(candidate.reasons) == 5


def test_highlight_ranking_rejects_cross_asset_transcript_and_invalid_evidence():
    with pytest.raises(ValueError, match="does not match"):
        analyze_highlights(
            asset_id="asset-1",
            asset_sha256=_ASSET_SHA256,
            duration_seconds=60,
            transcript=_transcript(asset_id="another-owner-asset"),
        )

    with pytest.raises(ValueError, match="does not match"):
        analyze_highlights(
            asset_id="asset-1",
            asset_sha256=_ASSET_SHA256,
            duration_seconds=60,
            additional_evidence=(
                HighlightEvidence(
                    evidence_id="other-asset-audio",
                    asset_id="another-owner-asset",
                    asset_sha256=_ASSET_SHA256,
                    kind=HighlightEvidenceKind.AUDIO_EVENT,
                    start_seconds=1,
                    end_seconds=2,
                    description="Evidence bound to a different asset.",
                    source_sha256="d" * 64,
                ),
            ),
        )

    with pytest.raises(ValueError, match="requires a score"):
        HighlightEvidence(
            evidence_id="invalid-importance",
            asset_id="asset-1",
            asset_sha256=_ASSET_SHA256,
            kind=HighlightEvidenceKind.TRANSCRIPT,
            start_seconds=0,
            end_seconds=1,
            description="Invalid importance metadata.",
            source_sha256="a" * 64,
            importance_score=0.5,
        )


def test_highlight_analysis_requires_real_evidence_inputs():
    with pytest.raises(ValueError, match="at least one evidence source"):
        analyze_highlights(
            asset_id="asset-1",
            asset_sha256=_ASSET_SHA256,
            duration_seconds=60,
        )
