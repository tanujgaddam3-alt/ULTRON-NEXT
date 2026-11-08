from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import subprocess
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import ModuleType
from types import SimpleNamespace

import pytest
import ultron.application.content.intelligence_service as intelligence_service_module

from tests.application import test_content_lifecycle as lifecycle
from tests.application.test_content_processing import _harness, _use
from ultron.ai.llm.ollama_provider import OllamaProvider
from ultron.application.content.intelligence import (
    FasterWhisperLocalEngine,
    _decode_pcm_wave_for_faster_whisper,
    select_highlights,
    validate_engine_transcript,
    validate_pcm_wave,
)
from ultron.application.content.intelligence_service import ContentIntelligenceService
from ultron.application.content.clip_renderer import FFmpegLocalClipRenderer
from ultron.domain.content import (
    AcquisitionMethod,
    AssetCategory,
    ContentPlatform,
    ContentCreativeEditPlanV3,
    ContentEditPlanAudio,
    ContentEditPlanSource,
    ContentTargetContext,
    RightsAction,
    RightsUse,
    SourceAccessPermissionState,
    SourceAsset,
    SourceProvenance,
    SourceReferenceRecord,
    ProcessingJobState,
)
from ultron.domain.content_intelligence import (
    EditorialBlueprint,
    EditingStyleProfile,
    EngineTranscript,
    EngineTranscriptSegment,
    HighlightCandidate,
    HighlightConfig,
    TranscriptionConfig,
    list_editing_style_profiles,
    select_editing_style,
    build_editorial_blueprint,
)
from ultron.domain.content_owner_processing import OwnerLocalProcessingOperation
from ultron.application.content.editorial import (
    EditorialIntelligenceService,
    compare_proposal_with_v3,
    evaluate_editorial_outputs,
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


def _service(harness, engine, *, local_editorial_provider=None):
    return ContentIntelligenceService(
        content_rights=harness.service,
        transcription_engine=engine,
        local_editorial_provider=local_editorial_provider,
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


def _audio_harness(
    tmp_path,
    monkeypatch,
    *,
    engine=None,
    duration=8.0,
    local_editorial_provider=None,
):
    data = _wav_bytes(duration)
    harness, asset, grant, storage = _harness(
        tmp_path,
        monkeypatch,
        media_bytes=data,
    )
    engine = engine or FakeTranscriptionEngine()
    return (
        harness,
        asset,
        grant,
        storage,
        _service(
            harness,
            engine,
            local_editorial_provider=local_editorial_provider,
        ),
        engine,
    )


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


def test_owner_local_authorization_transcribes_and_detects_highlights_without_rights_records(
    tmp_path,
    monkeypatch,
):
    source_bytes = _wav_bytes()
    harness, _, _, storage = _harness(
        tmp_path,
        monkeypatch,
        with_rights=False,
        media_bytes=source_bytes,
    )
    harness.service.reviewer_principals = frozenset()
    harness.store.migrate_owner_local_processing_schema()
    asset_sha256 = hashlib.sha256(source_bytes).hexdigest()
    asset_id = "owner-supplied-audio"
    source_reference_value = f"urn:ultron:owner-created:{asset_sha256}"
    now = datetime.now(UTC)
    asset = SourceAsset(
        asset_id=asset_id,
        owner_id=harness.authority.principal,
        category=AssetCategory.FOOTAGE,
        content_sha256=asset_sha256,
        provenance=SourceProvenance(
            acquisition_method=AcquisitionMethod.OWNER_CREATED,
            acquired_at=now,
            original_filename="owner-audio.wav",
            transfer_channel="direct_test_upload",
            source_reference=source_reference_value,
        ),
        storage_key="owner-supplied-audio-input",
        created_at=now,
    )
    use = RightsUse(
        asset_id=asset_id,
        expected_asset_sha256=asset_sha256,
        platform=ContentPlatform.YOUTUBE,
        account_id="private-owner",
        territory="US",
        actions=frozenset({RightsAction.ANALYZE}),
        commercial_intent=False,
        monetization_intent=False,
        duration_seconds=8,
    )
    source_reference = SourceReferenceRecord(
        intake_id="owner-supplied-audio-intake",
        asset_id=asset_id,
        owner_id=harness.authority.principal,
        source_reference=source_reference_value,
        acquisition_method=AcquisitionMethod.OWNER_CREATED,
        provenance=asset.provenance,
        claimed_source_sha256=asset_sha256,
        access_permission_state=SourceAccessPermissionState.NOT_REQUIRED,
        target_context=ContentTargetContext.YOUTUBE_SHORTS,
        rights_use=use,
        created_at=now,
    )
    operations = frozenset(
        {
            OwnerLocalProcessingOperation.TRANSCRIBE,
            OwnerLocalProcessingOperation.ANALYZE_AUDIO,
            OwnerLocalProcessingOperation.DETECT_HIGHLIGHTS,
        }
    )
    registration_payload = {
        "asset": asset.model_dump(mode="json"),
        "source_reference": source_reference.model_dump(mode="json"),
        "owner_local_processing_operations": sorted(
            operation.value for operation in operations
        ),
    }
    harness.service.register_asset(
        asset,
        source_stream=BytesIO(source_bytes),
        context=harness.context(
            "content_register_owner_created_asset",
            registration_payload,
        ),
        command_id="owner-supplied-audio-registration",
        source_reference=source_reference,
        owner_local_processing_operations=operations,
    )

    service = _service(harness, FakeTranscriptionEngine())
    config = TranscriptionConfig()
    job_payload = {
        "use": use.model_dump(mode="json"),
        "target_context": ContentTargetContext.YOUTUBE_SHORTS.value,
        "config": config.model_dump(mode="json"),
    }
    job = harness.invoke(
        "content_create_transcription_job",
        job_payload,
        service.create_transcription_job,
        use=use,
        target_context=ContentTargetContext.YOUTUBE_SHORTS,
        config=config,
    )
    transcript = _run(harness, service, job.job_id)
    read_back = harness.invoke(
        "content_read_transcript",
        {"transcript_id": transcript.transcript_id},
        service.get_transcript,
        transcript_id=transcript.transcript_id,
    )
    highlights = _generate(
        harness,
        service,
        transcript.transcript_id,
        HighlightConfig(),
    )

    authorization = harness.store.get_owner_local_processing_authorization_for_asset(
        asset.asset_id,
        asset_sha256,
        harness.authority.principal,
    )
    assert authorization is not None

    dispatch_engine = FakeTranscriptionEngine()
    dispatch_service = _service(harness, dispatch_engine)
    revoke_after_media_job = _create_job(
        harness,
        dispatch_service,
        config=config,
        use=use,
    )
    original_validate_pcm_wave = intelligence_service_module.validate_pcm_wave

    def revoke_during_media_preparation(*args, **kwargs):
        validated = original_validate_pcm_wave(*args, **kwargs)
        with harness.store._transaction() as connection:
            harness.store.revoke_owner_local_processing_authorization_in_transaction(
                connection,
                authorization_id=authorization.authorization_id,
                owner_id=harness.authority.principal,
                reason="Revoked after claim but before dispatch.",
            )
        return validated

    monkeypatch.setattr(
        intelligence_service_module,
        "validate_pcm_wave",
        revoke_during_media_preparation,
    )
    with pytest.raises(PermissionError, match="No active owner authorization"):
        _run(
            harness,
            dispatch_service,
            revoke_after_media_job.job_id,
        )
    assert dispatch_engine.calls == 0
    assert (
        harness.store.get_processing_job(revoke_after_media_job.job_id).state
        is ProcessingJobState.FAILED
    )
    monkeypatch.setattr(
        intelligence_service_module,
        "validate_pcm_wave",
        original_validate_pcm_wave,
    )

    renewed = harness.invoke(
        "content_renew_owner_local_processing_authorization",
        {
            "asset_id": asset.asset_id,
            "asset_sha256": asset_sha256,
            "operations": sorted(operation.value for operation in operations),
            "reason": "Renewed for dispatch-time expiry regression.",
        },
        harness.service.renew_owner_local_processing_authorization,
        asset_id=asset.asset_id,
        asset_sha256=asset_sha256,
        operations=operations,
        reason="Renewed for dispatch-time expiry regression.",
    )
    expiry_engine = FakeTranscriptionEngine()
    expiry_service = _service(harness, expiry_engine)
    expiry_job = _create_job(harness, expiry_service, config=config, use=use)
    original_store_now = harness.store._now

    def expire_during_media_preparation(*args, **kwargs):
        validated = original_validate_pcm_wave(*args, **kwargs)
        harness.store._now = lambda: (
            renewed.expires_at + timedelta(seconds=1)
        ).isoformat()
        return validated

    monkeypatch.setattr(
        intelligence_service_module,
        "validate_pcm_wave",
        expire_during_media_preparation,
    )
    with pytest.raises(PermissionError, match="expired"):
        _run(harness, expiry_service, expiry_job.job_id)
    assert expiry_engine.calls == 0
    assert (
        harness.store.get_processing_job(expiry_job.job_id).state
        is ProcessingJobState.FAILED
    )
    harness.store._now = original_store_now
    monkeypatch.setattr(
        intelligence_service_module,
        "validate_pcm_wave",
        original_validate_pcm_wave,
    )
    authorization = renewed

    in_flight_job = _create_job(harness, service, config=config, use=use)
    queued_after_revoke = _create_job(harness, service, config=config, use=use)

    class RevokingTranscriptionEngine(FakeTranscriptionEngine):
        def transcribe(
            self,
            audio_bytes,
            *,
            language,
            max_segments,
            max_segment_characters,
            max_text_characters,
        ):
            harness.invoke(
                "content_revoke_owner_local_processing_authorization",
                {
                    "authorization_id": authorization.authorization_id,
                    "reason": "Owner revoked during transcription.",
                },
                harness.service.revoke_owner_local_processing_authorization,
                authorization_id=authorization.authorization_id,
                reason="Owner revoked during transcription.",
            )
            return super().transcribe(
                audio_bytes,
                language=language,
                max_segments=max_segments,
                max_segment_characters=max_segment_characters,
                max_text_characters=max_text_characters,
            )

    revoking_engine = RevokingTranscriptionEngine()
    revoking_service = _service(harness, revoking_engine)
    read_count = 0
    original_read = storage.read_verified_input

    def tracked_read(storage_key, expected_sha256):
        nonlocal read_count
        read_count += 1
        return original_read(storage_key, expected_sha256)

    monkeypatch.setattr(storage, "read_verified_input", tracked_read)
    with pytest.raises(RuntimeError, match="UNCERTAIN"):
        _run(harness, revoking_service, in_flight_job.job_id)
    assert revoking_engine.calls == 1
    assert harness.store.get_transcript_for_job(in_flight_job.job_id) is None
    assert (
        harness.store.get_processing_job(in_flight_job.job_id).state
        is ProcessingJobState.UNCERTAIN
    )
    reads_after_revoked_run = read_count

    with pytest.raises(RuntimeError, match="failed before engine dispatch"):
        _run(harness, revoking_service, queued_after_revoke.job_id)
    assert read_count == reads_after_revoked_run
    assert revoking_engine.calls == 1

    assert transcript.asset_id == asset.asset_id
    assert transcript.asset_sha256 == asset_sha256
    assert read_back.fingerprint() == transcript.fingerprint()
    assert highlights.transcript_id == transcript.transcript_id
    assert harness.store._connection.execute(
        "SELECT COUNT(*) FROM rights_grants WHERE asset_id = ?",
        (asset_id,),
    ).fetchone()[0] == 0
    assert harness.store.get_latest_asset_license_record(
        asset_id,
        harness.authority.principal,
    ) is None
    assert storage.read_verified_input(asset.storage_key, asset_sha256) == source_bytes
    harness.close()


def test_unknown_license_restricted_test_runs_transcription_and_highlights(
    tmp_path,
    monkeypatch,
):
    source_bytes = _wav_bytes()
    harness, asset, _, _ = _harness(
        tmp_path,
        monkeypatch,
        with_rights=False,
        media_bytes=source_bytes,
    )
    acknowledgement = harness.service.RESTRICTED_TEST_ACKNOWLEDGEMENT
    permit_payload = {
        "asset_id": asset.asset_id,
        "asset_sha256": asset.content_sha256,
        "acknowledgement": acknowledgement,
        "license_verification_state": "unknown",
        "allowed_operations": sorted(
            {
                "transcription",
                "transcript_read",
                "highlight",
                "source_planning",
                "private_render",
                "project_render",
            }
        ),
        "commercial_use": False,
        "external_distribution": False,
    }
    permit = harness.invoke(
        "content_create_restricted_technical_test",
        permit_payload,
        harness.service.create_restricted_technical_test_permit,
        asset_id=asset.asset_id,
        asset_sha256=asset.content_sha256,
        acknowledgement=acknowledgement,
    )
    service = _service(harness, FakeTranscriptionEngine())
    job = _create_job(harness, service)
    transcript = _run(harness, service, job.job_id)
    read_back = harness.invoke(
        "content_read_transcript",
        {"transcript_id": transcript.transcript_id},
        service.get_transcript,
        transcript_id=transcript.transcript_id,
    )
    highlight_run = _generate(
        harness,
        service,
        transcript.transcript_id,
        HighlightConfig(),
    )

    license_record = harness.store.get_latest_asset_license_record(
        asset.asset_id,
        asset.owner_id,
    )
    assert license_record is not None
    assert license_record.verification_state.value == "unknown"
    assert permit.asset_sha256 == transcript.asset_sha256
    assert read_back.fingerprint() == transcript.fingerprint()
    assert highlight_run.transcript_id == transcript.transcript_id
    assert harness.store.get_processing_job(job.job_id).state == ProcessingJobState.SUCCEEDED
    with harness.store._lock:
        operations = harness.store._connection.execute(
            """
            SELECT operation FROM content_restricted_technical_test_events
            WHERE permit_id = ? AND event_type = 'restricted_local_operation'
            ORDER BY sequence
            """,
            (permit.permit_id,),
        ).fetchall()
    assert [row["operation"] for row in operations] == [
        "transcription",
        "transcription",
        "transcription",
        "transcription",
        "transcript_read",
        "highlight",
    ]


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
    assert all(
        "deterministic weighted heuristic" in item.explanation
        and not any(warning.startswith("local_ollama") for warning in item.warnings)
        for item in run.candidates
    )
    assert tuple(item.candidate_id for item in replay.candidates) == tuple(
        item.candidate_id for item in replay_again.candidates
    )


def test_local_ollama_editorial_order_is_opt_in_local_and_evidence_bound(
    tmp_path,
    monkeypatch,
):
    provider = OllamaProvider(
        timeout=5,
        local_only=True,
        num_predict=96,
        json_mode=True,
    )
    prompts = []

    def reorder(prompt):
        prompts.append(prompt)
        rows = json.loads(prompt.rsplit("\n", 1)[1])
        return json.dumps(
            {
                "decisions": [
                    {
                        "candidate_id": row["candidate_id"],
                        "rank": rank,
                        "semantic_relevance": 3,
                        "information_density": 2,
                        "novelty": 2,
                        "context_completeness": 3,
                        "standalone_value": 3,
                        "hook_potential": 2,
                        "narrative_importance": 2,
                        "redundancy": 0,
                        "audience_relevance": None,
                        "evidence_quotes": [row["excerpt"][:40]],
                        "rationale": "The excerpt states a coherent, supported point.",
                    }
                    for rank, row in enumerate(reversed(rows), start=1)
                ]
            }
        )

    monkeypatch.setattr(provider, "generate", reorder)
    h, _, _, _, service, _ = _audio_harness(
        tmp_path,
        monkeypatch,
        local_editorial_provider=provider,
    )
    transcript = _run(h, service, _create_job(h, service).job_id)
    config = HighlightConfig(
        min_duration_seconds=3,
        max_duration_seconds=8,
        max_candidates=5,
        min_score=0,
    )
    expected = select_highlights(transcript, run_id="expected", config=config)
    run = _generate(h, service, transcript.transcript_id, config)

    assert len(expected) > 1
    assert len(prompts) == 1
    assert "start_seconds" in prompts[0]
    assert "Why does this method work?" in prompts[0]
    assert tuple(item.candidate_id for item in run.candidates) == tuple(
        item.candidate_id for item in reversed(expected)
    )
    expected_by_id = {item.candidate_id: item for item in expected}
    for index, candidate in enumerate(run.candidates):
        original = expected_by_id[candidate.candidate_id]
        assert candidate.score == original.score
        assert candidate.transcript_segment_ids == original.transcript_segment_ids
        assert candidate.asset_id == transcript.asset_id
        assert candidate.asset_sha256 == transcript.asset_sha256
        assert candidate.start_seconds == original.start_seconds
        assert candidate.end_seconds == original.end_seconds
        assert "local_ollama_contextual_ordering" in candidate.warnings
        assert "ordinal and not a probability" in candidate.explanation
        assert "Model rationale:" in candidate.explanation
        assert "local_ollama_ordinal_assessment" in candidate.evidence
        assert "local_ollama_transcript_quotes" in candidate.evidence
        for prior in run.candidates[:index]:
            intersection = max(
                0.0,
                min(candidate.end_seconds, prior.end_seconds)
                - max(candidate.start_seconds, prior.start_seconds),
            )
            union = max(candidate.end_seconds, prior.end_seconds) - min(
                candidate.start_seconds,
                prior.start_seconds,
            )
            assert union == 0 or intersection / union <= 0.65
    assert "+local-ollama-editorial-assessment-v2-" in run.scoring_version


@pytest.mark.parametrize(
    "response",
    [
        "not-json",
        '{"candidate_ids":[]}',
        None,
        '{"decisions":[{"candidate_id":"wrong","rank":1}]}',
    ],
)
def test_local_ollama_invalid_output_reports_deterministic_fallback(
    tmp_path,
    monkeypatch,
    response,
):
    provider = OllamaProvider(
        timeout=5,
        local_only=True,
        num_predict=96,
        json_mode=True,
    )
    def generate(prompt):
        if response is None:
            raise TimeoutError("local Ollama unavailable")
        return response

    monkeypatch.setattr(provider, "generate", generate)
    h, _, _, _, service, _ = _audio_harness(
        tmp_path,
        monkeypatch,
        local_editorial_provider=provider,
    )
    transcript = _run(h, service, _create_job(h, service).job_id)
    config = HighlightConfig(
        min_duration_seconds=3,
        max_duration_seconds=8,
        max_candidates=5,
        min_score=0,
    )
    expected = select_highlights(transcript, run_id="expected", config=config)
    run = _generate(h, service, transcript.transcript_id, config)

    assert tuple(item.candidate_id for item in run.candidates) == tuple(
        item.candidate_id for item in expected
    )
    assert all(
        "local_ollama_fallback_deterministic_heuristic" in item.warnings
        and "deterministic transcript heuristic ordering is retained"
        in item.explanation
        for item in run.candidates
    )


def test_local_editorial_provider_rejects_non_local_or_unbounded_configuration(
    tmp_path,
    monkeypatch,
):
    with pytest.raises(ValueError, match="loopback"):
        OllamaProvider(url="https://example.com/api/generate", local_only=True)

    h, _, _, _, _, engine = _audio_harness(tmp_path, monkeypatch)
    default_provider = OllamaProvider(
        timeout=5,
        local_only=True,
        num_predict=96,
        json_mode=True,
    )
    assert default_provider._session.trust_env is False
    request = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response": '{"candidate_ids":["id"]}'}

    def post(url, *, json, timeout):
        request.update(url=url, payload=json, timeout=timeout)
        return Response()

    monkeypatch.setattr(default_provider._session, "post", post)
    assert default_provider.generate("bounded editorial request") == '{"candidate_ids":["id"]}'
    assert request["url"] == default_provider.url
    assert request["timeout"] == 5
    assert request["payload"]["format"] == "json"
    assert request["payload"]["options"] == {"num_predict": 96}
    assert "think" not in request["payload"]

    thinking_provider = OllamaProvider(
        timeout=5,
        local_only=True,
        num_predict=96,
        json_mode=True,
        think=False,
    )
    request = {}
    monkeypatch.setattr(thinking_provider._session, "post", post)
    assert thinking_provider.generate("structured editorial request") == '{"candidate_ids":["id"]}'
    assert request["payload"]["think"] is False

    with pytest.raises(ValueError, match="thinking mode"):
        OllamaProvider(think="false")

    with pytest.raises(ValueError, match="local-only Ollama"):
        ContentIntelligenceService(
            content_rights=h.service,
            transcription_engine=engine,
            local_editorial_provider=OllamaProvider(
                timeout=5,
                num_predict=96,
                json_mode=True,
            ),
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
                [
                    SimpleNamespace(
                        start=0.0,
                        end=1.0,
                        text="Local fixture transcript",
                        words=(
                            SimpleNamespace(
                                start=0.0,
                                end=0.4,
                                word="Local",
                                probability=0.95,
                            ),
                            SimpleNamespace(
                                start=0.41,
                                end=0.7,
                                word=" fixture",
                                probability=0.93,
                            ),
                            SimpleNamespace(
                                start=0.71,
                                end=1.0,
                                word=" transcript",
                                probability=0.91,
                            ),
                        ),
                    )
                ],
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
            "word_timestamps": True,
        },
    )
    assert result.segments[0].text == "Local fixture transcript"
    assert tuple(word.text.strip() for word in result.segments[0].words) == (
        "Local",
        "fixture",
        "transcript",
    )
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


def _editorial_fixture(tmp_path, monkeypatch):
    h, _, _, _, service, _ = _audio_harness(tmp_path, monkeypatch)
    transcript = _run(h, service, _create_job(h, service).job_id)
    candidates = select_highlights(
        transcript,
        run_id="editorial-fixture",
        config=HighlightConfig(
            min_duration_seconds=3,
            max_duration_seconds=8,
            max_candidates=5,
            min_score=0,
        ),
    )
    assert len(candidates) >= 2
    return transcript, candidates


def test_experimental_grouping_and_plan_proposal_preserve_evidence_and_compare_v3(
    tmp_path,
    monkeypatch,
):
    transcript, candidates = _editorial_fixture(tmp_path, monkeypatch)
    provider = OllamaProvider(
        timeout=5,
        local_only=True,
        num_predict=96,
        json_mode=True,
    )

    def generate(prompt):
        payload = json.loads(prompt.rsplit("\n", 1)[1])
        if isinstance(payload, list):
            left, right = payload[:2]
            return json.dumps(
                {
                    "relationships": [
                        {
                            "from_candidate_id": left["candidate_id"],
                            "to_candidate_id": right["candidate_id"],
                            "relation": "statement_support",
                            "from_evidence_quote": left["excerpt"][:30],
                            "to_evidence_quote": right["excerpt"][:30],
                            "explanation": "The second excerpt provides context for the first.",
                        }
                    ]
                }
            )
        return json.dumps(
            {
                "segments": [
                    {
                        "candidate_id": row["candidate_id"],
                        "editorial_role": role,
                        "evidence_quote": row["excerpt"][:30],
                        "rationale": "This role is supported by the quoted transcript.",
                    }
                    for row, role in zip(
                        payload["candidates"][:2],
                        ("opening_hook", "payoff"),
                        strict=True,
                    )
                ]
            }
        )

    monkeypatch.setattr(provider, "generate", generate)
    editorial = EditorialIntelligenceService(provider)
    grouping = editorial.group_highlights(transcript, candidates)
    proposal = editorial.propose_plan(
        transcript,
        candidates,
        target_duration_seconds=60,
        grouping=grouping,
    )
    v3 = ContentCreativeEditPlanV3(
        project_id="experimental-v3-comparison",
        revision=1,
        creative_direction="Existing deterministic reference plan.",
        sources=(
            ContentEditPlanSource(
                source_asset_id=transcript.asset_id,
                content_sha256=transcript.asset_sha256,
                duration_seconds=transcript.duration_seconds,
            ),
        ),
        segments=tuple(
            {
                "source_asset_id": candidate.asset_id,
                "source_in_seconds": candidate.start_seconds,
                "source_out_seconds": candidate.end_seconds,
                "timeline_order": index,
                "selection_method": "evenly_spaced_sample",
                "transition_before": "start" if index == 1 else "hard_cut",
                "audio": ContentEditPlanAudio(
                    mode="source_audio",
                    source_asset_id=candidate.asset_id,
                    source_in_seconds=candidate.start_seconds,
                    source_out_seconds=candidate.end_seconds,
                    gain_db=0,
                    timing_policy="trim_pad_to_video_segment",
                ),
            }
            for index, candidate in enumerate(
                (candidates[0], candidates[1]),
                start=1,
            )
        ),
        target_duration_seconds=60,
    )
    comparison = compare_proposal_with_v3(proposal, v3)
    evaluation = evaluate_editorial_outputs(
        transcript,
        candidates,
        candidates,
        grouping,
        proposal,
        comparison,
    )

    assert grouping.method == "local_ollama"
    assert len(grouping.groups) == 1
    relation = grouping.groups[0].relation
    assert relation.from_evidence_quote in " ".join(
        segment.text for segment in transcript.segments
    )
    assert grouping.groups[0].members[0].asset_id == transcript.asset_id
    assert proposal.method == "local_ollama"
    assert all(
        segment.asset_id == transcript.asset_id
        and segment.asset_sha256 == transcript.asset_sha256
        and segment.evidence_quote
        in " ".join(item.text for item in transcript.segments)
        for segment in proposal.segments
    )
    assert comparison.exact_interval_matches == len(proposal.segments)
    assert evaluation.preserved_candidate_provenance is True
    assert evaluation.human_review_required is True
    assert evaluation.quality_improvement_claimed is False


def test_experimental_editorial_services_report_fallback_and_reject_unsupported_quotes(
    tmp_path,
    monkeypatch,
):
    transcript, candidates = _editorial_fixture(tmp_path, monkeypatch)
    provider = OllamaProvider(
        timeout=5,
        local_only=True,
        num_predict=96,
        json_mode=True,
    )
    editorial = EditorialIntelligenceService()
    grouping = editorial.group_highlights(transcript, candidates)
    proposal = editorial.propose_plan(
        transcript,
        candidates,
        target_duration_seconds=60,
    )
    assert grouping.method == "no_model_fallback"
    assert grouping.groups == ()
    assert proposal.method == "no_model_fallback"
    assert "does not infer narrative coherence" in proposal.limitations[0]

    monkeypatch.setattr(
        provider,
        "generate",
        lambda prompt: json.dumps(
            {
                "relationships": [
                    {
                        "from_candidate_id": "not-a-candidate",
                        "to_candidate_id": candidates[0].candidate_id,
                        "relation": "question_answer",
                        "from_evidence_quote": "fabricated",
                        "to_evidence_quote": "fabricated",
                        "explanation": "Unsupported relationship.",
                    }
                ]
            }
        ),
    )
    invalid_grouping = EditorialIntelligenceService(provider).group_highlights(
        transcript,
        candidates,
    )
    assert invalid_grouping.method == "no_model_fallback"
    assert invalid_grouping.groups == ()
    assert invalid_grouping.warnings


def test_builtin_editing_styles_cover_major_editorial_modes_and_platforms():
    profiles = list_editing_style_profiles()
    profile_ids = {profile.style_id for profile in profiles}
    assert {
        "cinematic_documentary",
        "high_retention_viral",
        "educational_storytelling",
        "futuristic_science",
        "suspense_and_mystery",
        "dynamic_social_media",
        "premium_commercial",
        "motion_graphics_driven",
        "emotional_storytelling",
        "minimalist_storytelling",
    } <= profile_ids

    assert select_editing_style("science", target_platform="youtube") == next(
        profile for profile in profiles if profile.style_id == "futuristic_science"
    )
    assert select_editing_style("product launch", target_platform="instagram") == next(
        profile for profile in profiles if profile.style_id == "premium_commercial"
    )


def test_blueprint_builder_uses_evidence_and_produces_non_overlapping_intervals():
    asset_sha256 = "a" * 64
    candidate_one = HighlightCandidate(
        candidate_id="c-1",
        run_id="run-1",
        transcript_id="transcript-1",
        asset_id="asset-1",
        asset_sha256=asset_sha256,
        start_seconds=0.0,
        end_seconds=3.2,
        duration_seconds=3.2,
        rank=1,
        score=0.92,
        scores={"evidence": 0.92},
        evidence={"local_ollama_transcript_quotes": ("This proves the point.",)},
        transcript_segment_ids=("seg-1",),
        explanation="Strong opening evidence.",
        warnings=(),
        scoring_version="v1",
        created_at=datetime.now(UTC),
    )
    candidate_two = HighlightCandidate(
        candidate_id="c-2",
        run_id="run-1",
        transcript_id="transcript-1",
        asset_id="asset-1",
        asset_sha256=asset_sha256,
        start_seconds=3.2,
        end_seconds=6.4,
        duration_seconds=3.2,
        rank=2,
        score=0.9,
        scores={"evidence": 0.9},
        evidence={"local_ollama_transcript_quotes": ("This confirms the conclusion.",)},
        transcript_segment_ids=("seg-2",),
        explanation="Strong support evidence.",
        warnings=(),
        scoring_version="v1",
        created_at=datetime.now(UTC),
    )

    blueprint = build_editorial_blueprint(
        blueprint_id="blueprint-1",
        asset_id="asset-1",
        asset_sha256=asset_sha256,
        content_category="science",
        transcript_id="transcript-1",
        candidates=(candidate_one, candidate_two),
        target_platform="youtube_shorts",
        editing_style="futuristic_science",
    )

    assert isinstance(blueprint, EditorialBlueprint)
    assert blueprint.editing_style.style_id == "futuristic_science"
    assert blueprint.selected_intervals == ((0.0, 3.2), (3.2, 6.4))
    assert blueprint.source_evidence[0].startswith("This")
    assert blueprint.target_platform == "youtube_shorts"
    assert blueprint.pacing_profile == "fast"
