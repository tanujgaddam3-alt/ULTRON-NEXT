from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import subprocess
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from types import ModuleType
from types import SimpleNamespace

import pytest

from tests.application import test_content_lifecycle as lifecycle
from tests.application.test_content_processing import _harness, _use
from ultron.application.content.intelligence import (
    FasterWhisperLocalEngine,
    _decode_pcm_wave_for_faster_whisper,
    validate_engine_transcript,
    validate_pcm_wave,
)
from ultron.application.content.intelligence_service import ContentIntelligenceService
from ultron.application.content.clip_renderer import FFmpegLocalClipRenderer
from ultron.domain.content import ContentTargetContext, ProcessingJobState
from ultron.domain.content_intelligence import (
    EngineTranscript,
    EngineTranscriptSegment,
    HighlightConfig,
    TranscriptionConfig,
)


def _wav_bytes(seconds: float = 8.0) -> bytes:
    sample_rate = 8_000
    frames = int(sample_rate * seconds)
    target = BytesIO()
    with wave.open(target, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\x00\x00" * frames)
    return target.getvalue()


def _engine_segments() -> tuple[EngineTranscriptSegment, ...]:
    return (
        EngineTranscriptSegment(
            start_seconds=0.0,
            end_seconds=3.5,
            text="Why does this method work? Because the measured result changes.",
        ),
        EngineTranscriptSegment(
            start_seconds=3.5,
            end_seconds=7.0,
            text="The takeaway is that the evidence explains the difference.",
        ),
    )


class FakeTranscriptionEngine:
    engine_id = "test-fake"
    engine_version = "1"

    def __init__(self, segments=None, *, delay: float = 0):
        self.segments = _engine_segments() if segments is None else tuple(segments)
        self.delay = delay
        self.calls = 0

    def transcribe(
        self,
        audio_bytes,
        *,
        language,
        max_segments,
        max_segment_characters,
        max_text_characters,
    ):
        assert audio_bytes.startswith(b"RIFF")
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        raw = [segment.model_dump(mode="json") for segment in self.segments]
        return EngineTranscript(
            segments=self.segments,
            language=language or "en",
            raw_output_sha256=hashlib.sha256(
                json.dumps(raw, sort_keys=True).encode()
            ).hexdigest(),
        )


def _service(harness, engine):
    return ContentIntelligenceService(
        content_rights=harness.service,
        transcription_engine=engine,
    )


def _create_job(harness, service, *, config=None, use=None, command_id=None):
    config = config or TranscriptionConfig()
    use = use or _use()
    target = ContentTargetContext.YOUTUBE_SHORTS
    payload = {
        "use": use.model_dump(mode="json"),
        "target_context": target.value,
        "config": config.model_dump(mode="json"),
    }
    return harness.invoke(
        "content_create_transcription_job",
        payload,
        service.create_transcription_job,
        use=use,
        target_context=target,
        config=config,
        **({"command_id": command_id} if command_id else {}),
    )


def _run(harness, service, job_id, *, command_id=None):
    return harness.invoke(
        "content_run_transcription_job",
        {"job_id": job_id},
        service.run_transcription_job,
        job_id=job_id,
        **({"command_id": command_id} if command_id else {}),
    )


def _generate(harness, service, transcript_id, config, *, command_id=None):
    return harness.invoke(
        "content_generate_highlights",
        {
            "transcript_id": transcript_id,
            "config": config.model_dump(mode="json"),
        },
        service.generate_highlights,
        transcript_id=transcript_id,
        config=config,
        **({"command_id": command_id} if command_id else {}),
    )


def _audio_harness(tmp_path, monkeypatch, *, engine=None, duration=8.0):
    data = _wav_bytes(duration)
    harness, asset, grant, storage = _harness(
        tmp_path,
        monkeypatch,
        media_bytes=data,
    )
    engine = engine or FakeTranscriptionEngine()
    return harness, asset, grant, storage, _service(harness, engine), engine


def test_authorized_transcription_persists_validated_transcript(tmp_path, monkeypatch):
    h, asset, _, _, service, engine = _audio_harness(tmp_path, monkeypatch)
    job = _create_job(h, service)
    transcript = _run(h, service, job.job_id)

    assert transcript.asset_id == asset.asset_id
    assert transcript.asset_sha256 == asset.content_sha256
    assert transcript.duration_seconds == 8.0
    assert transcript.engine_id == "test-fake"
    assert len(transcript.segments) == 2
    assert engine.calls == 1
    assert h.store.get_processing_job(job.job_id).state == ProcessingJobState.SUCCEEDED
    assert h.store.get_transcript(transcript.transcript_id) == transcript
    read_back = h.invoke(
        "content_read_transcript",
        {"transcript_id": transcript.transcript_id},
        service.get_transcript,
        transcript_id=transcript.transcript_id,
    )
    assert read_back.fingerprint() == transcript.fingerprint()


def test_repeated_registered_asset_resume_reuses_transcription_job(
    tmp_path,
    monkeypatch,
):
    h, _, _, _, service, _ = _audio_harness(tmp_path, monkeypatch)
    first = _create_job(h, service, command_id="stable-source-resume")
    repeated = _create_job(h, service, command_id="stable-source-resume")

    assert repeated.job_id == first.job_id
    assert h.store._connection.execute(
        "SELECT COUNT(*) FROM content_processing_jobs WHERE asset_id = ?",
        (first.asset_id,),
    ).fetchone()[0] == 1


def test_source_video_audio_is_extracted_and_transcript_stays_source_linked(
    tmp_path,
    monkeypatch,
):
    source_path = tmp_path / "source.mp4"
    generated = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x120:r=15:d=8",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000:duration=8",
            "-c:v",
            "mpeg4",
            "-q:v",
            "8",
            "-c:a",
            "aac",
            "-b:a",
            "32k",
            "-shortest",
            str(source_path),
        ],
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert generated.returncode == 0, generated.stderr.decode(errors="replace")
    source_bytes = source_path.read_bytes()
    harness, asset, _, storage = _harness(
        tmp_path,
        monkeypatch,
        media_bytes=source_bytes,
    )
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory,
    )
    engine = FakeTranscriptionEngine()
    service = ContentIntelligenceService(
        content_rights=harness.service,
        transcription_engine=engine,
        media_renderer=renderer,
    )
    job = _create_job(harness, service, config=TranscriptionConfig(max_duration_seconds=30))

    transcript = _run(harness, service, job.job_id)

    assert transcript.asset_id == asset.asset_id
    assert transcript.asset_sha256 == hashlib.sha256(source_bytes).hexdigest()
    assert transcript.duration_seconds == 8.0
    assert engine.calls == 1
    assert [segment.start_seconds for segment in transcript.segments] == [0.0, 3.5]


