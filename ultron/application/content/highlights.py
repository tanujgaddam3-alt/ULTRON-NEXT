from __future__ import annotations

import hashlib
import math
import re

from ultron.domain.content_intelligence import TranscriptRecord
from ultron.domain.content_highlights import (
    HighlightAnalysis,
    HighlightEvidence,
    HighlightEvidenceKind,
    RankedHighlightCandidate,
)
from ultron.domain.content_visual_intelligence import (
    VisualConfidence,
    VisualConfidenceSemantics,
    VisualVideoAnalysis,
)


_SCORING_VERSION = "cross_modal_highlight_heuristic_v1"
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_CATEGORY_WEIGHTS = {
    HighlightEvidenceKind.VISUAL_OBSERVATION: 0.22,
    HighlightEvidenceKind.SCENE_TRANSITION: 0.12,
    HighlightEvidenceKind.TRANSCRIPT: 0.18,
    HighlightEvidenceKind.AUDIO_EVENT: 0.12,
    HighlightEvidenceKind.NARRATIVE_CONTEXT: 0.18,
    HighlightEvidenceKind.EVENT_IMPORTANCE: 0.18,
}
_CATEGORY_CAPS = {
    HighlightEvidenceKind.VISUAL_OBSERVATION: 3,
    HighlightEvidenceKind.SCENE_TRANSITION: 2,
    HighlightEvidenceKind.TRANSCRIPT: 3,
    HighlightEvidenceKind.AUDIO_EVENT: 2,
    HighlightEvidenceKind.NARRATIVE_CONTEXT: 2,
}


