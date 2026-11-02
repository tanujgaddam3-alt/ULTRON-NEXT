from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import re
import wave
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from ultron.domain.content_intelligence import (
    AudioMetadata,
    EngineTranscript,
    EngineTranscriptSegment,
    HighlightCandidate,
    HighlightConfig,
    TranscriptRecord,
    TranscriptSegment,
    TranscriptionConfig,
)


class TranscriptionEngine(Protocol):
    engine_id: str
    engine_version: str

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        language: str | None,
        max_segments: int,
        max_segment_characters: int,
        max_text_characters: int,
    ) -> EngineTranscript: ...


class FasterWhisperLocalEngine:
    """Local-only Faster-Whisper adapter; model files must already be present."""

    engine_id = "faster-whisper"

    def __init__(
        self,
        model_directory: str | Path,
        *,
        compute_type: str = "int8",
    ) -> None:
        directory = Path(model_directory)
        if (
            not directory.is_absolute()
            or directory.is_symlink()
            or not directory.is_dir()
            or not (directory / "model.bin").is_file()
            or not (directory / "config.json").is_file()
            or not (directory / "tokenizer.json").is_file()
        ):
            raise ValueError(
                "A fully provisioned local Faster-Whisper model directory is required."
            )
        if compute_type not in {"int8", "int8_float32", "float32"}:
            raise ValueError("Unsupported local CPU compute type.")
        self._model_directory = directory
        self._compute_type = compute_type
        self._model = None
        try:
            self.engine_version = importlib.metadata.version("faster-whisper")
        except importlib.metadata.PackageNotFoundError as exc:
            raise RuntimeError(
                "The optional faster-whisper runtime is not installed."
            ) from exc

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        language: str | None,
        max_segments: int,
        max_segment_characters: int,
        max_text_characters: int,
    ) -> EngineTranscript:
        if not isinstance(audio_bytes, bytes) or not audio_bytes:
            raise ValueError("Transcription requires non-empty validated audio bytes.")
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise RuntimeError(
                    "The optional faster-whisper runtime is not installed."
                ) from exc
            self._model = WhisperModel(
                str(self._model_directory),
                device="cpu",
                compute_type=self._compute_type,
                local_files_only=True,
            )
        raw_segments, info = self._model.transcribe(
            _decode_pcm_wave_for_faster_whisper(audio_bytes),
            language=language,
            task="transcribe",
            beam_size=5,
            vad_filter=False,
            without_timestamps=False,
        )
        collected: list[EngineTranscriptSegment] = []
        raw_items: list[dict[str, object]] = []
        total_characters = 0
        for segment in raw_segments:
            if len(collected) >= max_segments:
                raise ValueError("Transcription engine exceeded the segment limit.")
            text = segment.text
            if not isinstance(text, str) or len(text) > max_segment_characters:
                raise ValueError("Transcription engine exceeded the segment text limit.")
            total_characters += len(text)
            if total_characters > max_text_characters:
                raise ValueError("Transcription engine exceeded the total text limit.")
            item = {
                "start_seconds": segment.start,
                "end_seconds": segment.end,
                "text": text,
            }
            raw_items.append(item)
            collected.append(EngineTranscriptSegment(**item))
        raw_digest = hashlib.sha256(
            json.dumps(
                raw_items,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        return EngineTranscript(
            segments=tuple(collected),
            language=info.language,
            raw_output_sha256=raw_digest,
        )


def _decode_pcm_wave_for_faster_whisper(audio_bytes: bytes):
    import av
    import numpy as np

    resampler = av.audio.resampler.AudioResampler(
        format="s16",
        layout="mono",
        rate=16_000,
    )
    raw_audio = bytearray()
    with av.open(BytesIO(audio_bytes), mode="r") as container:
        for frame in container.decode(audio=0):
            for converted in resampler.resample(frame):
                raw_audio.extend(converted.to_ndarray().tobytes())
        for converted in resampler.resample(None):
            raw_audio.extend(converted.to_ndarray().tobytes())
    return np.frombuffer(raw_audio, dtype=np.int16).astype(np.float32) / 32_768.0


@dataclass(frozen=True)
class ValidatedAudio:
    metadata: AudioMetadata
    audio_bytes: bytes


def validate_pcm_wave(
    audio_bytes: bytes,
    *,
    expected_sha256: str,
    config: TranscriptionConfig,
) -> ValidatedAudio:
    if not isinstance(audio_bytes, bytes) or not audio_bytes:
        raise ValueError("Audio input must be non-empty bytes.")
    if len(audio_bytes) > config.max_input_bytes:
        raise ValueError("Audio input exceeds its configured size limit.")
    digest = hashlib.sha256(audio_bytes).hexdigest()
    if digest != expected_sha256:
        raise ValueError("Audio input does not match the registered asset SHA-256.")
    try:
        with wave.open(BytesIO(audio_bytes), "rb") as source:
            if source.getcomptype() != "NONE":
                raise ValueError("Only uncompressed PCM WAV audio is supported.")
            channels = source.getnchannels()
            sample_rate = source.getframerate()
            sample_width = source.getsampwidth()
            frames = source.getnframes()
            if (
                channels not in {1, 2}
                or sample_rate <= 0
                or sample_rate > 48_000
                or sample_width not in {1, 2, 3, 4}
                or frames <= 0
            ):
                raise ValueError("Audio stream has unsupported or empty PCM parameters.")
            duration = frames / sample_rate
            if not math.isfinite(duration) or duration > config.max_duration_seconds:
                raise ValueError("Audio duration exceeds its configured limit.")
            frame_bytes = source.readframes(frames)
            if len(frame_bytes) != frames * channels * sample_width:
                raise ValueError("PCM WAV frame data is truncated or malformed.")
    except (wave.Error, EOFError) as exc:
        raise ValueError("Audio input is not a supported PCM WAV stream.") from exc
    return ValidatedAudio(
        metadata=AudioMetadata(
            duration_seconds=duration,
            sample_rate=sample_rate,
            channels=channels,
            sample_width_bytes=sample_width,
            byte_size=len(audio_bytes),
            content_sha256=digest,
        ),
        audio_bytes=audio_bytes,
    )


def validate_engine_transcript(
    engine_result: EngineTranscript,
    *,
    transcript_id: str,
    job_id: str,
    asset_id: str,
    asset_sha256: str,
    target_context,
    duration_seconds: float,
    engine_id: str,
    engine_version: str,
    config: TranscriptionConfig,
) -> TranscriptRecord:
    if type(engine_result) is not EngineTranscript:
        raise ValueError("Transcription engine returned an unsupported result structure.")
    if not engine_result.segments:
        raise ValueError("Transcription engine returned no transcript segments.")
    if len(engine_result.segments) > config.max_segments:
        raise ValueError("Transcription engine exceeded the configured segment limit.")
    total_characters = 0
    validated: list[TranscriptSegment] = []
    for index, segment in enumerate(engine_result.segments):
        if type(segment) is not EngineTranscriptSegment:
            raise ValueError("Transcription engine returned a malformed segment.")
        if not isinstance(segment.text, str) or not segment.text.strip():
            raise ValueError("Transcription engine returned an empty segment.")
        if len(segment.text) > config.max_segment_characters:
            raise ValueError("Transcript segment exceeds the configured text limit.")
        total_characters += len(segment.text)
        if total_characters > config.max_text_characters:
            raise ValueError("Transcript exceeds the configured total text limit.")
        if (
            not math.isfinite(segment.start_seconds)
            or not math.isfinite(segment.end_seconds)
            or segment.start_seconds < 0
            or segment.end_seconds <= segment.start_seconds
            or segment.end_seconds > duration_seconds
        ):
            raise ValueError("Transcript engine returned an invalid timestamp interval.")
        if segment.confidence is not None and (
            not math.isfinite(segment.confidence)
            or not 0 <= segment.confidence <= 1
        ):
            raise ValueError("Transcript engine returned an invalid confidence value.")
        canonical = json.dumps(
            {
                "index": index,
                "start_seconds": segment.start_seconds,
                "end_seconds": segment.end_seconds,
                "text": segment.text,
                "confidence": segment.confidence,
                "speaker_id": segment.speaker_id,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        segment_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
        try:
            validated.append(
                TranscriptSegment(
                    segment_id=segment_id,
                    start_seconds=segment.start_seconds,
                    end_seconds=segment.end_seconds,
                    text=segment.text,
                    confidence=segment.confidence,
                    speaker_id=segment.speaker_id,
                )
            )
        except ValidationError as exc:
            raise ValueError("Transcription engine returned an invalid segment.") from exc
    return TranscriptRecord(
        transcript_id=transcript_id,
        job_id=job_id,
        asset_id=asset_id,
        asset_sha256=asset_sha256,
        target_context=target_context,
        duration_seconds=duration_seconds,
        engine_id=engine_id,
        engine_version=engine_version,
        language=engine_result.language,
        raw_output_sha256=engine_result.raw_output_sha256,
        segments=tuple(validated),
    )


SCORING_VERSION = "explainable-heuristics-v3-scene-analysis-status"
_WORD_RE = re.compile(r"\b[\w']+\b", re.UNICODE)
_CONTENT_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "because", "but", "by",
    "for", "from", "had", "has", "have", "he", "her", "i", "if", "in",
    "into", "is", "it", "its", "of", "on", "or", "our", "she", "so",
    "that", "the", "their", "then", "they", "this", "to", "was", "we",
    "were", "what", "when", "which", "who", "with", "you", "your",
}
_PAYOFF_MARKERS = (
    "because", "but", "finally", "however", "so", "therefore", "that is why",
    "the result", "which means",
)
_CLOSING_MARKERS = (
    "in short", "that is why", "the takeaway", "to sum up", "which is why",
)
_WEIGHTS = {
    "hook": 0.138,
    "coherence": 0.184,
    "information_density": 0.184,
    "narrative": 0.138,
    "ending": 0.138,
    "duration_fit": 0.138,
    "visual_boundary_alignment": 0.08,
}


def _score_window(
    segments: tuple[TranscriptSegment, ...],
    duration: float,
    config: HighlightConfig,
) -> tuple[float, dict[str, float], dict[str, tuple[str, ...]], tuple[str, ...]]:
    text = " ".join(segment.text for segment in segments)
    lowered = text.casefold()
    opening = segments[0].text.strip()
    final = segments[-1].text.strip()
    words = [word.casefold() for word in _WORD_RE.findall(text)]
    content_words = [word for word in words if word not in _CONTENT_WORDS]
    evidence: dict[str, tuple[str, ...]] = {}

    hook_signals: list[str] = []
    if "?" in opening and any(
        marker in lowered for marker in ("because", "so", "the answer", "that means")
    ):
        hook_signals.append("opening_question_with_explanation")
    if re.search(r"\b\d+(?:\.\d+)?\b", opening):
        hook_signals.append("concrete_number_in_opening")
    if re.search(r"\b(but|however|instead|actually)\b", opening.casefold()):
        hook_signals.append("opening_contrast")
    if len(_WORD_RE.findall(opening)) <= 14 and opening.endswith(("?", "!", ".")):
        hook_signals.append("concise_complete_opening")
    hook = min(1.0, 0.25 + 0.25 * len(hook_signals))
    evidence["hook"] = tuple(hook_signals)

    ending_complete = final.rstrip().endswith((".", "!", "?", "…"))
    dangling = bool(
        re.search(r"\b(and|because|but|for|if|of|or|so|to|with|which|that)$", final.casefold())
    )
    coherent_segments = sum(
        segment.text.rstrip().endswith((".", "!", "?", "…"))
        for segment in segments
    )
    coherence = min(
        1.0,
        0.25
        + 0.35 * (coherent_segments / len(segments))
        + (0.25 if len(words) >= 12 else 0.0)
        - (0.2 if dangling else 0.0),
    )
    coherence = max(0.0, coherence)
    evidence["coherence"] = tuple(
        signal
        for signal, active in (
            ("sentence_boundaries_preserved", coherent_segments > 0),
            ("sufficient_standalone_context", len(words) >= 12),
            ("possible_incomplete_boundary", dangling),
        )
        if active
    )

    words_per_second = len(words) / max(duration, 0.1)
    lexical_diversity = len(set(content_words)) / max(len(content_words), 1)
    density = min(1.0, 0.55 * min(words_per_second / 2.0, 1.0) + 0.45 * lexical_diversity)
    density_signals = ["bounded_word_rate", "lexical_diversity"]
    if re.search(r"\b\d+(?:\.\d+)?\b", text):
        density_signals.append("specific_numeric_detail")
        density = min(1.0, density + 0.08)
    evidence["information_density"] = tuple(density_signals)

    payoff_signals = tuple(
        marker for marker in _PAYOFF_MARKERS if marker in lowered
    )
    has_question = "?" in text
    narrative = min(
        1.0,
        0.25
        + (0.35 if payoff_signals else 0.0)
        + (0.2 if has_question and payoff_signals else 0.0)
        + (0.2 if len(segments) >= 3 else 0.0),
    )
    evidence["narrative"] = payoff_signals + (
        ("question_and_explanation",) if has_question and payoff_signals else ()
    )

    closing_signals = tuple(marker for marker in _CLOSING_MARKERS if marker in lowered)
    ending = 0.7 if ending_complete else 0.25
    if closing_signals:
        ending = min(1.0, ending + 0.2)
    if dangling:
        ending = max(0.0, ending - 0.25)
    evidence["ending"] = (
        (("terminal_punctuation",) if ending_complete else ())
        + closing_signals
        + (("dangling_clause",) if dangling else ())
    )
    cuts = config.scene_boundary_seconds
    boundary_evidence: list[str] = []
    window_start = segments[0].start_seconds
    window_end = segments[-1].end_seconds
    scene_changes_inside = sum(
        window_start < boundary < window_end for boundary in cuts
    )
    if cuts:
        edge_scores = []
        for label, point in (("start", window_start), ("end", window_end)):
            distance = min(abs(point - boundary) for boundary in cuts)
            edge_scores.append(max(0.0, 1.0 - distance / 1.5))
            if distance <= 0.5:
                boundary_evidence.append(f"scene_cut_near_{label}")
        visual_alignment = sum(edge_scores) / len(edge_scores)
        if scene_changes_inside:
            boundary_evidence.append(
                f"scene_changes_inside_window:{scene_changes_inside}"
            )
    elif config.scene_analysis_performed:
        visual_alignment = 0.5
        boundary_evidence.append("no_scene_changes_detected")
    else:
        visual_alignment = 0.5
        boundary_evidence.append("scene_boundary_analysis_unavailable")
    evidence["visual_boundary_alignment"] = tuple(boundary_evidence)

    midpoint = (config.min_duration_seconds + config.max_duration_seconds) / 2
    half_range = max((config.max_duration_seconds - config.min_duration_seconds) / 2, 0.1)
    duration_fit = max(0.0, 1.0 - abs(duration - midpoint) / half_range)
    evidence["duration_fit"] = ("within_configured_duration_range",)

    scores = {
        "hook": hook,
        "coherence": coherence,
        "information_density": density,
        "narrative": narrative,
        "ending": ending,
        "duration_fit": duration_fit,
        "visual_boundary_alignment": visual_alignment,
    }
    overall = sum(scores[key] * _WEIGHTS[key] for key in _WEIGHTS)
    warnings = ("ending_may_be_incomplete",) if not ending_complete or dangling else ()
    return overall, scores, evidence, warnings


def select_highlights(
    transcript: TranscriptRecord,
    *,
    run_id: str,
    config: HighlightConfig,
) -> tuple[HighlightCandidate, ...]:
    segments = transcript.segments
    evaluated: list[
        tuple[
            float,
            tuple[TranscriptSegment, ...],
            float,
            float,
            dict[str, float],
            dict[str, tuple[str, ...]],
            tuple[str, ...],
        ]
    ] = []
    windows_evaluated = 0
    for start_index, first in enumerate(segments):
        for end_index in range(start_index, len(segments)):
            last = segments[end_index]
            duration = last.end_seconds - first.start_seconds
            if duration > config.max_duration_seconds:
                break
            if duration < config.min_duration_seconds:
                continue
            windows_evaluated += 1
            if windows_evaluated > config.max_windows_evaluated:
                break
            window = segments[start_index : end_index + 1]
            score, scores, evidence, warnings = _score_window(window, duration, config)
            if score >= config.min_score:
                evaluated.append(
                    (
                        score,
                        window,
                        first.start_seconds,
                        last.end_seconds,
                        scores,
                        evidence,
                        warnings,
                    )
                )
        if windows_evaluated > config.max_windows_evaluated:
            break

    evaluated.sort(
        key=lambda item: (
            -item[0],
            item[2],
            item[3],
            tuple(segment.segment_id for segment in item[1]),
        )
    )
    kept: list[
        tuple[
            float,
            tuple[TranscriptSegment, ...],
            float,
            float,
            dict[str, float],
            dict[str, tuple[str, ...]],
            tuple[str, ...],
        ]
    ] = []
    for candidate in evaluated:
        _, _, start, end, _, _, _ = candidate
        duplicate = False
        for prior in kept:
            prior_start, prior_end = prior[2], prior[3]
            intersection = max(0.0, min(end, prior_end) - max(start, prior_start))
            union = max(end, prior_end) - min(start, prior_start)
            if union > 0 and intersection / union > 0.65:
                duplicate = True
                break
        if not duplicate:
            kept.append(candidate)
        if len(kept) >= config.max_candidates:
            break

    created_at = datetime.now(UTC)
    config_fingerprint = hashlib.sha256(
        json.dumps(
            config.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    result: list[HighlightCandidate] = []
    for rank, item in enumerate(kept, start=1):
        score, window, start, end, scores, evidence, warnings = item
        identity = "|".join(
            (
                transcript.transcript_id,
                transcript.fingerprint(),
                config_fingerprint,
                window[0].segment_id,
                window[-1].segment_id,
                SCORING_VERSION,
            )
        )
        candidate_id = "hc-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        explanation = (
            "Ranked by a deterministic weighted heuristic: "
            + ", ".join(f"{name}={scores[name]:.2f}" for name in _WEIGHTS)
            + ". These scores are not engagement or virality probabilities."
        )
        result.append(
            HighlightCandidate(
                candidate_id=candidate_id,
                run_id=run_id,
                transcript_id=transcript.transcript_id,
                asset_id=transcript.asset_id,
                asset_sha256=transcript.asset_sha256,
                start_seconds=start,
                end_seconds=end,
                duration_seconds=end - start,
                rank=rank,
                score=score,
                scores=scores,
                evidence=evidence,
                transcript_segment_ids=tuple(segment.segment_id for segment in window),
                explanation=explanation,
                warnings=warnings,
                scoring_version=SCORING_VERSION,
                created_at=created_at,
            )
        )
    return tuple(result)