def test_transcription_rights_revocation_prevents_engine_dispatch(
    tmp_path, monkeypatch
):
    h, _, grant, _, service, engine = _audio_harness(tmp_path, monkeypatch)
    job = _create_job(h, service)
    h.invoke(
        "content_revoke_rights_grant",
        {"grant_id": grant.grant_id, "reason": "test revocation"},
        h.service.revoke_rights_grant,
        grant_id=grant.grant_id,
        reason="test revocation",
    )

    with pytest.raises(RuntimeError, match="failed before engine dispatch"):
        _run(h, service, job.job_id)

    assert engine.calls == 0
    assert h.store.get_processing_job(job.job_id).state == ProcessingJobState.FAILED


def test_synthetic_worker_cannot_claim_transcription_job(tmp_path, monkeypatch):
    h, _, _, _, service, engine = _audio_harness(tmp_path, monkeypatch)
    job = _create_job(h, service)

    with pytest.raises(PermissionError, match="transcription worker"):
        h.invoke(
            "content_run_processing_job",
            {"job_id": job.job_id},
            h.service.run_processing_job,
            job_id=job.job_id,
        )

    assert engine.calls == 0
    assert h.store.get_processing_job(job.job_id).state == ProcessingJobState.QUEUED


def test_wrong_asset_hash_is_rejected_at_job_creation(tmp_path, monkeypatch):
    h, _, _, _, service, engine = _audio_harness(tmp_path, monkeypatch)
    wrong_use = _use().model_copy(update={"expected_asset_sha256": "0" * 64})

    with pytest.raises(PermissionError, match="does not match"):
        _create_job(h, service, use=wrong_use)

    assert engine.calls == 0
    assert h.store._connection.execute(
        "SELECT COUNT(*) FROM content_processing_jobs"
    ).fetchone()[0] == 0