def analyze_highlights(
    *,
    asset_id: str,
    asset_sha256: str,
    duration_seconds: float,
    visual_analysis: VisualVideoAnalysis | None = None,
    transcript: TranscriptRecord | None = None,
    additional_evidence: tuple[HighlightEvidence, ...] = (),
    window_seconds: float = 20.0,
    max_candidates: int = 10,
) -> HighlightAnalysis:
    if (
        not asset_id
        or len(asset_id) > 128
        or not isinstance(asset_sha256, str)
        or not _SHA256_RE.fullmatch(asset_sha256)
        or not math.isfinite(duration_seconds)
        or duration_seconds <= 0
        or duration_seconds > 900
        or not math.isfinite(window_seconds)
        or window_seconds <= 0
        or type(max_candidates) is not int
        or not 1 <= max_candidates <= 50
    ):
        raise ValueError("Highlight analysis configuration or source identity is invalid.")
    if visual_analysis is not None and (
        visual_analysis.asset_id != asset_id
        or visual_analysis.asset_sha256 != asset_sha256
        or abs(visual_analysis.duration_seconds - duration_seconds) > 0.001
    ):
        raise ValueError("Visual analysis does not match the requested source asset.")
    if transcript is not None and (
        transcript.asset_id != asset_id
        or transcript.asset_sha256 != asset_sha256
        or abs(transcript.duration_seconds - duration_seconds) > 0.001
    ):
        raise ValueError("Transcript does not match the requested source asset.")
    if len(additional_evidence) > 5_000:
        raise ValueError("Additional highlight evidence exceeds its configured limit.")
    if any(not isinstance(item, HighlightEvidence) for item in additional_evidence):
        raise ValueError("Additional highlight evidence contains an invalid record.")

    evidence = list(_visual_evidence(visual_analysis))
    evidence.extend(_transcript_evidence(transcript))
    evidence.extend(additional_evidence)
    if not evidence:
        raise ValueError("Highlight analysis requires at least one evidence source.")
    if len(evidence) > 5_000:
        raise ValueError("Combined highlight evidence exceeds its configured limit.")
    if any(
        item.end_seconds > duration_seconds
        or item.kind
        in {
            HighlightEvidenceKind.VISUAL_OBSERVATION,
            HighlightEvidenceKind.TRANSCRIPT,
        }
        for item in additional_evidence
    ):
        raise ValueError("Additional evidence has invalid timing or an invalid source kind.")
    if any(
        item.asset_id != asset_id
        or item.asset_sha256 != asset_sha256
        or item.end_seconds > duration_seconds
        for item in evidence
    ):
        raise ValueError("Highlight evidence does not match the requested source asset.")
    evidence_ids = tuple(item.evidence_id for item in evidence)
    if len(set(evidence_ids)) != len(evidence_ids):
        raise ValueError("Highlight evidence identifiers must be unique.")

    available_kinds = tuple(
        kind for kind in HighlightEvidenceKind if any(item.kind == kind for item in evidence)
    )
    weight_total = sum(_CATEGORY_WEIGHTS[kind] for kind in available_kinds)
    window_duration = min(window_seconds, duration_seconds)
    proposed_windows = {
        _candidate_window(
            (item.start_seconds + item.end_seconds) / 2,
            duration_seconds,
            window_duration,
        )
        for item in evidence
    }

    evaluated: list[tuple[float, float, float, tuple[HighlightEvidence, ...], dict[str, float]]] = []
    for start, end in sorted(proposed_windows):
        overlapping = tuple(
            item
            for item in evidence
            if _overlaps(item, start, end)
        )
        if not overlapping:
            continue
        component_scores = _score_components(overlapping)
        score = sum(
            component_scores[kind.value] * _CATEGORY_WEIGHTS[kind]
            for kind in available_kinds
        ) / weight_total
        evaluated.append((score, start, end, overlapping, component_scores))

    evaluated.sort(
        key=lambda item: (
            -item[0],
            item[1],
            item[2],
            tuple(evidence_item.evidence_id for evidence_item in item[3]),
        )
    )
    selected: list[
        tuple[float, float, float, tuple[HighlightEvidence, ...], dict[str, float]]
    ] = []
    for candidate in evaluated:
        _, start, end, _, _ = candidate
        if any(_intersection_over_union(start, end, prior[1], prior[2]) > 0.65 for prior in selected):
            continue
        selected.append(candidate)
        if len(selected) == max_candidates:
            break

    candidates: list[RankedHighlightCandidate] = []
    for rank, (score, start, end, supporting, component_scores) in enumerate(
        selected,
        start=1,
    ):
        supporting_ids = tuple(sorted(item.evidence_id for item in supporting))
        candidate_identity = "|".join(
            (
                asset_id,
                asset_sha256,
                f"{start:.6f}",
                f"{end:.6f}",
                *supporting_ids,
                _SCORING_VERSION,
            )
        )
        reasons = tuple(
            f"{item.kind.value}: {item.description}"
            for item in supporting[:64]
        )
        confidence_evidence = tuple(
            item.confidence
            for item in supporting
            if item.confidence.value is not None
        )[:128]
        candidates.append(
            RankedHighlightCandidate(
                candidate_id="hi-" + hashlib.sha256(
                    candidate_identity.encode("utf-8")
                ).hexdigest()[:32],
                asset_id=asset_id,
                asset_sha256=asset_sha256,
                start_seconds=start,
                end_seconds=end,
                rank=rank,
                ranking_score=score,
                component_scores=component_scores,
                evidence_ids=supporting_ids[:500],
                reasons=reasons,
                confidence_evidence=confidence_evidence,
            )
        )

    return HighlightAnalysis(
        asset_id=asset_id,
        asset_sha256=asset_sha256,
        duration_seconds=duration_seconds,
        available_evidence_kinds=available_kinds,
        candidates=tuple(candidates),
        scoring_version=_SCORING_VERSION,
    )