def test_corrupt_audio_bytes_fail_before_engine_dispatch(tmp_path, monkeypatch):
    h, asset, _, storage, service, engine = _audio_harness(tmp_path, monkeypatch)
    job = _create_job(h, service)
    path = storage._root / "inputs" / asset.storage_key
    path.write_bytes(b"corrupted wav")

    with pytest.raises(RuntimeError, match="failed before engine dispatch"):
        _run(h, service, job.job_id)

    assert engine.calls == 0
    assert h.store.get_processing_job(job.job_id).state == ProcessingJobState.FAILED


@pytest.mark.parametrize(
    ("start", "end", "expected_error"),
    [
        (-1.0, 1.0, "invalid timestamp"),
        (2.0, 1.0, "invalid timestamp"),
        (7.5, 9.0, "invalid timestamp"),
    ],
)
def test_invalid_engine_timestamps_are_rejected(
    tmp_path,
    monkeypatch,
    start,
    end,
    expected_error,
):
    segment = EngineTranscriptSegment(
        start_seconds=start,
        end_seconds=end,
        text="A timestamp test sentence.",
    )
    h, _, _, _, service, _ = _audio_harness(
        tmp_path,
        monkeypatch,
        engine=FakeTranscriptionEngine((segment,)),
    )
    job = _create_job(h, service)

    with pytest.raises(ValueError, match=expected_error):
        _run(h, service, job.job_id)

    assert h.store.get_transcript_for_job(job.job_id) is None
    assert h.store.get_processing_job(job.job_id).state == ProcessingJobState.FAILED


def test_overlapping_transcript_segments_are_preserved_in_engine_order():
    audio = _wav_bytes()
    config = TranscriptionConfig()
    expected_hash = hashlib.sha256(audio).hexdigest()
    validated_audio = validate_pcm_wave(
        audio,
        expected_sha256=expected_hash,
        config=config,
    )
    segments = (
        EngineTranscriptSegment(
            start_seconds=0.0,
            end_seconds=2.0,
            text="First overlapping sentence.",
        ),
        EngineTranscriptSegment(
            start_seconds=1.5,
            end_seconds=3.0,
            text="Second overlapping sentence.",
        ),
    )
    result = EngineTranscript(
        segments=segments,
        raw_output_sha256="a" * 64,
    )
    transcript = validate_engine_transcript(
        result,
        transcript_id="transcript",
        job_id="job",
        asset_id="asset",
        asset_sha256=expected_hash,
        target_context=ContentTargetContext.YOUTUBE_SHORTS,
        duration_seconds=validated_audio.metadata.duration_seconds,
        engine_id="test-fake",
        engine_version="1",
        config=config,
    )

    assert tuple(segment.text for segment in transcript.segments) == (
        "First overlapping sentence.",
        "Second overlapping sentence.",
    )
    assert transcript.segments[1].start_seconds == 1.5


def test_transcript_audit_failure_marks_execution_uncertain_without_result(
    tmp_path, monkeypatch
):
    h, _, _, _, service, _ = _audio_harness(tmp_path, monkeypatch)
    job = _create_job(h, service)
    h.store._connection.execute(
        """
        CREATE TRIGGER fail_transcript_audit
        BEFORE INSERT ON content_audit_events
        WHEN NEW.action = 'content_transcription_succeeded'
        BEGIN SELECT RAISE(ABORT, 'injected transcript audit failure'); END
        """
    )

    with pytest.raises(RuntimeError, match="UNCERTAIN.*no transcript result"):
        _run(h, service, job.job_id)

    assert h.store.get_transcript_for_job(job.job_id) is None
    assert h.store.get_processing_job(job.job_id).state == ProcessingJobState.UNCERTAIN


def test_local_engine_audio_decode_resamples_pcm_to_faster_whisper_format():
    audio = _decode_pcm_wave_for_faster_whisper(_wav_bytes(seconds=1))

    assert audio.shape == (16_000,)
    assert str(audio.dtype) == "float32"


def test_local_engine_passes_decoded_audio_array_to_transcription_model():
    class FakeWhisperModel:
        def transcribe(self, audio, **kwargs):
            assert audio.shape == (16_000,)
            assert str(audio.dtype) == "float32"
            assert kwargs["task"] == "transcribe"
            return (
                iter((SimpleNamespace(start=0.0, end=1.0, text="synthetic"),)),
                SimpleNamespace(language="en"),
            )

    engine = object.__new__(FasterWhisperLocalEngine)
    engine._model = FakeWhisperModel()
    engine.engine_version = "test"

    result = engine.transcribe(
        _wav_bytes(seconds=1),
        language="en",
        max_segments=10,
        max_segment_characters=100,
        max_text_characters=100,
    )

    assert result.language == "en"
    assert result.segments[0].text == "synthetic"


def test_highlight_audit_failure_rolls_back_run_and_candidates(tmp_path, monkeypatch):
    h, _, _, _, service, _ = _audio_harness(tmp_path, monkeypatch)
    transcript = _run(h, service, _create_job(h, service).job_id)
    config = HighlightConfig(
        min_duration_seconds=3,
        max_duration_seconds=8,
        min_score=0,
    )
    h.store._connection.execute(
        """
        CREATE TRIGGER fail_highlight_audit
        BEFORE INSERT ON content_audit_events
        WHEN NEW.action = 'content_generate_highlights'
        BEGIN SELECT RAISE(ABORT, 'injected highlight audit failure'); END
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="injected highlight audit"):
        _generate(h, service, transcript.transcript_id, config)

    assert h.store._connection.execute(
        "SELECT COUNT(*) FROM content_highlight_runs"
    ).fetchone()[0] == 0
    assert h.store._connection.execute(
        "SELECT COUNT(*) FROM content_highlight_candidates"
    ).fetchone()[0] == 0


def test_interrupted_transcription_recovers_uncertain_without_retry(
    tmp_path, monkeypatch
):
    h, _, _, _, service, engine = _audio_harness(tmp_path, monkeypatch)
    job = _create_job(h, service)
    h.store._connection.execute(
        "UPDATE content_processing_jobs SET state = ? WHERE job_id = ?",
        (ProcessingJobState.RUNNING.value, job.job_id),
    )
    database = h.store.database_identity
    h.close()

    from ultron.application.content.store import ContentStore

    reopened = ContentStore(database)
    recovered = reopened.get_processing_job(job.job_id)
    assert recovered.state == ProcessingJobState.UNCERTAIN
    restarted_service = ContentIntelligenceService(
        content_rights=type(h.service)(
            store=reopened,
            authority=h.authority,
            content_storage=service.content_rights.content_storage,
            processor=service.content_rights.processor,
        ),
        transcription_engine=engine,
    )
    with pytest.raises(PermissionError, match="not available"):
        _run(h, restarted_service, job.job_id)
    assert engine.calls == 0
    reopened.close()


def test_concurrent_transcription_claims_dispatch_engine_once(tmp_path, monkeypatch):
    engine = FakeTranscriptionEngine(delay=0.05)
    h, _, _, _, service, engine = _audio_harness(
        tmp_path,
        monkeypatch,
        engine=engine,
    )
    job = _create_job(h, service)

    def run():
        try:
            return _run(h, service, job.job_id)
        except PermissionError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: run(), range(2)))

    assert engine.calls == 1
    assert sum(result is not None for result in results) == 1
    assert h.store.get_processing_job(job.job_id).state == ProcessingJobState.SUCCEEDED


def test_transcription_runtime_limit_marks_outcome_uncertain(tmp_path, monkeypatch):
    engine = FakeTranscriptionEngine(delay=0.02)
    h, _, _, _, service, _ = _audio_harness(
        tmp_path,
        monkeypatch,
        engine=engine,
    )
    job = _create_job(
        h,
        service,
        config=TranscriptionConfig(max_runtime_seconds=0.001),
    )

    with pytest.raises(TimeoutError, match="not preempted"):
        _run(h, service, job.job_id)

    assert h.store.get_processing_job(job.job_id).state == ProcessingJobState.UNCERTAIN
    assert h.store.get_transcript_for_job(job.job_id) is None


def test_highlights_are_deterministic_ranked_and_traceable(tmp_path, monkeypatch):
    h, _, _, _, service, _ = _audio_harness(tmp_path, monkeypatch)
    job = _create_job(h, service)
    transcript = _run(h, service, job.job_id)
    config = HighlightConfig(
        min_duration_seconds=3,
        max_duration_seconds=8,
        max_candidates=5,
        min_score=0,
    )
    run = _generate(h, service, transcript.transcript_id, config)
    candidates_read = h.invoke(
        "content_read_highlight_candidates",
        {"run_id": run.run_id},
        service.get_highlight_candidates,
        run_id=run.run_id,
    )
    assert candidates_read == run.candidates
    replay = _generate(
        h,
        service,
        transcript.transcript_id,
        config,
        command_id="same-highlight-command",
    )
    replay_again = _generate(
        h,
        service,
        transcript.transcript_id,
        config,
        command_id="same-highlight-command",
    )

    assert run.candidates
    assert replay.run_id == replay_again.run_id
    assert all(item.asset_sha256 == transcript.asset_sha256 for item in run.candidates)
    assert all(
        item.transcript_segment_ids
        and item.start_seconds < item.end_seconds
        and 3 <= item.duration_seconds <= 8
        for item in run.candidates
    )
    assert tuple(item.candidate_id for item in replay.candidates) == tuple(
        item.candidate_id for item in replay_again.candidates
    )


def test_explainable_highlight_heuristics_rank_complete_moment_higher(
    tmp_path, monkeypatch
):
    segments = (
        EngineTranscriptSegment(
            start_seconds=0,
            end_seconds=7,
            text=(
                "Why does the measure change? Because the new method removes "
                "the error. The result is a stable reading."
            ),
        ),
        EngineTranscriptSegment(
            start_seconds=10,
            end_seconds=17,
            text="I mean and then because",
        ),
    )
    h, _, _, _, service, _ = _audio_harness(
        tmp_path,
        monkeypatch,
        engine=FakeTranscriptionEngine(segments),
        duration=18,
    )
    transcript = _run(h, service, _create_job(h, service).job_id)
    config = HighlightConfig(
        min_duration_seconds=6,
        max_duration_seconds=8,
        min_score=0,
    )
    run = _generate(h, service, transcript.transcript_id, config)

    assert len(run.candidates) == 2
    assert run.candidates[0].start_seconds == 0
    assert run.candidates[0].score > run.candidates[1].score
    assert run.candidates[0].evidence["hook"]
    assert "ending_may_be_incomplete" in run.candidates[1].warnings


def test_scene_boundaries_are_validated_and_inform_highlight_timing(
    tmp_path, monkeypatch
):
    with pytest.raises(ValueError, match="strictly increasing"):
        HighlightConfig(scene_boundary_seconds=(2.0, 1.0))
    with pytest.raises(ValueError, match="finite and non-negative"):
        HighlightConfig(scene_boundary_seconds=(-1.0,))
    with pytest.raises(ValueError, match="finite and non-negative"):
        HighlightConfig(scene_boundary_seconds=(float("nan"),))

    h, _, _, _, service, _ = _audio_harness(tmp_path, monkeypatch)
    transcript = _run(h, service, _create_job(h, service).job_id)
    no_visual_run = _generate(
        h,
        service,
        transcript.transcript_id,
        HighlightConfig(
            min_duration_seconds=3.5,
            max_duration_seconds=3.5,
            max_candidates=5,
            min_score=0,
        ),
    )
    assert "scene_boundary_analysis_unavailable" in (
        no_visual_run.candidates[0].evidence["visual_boundary_alignment"]
    )
    empty_visual_run = _generate(
        h,
        service,
        transcript.transcript_id,
        HighlightConfig(
            min_duration_seconds=3.5,
            max_duration_seconds=3.5,
            max_candidates=5,
            min_score=0,
            scene_analysis_performed=True,
        ),
    )
    assert "no_scene_changes_detected" in (
        empty_visual_run.candidates[0].evidence["visual_boundary_alignment"]
    )
    run = _generate(
        h,
        service,
        transcript.transcript_id,
        HighlightConfig(
            min_duration_seconds=3.5,
            max_duration_seconds=3.5,
            max_candidates=5,
            min_score=0,
            scene_boundary_seconds=(0.0, 3.5, 8.0),
        ),
    )

    assert run.candidates
    assert all(
        candidate.scores["visual_boundary_alignment"] > 0
        and candidate.evidence["visual_boundary_alignment"]
        for candidate in run.candidates
    )
    assert any(
        "scene_cut_near_start" in candidate.evidence["visual_boundary_alignment"]
        for candidate in run.candidates
    )


def test_highlight_analysis_revalidates_rights_and_can_return_no_candidates(
    tmp_path, monkeypatch
):
    h, _, grant, _, service, _ = _audio_harness(tmp_path, monkeypatch)
    job = _create_job(h, service)
    transcript = _run(h, service, job.job_id)
    config = HighlightConfig(
        min_duration_seconds=20,
        max_duration_seconds=30,
        min_score=0,
    )
    no_candidates = _generate(h, service, transcript.transcript_id, config)
    assert no_candidates.candidates == ()

    h.invoke(
        "content_revoke_rights_grant",
        {"grant_id": grant.grant_id, "reason": "test revocation"},
        h.service.revoke_rights_grant,
        grant_id=grant.grant_id,
        reason="test revocation",
    )
    with pytest.raises(PermissionError, match="Current rights"):
        _generate(h, service, transcript.transcript_id, config)
    with pytest.raises(PermissionError, match="Current rights"):
        h.invoke(
            "content_read_highlight_candidates",
            {"run_id": no_candidates.run_id},
            service.get_highlight_candidates,
            run_id=no_candidates.run_id,
        )


def test_local_engine_rejects_missing_model_without_download(tmp_path):
    with pytest.raises(ValueError, match="fully provisioned local"):
        FasterWhisperLocalEngine(tmp_path / "missing-model")


def test_local_engine_rejects_model_that_would_download_tokenizer(tmp_path):
    model = tmp_path / "partial-model"
    model.mkdir()
    (model / "model.bin").write_bytes(b"synthetic")
    (model / "config.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="fully provisioned local"):
        FasterWhisperLocalEngine(model)


def test_local_engine_uses_cpu_and_local_files_for_inference(tmp_path, monkeypatch):
    model = tmp_path / "model"
    model.mkdir()
    for name in ("model.bin", "config.json", "tokenizer.json"):
        (model / name).write_bytes(b"fixture")
    calls = {}

    class FakeWhisperModel:
        def __init__(self, path, **kwargs):
            calls["model"] = (path, kwargs)

        def transcribe(self, audio, **kwargs):
            calls["inference"] = (audio, kwargs)
            return (
                [SimpleNamespace(start=0.0, end=1.0, text="Local fixture transcript")],
                SimpleNamespace(language="en"),
            )

    fake_module = ModuleType("faster_whisper")
    fake_module.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    monkeypatch.setattr(
        "ultron.application.content.intelligence.importlib.metadata.version",
        lambda package: "fixture-version",
    )
    monkeypatch.setattr(
        "ultron.application.content.intelligence._decode_pcm_wave_for_faster_whisper",
        lambda audio: "decoded-local-audio",
    )
    engine = FasterWhisperLocalEngine(model)
    result = engine.transcribe(
        _wav_bytes(1.0),
        language="en",
        max_segments=4,
        max_segment_characters=100,
        max_text_characters=200,
    )

    assert calls["model"] == (
        str(model),
        {"device": "cpu", "compute_type": "int8", "local_files_only": True},
    )
    assert calls["inference"] == (
        "decoded-local-audio",
        {
            "language": "en",
            "task": "transcribe",
            "beam_size": 5,
            "vad_filter": False,
            "without_timestamps": False,
        },
    )
    assert result.segments[0].text == "Local fixture transcript"
    assert result.language == "en"


def test_local_engine_inference_error_propagates_without_success_result(
    tmp_path,
    monkeypatch,
):
    model = tmp_path / "model"
    model.mkdir()
    for name in ("model.bin", "config.json", "tokenizer.json"):
        (model / name).write_bytes(b"fixture")

    class FailingWhisperModel:
        def __init__(self, path, **kwargs):
            pass

        def transcribe(self, audio, **kwargs):
            raise OSError("fixture inference failure")

    fake_module = ModuleType("faster_whisper")
    fake_module.WhisperModel = FailingWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    monkeypatch.setattr(
        "ultron.application.content.intelligence.importlib.metadata.version",
        lambda package: "fixture-version",
    )
    monkeypatch.setattr(
        "ultron.application.content.intelligence._decode_pcm_wave_for_faster_whisper",
        lambda audio: "decoded-local-audio",
    )
    engine = FasterWhisperLocalEngine(model)

    with pytest.raises(OSError, match="fixture inference failure"):
        engine.transcribe(
            _wav_bytes(1.0),
            language="en",
            max_segments=4,
            max_segment_characters=100,
            max_text_characters=200,
        )


def test_unsupported_and_oversized_audio_fail_closed():
    config = TranscriptionConfig(max_input_bytes=100)
    data = b"not-a-wave"
    with pytest.raises(ValueError, match="supported PCM WAV"):
        validate_pcm_wave(
            data,
            expected_sha256=hashlib.sha256(data).hexdigest(),
            config=TranscriptionConfig(),
        )
    too_large = b"x" * 101
    with pytest.raises(ValueError, match="size limit"):
        validate_pcm_wave(
            too_large,
            expected_sha256=hashlib.sha256(too_large).hexdigest(),
            config=config,
        )