def _visual_evidence(
    analysis: VisualVideoAnalysis | None,
) -> tuple[HighlightEvidence, ...]:
    if analysis is None:
        return ()
    result: list[HighlightEvidence] = []
    for frame in analysis.frames:
        evidence = frame.evidence
        at = evidence.timestamp_seconds
        source_hash = frame.raw_output_sha256
        result.append(
            HighlightEvidence(
                evidence_id=f"{evidence.frame_id}:scene",
                asset_id=analysis.asset_id,
                asset_sha256=analysis.asset_sha256,
                kind=HighlightEvidenceKind.VISUAL_OBSERVATION,
                start_seconds=at,
                end_seconds=at,
                description=frame.scene_description,
                source_sha256=source_hash,
            )
        )
        for index, item in enumerate(frame.objects):
            description = item.label
            if item.description:
                description = f"{description}: {item.description}"
            if item.spatial_relation:
                description = f"{description} ({item.spatial_relation})"
            result.append(
                HighlightEvidence(
                    evidence_id=f"{evidence.frame_id}:object:{index}",
                    asset_id=analysis.asset_id,
                    asset_sha256=analysis.asset_sha256,
                    kind=HighlightEvidenceKind.VISUAL_OBSERVATION,
                    start_seconds=at,
                    end_seconds=at,
                    description=description,
                    source_sha256=source_hash,
                    confidence=item.confidence,
                )
            )
        for kind, observations in (
            ("action", frame.actions),
            ("event", frame.events),
        ):
            for index, item in enumerate(observations):
                result.append(
                    HighlightEvidence(
                        evidence_id=f"{evidence.frame_id}:{kind}:{index}",
                        asset_id=analysis.asset_id,
                        asset_sha256=analysis.asset_sha256,
                        kind=HighlightEvidenceKind.VISUAL_OBSERVATION,
                        start_seconds=at,
                        end_seconds=at,
                        description=item.description,
                        source_sha256=source_hash,
                        confidence=item.confidence,
                    )
                )
    return tuple(result)


def _transcript_evidence(
    transcript: TranscriptRecord | None,
) -> tuple[HighlightEvidence, ...]:
    if transcript is None:
        return ()
    fingerprint = transcript.fingerprint()
    result: list[HighlightEvidence] = []
    for segment in transcript.segments:
        confidence = VisualConfidence()
        if segment.confidence is not None:
            confidence = VisualConfidence(
                value=segment.confidence,
                semantics=VisualConfidenceSemantics.MODEL_REPORTED_UNCALIBRATED,
            )
        result.append(
            HighlightEvidence(
                evidence_id=segment.segment_id,
                asset_id=transcript.asset_id,
                asset_sha256=transcript.asset_sha256,
                kind=HighlightEvidenceKind.TRANSCRIPT,
                start_seconds=segment.start_seconds,
                end_seconds=segment.end_seconds,
                description=segment.text,
                source_sha256=fingerprint,
                confidence=confidence,
            )
        )
    return tuple(result)


def _candidate_window(
    anchor: float,
    duration_seconds: float,
    window_duration: float,
) -> tuple[float, float]:
    start = min(max(anchor - window_duration / 2, 0), duration_seconds - window_duration)
    return start, start + window_duration


def _overlaps(item: HighlightEvidence, start: float, end: float) -> bool:
    if item.start_seconds == item.end_seconds:
        return start <= item.start_seconds <= end
    return item.start_seconds < end and item.end_seconds > start


def _score_components(
    evidence: tuple[HighlightEvidence, ...],
) -> dict[str, float]:
    grouped = {
        kind: tuple(item for item in evidence if item.kind == kind)
        for kind in HighlightEvidenceKind
    }
    scores = {
        kind.value: min(1.0, len(grouped[kind]) / _CATEGORY_CAPS[kind])
        for kind in _CATEGORY_CAPS
    }
    importance_scores = tuple(
        item.importance_score
        for item in grouped[HighlightEvidenceKind.EVENT_IMPORTANCE]
        if item.importance_score is not None
    )
    scores[HighlightEvidenceKind.EVENT_IMPORTANCE.value] = (
        max(importance_scores) if importance_scores else 0.0
    )
    return scores


def _intersection_over_union(
    start: float,
    end: float,
    other_start: float,
    other_end: float,
) -> float:
    intersection = max(0.0, min(end, other_end) - max(start, other_start))
    union = max(end, other_end) - min(start, other_start)
    return intersection / union if union > 0 else 0.0
