from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import statistics
import struct
import subprocess
import sys
import wave
from array import array
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image, ImageChops

from tests.application import test_content_lifecycle as lifecycle
from tests.application.test_content_intelligence import FakeTranscriptionEngine
from tests.application.test_content_processing import _harness
from tests.application.schema_test_utils import set_legacy_schema_version
from ultron.application.content.clip_renderer import (
    ClipRenderTimeout,
    FFmpegLocalClipRenderer,
    MediaInfo,
    RenderCaption,
    SourceEditSegment,
    _map_caption_to_output_timeline,
)
from ultron.application.content.intelligence_service import ContentIntelligenceService
from ultron.application.content.processing import LocalTemporaryContentStore, SyntheticContentProcessor
from ultron.application.content.rendering import (
    ContentClipRenderingService,
    _caption_directives_for_interval,
    _edit_direction_settings,
    _graphic_directives_for_interval,
    _select_scene_diverse_intervals,
    _transition_directive_for_boundary,
)
from ultron.application.content.service import ContentRightsService
from ultron.application.content.store import ContentStore
from ultron.domain.content import (
    AcquisitionMethod,
    AssetCategory,
    ContentCreativeEditPlan,
    ContentCreativeEditPlanV2,
    ContentCreativeEditPlanV3,
    ContentEditPlanAudio,
    ContentEditPlanCaption,
    ContentEditPlanGraphic,
    ContentEditPlanMotion,
    ContentEditPlanSegment,
    ContentEditPlanSource,
    ContentEditPlanTransition,
    ContentEditSegment,
    ContentEditProject,
    ContentPlatform,
    ContentTargetContext,
    ProcessingJobState,
    RightsAction,
    RightsUse,
    SourceAccessPermissionState,
    SourceAsset,
    SourceProvenance,
    SourceReferenceRecord,
)
from ultron.domain.content_intelligence import (
    EngineTranscript,
    EngineTranscriptSegment,
    HighlightConfig,
    TranscriptionConfig,
    TranscriptRecord,
    TranscriptSegment,
    TranscriptWord,
)
from ultron.domain.content_owner_processing import OwnerLocalProcessingOperation


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


def _tone_wav_bytes(seconds: float = 4.0) -> bytes:
    sample_rate = 16_000
    frames = int(sample_rate * seconds)
    samples = b"".join(
        struct.pack(
            "<h",
            int(9_000 * math.sin(2 * math.pi * 440 * index / sample_rate)),
        )
        for index in range(frames)
    )
    target = BytesIO()
    with wave.open(target, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(samples)
    return target.getvalue()


def _structured_plan(project_id, asset, *, revision=1, out_seconds=4.0):
    return ContentCreativeEditPlan(
        project_id=project_id,
        revision=revision,
        creative_direction="Synthetic continuity test.",
        sources=(
            ContentEditPlanSource(
                source_asset_id=asset.asset_id,
                content_sha256=asset.content_sha256,
                duration_seconds=8,
                scene_boundary_seconds=(2.0, 4.0, 6.0),
            ),
        ),
        segments=(
            ContentEditPlanSegment(
                source_asset_id=asset.asset_id,
                source_in_seconds=0,
                source_out_seconds=2.0,
                timeline_order=1,
                selection_method="nearest_detected_scene_cut",
                selected_scene_cut_seconds=2.0,
                transition_before="start",
            ),
            ContentEditPlanSegment(
                source_asset_id=asset.asset_id,
                source_in_seconds=4,
                source_out_seconds=4 + out_seconds - 2.0,
                timeline_order=2,
                selection_method="evenly_spaced_sample",
                transition_before="hard_cut",
            ),
        ),
    )


def _structured_plan_v2(project_id, asset, *, revision=1):
    silent_audio = ContentEditPlanAudio(
        mode="silence",
        source_asset_id=None,
        source_in_seconds=None,
        source_out_seconds=None,
        gain_db=0,
        timing_policy="trim_pad_to_video_segment",
    )
    return ContentCreativeEditPlanV2(
        project_id=project_id,
        revision=revision,
        creative_direction="Synthetic caption persistence test.",
        target_duration_seconds=4.0,
        render_style_id="high_retention_viral",
        aspect_ratio="9:16",
        audio_policy="explicit_segment_audio",
        sources=(
            ContentEditPlanSource(
                source_asset_id=asset.asset_id,
                content_sha256=asset.content_sha256,
                duration_seconds=8,
                scene_boundary_seconds=(2.0, 4.0, 6.0),
            ),
        ),
        segments=(
            {
                "source_asset_id": asset.asset_id,
                "source_in_seconds": 0.0,
                "source_out_seconds": 2.0,
                "timeline_order": 1,
                "selection_method": "nearest_detected_scene_cut",
                "selected_scene_cut_seconds": 2.0,
                "transition_before": "start",
                "audio": silent_audio,
            },
            {
                "source_asset_id": asset.asset_id,
                "source_in_seconds": 4.0,
                "source_out_seconds": 6.0,
                "timeline_order": 2,
                "selection_method": "evenly_spaced_sample",
                "transition_before": "hard_cut",
                "audio": silent_audio,
            },
        ),
    )


def _transition_plan(
    project_id,
    asset,
    *,
    transition: ContentEditPlanTransition,
    style_id="cinematic_documentary",
):
    data = _structured_plan_v2(project_id, asset).model_dump(mode="json")
    data["render_style_id"] = style_id
    data["audio_policy"] = "source_audio"
    first, second = data["segments"]
    first["audio"].update(
        {
            "mode": "source_audio",
            "source_asset_id": asset.asset_id,
            "source_in_seconds": 0.0,
            "source_out_seconds": 2.0,
        }
    )
    second["audio"].update(
        {
            "mode": "source_audio",
            "source_asset_id": asset.asset_id,
            "source_in_seconds": 4.0,
            "source_out_seconds": 6.0,
        }
    )
    second.update(
        {
            "selection_method": "distinct_detected_scene_interval",
            "selected_scene_cut_seconds": 4.0,
            "transition_before": transition.kind,
            "transition_duration_seconds": transition.duration_seconds,
            "transition": transition.model_dump(mode="json"),
            "motion": ContentEditPlanMotion(
                direction="slow_zoom_in",
                intensity=0.8,
                start_scale=1.0,
                end_scale=1.12,
                duration_seconds=1.5,
                easing="ease_in_out",
            ).model_dump(mode="json"),
            "captions": [
                ContentEditPlanCaption(
                    transcript_id="synthetic-transition-transcript",
                    transcript_segment_ids=("segment-2",),
                    start_seconds=4.2,
                    end_seconds=4.8,
                    text="Transition timing fixture",
                    kind="phrase",
                    presentation="cinematic_documentary",
                ).model_dump(mode="json")
            ],
        }
    )
    return ContentCreativeEditPlanV2.model_validate(data)


def _original_overlap_plan(
    project_id,
    asset,
    *,
    final_interval=(0.0, 2.5),
    final_audio_interval=None,
    final_selection_method="repeated_detected_scene_interval",
    final_audio_mode="source_audio",
):
    source_start, source_end = final_interval
    audio_start, audio_end = (
        final_interval if final_audio_interval is None else final_audio_interval
    )

    def audio_decision(start, end, mode="source_audio"):
        return ContentEditPlanAudio(
            mode=mode,
            source_asset_id=asset.asset_id if mode == "source_audio" else None,
            source_in_seconds=start if mode == "source_audio" else None,
            source_out_seconds=end if mode == "source_audio" else None,
            gain_db=0,
            timing_policy="trim_pad_to_video_segment",
        )

    return ContentCreativeEditPlanV2(
        project_id=project_id,
        revision=1,
        creative_direction="Reproduce the original repeated source interval.",
        target_duration_seconds=7.5,
        sources=(
            ContentEditPlanSource(
                source_asset_id=asset.asset_id,
                content_sha256=asset.content_sha256,
                duration_seconds=8.100907,
                scene_boundary_seconds=(3.933333,),
            ),
        ),
        segments=(
            {
                "source_asset_id": asset.asset_id,
                "source_in_seconds": 0.0,
                "source_out_seconds": 2.5,
                "timeline_order": 1,
                "selection_method": "distinct_detected_scene_interval",
                "transition_before": "start",
                "audio": audio_decision(0.0, 2.5),
            },
            {
                "source_asset_id": asset.asset_id,
                "source_in_seconds": 3.933333,
                "source_out_seconds": 6.433333,
                "timeline_order": 2,
                "selection_method": "distinct_detected_scene_interval",
                "selected_scene_cut_seconds": 3.933333,
                "transition_before": "hard_cut",
                "audio": audio_decision(3.933333, 6.433333),
            },
            {
                "source_asset_id": asset.asset_id,
                "source_in_seconds": source_start,
                "source_out_seconds": source_end,
                "timeline_order": 3,
                "selection_method": final_selection_method,
                "transition_before": "hard_cut",
                "audio": audio_decision(
                    audio_start,
                    audio_end,
                    final_audio_mode,
                ),
            },
        ),
        audio_policy="source_audio",
    )


def _create_restricted_test_permit(harness, asset):
    acknowledgement = harness.service.RESTRICTED_TEST_ACKNOWLEDGEMENT
    payload = {
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
    return harness.invoke(
        "content_create_restricted_technical_test",
        payload,
        harness.service.create_restricted_technical_test_permit,
        asset_id=asset.asset_id,
        asset_sha256=asset.content_sha256,
        acknowledgement=acknowledgement,
    )


class _FixtureTranscriptionEngine:
    engine_id = "rendering-test-engine"
    engine_version = "1"

    def transcribe(
        self,
        audio_bytes,
        *,
        language,
        max_segments,
        max_segment_characters,
        max_text_characters,
    ):
        segments = (
            EngineTranscriptSegment(
                start_seconds=0.5,
                end_seconds=1.5,
                text="Why does this method work? The measured result changes.",
                words=(
                    TranscriptWord(start_seconds=0.50, end_seconds=0.58, text="Why"),
                    TranscriptWord(start_seconds=0.58, end_seconds=0.66, text="does"),
                    TranscriptWord(start_seconds=0.66, end_seconds=0.74, text="this"),
                    TranscriptWord(start_seconds=0.74, end_seconds=0.86, text="method"),
                    TranscriptWord(start_seconds=0.86, end_seconds=0.96, text="work?"),
                    TranscriptWord(start_seconds=0.96, end_seconds=1.02, text="The"),
                    TranscriptWord(start_seconds=1.02, end_seconds=1.20, text="measured"),
                    TranscriptWord(start_seconds=1.20, end_seconds=1.32, text="result"),
                    TranscriptWord(start_seconds=1.32, end_seconds=1.50, text="changes."),
                ),
            ),
            EngineTranscriptSegment(
                start_seconds=2.0,
                end_seconds=3.5,
                text="The evidence explains the difference.",
            ),
        )
        raw = [segment.model_dump(mode="json") for segment in segments]
        digest = hashlib.sha256(
            json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return EngineTranscript(
            segments=segments,
            language="en",
            raw_output_sha256=digest,
        )


def _authorized_pipeline(
    tmp_path,
    monkeypatch,
    *,
    media_bytes: bytes | None = None,
    media_renderer: FFmpegLocalClipRenderer | None = None,
    scene_boundary_seconds: tuple[float, ...] = (),
    rights_actions=frozenset(
        {RightsAction.ANALYZE, RightsAction.CREATE_DERIVATIVE}
    ),
):
    data = _wav_bytes() if media_bytes is None else media_bytes
    harness, asset, grant, storage = _harness(
        tmp_path,
        monkeypatch,
        media_bytes=data,
        rights_actions=rights_actions,
    )
    intelligence = ContentIntelligenceService(
        content_rights=harness.service,
        transcription_engine=_FixtureTranscriptionEngine(),
        media_renderer=media_renderer,
    )
    use = lifecycle._rights_use()
    transcription_config = TranscriptionConfig(max_duration_seconds=30)
    job_payload = {
        "use": use.model_dump(mode="json"),
        "target_context": ContentTargetContext.YOUTUBE_SHORTS.value,
        "config": transcription_config.model_dump(mode="json"),
    }
    job = harness.invoke(
        "content_create_transcription_job",
        job_payload,
        intelligence.create_transcription_job,
        use=use,
        target_context=ContentTargetContext.YOUTUBE_SHORTS,
        config=transcription_config,
    )
    transcript = harness.invoke(
        "content_run_transcription_job",
        {"job_id": job.job_id},
        intelligence.run_transcription_job,
        job_id=job.job_id,
    )
    highlight_config = HighlightConfig(
        min_duration_seconds=3,
        max_duration_seconds=4,
        min_score=0,
        scene_boundary_seconds=scene_boundary_seconds,
    )
    highlight_payload = {
        "transcript_id": transcript.transcript_id,
        "config": highlight_config.model_dump(mode="json"),
    }
    run = harness.invoke(
        "content_generate_highlights",
        highlight_payload,
        intelligence.generate_highlights,
        transcript_id=transcript.transcript_id,
        config=highlight_config,
    )
    assert run.candidates
    rendering = ContentClipRenderingService(
        content_rights=harness.service,
        renderer=media_renderer,
    )
    return harness, asset, grant, storage, transcript, run.candidates[0], rendering


def _derivative_use(asset, candidate) -> RightsUse:
    return RightsUse(
        asset_id=asset.asset_id,
        expected_asset_sha256=asset.content_sha256,
        platform=ContentPlatform.YOUTUBE,
        account_id="channel-1",
        territory="US",
        actions=frozenset({RightsAction.CREATE_DERIVATIVE}),
        commercial_intent=False,
        monetization_intent=False,
        duration_seconds=math.ceil(candidate.duration_seconds),
    )


def _create_render(harness, rendering, candidate, use, *, command_id=None):
    payload = {
        "candidate_id": candidate.candidate_id,
        "rights_use": use.model_dump(mode="json"),
    }
    return harness.invoke(
        "content_create_clip_render",
        payload,
        rendering.create_clip_render,
        candidate_id=candidate.candidate_id,
        use=use,
        **({"command_id": command_id} if command_id else {}),
    )


def _register_analysis_scope(harness, asset):
    use = lifecycle._rights_use(
        actions=frozenset({RightsAction.ANALYZE}),
    ).model_copy(
        update={
            "asset_id": asset.asset_id,
            "expected_asset_sha256": asset.content_sha256,
        }
    )
    source = lifecycle._source(
        rights_use=use,
    )
    harness.invoke(
        "content_register_source_reference",
        source.model_dump(mode="json"),
        harness.service.register_source_reference,
        source=source,
    )


def test_owner_local_authorization_autoplans_and_renders_private_video(
    tmp_path,
    monkeypatch,
):
    source_bytes = _source_video_bytes(tmp_path)
    harness, _, _, storage = _harness(
        tmp_path,
        monkeypatch,
        media_bytes=source_bytes,
        with_rights=False,
    )
    harness.store.migrate_owner_local_processing_schema()
    asset_sha256 = hashlib.sha256(source_bytes).hexdigest()
    asset = SourceAsset(
        asset_id="owner-private-render-source",
        owner_id=harness.authority.principal,
        category=AssetCategory.FOOTAGE,
        content_sha256=asset_sha256,
        provenance=SourceProvenance(
            acquisition_method=AcquisitionMethod.OWNER_CREATED,
            acquired_at=datetime.now(UTC),
            original_filename="owner-private-render-source.mp4",
            transfer_channel="direct_test_upload",
            source_reference=f"urn:ultron:owner-created:{asset_sha256}",
        ),
        storage_key="owner-private-render-source-input",
    )
    analysis_use = lifecycle._rights_use(
        actions=frozenset({RightsAction.ANALYZE}),
    ).model_copy(
        update={
            "asset_id": asset.asset_id,
            "expected_asset_sha256": asset.content_sha256,
            "duration_seconds": 8,
        }
    )
    source_reference = SourceReferenceRecord(
        intake_id="owner-private-render-source-intake",
        asset_id=asset.asset_id,
        owner_id=asset.owner_id,
        source_reference=asset.provenance.source_reference,
        acquisition_method=AcquisitionMethod.OWNER_CREATED,
        provenance=asset.provenance,
        claimed_source_sha256=asset_sha256,
        access_permission_state=SourceAccessPermissionState.NOT_REQUIRED,
        target_context=ContentTargetContext.YOUTUBE_SHORTS,
        rights_use=analysis_use,
    )
    operations = frozenset(
        {
            OwnerLocalProcessingOperation.TRANSCRIBE,
            OwnerLocalProcessingOperation.ANALYZE_AUDIO,
            OwnerLocalProcessingOperation.ANALYZE_VIDEO,
            OwnerLocalProcessingOperation.DETECT_HIGHLIGHTS,
            OwnerLocalProcessingOperation.GENERATE_EDIT_PLAN,
            OwnerLocalProcessingOperation.RENDER_PRIVATE,
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
        command_id="owner-private-render-registration",
        source_reference=source_reference,
        owner_local_processing_operations=operations,
    )
    rendering = ContentClipRenderingService(
        content_rights=harness.service,
        renderer=FFmpegLocalClipRenderer(
            managed_staging_directory=storage.clip_staging_directory,
        ),
    )
    intelligence = ContentIntelligenceService(
        content_rights=harness.service,
        transcription_engine=FakeTranscriptionEngine(),
        media_renderer=rendering.renderer,
    )
    transcription_config = TranscriptionConfig()
    transcription_payload = {
        "use": analysis_use.model_dump(mode="json"),
        "target_context": ContentTargetContext.YOUTUBE_SHORTS.value,
        "config": transcription_config.model_dump(mode="json"),
    }
    transcription_job = harness.invoke(
        "content_create_transcription_job",
        transcription_payload,
        intelligence.create_transcription_job,
        use=analysis_use,
        target_context=ContentTargetContext.YOUTUBE_SHORTS,
        config=transcription_config,
        command_id="owner-private-render-transcription-create",
    )
    transcript = harness.invoke(
        "content_run_transcription_job",
        {"job_id": transcription_job.job_id},
        intelligence.run_transcription_job,
        job_id=transcription_job.job_id,
        command_id="owner-private-render-transcription-run",
    )
    harness.invoke(
        "content_read_transcript",
        {"transcript_id": transcript.transcript_id},
        intelligence.get_transcript,
        transcript_id=transcript.transcript_id,
        command_id="owner-private-render-transcript-read",
    )
    harness.invoke(
        "content_generate_highlights",
        {
            "transcript_id": transcript.transcript_id,
            "config": HighlightConfig().model_dump(mode="json"),
        },
        intelligence.generate_highlights,
        transcript_id=transcript.transcript_id,
        config=HighlightConfig(),
        command_id="owner-private-render-highlights",
    )
    project = ContentEditProject(
        project_id="owner-private-render-project",
        owner_id=asset.owner_id,
        source_asset_ids=(asset.asset_id,),
        script="Create a private local review edit.",
    )
    harness.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        harness.service.create_edit_project,
        project=project,
        command_id="owner-private-render-project-create",
    )
    instruction = "Create a short private review edit."
    plan = harness.invoke(
        "content_autoplan_edit_project",
        {
            "project_id": project.project_id,
            "instruction_length": len(instruction),
        },
        rendering.autoplan_edit_project,
        project_id=project.project_id,
        instruction=instruction,
        command_id="owner-private-render-plan",
    )
    persisted_plan = harness.store.get_latest_edit_plan(
        project.project_id,
        asset.owner_id,
    )
    assert isinstance(persisted_plan, ContentCreativeEditPlanV3)
    render_use = lifecycle._rights_use(
        actions=frozenset({RightsAction.EDIT, RightsAction.CREATE_DERIVATIVE}),
    ).model_copy(
        update={
            "asset_id": asset.asset_id,
            "expected_asset_sha256": asset.content_sha256,
            "duration_seconds": math.ceil(persisted_plan.target_duration_seconds),
        }
    )
    render_result = harness.invoke(
        "content_render_edit_project",
        {
            "project_id": project.project_id,
            "asset_ids": [asset.asset_id],
            "vertical": True,
        },
        rendering.render_edit_project,
        project_id=project.project_id,
        rights_uses={asset.asset_id: render_use},
        vertical=True,
        command_id="owner-private-render-output",
    )
    rendered_bytes = storage.read_clip_output(
        str(render_result["artifactId"]),
        str(render_result["sha256"]),
    )
    output_path = tmp_path / "owner-private-render-output.mp4"
    output_path.write_bytes(rendered_bytes)
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type",
            "-of",
            "json",
            str(output_path),
        ],
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert probe.returncode == 0, probe.stderr.decode(errors="replace")
    media = json.loads(probe.stdout)
    assert {stream["codec_type"] for stream in media["streams"]} == {"audio", "video"}
    assert float(media["format"]["duration"]) == pytest.approx(
        float(render_result["durationSeconds"]),
        abs=0.25,
    )
    decoded = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(output_path),
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert decoded.returncode == 0, decoded.stderr.decode(errors="replace")
    assert plan["projectId"] == project.project_id
    assert render_result["projectId"] == project.project_id
    assert harness.store._connection.execute(
        "SELECT COUNT(*) FROM rights_grants WHERE asset_id = ?",
        (asset.asset_id,),
    ).fetchone()[0] == 0
    assert harness.store.get_latest_asset_license_record(
        asset.asset_id,
        asset.owner_id,
    ) is None
    harness.close()


def test_autoplan_persists_segments_inside_authorized_transaction(
    tmp_path,
    monkeypatch,
):
    h, asset, _, storage = _harness(
        tmp_path,
        monkeypatch,
        media_bytes=_wav_bytes(),
        rights_actions=frozenset({RightsAction.ANALYZE}),
    )
    _register_analysis_scope(h, asset)
    rendering = ContentClipRenderingService(content_rights=h.service)
    monkeypatch.setattr(
        rendering.renderer,
        "inspect_video_source",
        lambda source: MediaInfo(duration_seconds=10, has_video=True, has_audio=False),
    )
    monkeypatch.setattr(rendering.renderer, "detect_scene_changes", lambda source: ())
    project = ContentEditProject(
        project_id="autoplan-transaction-test",
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script="Synthetic private edit.",
    )
    h.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        h.service.create_edit_project,
        project=project,
        command_id="create-autoplan-transaction-test",
    )
    command_id = "autoplan-transaction-test-command"
    instruction = "Create a synthetic private review edit."
    payload = {
        "project_id": project.project_id,
        "instruction_length": len(instruction),
    }

    plan = h.invoke(
        "content_autoplan_edit_project",
        payload,
        rendering.autoplan_edit_project,
        project_id=project.project_id,
        instruction=instruction,
        command_id=command_id,
    )

    assert plan["projectId"] == project.project_id
    assert plan["segmentCount"] >= 2
    persisted_plan = h.store.get_latest_edit_plan(project.project_id, "alice")
    assert isinstance(persisted_plan, ContentCreativeEditPlanV3)
    assert all(segment.audio.mode == "silence" for segment in persisted_plan.segments)
    assert all(not segment.captions for segment in persisted_plan.segments)
    assert persisted_plan.segments[0].transition is None
    assert all(
        segment.transition is not None
        and segment.transition.kind == "hard_cut"
        for segment in persisted_plan.segments[1:]
    )
    assert all(
        "no matching transcript evidence" in segment.text_decision.lower()
        for segment in persisted_plan.segments
    )
    persisted_segments = h.store.get_edit_segments(project.project_id, "alice")
    assert len(persisted_segments) == plan["segmentCount"]
    assert [
        {
            key: row[key]
            for key in (
                "source_asset_id",
                "source_in_seconds",
                "source_out_seconds",
                "timeline_order",
            )
        }
        for row in plan["segments"]
    ] == [
        segment.model_dump(mode="json") for segment in persisted_segments
    ]
    assert [item["narrative_purpose"] for item in plan["segments"]][0] == "opening"
    assert [item["narrative_purpose"] for item in plan["segments"]][-1] == "ending"
    assert all(item["decision_reason"] for item in plan["segments"])
    assert all(item["text_decision"] for item in plan["segments"])
    assert plan["targetDurationSeconds"] <= 10
    assert all(segment.source_asset_id == asset.asset_id for segment in persisted_segments)
    command = h.store._connection.execute(
        "SELECT result_id FROM content_commands "
        "WHERE principal = ? AND action = ? AND command_id = ?",
        ("alice", "content_autoplan_edit_project", command_id),
    ).fetchone()
    assert command["result_id"] == project.project_id
    audit = h.store._connection.execute(
        "SELECT decision, details_json FROM content_audit_events "
        "WHERE principal = ? AND action = ? AND subject_id = ?",
        ("alice", "content_autoplan_edit_project", project.project_id),
    ).fetchone()
    assert audit["decision"] == "planned"
    audit_details = json.loads(audit["details_json"])
    assert audit_details["segment_count"] == plan["segmentCount"]
    assert audit_details["source_asset_ids"] == [asset.asset_id]
    assert audit_details["plan_revision"] == plan["planRevision"] == 1
    assert audit_details["plan_sha256"] == plan["planSha256"]
    assert not h.store._connection.in_transaction
    assert storage.clip_staging_directory.exists()
    assert h.store.replace_edit_segments(
        project.project_id,
        "alice",
        persisted_segments,
    ) == persisted_segments
    timeline_payload = {
        "project_id": project.project_id,
        "owner_id": "alice",
        "segments": [segment.model_dump(mode="json") for segment in persisted_segments],
    }
    assert h.invoke(
        "content_replace_edit_timeline",
        timeline_payload,
        h.service.replace_edit_timeline,
        project_id=project.project_id,
        owner_id="alice",
        segments=persisted_segments,
    ) == persisted_segments
    assert not h.store._connection.in_transaction


def test_motion_directives_validate_and_execute_with_measurable_frame_change(tmp_path):
    motion = ContentEditPlanMotion(
        direction="slow_zoom_in",
        intensity=1.2,
        start_scale=1.0,
        end_scale=1.18,
        focus_x=0.5,
        focus_y=0.5,
        duration_seconds=1.5,
        easing="ease_in_out",
    )
    assert motion.direction == "slow_zoom_in"
    assert motion.end_scale > motion.start_scale

    with pytest.raises(ValueError, match="Static motion"):
        ContentEditPlanMotion(
            direction="static",
            start_scale=1.1,
            end_scale=1.1,
            focus_x=0.5,
            focus_y=0.5,
            duration_seconds=0.0,
        )

    with pytest.raises(ValueError, match="Motion focal"):
        ContentEditPlanMotion(
            direction="pan_left",
            focus_x=1.5,
            focus_y=0.5,
            duration_seconds=1.0,
        )
    with pytest.raises(ValueError):
        ContentEditPlanMotion(direction="invalid_motion")
    with pytest.raises(ValueError):
        ContentEditPlanMotion(direction="slow_zoom_in", end_scale=1.6)

    short_motion_filter = FFmpegLocalClipRenderer._segment_motion_filter(
        {"direction": "slow_zoom_in", "duration_seconds": 2.0},
        vertical=True,
        segment_duration_seconds=0.05,
    )
    assert "t/0.050000" in short_motion_filter

    source_bytes = _source_video_bytes(tmp_path)
    managed = tmp_path / "managed"
    managed.mkdir()
    source_path = managed / f".clip-motion-source-{uuid4().hex}.media"
    source_path.write_bytes(source_bytes)
    resized_source = managed / f".clip-motion-source-resized-{uuid4().hex}.media"
    resized = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source_path),
            "-vf",
            "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "30",
            "-f",
            "mp4",
            "-y",
            str(resized_source),
        ],
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert resized.returncode == 0, resized.stderr.decode(errors="replace")
    source_path = resized_source
    renderer = FFmpegLocalClipRenderer(managed_staging_directory=managed)

    variants = {
        "static": {"direction": "static"},
        "slow_zoom_in": {"direction": "slow_zoom_in", "duration_seconds": 1.5},
        "slow_zoom_out": {"direction": "slow_zoom_out", "duration_seconds": 1.5},
        "punch_in": {"direction": "punch_in", "duration_seconds": 1.5},
        "pan_left": {"direction": "pan_left", "duration_seconds": 1.5},
        "pan_right": {"direction": "pan_right", "duration_seconds": 1.5},
        "pan_up": {"direction": "pan_up", "duration_seconds": 1.5},
        "pan_down": {"direction": "pan_down", "duration_seconds": 1.5},
    }
    frame_diffs: dict[str, float] = {}
    for name, config in variants.items():
        out = managed / f".clip-motion-{name}-output-{uuid4().hex}.mp4"
        out.write_bytes(b"")
        segments = (
            SourceEditSegment(
                source_in_seconds=0.0,
                source_out_seconds=1.5,
                source_asset_id="asset-1",
                motion=config,
            ),
            SourceEditSegment(
                source_in_seconds=1.5,
                source_out_seconds=3.0,
                source_asset_id="asset-1",
                motion={"direction": "static"},
            ),
        )
        renderer.render_multi_source_timeline(
            {"asset-1": source_path},
            segments=segments,
            staged_output=out,
            vertical=True,
        )
        first_frame_path = tmp_path / f"{name}-first.png"
        mid_frame_path = tmp_path / f"{name}-mid.png"
        subprocess.run(
            [
                renderer.ffmpeg,
                "-y",
                "-ss",
                "0.0",
                "-i",
                str(out),
                "-frames:v",
                "1",
                str(first_frame_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        subprocess.run(
            [
                renderer.ffmpeg,
                "-y",
                "-ss",
                "0.5",
                "-i",
                str(out),
                "-frames:v",
                "1",
                str(mid_frame_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        with Image.open(first_frame_path) as first_image, Image.open(mid_frame_path) as mid_image:
            diff = sum(
                abs(int(a) - int(b))
                for a, b in zip(first_image.convert("L").getdata(), mid_image.convert("L").getdata())
            ) / (first_image.width * first_image.height)
        frame_diffs[name] = diff

    assert frame_diffs["static"] < 0.02
    for direction in variants.keys() - {"static"}:
        assert frame_diffs[direction] > 1.0


def test_autoplan_video_only_source_uses_real_scene_detection_and_persists_plan(
    tmp_path,
    monkeypatch,
):
    source_bytes = _video_only_source_bytes(
        tmp_path,
        name="video-only-autoplan",
    )
    harness, asset, _, storage = _harness(
        tmp_path,
        monkeypatch,
        media_bytes=source_bytes,
        rights_actions=frozenset({RightsAction.ANALYZE}),
    )
    _register_analysis_scope(harness, asset)
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory,
    )
    rendering = ContentClipRenderingService(
        content_rights=harness.service,
        renderer=renderer,
    )
    project = ContentEditProject(
        project_id="video-only-autoplan-test",
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script="Synthetic video-only scene detection and planning.",
    )
    harness.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        harness.service.create_edit_project,
        project=project,
        command_id="create-video-only-autoplan-test",
    )

    instruction = "Create a synthetic video edit, 6 sec."
    result = harness.invoke(
        "content_autoplan_edit_project",
        {
            "project_id": project.project_id,
            "instruction_length": len(instruction),
        },
        rendering.autoplan_edit_project,
        project_id=project.project_id,
        instruction=instruction,
        command_id="plan-video-only-autoplan-test",
    )

    plan = harness.store.get_latest_edit_plan(project.project_id, "alice")
    projection = harness.store.get_edit_segments(project.project_id, "alice")
    assert isinstance(plan, ContentCreativeEditPlanV2)
    assert result["planRevision"] == plan.revision == 1
    assert result["planSha256"] == hashlib.sha256(
        harness.store._canonical(plan).encode("utf-8")
    ).hexdigest()
    assert plan.sources[0].source_asset_id == asset.asset_id
    assert plan.sources[0].content_sha256 == asset.content_sha256
    assert plan.sources[0].scene_boundary_seconds
    assert len(plan.segments) == result["segmentCount"] >= 2
    assert len(projection) == len(plan.segments)
    assert [segment.timeline_order for segment in plan.segments] == list(
        range(1, len(plan.segments) + 1)
    )
    assert all(
        segment.source_asset_id == asset.asset_id
        and 0 <= segment.source_in_seconds < segment.source_out_seconds
        <= plan.sources[0].duration_seconds
        for segment in plan.segments
    )
    assert all(segment.audio.mode == "silence" for segment in plan.segments)
    assert plan.audio_policy == "explicit_segment_audio"
    assert tuple(
        (
            segment.source_asset_id,
            segment.source_in_seconds,
            segment.source_out_seconds,
            segment.timeline_order,
        )
        for segment in plan.segments
    ) == tuple(
        (
            segment.source_asset_id,
            segment.source_in_seconds,
            segment.source_out_seconds,
            segment.timeline_order,
        )
        for segment in projection
    )
    assert not harness.store._connection.in_transaction
    database = harness.store.database_identity
    harness.store.close()
    reopened = ContentStore(database)
    try:
        assert reopened.get_latest_edit_plan(project.project_id, "alice") == plan
        assert reopened.get_edit_segments(project.project_id, "alice") == projection
    finally:
        reopened.close()


def test_autoplan_clamps_source_intervals_to_video_when_audio_runs_longer(
    tmp_path,
    monkeypatch,
):
    source_bytes = _video_with_longer_audio_source_bytes(tmp_path)
    source_path = tmp_path / "longer-audio-source.mp4"
    harness, asset, _, storage = _harness(
        tmp_path,
        monkeypatch,
        media_bytes=source_bytes,
        rights_actions=frozenset(
            {
                RightsAction.ANALYZE,
                RightsAction.EDIT,
                RightsAction.CREATE_DERIVATIVE,
            }
        ),
    )
    _register_analysis_scope(harness, asset)
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory,
    )
    staged_source = storage.stage_clip_source(asset.asset_id, source_bytes)
    try:
        media_info = renderer.inspect_video_source(staged_source)
    finally:
        storage.delete_clip_staging(staged_source)
    assert media_info.video_duration_seconds is not None
    assert media_info.audio_duration_seconds is not None
    assert media_info.audio_duration_seconds > media_info.video_duration_seconds
    rendering = ContentClipRenderingService(
        content_rights=harness.service,
        renderer=renderer,
    )
    project = ContentEditProject(
        project_id="longer-audio-autoplan-test",
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script="Synthetic source duration boundary test.",
    )
    harness.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        harness.service.create_edit_project,
        project=project,
        command_id="create-longer-audio-autoplan-test",
    )
    instruction = "Create a synthetic source edit."
    harness.invoke(
        "content_autoplan_edit_project",
        {
            "project_id": project.project_id,
            "instruction_length": len(instruction),
        },
        rendering.autoplan_edit_project,
        project_id=project.project_id,
        instruction=instruction,
        command_id="plan-longer-audio-autoplan-test",
    )

    plan = harness.store.get_latest_edit_plan(project.project_id, "alice")
    assert isinstance(plan, ContentCreativeEditPlanV3)
    video_duration = media_info.video_duration_seconds
    assert plan.sources[0].duration_seconds == pytest.approx(video_duration)
    assert plan.target_duration_seconds == pytest.approx(video_duration)
    assert all(
        segment.source_out_seconds <= video_duration
        for segment in plan.segments
    )
    assert all(
        abs(
            segment.source_in_seconds * renderer.EDIT_FRAME_RATE_FPS
            - round(segment.source_in_seconds * renderer.EDIT_FRAME_RATE_FPS)
        )
        < 1e-6
        and abs(
            segment.source_out_seconds * renderer.EDIT_FRAME_RATE_FPS
            - round(segment.source_out_seconds * renderer.EDIT_FRAME_RATE_FPS)
        )
        < 1e-6
        for segment in plan.segments
    )
    ordered_plan_segments = sorted(
        plan.segments,
        key=lambda segment: segment.timeline_order,
    )
    assert all(
        current.source_in_seconds >= previous.source_out_seconds - 1e-6
        for previous, current in zip(
            ordered_plan_segments,
            ordered_plan_segments[1:],
        )
    )
    assert len({
        (segment.source_in_seconds, segment.source_out_seconds)
        for segment in plan.segments
    }) == len(plan.segments)

    rights_use = lifecycle._rights_use(
        actions=frozenset({RightsAction.EDIT, RightsAction.CREATE_DERIVATIVE}),
    ).model_copy(
        update={
            "asset_id": asset.asset_id,
            "expected_asset_sha256": asset.content_sha256,
            "duration_seconds": math.ceil(plan.target_duration_seconds),
        }
    )
    render_result = harness.invoke(
        "content_render_edit_project",
        {
            "project_id": project.project_id,
            "asset_ids": [asset.asset_id],
            "vertical": True,
        },
        rendering.render_edit_project,
        project_id=project.project_id,
        rights_uses={asset.asset_id: rights_use},
        vertical=True,
        command_id="render-longer-audio-autoplan-test",
    )
    rendered_bytes = storage.read_clip_output(
        str(render_result["artifactId"]),
        str(render_result["sha256"]),
    )
    output_path = tmp_path / "longer-audio-rendered-output.mp4"
    output_path.write_bytes(rendered_bytes)
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(output_path),
        ],
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert probe.returncode == 0, probe.stderr.decode(errors="replace")
    media = json.loads(probe.stdout)
    video_streams = [
        stream for stream in media["streams"] if stream["codec_type"] == "video"
    ]
    audio_streams = [
        stream for stream in media["streams"] if stream["codec_type"] == "audio"
    ]
    assert len(video_streams) == len(audio_streams) == 1
    video_stream = video_streams[0]
    audio_stream = audio_streams[0]
    video_duration = float(
        video_stream.get("duration") or media["format"]["duration"]
    )
    audio_duration = float(
        audio_stream.get("duration") or media["format"]["duration"]
    )
    assert abs(video_duration - audio_duration) <= 0.1
    assert (video_stream["width"], video_stream["height"]) == (720, 1280)
    video_start_time = float(video_stream.get("start_time", 0))
    audio_start_time = float(audio_stream.get("start_time", 0))
    assert abs(audio_start_time - video_start_time) <= 1024 / 48_000
    source_audio = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-xerror",
            "-i",
            str(source_path),
            "-map",
            "0:a:0",
            "-f",
            "s16le",
            "-ac",
            "2",
            "-ar",
            "48000",
            "pipe:1",
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    output_audio = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-xerror",
            "-i",
            str(output_path),
            "-map",
            "0:a:0",
            "-f",
            "s16le",
            "-ac",
            "2",
            "-ar",
            "48000",
            "pipe:1",
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert source_audio.returncode == output_audio.returncode == 0
    source_samples = array("h", source_audio.stdout)[::2]
    output_samples = array("h", output_audio.stdout)[::2]
    aac_encoder_delay_samples = 1024
    aligned_samples = min(
        len(source_samples),
        len(output_samples) - aac_encoder_delay_samples,
        240_000,
    )
    assert statistics.correlation(
        source_samples[:aligned_samples],
        output_samples[
            aac_encoder_delay_samples : aac_encoder_delay_samples
            + aligned_samples
        ],
    ) > 0.9


def test_structured_plan_revisions_are_append_only_and_survive_reopen(
    tmp_path,
    monkeypatch,
):
    h, asset, _, _ = _harness(tmp_path, monkeypatch, media_bytes=_wav_bytes())
    project = ContentEditProject(
        project_id="structured-plan-persistence-test",
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script="Synthetic private edit.",
    )
    h.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        h.service.create_edit_project,
        project=project,
        command_id="create-structured-plan-persistence-test",
    )
    first_plan = _structured_plan(project.project_id, asset)
    with h.store._transaction() as connection:
        first_projection = h.store.save_edit_plan_in_transaction(
            connection,
            "alice",
            first_plan,
        )
    assert first_projection == h.store.get_edit_segments(project.project_id, "alice")
    assert h.store.get_latest_edit_plan(project.project_id, "alice") == first_plan

    second_plan = _structured_plan_v2(
        project.project_id,
        asset,
        revision=2,
    )
    persisted_motion = ContentEditPlanMotion(
        direction="pan_right",
        intensity=1.2,
        duration_seconds=1.0,
        easing="ease_in_out",
    )
    persisted_caption = ContentEditPlanCaption(
        transcript_id="fixture-transcript",
        transcript_segment_ids=("fixture-segment",),
        start_seconds=0.5,
        end_seconds=1.0,
        text="Known phrase",
        kind="phrase",
        presentation="high_retention_viral",
        emphasis="strong",
        emphasis_text="phrase",
    )
    persisted_transition = ContentEditPlanTransition(
        kind="cross_dissolve",
        duration_seconds=0.25,
        from_timeline_order=1,
        to_timeline_order=2,
        decision_reason="Persisted style decision at an observed scene boundary.",
        evidence=("observed_scene_boundary", "editing_style"),
    )
    persisted_graphic = ContentEditPlanGraphic(
        graphic_type="educational_overlay",
        segment_order=1,
        start_seconds=0.5,
        end_seconds=1.5,
        x=0.08,
        y=0.1,
        width=0.84,
        height=0.16,
        visual_hierarchy="primary",
        typography_profile="educational_storytelling",
        entry_animation="slide_up",
        exit_animation="fade",
        entry_duration_seconds=0.25,
        exit_duration_seconds=0.2,
        easing="ease_out",
        font_size=32,
        content_text="Known phrase",
        evidence_transcript_id="fixture-transcript",
        evidence_id="fixture-segment",
        evidence_asset_id=asset.asset_id,
        evidence_text="Known phrase is spoken in this segment.",
        editorial_rationale="Persisted from transcript evidence for emphasis.",
    )
    second_plan_data = second_plan.model_dump(mode="json")
    second_plan_data["schema_version"] = "cre-creative-plan-v3"
    second_plan_data["segments"][0].update(
        {
            "motion": persisted_motion.model_dump(mode="json"),
            "captions": [persisted_caption.model_dump(mode="json")],
            "graphics": [persisted_graphic.model_dump(mode="json")],
        }
    )
    second_plan_data["segments"][1].update(
        {
            "selection_method": "distinct_detected_scene_interval",
            "selected_scene_cut_seconds": 4.0,
            "transition_before": "cross_dissolve",
            "transition_duration_seconds": 0.25,
            "transition": persisted_transition.model_dump(mode="json"),
        }
    )
    second_plan = ContentCreativeEditPlanV3.model_validate(second_plan_data)
    with h.store._transaction() as connection:
        h.store.save_edit_plan_in_transaction(connection, "alice", second_plan)
    assert h.store.get_latest_edit_plan(project.project_id, "alice") == second_plan
    with pytest.raises(ValueError, match="new plan revision"):
        h.store.replace_edit_segments(
            project.project_id,
            "alice",
            (
                ContentEditSegment(
                    source_asset_id=asset.asset_id,
                    source_in_seconds=0.1,
                    source_out_seconds=2.0,
                    timeline_order=1,
                ),
                ContentEditSegment(
                    source_asset_id=asset.asset_id,
                    source_in_seconds=4,
                    source_out_seconds=5,
                    timeline_order=2,
                ),
            ),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        h.store._connection.execute(
            "UPDATE content_edit_plan_revisions SET plan_json = '{}' "
            "WHERE project_id = ? AND revision = 1",
            (project.project_id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        h.store._connection.execute(
            "DELETE FROM content_edit_plan_revisions "
            "WHERE project_id = ? AND revision = 1",
            (project.project_id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="sequential"):
        h.store._connection.execute(
            """
            INSERT INTO content_edit_plan_revisions (
                project_id, revision, plan_json, plan_sha256, created_at
            ) VALUES (?, 4, '{}', ?, ?)
            """,
            (project.project_id, "0" * 64, h.store._now()),
        )

    h.store._connection.close()
    reopened = ContentStore(tmp_path / "content.db")
    assert reopened.get_latest_edit_plan(project.project_id, "alice") == second_plan
    assert (
        reopened.get_latest_edit_plan(project.project_id, "alice")
        .segments[0]
        .motion
        == persisted_motion
    )
    assert (
        reopened.get_latest_edit_plan(project.project_id, "alice")
        .segments[0]
        .captions
        == (persisted_caption,)
    )
    assert (
        reopened.get_latest_edit_plan(project.project_id, "alice")
        .segments[0]
        .graphics
        == (persisted_graphic,)
    )
    assert (
        reopened.get_latest_edit_plan(project.project_id, "alice")
        .segments[1]
        .transition
        == persisted_transition
    )
    assert len(reopened.get_edit_segments(project.project_id, "alice")) == 2
    persisted_revision = reopened._connection.execute(
        """
        SELECT plan_sha256 FROM content_edit_plan_revisions
        WHERE project_id = ? AND revision = ?
        """,
        (project.project_id, second_plan.revision),
    ).fetchone()
    assert persisted_revision["plan_sha256"] == hashlib.sha256(
        reopened._canonical(second_plan).encode("utf-8")
    ).hexdigest()
    reopened._connection.close()


def test_structured_motion_directive_reaches_renderer(tmp_path, monkeypatch):
    h, asset, _, _ = _harness(tmp_path, monkeypatch, media_bytes=_wav_bytes())
    plan = _structured_plan("motion-render-propagation-test", asset)
    motion = ContentEditPlanMotion(
        direction="slow_zoom_in",
        intensity=1.1,
        start_scale=1.0,
        end_scale=1.12,
        duration_seconds=1.0,
        easing="ease_in_out",
    )
    plan = plan.model_copy(
        update={
            "segments": (
                plan.segments[0].model_copy(update={"motion": motion}),
                *plan.segments[1:],
            )
        }
    )
    managed = tmp_path / "managed"
    managed.mkdir()
    renderer = FFmpegLocalClipRenderer(managed_staging_directory=managed)
    captured = {}
    expected_result = object()

    def capture(source_paths, *, segments, staged_output, vertical, **kwargs):
        captured["segments"] = segments
        return {}, expected_result

    monkeypatch.setattr(renderer, "render_multi_source_timeline", capture)
    _, output_info = renderer.render_creative_edit_plan(
        {asset.asset_id: tmp_path / "source.mp4"},
        plan=plan,
        staged_output=tmp_path / "output.mp4",
        vertical=True,
    )

    assert output_info is expected_result
    assert captured["segments"][0].motion["direction"] == "slow_zoom_in"
    assert captured["segments"][0].motion["end_scale"] == 1.12


def test_structured_plan_write_rolls_back_projection_and_revision_together(
    tmp_path,
    monkeypatch,
):
    h, asset, _, _ = _harness(tmp_path, monkeypatch, media_bytes=_wav_bytes())
    project = ContentEditProject(
        project_id="structured-plan-rollback-test",
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script="Synthetic private edit.",
    )
    h.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        h.service.create_edit_project,
        project=project,
        command_id="create-structured-plan-rollback-test",
    )

    with pytest.raises(RuntimeError, match="test rollback"):
        with h.store._transaction() as connection:
            h.store.save_edit_plan_in_transaction(
                connection,
                "alice",
                _structured_plan(project.project_id, asset),
            )
            raise RuntimeError("test rollback")

    assert h.store.get_edit_segments(project.project_id, "alice") == ()
    assert h.store.get_latest_edit_plan(project.project_id, "alice") is None
    assert not h.store._connection.in_transaction


@pytest.mark.parametrize(
    ("final_interval", "selection_method"),
    (
        ((0.0, 2.5), "repeated_detected_scene_interval"),
        ((2.4, 4.9), "evenly_spaced_sample"),
    ),
    ids=("repeated-interval", "partial-overlap"),
)
def test_structured_plan_persistence_rejects_repeated_or_overlapping_source_intervals(
    tmp_path,
    monkeypatch,
    final_interval,
    selection_method,
):
    h, asset, _, _ = _harness(tmp_path, monkeypatch, media_bytes=_wav_bytes())
    project = ContentEditProject(
        project_id="repeated-source-interval-persistence-test",
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script="Reject repeated or overlapping source intervals.",
    )
    h.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        h.service.create_edit_project,
        project=project,
        command_id="create-repeated-source-interval-persistence-test",
    )
    plan = _original_overlap_plan(
        project.project_id,
        asset,
        final_interval=final_interval,
        final_selection_method=selection_method,
    )

    with pytest.raises(ValueError, match="must not overlap"):
        with h.store._transaction() as connection:
            h.store.save_edit_plan_in_transaction(
                connection,
                "alice",
                plan,
            )

    assert h.store.get_latest_edit_plan(project.project_id, "alice") is None
    assert h.store.get_edit_segments(project.project_id, "alice") == ()
    h.close()


def test_structured_plan_persistence_rejects_overlapping_audio_source_intervals(
    tmp_path,
    monkeypatch,
):
    h, asset, _, _ = _harness(tmp_path, monkeypatch, media_bytes=_wav_bytes())
    project = ContentEditProject(
        project_id="overlapping-audio-source-persistence-test",
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script="Reject overlapping source audio intervals.",
    )
    h.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        h.service.create_edit_project,
        project=project,
        command_id="create-overlapping-audio-source-persistence-test",
    )
    plan = _original_overlap_plan(
        project.project_id,
        asset,
        final_interval=(6.433333, 8.0),
        final_audio_interval=(0.933333, 2.5),
        final_selection_method="evenly_spaced_sample",
    )

    with pytest.raises(ValueError, match="audio source intervals must not overlap"):
        with h.store._transaction() as connection:
            h.store.save_edit_plan_in_transaction(
                connection,
                "alice",
                plan,
            )

    assert h.store.get_latest_edit_plan(project.project_id, "alice") is None
    assert h.store.get_edit_segments(project.project_id, "alice") == ()
    h.close()


def test_multi_source_renderer_rejects_repeated_video_intervals_without_audio_repeats(
    tmp_path,
    monkeypatch,
):
    h, asset, _, _ = _harness(tmp_path, monkeypatch, media_bytes=_wav_bytes())
    plan = _original_overlap_plan(
        "repeated-video-interval-render-test",
        asset,
        final_audio_mode="silence",
    )
    staging_directory = tmp_path / "staging"
    staging_directory.mkdir()
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=staging_directory,
    )

    with pytest.raises(
        ValueError,
        match="video source intervals must not overlap",
    ):
        renderer.render_creative_edit_plan(
            {asset.asset_id: tmp_path / "missing-source.mp4"},
            plan=plan,
            staged_output=tmp_path / "unused-output.mp4",
        )
    h.close()


def test_multi_source_renderer_rejects_overlapping_audio_source_intervals(
        tmp_path,
        monkeypatch,
):
        h, asset, _, _ = _harness(tmp_path, monkeypatch, media_bytes=_wav_bytes())
        plan = _original_overlap_plan(
            "overlapping-audio-source-render-test",
            asset,
            final_interval=(6.433333, 8.0),
            final_audio_interval=(0.933333, 2.5),
            final_selection_method="evenly_spaced_sample",
        )
        staging_directory = tmp_path / "staging"
        staging_directory.mkdir()
        renderer = FFmpegLocalClipRenderer(
            managed_staging_directory=staging_directory,
        )

        with pytest.raises(
            ValueError,
            match="audio source intervals must not overlap",
        ):
            renderer.render_multi_source_timeline(
                {asset.asset_id: tmp_path / "missing-source.mp4"},
                segments=tuple(
                    SourceEditSegment(
                        source_in_seconds=segment.source_in_seconds,
                        source_out_seconds=segment.source_out_seconds,
                        source_asset_id=segment.source_asset_id,
                    )
                    for segment in plan.segments
                ),
                staged_output=tmp_path / "unused-output.mp4",
                audio_decisions=tuple(segment.audio for segment in plan.segments),
                audio_policy=plan.audio_policy,
            )
        h.close()


def test_original_persisted_repeat_plan_reloads_rejects_and_renders_new_revision(
    tmp_path,
    monkeypatch,
):
    source_bytes = _video_with_longer_audio_source_bytes(tmp_path)
    h, asset, _, storage = _harness(
        tmp_path,
        monkeypatch,
        media_bytes=source_bytes,
    )
    project = ContentEditProject(
        project_id="persisted-repeat-plan-recovery-test",
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script="Replan without repeating source footage.",
    )
    h.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        h.service.create_edit_project,
        project=project,
        command_id="create-persisted-repeat-plan-recovery-test",
    )
    original_plan = _original_overlap_plan(project.project_id, asset)
    with pytest.raises(ValueError, match="must not overlap"):
        with h.store._transaction() as connection:
            h.store.save_edit_plan_in_transaction(
                connection,
                "alice",
                original_plan,
            )

    projection = tuple(
        ContentEditSegment(
            source_asset_id=segment.source_asset_id,
            source_in_seconds=segment.source_in_seconds,
            source_out_seconds=segment.source_out_seconds,
            timeline_order=segment.timeline_order,
        )
        for segment in original_plan.segments
    )
    with h.store._transaction() as connection:
        h.store.replace_edit_segments_in_transaction(
            connection,
            project.project_id,
            "alice",
            projection,
            allow_structured_plan_revision=True,
        )
        canonical = h.store._canonical(original_plan)
        connection.execute(
            """
            INSERT INTO content_edit_plan_revisions (
                project_id, revision, plan_json, plan_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                original_plan.project_id,
                original_plan.revision,
                canonical,
                hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                h.store._now(),
            ),
        )
    h.store._connection.close()

    store = ContentStore(tmp_path / "content.db")
    assert store.get_latest_edit_plan(project.project_id, "alice") == original_plan
    staged_source = storage.stage_clip_source(asset.asset_id, source_bytes)
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory,
    )
    with pytest.raises(ValueError, match="repeats a source audio interval"):
        renderer.render_creative_edit_plan(
            {asset.asset_id: staged_source},
            plan=original_plan,
            staged_output=storage.create_clip_staging_path(
                "rejected-legacy-repeat-plan"
            ),
        )

    video_duration = renderer.inspect_video_source(staged_source).video_duration_seconds
    assert video_duration is not None
    cut = 3.933333

    def audio_decision(start, end):
        return ContentEditPlanAudio(
            mode="source_audio",
            source_asset_id=asset.asset_id,
            source_in_seconds=start,
            source_out_seconds=end,
            gain_db=0,
            timing_policy="trim_pad_to_video_segment",
        )

    def planned_segment(start, end, order, method, transition, scene_cut=None):
        segment = {
            "source_asset_id": asset.asset_id,
            "source_in_seconds": start,
            "source_out_seconds": end,
            "timeline_order": order,
            "selection_method": method,
            "transition_before": transition,
            "audio": audio_decision(start, end),
        }
        if scene_cut is not None:
            segment["selected_scene_cut_seconds"] = scene_cut
        return segment

    corrected_plan = ContentCreativeEditPlanV2(
        project_id=project.project_id,
        revision=2,
        creative_direction="Use distinct adjacent source intervals.",
        target_duration_seconds=6.433333,
        sources=(
            ContentEditPlanSource(
                source_asset_id=asset.asset_id,
                content_sha256=asset.content_sha256,
                duration_seconds=video_duration,
                scene_boundary_seconds=(cut,),
            ),
        ),
        segments=(
            planned_segment(
                0.0,
                2.5,
                1,
                "distinct_detected_scene_interval",
                "start",
            ),
            planned_segment(
                2.5,
                cut,
                2,
                "evenly_spaced_sample",
                "hard_cut",
            ),
            planned_segment(
                cut,
                6.433333,
                3,
                "distinct_detected_scene_interval",
                "hard_cut",
                scene_cut=cut,
            ),
        ),
        audio_policy="source_audio",
    )
    with store._transaction() as connection:
        store.save_edit_plan_in_transaction(
            connection,
            "alice",
            corrected_plan,
        )
    store._connection.close()

    reopened = ContentStore(tmp_path / "content.db")
    assert reopened.get_latest_edit_plan(project.project_id, "alice") == corrected_plan
    output = storage.create_clip_staging_path("corrected-persisted-plan")
    _, output_info = renderer.render_creative_edit_plan(
        {asset.asset_id: staged_source},
        plan=reopened.get_latest_edit_plan(project.project_id, "alice"),
        staged_output=output,
    )
    decoded = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-xerror",
            "-i",
            str(output),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert decoded.returncode == 0, decoded.stderr.decode(errors="replace")
    assert output_info.duration_seconds == pytest.approx(6.433333, abs=0.1)
    assert renderer.inspect_render_quality(output)["status"] == "PASSED"
    reopened._connection.close()


def test_structured_plan_persistence_rejects_wrong_owner_and_source_hash(
        tmp_path,
        monkeypatch,
):
    h, asset, _, _ = _harness(tmp_path, monkeypatch, media_bytes=_wav_bytes())
    project = ContentEditProject(
        project_id="structured-plan-identity-test",
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script="Synthetic private edit.",
    )
    h.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        h.service.create_edit_project,
        project=project,
        command_id="create-structured-plan-identity-test",
    )
    plan = _structured_plan(project.project_id, asset)
    with pytest.raises(PermissionError, match="owned CRE"):
        with h.store._transaction() as connection:
            h.store.save_edit_plan_in_transaction(connection, "mallory", plan)
    mismatched_source_plan = plan.model_copy(
        update={
            "sources": (
                ContentEditPlanSource(
                    source_asset_id=asset.asset_id,
                    content_sha256="0" * 64,
                    duration_seconds=8,
                    scene_boundary_seconds=(2.0, 4.0, 6.0),
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="source hash"):
        with h.store._transaction() as connection:
            h.store.save_edit_plan_in_transaction(
                connection,
                "alice",
                mismatched_source_plan,
            )
    assert h.store.get_latest_edit_plan(project.project_id, "alice") is None
    assert h.store.get_edit_segments(project.project_id, "alice") == ()


def test_structured_plan_rejects_corrupted_timeline_projection(
    tmp_path,
    monkeypatch,
):
    h, asset, _, _ = _harness(tmp_path, monkeypatch, media_bytes=_wav_bytes())
    project = ContentEditProject(
        project_id="structured-plan-projection-tamper-test",
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script="Synthetic projection integrity test.",
    )
    h.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        h.service.create_edit_project,
        command_id="create-structured-plan-projection-tamper-test",
        project=project,
    )
    plan = _structured_plan(project.project_id, asset)
    with h.store._transaction() as connection:
        h.store.save_edit_plan_in_transaction(connection, "alice", plan)
    h.store._connection.execute(
        """
        UPDATE content_edit_segments
        SET source_out_seconds = source_out_seconds - 0.25
        WHERE project_id = ? AND timeline_order = 1
        """,
        (project.project_id,),
    )

    with pytest.raises(ValueError, match="timeline does not match"):
        h.store.get_latest_edit_plan(project.project_id, "alice")
    h.close()


def test_invalid_structured_plan_does_not_fall_back_to_legacy_render(
    tmp_path,
    monkeypatch,
):
    h, asset, _, _ = _harness(tmp_path, monkeypatch, media_bytes=_wav_bytes())
    rendering = ContentClipRenderingService(content_rights=h.service)
    project = ContentEditProject(
        project_id="structured-plan-no-legacy-fallback-test",
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script="Synthetic invalid plan test.",
    )
    h.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        h.service.create_edit_project,
        command_id="create-structured-plan-no-legacy-fallback-test",
        project=project,
    )
    with h.store._transaction() as connection:
        h.store.save_edit_plan_in_transaction(
            connection,
            "alice",
            _structured_plan(project.project_id, asset),
        )
    h.store._connection.execute(
        "DROP TRIGGER content_edit_plan_revisions_no_update"
    )
    h.store._connection.execute(
        """
        UPDATE content_edit_plan_revisions
        SET plan_sha256 = ?
        WHERE project_id = ? AND revision = 1
        """,
        ("0" * 64, project.project_id),
    )

    def legacy_render_must_not_run(*args, **kwargs):
        pytest.fail("Invalid structured plan fell back to the legacy renderer.")

    monkeypatch.setattr(
        rendering.renderer,
        "render_multi_source_timeline",
        legacy_render_must_not_run,
    )
    monkeypatch.setattr(
        rendering.renderer,
        "render_creative_edit_plan",
        legacy_render_must_not_run,
    )
    with pytest.raises(ValueError, match="plan digest is invalid"):
        h.invoke(
            "content_render_private_edit_project",
            {"project_id": project.project_id, "vertical": False},
            rendering.render_private_edit_project,
            project_id=project.project_id,
            vertical=False,
            command_id="render-structured-plan-no-legacy-fallback-test",
        )
    assert h.store._connection.execute(
        """
        SELECT COUNT(*) FROM content_commands
        WHERE principal = ? AND action = ? AND command_id = ?
        """,
        (
            "alice",
            "content_render_private_edit_project",
            "render-structured-plan-no-legacy-fallback-test",
        ),
    ).fetchone()[0] == 0
    assert not h.store._connection.in_transaction
    h.close()


def test_autoplan_persists_transcript_captions_and_dispatches_v3_to_renderer(
    tmp_path,
    monkeypatch,
):
    h, asset, _, _ = _harness(
        tmp_path,
        monkeypatch,
        media_bytes=_wav_bytes(),
        rights_actions=frozenset({RightsAction.ANALYZE}),
    )
    _register_analysis_scope(h, asset)
    intelligence = ContentIntelligenceService(
        content_rights=h.service,
        transcription_engine=_FixtureTranscriptionEngine(),
    )
    use = lifecycle._rights_use().model_copy(
        update={
            "asset_id": asset.asset_id,
            "expected_asset_sha256": asset.content_sha256,
            "actions": frozenset({RightsAction.ANALYZE}),
        }
    )
    transcription_config = TranscriptionConfig(max_duration_seconds=30)
    job = h.invoke(
        "content_create_transcription_job",
        {
            "use": use.model_dump(mode="json"),
            "target_context": ContentTargetContext.YOUTUBE_SHORTS.value,
            "config": transcription_config.model_dump(mode="json"),
        },
        intelligence.create_transcription_job,
        use=use,
        target_context=ContentTargetContext.YOUTUBE_SHORTS,
        config=transcription_config,
    )
    transcript = h.invoke(
        "content_run_transcription_job",
        {"job_id": job.job_id},
        intelligence.run_transcription_job,
        job_id=job.job_id,
    )
    assert transcript.segments[0].words

    rendering = ContentClipRenderingService(content_rights=h.service)
    monkeypatch.setattr(
        rendering.renderer,
        "inspect_video_source",
        lambda source: MediaInfo(duration_seconds=10, has_video=True, has_audio=False),
    )
    monkeypatch.setattr(rendering.renderer, "detect_scene_changes", lambda source: ())
    project = ContentEditProject(
        project_id="transcript-caption-autoplan-test",
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script="Synthetic caption integration fixture.",
    )
    h.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        h.service.create_edit_project,
        project=project,
        command_id="create-transcript-caption-autoplan-test",
    )
    instruction = "Create a high-retention viral vertical short."
    plan_result = h.invoke(
        "content_autoplan_edit_project",
        {"project_id": project.project_id, "instruction_length": len(instruction)},
        rendering.autoplan_edit_project,
        project_id=project.project_id,
        instruction=instruction,
        command_id="transcript-caption-autoplan-command",
    )
    persisted_plan = h.store.get_latest_edit_plan(project.project_id, "alice")
    assert isinstance(persisted_plan, ContentCreativeEditPlanV3)
    assert persisted_plan.render_style_id == "high_retention_viral"
    persisted_captions = tuple(
        caption
        for segment in persisted_plan.segments
        for caption in segment.captions
    )
    assert persisted_captions
    assert all(caption.transcript_id == transcript.transcript_id for caption in persisted_captions)
    assert all(caption.kind == "word" for caption in persisted_captions)
    assert plan_result["segmentCount"] == len(persisted_plan.segments)

    captured = {}

    def capture(source_paths, *, segments, staged_output, vertical, **kwargs):
        captured["segments"] = segments
        captured["style_id"] = kwargs["style_id"]
        return {}, object()

    monkeypatch.setattr(rendering.renderer, "render_multi_source_timeline", capture)
    rendering.renderer.render_creative_edit_plan(
        {asset.asset_id: tmp_path / "source.mp4"},
        plan=persisted_plan,
        staged_output=tmp_path / "output.mp4",
        vertical=True,
    )
    assert captured["style_id"] == "high_retention_viral"
    assert any(segment.captions for segment in captured["segments"])
    assert all(
        caption.presentation == "high_retention_viral"
        for segment in captured["segments"]
        for caption in segment.captions
    )
    h.close()


def test_private_project_render_burns_persisted_fixture_captions(
    tmp_path,
    monkeypatch,
):
    fixture = tmp_path / "private-caption-render-source.mp4"
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
            "testsrc=size=320x180:rate=15:duration=6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000:duration=6",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "32k",
            "-shortest",
            str(fixture),
        ],
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert generated.returncode == 0, generated.stderr.decode(errors="replace")
    source_bytes = fixture.read_bytes()
    renderer_storage = LocalTemporaryContentStore(tmp_path / "managed-content")
    media_renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=renderer_storage.clip_staging_directory
    )
    h, asset, _, storage, transcript, _, rendering = _authorized_pipeline(
        tmp_path,
        monkeypatch,
        media_bytes=source_bytes,
        media_renderer=media_renderer,
    )
    _create_restricted_test_permit(h, asset)
    project = ContentEditProject(
        project_id="private-fixture-caption-render-test",
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script="Deterministic transcript fixture render.",
    )
    h.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        h.service.create_edit_project,
        project=project,
        command_id="create-private-fixture-caption-render-test",
    )
    instruction = "Make a cinematic documentary vertical short."
    h.invoke(
        "content_autoplan_edit_project",
        {"project_id": project.project_id, "instruction_length": len(instruction)},
        rendering.autoplan_edit_project,
        project_id=project.project_id,
        instruction=instruction,
        command_id="private-fixture-caption-autoplan",
    )
    plan = h.store.get_latest_edit_plan(project.project_id, "alice")
    assert isinstance(plan, ContentCreativeEditPlanV3)
    assert any(segment.captions for segment in plan.segments), [
        (
            segment.source_in_seconds,
            segment.source_out_seconds,
            segment.text_decision,
        )
        for segment in plan.segments
    ]
    assert all(
        caption.transcript_id == transcript.transcript_id
        for segment in plan.segments
        for caption in segment.captions
    )

    def caption_variant(style_id):
        return tuple(
            segment.model_copy(
                update={
                    "captions": _caption_directives_for_interval(
                        transcript,
                        source_in_seconds=segment.source_in_seconds,
                        source_out_seconds=segment.source_out_seconds,
                        style_id=style_id,
                    )
                }
            )
            for segment in plan.segments
        )

    variants = (
        (
            "baseline",
            plan.model_copy(
                update={
                    "revision": 2,
                    "render_style_id": None,
                    "segments": tuple(
                        segment.model_copy(update={"captions": ()})
                        for segment in plan.segments
                    ),
                }
            ),
        ),
        (
            "phrase-cinematic",
            plan.model_copy(
                update={
                    "revision": 3,
                    "render_style_id": "cinematic_documentary",
                    "segments": caption_variant("cinematic_documentary"),
                }
            ),
        ),
        (
            "word-viral",
            plan.model_copy(
                update={
                    "revision": 4,
                    "render_style_id": "high_retention_viral",
                    "segments": caption_variant("high_retention_viral"),
                }
            ),
        ),
        (
            "phrase-futuristic",
            plan.model_copy(
                update={
                    "revision": 5,
                    "render_style_id": "futuristic_science",
                    "segments": caption_variant("futuristic_science"),
                }
            ),
        ),
    )
    expected_intervals = tuple(
        (segment.source_in_seconds, segment.source_out_seconds)
        for segment in plan.segments
    )
    assert all(
        tuple(
            (segment.source_in_seconds, segment.source_out_seconds)
            for segment in variant.segments
        )
        == expected_intervals
        for _, variant in variants
    )
    outputs = {}
    for name, variant in variants:
        with h.store._transaction() as connection:
            h.store.save_edit_plan_in_transaction(connection, "alice", variant)
        result = h.invoke(
            "content_render_private_edit_project",
            {"project_id": project.project_id, "vertical": True},
            rendering.render_private_edit_project,
            project_id=project.project_id,
            vertical=True,
            command_id=f"private-fixture-caption-render-{name}",
        )
        rendered_bytes = storage.read_clip_output(
            result["artifactId"],
            result["sha256"],
        )
        output_path = tmp_path / f"private-fixture-caption-{name}.mp4"
        output_path.write_bytes(rendered_bytes)
        probe_path = storage.stage_clip_source(
            f"caption-{name}-probe",
            rendered_bytes,
        )
        try:
            output_info = media_renderer.inspect(probe_path)
            media_renderer._decode(probe_path)
        finally:
            storage.delete_clip_staging(probe_path)
        assert result["privateReviewOnly"] is True
        assert output_info.video_width == 720
        assert output_info.video_height == 1280
        assert output_info.video_codec == "h264"
        assert output_info.audio_codec == "aac"
        assert output_info.video_fps == pytest.approx(30, abs=0.1)
        assert output_info.video_duration_seconds == pytest.approx(
            output_info.audio_duration_seconds,
            abs=0.25,
        )
        assert hashlib.sha256(rendered_bytes).hexdigest() == result["sha256"]
        outputs[name] = output_path

    def rendered_frame(path, name):
        frame_path = tmp_path / f"{name}.png"
        frame_result = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-ss",
                "0.7",
                "-frames:v",
                "1",
                "-y",
                str(frame_path),
            ],
            capture_output=True,
            check=False,
            timeout=20,
        )
        assert frame_result.returncode == 0, frame_result.stderr.decode(
            errors="replace"
        )
        with Image.open(frame_path) as image:
            return image.convert("RGB").copy()

    baseline_frame = rendered_frame(outputs["baseline"], "caption-baseline-frame")
    cinematic_frame = rendered_frame(
        outputs["phrase-cinematic"],
        "caption-cinematic-frame",
    )
    viral_frame = rendered_frame(outputs["word-viral"], "caption-viral-frame")
    futuristic_frame = rendered_frame(
        outputs["phrase-futuristic"],
        "caption-futuristic-frame",
    )
    caption_crop = lambda image: image.crop((0, 850, 720, 1280))
    assert ImageChops.difference(
        caption_crop(baseline_frame),
        caption_crop(cinematic_frame),
    ).getbbox()
    assert ImageChops.difference(
        caption_crop(cinematic_frame),
        caption_crop(futuristic_frame),
    ).getbbox()
    assert ImageChops.difference(
        caption_crop(baseline_frame),
        caption_crop(viral_frame),
    ).getbbox()
    h.close()


def test_scene_diversity_plan_persists_with_legacy_digest_shape(
    tmp_path,
    monkeypatch,
):
    h, asset, _, _ = _harness(tmp_path, monkeypatch, media_bytes=_wav_bytes())
    project = ContentEditProject(
        project_id="scene-diversity-persistence-test",
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script="Synthetic scene interval persistence test.",
    )
    h.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        h.service.create_edit_project,
        command_id="create-scene-diversity-persistence-test",
        project=project,
    )
    scene_starts = (0.0, 2.0, 4.0, 6.0)
    methods = (
        "distinct_detected_scene_interval",
        "distinct_detected_scene_interval",
        "distinct_detected_scene_interval",
        "distinct_detected_scene_interval",
    )
    plan = ContentCreativeEditPlan(
        project_id=project.project_id,
        revision=1,
        creative_direction="Use distinct synthetic scene intervals.",
        sources=(
            ContentEditPlanSource(
                source_asset_id=asset.asset_id,
                content_sha256=asset.content_sha256,
                duration_seconds=8,
                scene_boundary_seconds=(2.0, 4.0, 6.0),
            ),
        ),
        segments=tuple(
            ContentEditPlanSegment(
                source_asset_id=asset.asset_id,
                source_in_seconds=scene_start,
                source_out_seconds=scene_start + 1.5,
                timeline_order=order,
                selection_method=method,
                selected_scene_cut_seconds=(
                    scene_start if scene_start > 0 else None
                ),
                transition_before="start" if order == 1 else "hard_cut",
            )
            for order, (scene_start, method) in enumerate(
                zip(scene_starts, methods),
                start=1,
            )
        ),
    )
    with h.store._transaction() as connection:
        projection = h.store.save_edit_plan_in_transaction(
            connection,
            "alice",
            plan,
        )
    stored_digest = hashlib.sha256(
        h.store._canonical(plan).encode("utf-8")
    ).hexdigest()
    assert tuple(
        (segment.source_in_seconds, segment.source_out_seconds)
        for segment in projection
    ) == tuple(
        (segment.source_in_seconds, segment.source_out_seconds)
        for segment in plan.segments
    )
    assert h.store.get_latest_edit_plan(project.project_id, "alice") == plan
    database = h.store.database_identity
    h.close()

    reopened = ContentStore(database)
    persisted = reopened.get_latest_edit_plan(project.project_id, "alice")
    assert persisted == plan
    row = reopened._connection.execute(
        "SELECT plan_sha256 FROM content_edit_plan_revisions "
        "WHERE project_id = ? AND revision = 1",
        (project.project_id,),
    ).fetchone()
    assert row["plan_sha256"] == stored_digest
    reopened._connection.close()


@pytest.mark.parametrize("existing_schema_version", [1, 2, 3, 4, 5, 6, 7, 9])
def test_plan_table_initialization_preserves_existing_project_records(
    tmp_path,
    monkeypatch,
    existing_schema_version,
):
    h, asset, _, _ = _harness(tmp_path, monkeypatch, media_bytes=_wav_bytes())
    project = ContentEditProject(
        project_id=f"plan-table-migration-v{existing_schema_version}",
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script="Synthetic migration compatibility test.",
    )
    h.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        h.service.create_edit_project,
        command_id=f"create-plan-table-migration-v{existing_schema_version}",
        project=project,
    )
    segments = (
        ContentEditSegment(
            source_asset_id=asset.asset_id,
            source_in_seconds=0,
            source_out_seconds=2,
            timeline_order=1,
        ),
        ContentEditSegment(
            source_asset_id=asset.asset_id,
            source_in_seconds=4,
            source_out_seconds=6,
            timeline_order=2,
        ),
    )
    h.store.replace_edit_segments(project.project_id, "alice", segments)
    assert h.store._connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name = 'content_edit_plan_revisions'"
    ).fetchone() is not None
    project_row_before = tuple(
        h.store._connection.execute(
            "SELECT project_id, owner_id, source_asset_ids_json, script, created_at "
            "FROM content_edit_projects WHERE project_id = ?",
            (project.project_id,),
        ).fetchone()
    )
    source_rows_before = tuple(
        tuple(row)
        for row in h.store._connection.execute(
            "SELECT project_id, source_asset_id, source_order "
            "FROM content_edit_project_sources WHERE project_id = ? "
            "ORDER BY source_order",
            (project.project_id,),
        ).fetchall()
    )
    segments_before = tuple(
        tuple(row)
        for row in h.store._connection.execute(
            "SELECT project_id, timeline_order, source_asset_id, "
            "source_in_seconds, source_out_seconds FROM content_edit_segments "
            "WHERE project_id = ? ORDER BY timeline_order",
            (project.project_id,),
        ).fetchall()
    )
    database = h.store.database_identity
    set_legacy_schema_version(
        h.store,
        9 if existing_schema_version == 9 else 7,
    )
    if existing_schema_version == 9:
        assert h.store.migrate_publishing_attempt_schema() == 9
    h.store._connection.execute("DROP TABLE content_edit_plan_revisions")
    h.store._connection.execute(
        "UPDATE content_schema_meta SET schema_version = ? WHERE singleton = 1",
        (existing_schema_version,),
    )
    h.close()

    for _ in range(2):
        reopened = ContentStore(database)
        assert reopened._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'content_edit_plan_revisions'"
        ).fetchone() is not None
        assert tuple(
            reopened._connection.execute(
                "SELECT project_id, owner_id, source_asset_ids_json, script, created_at "
                "FROM content_edit_projects WHERE project_id = ?",
                (project.project_id,),
            ).fetchone()
        ) == project_row_before
        assert tuple(
            tuple(row)
            for row in reopened._connection.execute(
                "SELECT project_id, source_asset_id, source_order "
                "FROM content_edit_project_sources WHERE project_id = ? "
                "ORDER BY source_order",
                (project.project_id,),
            ).fetchall()
        ) == source_rows_before
        assert tuple(
            tuple(row)
            for row in reopened._connection.execute(
                "SELECT project_id, timeline_order, source_asset_id, "
                "source_in_seconds, source_out_seconds FROM content_edit_segments "
                "WHERE project_id = ? ORDER BY timeline_order",
                (project.project_id,),
            ).fetchall()
        ) == segments_before
        assert reopened.get_edit_project(project.project_id, "alice") == project
        assert reopened.get_edit_segments(project.project_id, "alice") == segments
        reopened.close()


def test_private_edit_render_persists_authorized_result_and_audit(
    tmp_path,
    monkeypatch,
):
    h, asset, _, storage = _harness(
        tmp_path,
        monkeypatch,
        media_bytes=_wav_bytes(),
        rights_actions=frozenset({RightsAction.ANALYZE}),
    )
    _create_restricted_test_permit(h, asset)
    rendering = ContentClipRenderingService(content_rights=h.service)
    project = ContentEditProject(
        project_id="private-render-transaction-test",
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script="Synthetic private edit.",
    )
    h.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        h.service.create_edit_project,
        project=project,
        command_id="create-private-render-transaction-test",
    )
    segments = (
        ContentEditSegment(
            source_asset_id=asset.asset_id,
            source_in_seconds=0,
            source_out_seconds=2,
            timeline_order=1,
        ),
        ContentEditSegment(
            source_asset_id=asset.asset_id,
            source_in_seconds=2,
            source_out_seconds=4,
            timeline_order=2,
        ),
    )
    h.store.replace_edit_segments(project.project_id, "alice", segments)
    output_info = MediaInfo(
        duration_seconds=4,
        has_video=True,
        has_audio=True,
        video_width=1280,
        video_height=720,
    )

    def render_stub(source_paths, *, segments, staged_output, vertical=False):
        assert set(source_paths) == {asset.asset_id}
        assert len(segments) == 2
        assert vertical is False
        staged_output.write_bytes(b"synthetic-private-render-output")
        return {asset.asset_id: output_info}, output_info

    monkeypatch.setattr(rendering.renderer, "render_multi_source_timeline", render_stub)
    command_id = "private-render-transaction-test-command"
    payload = {"project_id": project.project_id, "vertical": False}

    result = h.invoke(
        "content_render_private_edit_project",
        payload,
        rendering.render_private_edit_project,
        project_id=project.project_id,
        vertical=False,
        command_id=command_id,
    )

    assert result["projectId"] == project.project_id
    assert result["renderId"]
    assert result["artifactId"].startswith("clip-")
    assert result["sizeBytes"] == len(b"synthetic-private-render-output")
    assert result["privateReviewOnly"] is True
    stored_output = storage.read_clip_output(
        result["artifactId"],
        result["sha256"],
    )
    assert stored_output == b"synthetic-private-render-output"
    command = h.store._connection.execute(
        "SELECT result_id FROM content_commands "
        "WHERE principal = ? AND action = ? AND command_id = ?",
        ("alice", "content_render_private_edit_project", command_id),
    ).fetchone()
    assert command["result_id"] == project.project_id
    audit = h.store._connection.execute(
        "SELECT decision, details_json FROM content_audit_events "
        "WHERE principal = ? AND action = ? AND subject_id = ?",
        ("alice", "content_render_private_edit_project", project.project_id),
    ).fetchone()
    assert audit["decision"] == "rendered"
    audit_details = json.loads(audit["details_json"])
    expected_audit_details = {
        "render_id": result["renderId"],
        "artifact_id": result["artifactId"],
        "sha256": result["sha256"],
        "size_bytes": result["sizeBytes"],
        "duration_seconds": result["durationSeconds"],
        "width": result["width"],
        "height": result["height"],
        "mime_type": "video/mp4",
    }
    assert {
        key: audit_details[key] for key in expected_audit_details
    } == expected_audit_details
    assert audit_details["plan_mode"] == result["planMode"] == "legacy"
    assert audit_details["plan_revision"] is None
    assert audit_details["plan_sha256"] is None
    assert audit_details["orientation"] == result["orientation"] == "landscape"
    assert audit_details["renderer_settings"] == result["rendererSettings"]
    assert not h.store._connection.in_transaction


def test_structured_private_render_records_plan_provenance(
    tmp_path,
    monkeypatch,
):
    h, asset, _, storage = _harness(tmp_path, monkeypatch, media_bytes=_wav_bytes())
    _create_restricted_test_permit(h, asset)
    rendering = ContentClipRenderingService(content_rights=h.service)
    project = ContentEditProject(
        project_id="structured-private-render-test",
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script="Synthetic private edit.",
    )
    h.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        h.service.create_edit_project,
        project=project,
        command_id="create-structured-private-render-test",
    )
    plan = _structured_plan(project.project_id, asset)
    with h.store._transaction() as connection:
        h.store.save_edit_plan_in_transaction(connection, "alice", plan)
    plan_digest = hashlib.sha256(h.store._canonical(plan).encode()).hexdigest()
    output_info = MediaInfo(
        duration_seconds=4,
        has_video=True,
        has_audio=True,
        video_width=1280,
        video_height=720,
    )

    def render_stub(source_paths, *, plan, staged_output, vertical=False):
        assert set(source_paths) == {asset.asset_id}
        assert plan.project_id == project.project_id
        assert vertical is False
        staged_output.write_bytes(b"structured-private-render")
        return {}, output_info

    monkeypatch.setattr(rendering.renderer, "render_creative_edit_plan", render_stub)
    command_id = "structured-private-render-command"
    result = h.invoke(
        "content_render_private_edit_project",
        {"project_id": project.project_id, "vertical": False},
        rendering.render_private_edit_project,
        project_id=project.project_id,
        vertical=False,
        command_id=command_id,
    )

    assert result["planMode"] == "structured"
    assert result["planRevision"] == 1
    assert result["planSha256"] == plan_digest
    assert result["orientation"] == "landscape"
    assert storage.read_clip_output(result["artifactId"], result["sha256"]) == (
        b"structured-private-render"
    )
    audit = h.store._connection.execute(
        "SELECT details_json FROM content_audit_events "
        "WHERE principal = ? AND action = ? AND subject_id = ?",
        ("alice", "content_render_private_edit_project", project.project_id),
    ).fetchone()
    details = json.loads(audit["details_json"])
    assert details["plan_revision"] == 1
    assert details["plan_sha256"] == plan_digest
    assert details["artifact_id"] == result["artifactId"]
    assert details["sha256"] == result["sha256"]
    assert details["renderer_settings"] == result["rendererSettings"]
    assert not h.store._connection.in_transaction


def test_real_structured_render_provenance_survives_database_reopen(
    tmp_path,
    monkeypatch,
):
    source_bytes = _source_video_bytes(tmp_path)
    h, asset, _, storage = _harness(
        tmp_path,
        monkeypatch,
        media_bytes=source_bytes,
        rights_actions=frozenset({RightsAction.ANALYZE}),
    )
    _register_analysis_scope(h, asset)
    _create_restricted_test_permit(h, asset)
    database = h.store.database_identity
    rendering = ContentClipRenderingService(content_rights=h.service)
    project = ContentEditProject(
        project_id="real-structured-provenance-test",
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script="Synthetic media provenance test.",
    )
    h.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        h.service.create_edit_project,
        project=project,
        command_id="create-real-structured-provenance-test",
    )
    instruction = "Create a synthetic private review edit."
    h.invoke(
        "content_autoplan_edit_project",
        {"project_id": project.project_id, "instruction_length": len(instruction)},
        rendering.autoplan_edit_project,
        project_id=project.project_id,
        instruction=instruction,
        command_id="plan-real-structured-provenance-test",
    )
    plan = h.store.get_latest_edit_plan(project.project_id, "alice")
    assert plan is not None
    plan_digest = hashlib.sha256(h.store._canonical(plan).encode("utf-8")).hexdigest()

    result = h.invoke(
        "content_render_private_edit_project",
        {"project_id": project.project_id, "vertical": True},
        rendering.render_private_edit_project,
        project_id=project.project_id,
        vertical=True,
        command_id="render-real-structured-provenance-test",
    )
    output_bytes = storage.read_clip_output(result["artifactId"], result["sha256"])
    assert hashlib.sha256(output_bytes).hexdigest() == result["sha256"]
    assert result["planMode"] == "structured"
    assert result["planRevision"] == plan.revision
    assert result["planSha256"] == plan_digest
    assert result["orientation"] == "vertical"
    assert result["rendererSettings"] == {
        "videoCodec": "h264",
        "frameRateFps": 30,
        "maxWidth": 1280,
        "maxHeight": 1280,
        "audioPolicy": "explicit_segment_audio",
        "transitions": "hard_cut",
        "audioSampleRateHz": 48_000,
        "audioEdgeFadeSeconds": 0.01,
        "peakProtectionPolicy": "sample_peak_limit_minus_1_5_dbfs",
    }
    assert result["planVersion"] == "cre-creative-plan-v3"
    assert result["audioTimeline"] == [
        {
            "timelineOrder": segment.timeline_order,
            "mode": segment.audio.mode,
            "sourceAssetId": segment.audio.source_asset_id,
            "sourceInSeconds": segment.audio.source_in_seconds,
            "sourceOutSeconds": segment.audio.source_out_seconds,
            "gainDb": segment.audio.gain_db,
            "bedSourceAssetId": segment.audio.bed_source_asset_id,
            "bedSourceInSeconds": segment.audio.bed_source_in_seconds,
            "bedSourceOutSeconds": segment.audio.bed_source_out_seconds,
            "bedGainDb": segment.audio.bed_gain_db,
            "timingPolicy": segment.audio.timing_policy,
        }
        for segment in plan.segments
    ]
    assert result["audioValidation"]["outputPresent"] is True
    assert result["audioValidation"]["status"] == "PASSED"
    assert result["audioValidation"]["decodedPeakDbfs"] <= -1.0
    audit = h.store._connection.execute(
        """
        SELECT subject_id, decision, details_json
        FROM content_audit_events
        WHERE principal = ? AND action = ? AND subject_id = ?
        """,
        ("alice", "content_render_private_edit_project", project.project_id),
    ).fetchone()
    assert audit["subject_id"] == project.project_id
    assert audit["decision"] == "rendered"
    audit_details = json.loads(audit["details_json"])
    assert audit_details["plan_revision"] == plan.revision
    assert audit_details["plan_sha256"] == plan_digest
    assert audit_details["orientation"] == "vertical"
    assert audit_details["renderer_settings"] == result["rendererSettings"]
    assert audit_details["audio_timeline"] == result["audioTimeline"]
    assert audit_details["audio_validation"] == result["audioValidation"]
    assert audit_details["artifact_id"] == result["artifactId"]
    assert audit_details["sha256"] == hashlib.sha256(output_bytes).hexdigest()
    h.close()

    reopened = ContentStore(database)
    persisted_plan = reopened.get_latest_edit_plan(project.project_id, "alice")
    assert persisted_plan == plan
    stored_plan = reopened._connection.execute(
        """
        SELECT plan_sha256 FROM content_edit_plan_revisions
        WHERE project_id = ? AND revision = ?
        """,
        (project.project_id, plan.revision),
    ).fetchone()
    assert stored_plan["plan_sha256"] == plan_digest
    persisted_command = reopened._connection.execute(
        """
        SELECT result_id FROM content_commands
        WHERE principal = ? AND action = ? AND command_id = ?
        """,
        (
            "alice",
            "content_render_private_edit_project",
            "render-real-structured-provenance-test",
        ),
    ).fetchone()
    assert persisted_command["result_id"] == project.project_id
    persisted_audit = reopened._connection.execute(
        """
        SELECT subject_id, decision, details_json
        FROM content_audit_events
        WHERE principal = ? AND action = ? AND subject_id = ?
        """,
        ("alice", "content_render_private_edit_project", project.project_id),
    ).fetchone()
    persisted_details = json.loads(persisted_audit["details_json"])
    assert persisted_audit["subject_id"] == project.project_id
    assert persisted_audit["decision"] == "rendered"
    assert persisted_details["plan_revision"] == plan.revision
    assert persisted_details["plan_sha256"] == plan_digest
    assert persisted_details["orientation"] == result["orientation"]
    assert persisted_details["renderer_settings"] == result["rendererSettings"]
    assert persisted_details["artifact_id"] == result["artifactId"]
    assert persisted_details["sha256"] == result["sha256"]
    assert hashlib.sha256(
        storage.read_clip_output(result["artifactId"], result["sha256"])
    ).hexdigest() == persisted_details["sha256"]
    reopened.close()


@pytest.mark.parametrize(
    ("failure_point", "expected_error"),
    [
        ("renderer_validation", "injected v2 post-render validation failure"),
        ("ffmpeg", "FFmpeg failed to render the multi-source timeline"),
    ],
)
def test_v2_render_failure_does_not_register_partial_output(
    tmp_path,
    monkeypatch,
    failure_point,
    expected_error,
):
    source_bytes = _source_video_bytes(tmp_path)
    h, asset, _, storage = _harness(
        tmp_path,
        monkeypatch,
        media_bytes=source_bytes,
        rights_actions=frozenset({RightsAction.ANALYZE}),
    )
    _register_analysis_scope(h, asset)
    _create_restricted_test_permit(h, asset)
    rendering = ContentClipRenderingService(content_rights=h.service)
    project = ContentEditProject(
        project_id="v2-render-failure-test",
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script="Synthetic renderer failure test.",
    )
    h.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        h.service.create_edit_project,
        project=project,
        command_id="create-v2-render-failure-test",
    )
    instruction = "Create a synthetic private review edit."
    h.invoke(
        "content_autoplan_edit_project",
        {"project_id": project.project_id, "instruction_length": len(instruction)},
        rendering.autoplan_edit_project,
        project_id=project.project_id,
        instruction=instruction,
        command_id="plan-v2-render-failure-test",
    )
    assert isinstance(
        h.store.get_latest_edit_plan(project.project_id, "alice"),
        ContentCreativeEditPlanV2,
    )
    output_directory = storage._root / "outputs"
    output_directory.mkdir(exist_ok=True)

    def fail_after_partial(
        source_paths,
        *,
        plan,
        staged_output,
        vertical,
    ):
        staged_output.write_bytes(b"partial output must not be registered")
        raise RuntimeError("injected v2 post-render validation failure")

    if failure_point == "renderer_validation":
        monkeypatch.setattr(
            rendering.renderer,
            "render_creative_edit_plan",
            fail_after_partial,
        )
    else:
        monkeypatch.setattr(
            "ultron.application.content.clip_renderer.subprocess.run",
            lambda command, **kwargs: subprocess.CompletedProcess(command, 1),
        )
    with pytest.raises(RuntimeError, match=expected_error):
        h.invoke(
            "content_render_private_edit_project",
            {"project_id": project.project_id, "vertical": False},
            rendering.render_private_edit_project,
            project_id=project.project_id,
            vertical=False,
            command_id="render-v2-render-failure-test",
        )

    assert tuple(output_directory.iterdir()) == ()
    assert not tuple((storage._root / ".staging").glob(".clip-*.mp4"))
    assert h.store._connection.execute(
        "SELECT COUNT(*) FROM content_audit_events "
        "WHERE principal = ? AND action = ? AND decision = 'rendered'",
        ("alice", "content_render_private_edit_project"),
    ).fetchone()[0] == 0


@pytest.mark.parametrize("failure_point", ["command", "audit"])
def test_authorized_planning_failure_rolls_back_plan_projection_and_records(
    tmp_path,
    monkeypatch,
    failure_point,
):
    source_bytes = _source_video_bytes(tmp_path)
    h, asset, _, _ = _harness(
        tmp_path,
        monkeypatch,
        media_bytes=source_bytes,
        rights_actions=frozenset({RightsAction.ANALYZE}),
    )
    _register_analysis_scope(h, asset)
    rendering = ContentClipRenderingService(content_rights=h.service)
    project = ContentEditProject(
        project_id=f"plan-{failure_point}-failure-test",
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script="Synthetic transaction failure test.",
    )
    h.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        h.service.create_edit_project,
        command_id=f"create-plan-{failure_point}-failure-test",
        project=project,
    )
    if failure_point == "command":
        h.store._connection.execute(
            """
            CREATE TRIGGER injected_plan_command_failure
            BEFORE INSERT ON content_commands
            WHEN NEW.action = 'content_autoplan_edit_project'
            BEGIN
                SELECT RAISE(ABORT, 'injected command insertion failure');
            END
            """
        )
    else:
        h.store._connection.execute(
            """
            CREATE TRIGGER injected_plan_audit_failure
            BEFORE INSERT ON content_audit_events
            WHEN NEW.action = 'content_autoplan_edit_project'
            BEGIN
                SELECT RAISE(ABORT, 'injected audit insertion failure');
            END
            """
        )

    command_id = f"plan-{failure_point}-failure-operation"
    with pytest.raises(sqlite3.IntegrityError, match=f"injected {failure_point} insertion failure"):
        h.invoke(
            "content_autoplan_edit_project",
            {
                "project_id": project.project_id,
                "instruction_length": len("Synthetic rollback plan."),
            },
            rendering.autoplan_edit_project,
            project_id=project.project_id,
            instruction="Synthetic rollback plan.",
            command_id=command_id,
        )

    assert h.store.get_latest_edit_plan(project.project_id, "alice") is None
    assert h.store.get_edit_segments(project.project_id, "alice") == ()
    assert h.store._connection.execute(
        """
        SELECT COUNT(*) FROM content_commands
        WHERE principal = ? AND action = ? AND command_id = ?
        """,
        ("alice", "content_autoplan_edit_project", command_id),
    ).fetchone()[0] == 0
    assert h.store._connection.execute(
        """
        SELECT COUNT(*) FROM content_audit_events
        WHERE principal = ? AND action = ? AND subject_id = ?
        """,
        ("alice", "content_autoplan_edit_project", project.project_id),
    ).fetchone()[0] == 0
    assert not h.store._connection.in_transaction
    h.close()


@pytest.mark.parametrize(
    "unsupported_change",
    [
        {"audio_policy": "mute_source_audio"},
        {"output_constraints": {"frame_rate_fps": 60}},
        {"transition_before": "crossfade"},
    ],
)
def test_plan_renderer_rejects_unsupported_decisions_before_render(
    tmp_path,
    monkeypatch,
    unsupported_change,
):
    h, asset, _, _ = _harness(tmp_path, monkeypatch, media_bytes=_wav_bytes())
    rendering = ContentClipRenderingService(content_rights=h.service)
    plan = _structured_plan("renderer-unsupported-plan-test", asset)
    if "output_constraints" in unsupported_change:
        unsupported_change = {
            "output_constraints": plan.output_constraints.model_copy(
                update=unsupported_change["output_constraints"]
            )
        }
    if "transition_before" in unsupported_change:
        unsupported_plan = plan.model_copy(
            update={
                "segments": (
                    plan.segments[0].model_copy(
                        update={"transition_before": unsupported_change["transition_before"]}
                    ),
                    *plan.segments[1:],
                )
            }
        )
    else:
        unsupported_plan = plan.model_copy(update=unsupported_change)

    def must_not_render(*args, **kwargs):
        pytest.fail("Unsupported plan reached the FFmpeg renderer.")

    monkeypatch.setattr(rendering.renderer, "render_multi_source_timeline", must_not_render)
    with pytest.raises(ValueError, match="unsupported renderer behavior"):
        rendering.renderer.render_creative_edit_plan(
            {asset.asset_id: tmp_path / "source.media"},
            plan=unsupported_plan,
            staged_output=tmp_path / "output.mp4",
        )


def test_scene_diverse_selection_uses_distinct_detected_scenes_deterministically():
    first = _select_scene_diverse_intervals(
        40.0,
        tuple(float(boundary) for boundary in range(4, 40, 4)),
        3.0,
        8,
    )
    second = _select_scene_diverse_intervals(
        40.0,
        tuple(float(boundary) for boundary in range(4, 40, 4)),
        3.0,
        8,
    )

    assert first == second
    assert len({interval[3] or 0.0 for interval in first}) == 8
    assert all(
        method == "distinct_detected_scene_interval"
        for _, _, method, _ in first
    )
    assert all(
        left[1] <= right[0] or right[1] <= left[0]
        for index, left in enumerate(first)
        for right in first[index + 1 :]
    )
    assert all(0 <= start < end <= 40 for start, end, _, _ in first)


def test_scene_diverse_selection_uses_unique_windows_without_repetition():
    selected = _select_scene_diverse_intervals(
        12.0,
        (4.0, 8.0),
        3.0,
        4,
    )

    assert len({interval[:2] for interval in selected}) == len(selected)
    assert all(interval[2] == "evenly_spaced_sample" for interval in selected)
    assert all(
        left[1] <= right[0]
        for left, right in zip(selected, selected[1:])
    )


def test_scene_diverse_selection_preserves_subframe_boundaries_without_overlap():
    selected = _select_scene_diverse_intervals(
        7.966667,
        (3.933333,),
        59 / 30,
        4,
    )
    chronological = sorted(selected, key=lambda interval: interval[0])

    assert all(
        left[1] <= right[0]
        for left, right in zip(chronological, chronological[1:])
    )


def test_scene_diverse_selection_rejects_repeated_windows_when_material_is_insufficient():
    with pytest.raises(ValueError, match="exceed the available source footage"):
        _select_scene_diverse_intervals(
            8.0,
            (2.0, 4.0, 6.0),
            2.5,
            7,
        )


def test_scene_diverse_selection_rejects_invalid_inputs():
    with pytest.raises(ValueError, match="input is invalid"):
        _select_scene_diverse_intervals(8.0, (4.0, 2.0), 2.0, 2)


def test_edit_direction_settings_are_explicit_and_deterministic():
    instruction = (
        "Mood: cinematic\n"
        "Pacing: fast\n"
        "Target duration: 18 seconds\n"
        "Aspect ratio: 9:16\n"
        "Audio mode: dialogue_first"
    )
    expected = {
        "pacing": "fast",
        "mood": "cinematic",
        "audio_policy": "dialogue_first",
        "aspect_ratio": "9:16",
        "requested_duration_seconds": 18.0,
    }

    assert _edit_direction_settings(instruction) == expected
    assert _edit_direction_settings(instruction) == expected


def test_autoplan_is_deterministic_and_clamps_to_available_material(
    tmp_path,
    monkeypatch,
):
    h, asset, _, _ = _harness(
        tmp_path,
        monkeypatch,
        media_bytes=_wav_bytes(),
        rights_actions=frozenset({RightsAction.ANALYZE}),
    )
    _register_analysis_scope(h, asset)
    rendering = ContentClipRenderingService(content_rights=h.service)
    monkeypatch.setattr(
        rendering.renderer,
        "inspect_video_source",
        lambda source: MediaInfo(duration_seconds=10, has_video=True, has_audio=False),
    )
    monkeypatch.setattr(rendering.renderer, "detect_scene_changes", lambda source: ())
    project = ContentEditProject(
        project_id="deterministic-autoplan-test",
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script="Synthetic private edit.",
    )
    h.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        h.service.create_edit_project,
        project=project,
        command_id="create-deterministic-autoplan-test",
    )
    instruction = (
        "Mood: cinematic\n"
        "Pacing: fast\n"
        "Target duration: 18 seconds\n"
        "Aspect ratio: 9:16\n"
        "Audio mode: dialogue_first"
    )
    first = h.invoke(
        "content_autoplan_edit_project",
        {"project_id": project.project_id, "instruction_length": len(instruction)},
        rendering.autoplan_edit_project,
        project_id=project.project_id,
        instruction=instruction,
        command_id="plan-deterministic-autoplan-test-1",
    )
    second = h.invoke(
        "content_autoplan_edit_project",
        {"project_id": project.project_id, "instruction_length": len(instruction)},
        rendering.autoplan_edit_project,
        project_id=project.project_id,
        instruction=instruction,
        command_id="plan-deterministic-autoplan-test-2",
    )

    assert first["segments"] == second["segments"]
    assert first["mood"] == "cinematic"
    assert first["pacing"] == "fast"
    assert first["audioPolicy"] == "dialogue_first"
    assert first["aspectRatio"] == "9:16"
    assert first["requestedDurationSeconds"] == 18
    assert first["targetDurationSeconds"] == pytest.approx(10)
    assert first["limitations"]
    assert all(
        segment["transition_duration_seconds"] == 0
        and segment["narrative_purpose"]
        and segment["decision_reason"]
        for segment in first["segments"]
    )


def _run_render(harness, rendering, render_id, *, command_id=None):
    return harness.invoke(
        "content_run_clip_render",
        {"render_id": render_id},
        rendering.run_clip_render,
        render_id=render_id,
        **({"command_id": command_id} if command_id else {}),
    )


def _source_video_bytes(tmp_path) -> bytes:
    source_path = tmp_path / "source-video-fixture.mp4"
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
            "color=c=red:s=160x120:r=15:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x120:r=15:d=1.75",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=160x120:r=15:d=5.25",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000:duration=8",
            "-filter_complex",
            "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
            "-map",
            "[v]",
            "-map",
            "3:a:0",
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
    return source_path.read_bytes()


def _transition_frame_center(path: Path, timestamp: float) -> tuple[int, int, int]:
    extracted = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp:.6f}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            "scale=32:56",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert extracted.returncode == 0, extracted.stderr.decode(errors="replace")
    assert len(extracted.stdout) == 32 * 56 * 3
    center = (28 * 32 + 16) * 3
    return tuple(extracted.stdout[center : center + 3])


def test_invalid_transition_directive_is_rejected_before_source_probe(
    tmp_path,
    monkeypatch,
):
    storage = LocalTemporaryContentStore(tmp_path / "invalid-transition-store")
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory
    )
    monkeypatch.setattr(
        renderer,
        "inspect",
        lambda *_args, **_kwargs: pytest.fail(
            "Invalid transition reached source probing."
        ),
    )
    with pytest.raises(ValueError, match="timeline transition is invalid"):
        renderer.render_multi_source_timeline(
            {"source-a": tmp_path / "not-probed.mp4"},
            segments=(
                SourceEditSegment(0.0, 1.0, "source-a"),
                SourceEditSegment(
                    2.0,
                    3.0,
                    "source-a",
                    transition={
                        "kind": "cross_dissolve",
                        "duration_seconds": 0.2,
                        "decision_reason": "Missing required editorial evidence.",
                    },
                ),
            ),
            staged_output=storage.create_clip_staging_path("invalid-transition"),
        )


def _moving_transition_source_bytes(tmp_path) -> bytes:
    source_path = tmp_path / "moving-transition-source.mp4"
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
            "testsrc2=size=160x120:rate=15:duration=8",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000:duration=8",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
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
    return source_path.read_bytes()


def test_persisted_transitions_render_with_motion_captions_and_valid_av(
    tmp_path,
    monkeypatch,
):
    source_bytes = _moving_transition_source_bytes(tmp_path)
    h, asset, _, storage = _harness(
        tmp_path,
        monkeypatch,
        media_bytes=source_bytes,
    )
    source_path = storage.stage_clip_source("p13c-transition-source", source_bytes)
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory
    )
    previous_segment = ContentEditPlanSegment(
        source_asset_id=asset.asset_id,
        source_in_seconds=0.0,
        source_out_seconds=2.0,
        timeline_order=1,
        selection_method="nearest_detected_scene_cut",
        selected_scene_cut_seconds=2.0,
        transition_before="start",
    )
    selected_segment = ContentEditPlanSegment(
        source_asset_id=asset.asset_id,
        source_in_seconds=4.0,
        source_out_seconds=6.0,
        timeline_order=2,
        selection_method="distinct_detected_scene_interval",
        selected_scene_cut_seconds=4.0,
        transition_before="hard_cut",
    )
    previous_audio = ContentEditPlanAudio(
        mode="source_audio",
        source_asset_id=asset.asset_id,
        source_in_seconds=0.0,
        source_out_seconds=2.0,
        gain_db=0.0,
        timing_policy="trim_pad_to_video_segment",
    )
    next_audio = previous_audio.model_copy(
        update={"source_in_seconds": 4.0, "source_out_seconds": 6.0}
    )
    style_selected = _transition_directive_for_boundary(
        style_id="cinematic_documentary",
        pacing="balanced",
        previous_segment=previous_segment,
        segment=selected_segment,
        timeline_order=2,
        narrative_purpose="development",
        previous_audio=previous_audio,
        audio=next_audio,
    )
    assert style_selected.kind == "cross_dissolve"
    assert "observed_scene_boundary" in style_selected.evidence
    assert "editing_style" in style_selected.evidence
    persisted_style_plan = _transition_plan(
        "p13c-style-selected",
        asset,
        transition=style_selected,
    )
    project = ContentEditProject(
        project_id=persisted_style_plan.project_id,
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script="Synthetic private transition review.",
    )
    h.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        h.service.create_edit_project,
        project=project,
        command_id="p13c-style-selected-project",
    )
    with h.store._transaction() as connection:
        h.store.save_edit_plan_in_transaction(
            connection,
            "alice",
            persisted_style_plan,
        )
    persisted_style_plan = h.store.get_latest_edit_plan(
        persisted_style_plan.project_id,
        "alice",
    )
    assert persisted_style_plan is not None
    assert persisted_style_plan.segments[1].transition == style_selected

    variants = (
        (
            "hard-cut",
            ContentEditPlanTransition(
                kind="hard_cut",
                from_timeline_order=1,
                to_timeline_order=2,
                decision_reason="Retained a hard cut for the baseline.",
                evidence=("observed_scene_boundary",),
            ),
        ),
        ("style-cross-dissolve", style_selected),
        (
            "fade-through-black",
            ContentEditPlanTransition(
                kind="fade_through_black",
                duration_seconds=0.25,
                from_timeline_order=1,
                to_timeline_order=2,
                decision_reason="Tested at an observed boundary using the style profile.",
                evidence=("observed_scene_boundary", "editing_style"),
            ),
        ),
        (
            "fade-through-white",
            ContentEditPlanTransition(
                kind="fade_through_white",
                duration_seconds=0.25,
                from_timeline_order=1,
                to_timeline_order=2,
                decision_reason="Tested at an observed boundary using the style profile.",
                evidence=("observed_scene_boundary", "editing_style"),
            ),
        ),
        (
            "directional-wipe",
            ContentEditPlanTransition(
                kind="directional_wipe",
                duration_seconds=0.25,
                direction="right",
                from_timeline_order=1,
                to_timeline_order=2,
                decision_reason="Tested at an observed boundary using the style profile.",
                evidence=("observed_scene_boundary", "editing_style"),
            ),
        ),
        (
            "zoom",
            ContentEditPlanTransition(
                kind="zoom",
                duration_seconds=0.25,
                from_timeline_order=1,
                to_timeline_order=2,
                decision_reason="Tested at an observed boundary using the style profile.",
                evidence=("observed_scene_boundary", "editing_style"),
            ),
        ),
    )
    rendered: dict[str, tuple[Path, MediaInfo, float]] = {}
    for name, transition in variants:
        plan = _transition_plan(
            f"p13c-{name}",
            asset,
            transition=transition,
        )
        if name == "style-cross-dissolve":
            plan = persisted_style_plan
        output_path = storage.create_clip_staging_path(f"p13c-{name}")
        source_infos, output_info = renderer.render_creative_edit_plan(
            {asset.asset_id: source_path},
            plan=plan,
            staged_output=output_path,
            vertical=True,
        )
        assert source_infos[asset.asset_id].has_video
        assert output_info.video_width == 720
        assert output_info.video_height == 1280
        assert output_info.video_codec == "h264"
        assert output_info.audio_codec == "aac"
        assert output_info.video_fps == pytest.approx(30, abs=0.1)
        assert output_info.has_audio
        assert output_info.video_duration_seconds == pytest.approx(
            4.0 - transition.duration_seconds,
            abs=0.15,
        )
        assert output_info.audio_duration_seconds == pytest.approx(
            output_info.video_duration_seconds,
            abs=0.25,
        )
        rendered[name] = (
            output_path,
            output_info,
            transition.duration_seconds,
        )

    before_colors = []
    transition_colors = []
    after_colors = []
    for path, _, duration in rendered.values():
        boundary_start = 2.0 - duration
        before_colors.append(
            _transition_frame_center(path, max(0.1, boundary_start - 0.05))
        )
        center_time = (
            boundary_start + duration / 2
            if duration > 0
            else boundary_start + 0.02
        )
        transition_colors.append(_transition_frame_center(path, center_time))
        after_colors.append(
            _transition_frame_center(path, boundary_start + duration + 0.05)
        )
    assert all(before != after for before, after in zip(before_colors, after_colors))
    assert len(set(transition_colors)) >= 4
    h.close()


def _video_with_longer_audio_source_bytes(tmp_path) -> bytes:
    video_path = tmp_path / "longer-audio-video-only.mp4"
    video = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=160x120:r=30:d=8",
            "-frames:v",
            "239",
            "-an",
            "-c:v",
            "mpeg4",
            "-q:v",
            "8",
            str(video_path),
        ],
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert video.returncode == 0, video.stderr.decode(errors="replace")
    audio_path = tmp_path / "longer-audio-track.m4a"
    audio = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=8.1",
            "-c:a",
            "aac",
            "-b:a",
            "32k",
            str(audio_path),
        ],
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert audio.returncode == 0, audio.stderr.decode(errors="replace")
    source_path = tmp_path / "longer-audio-source.mp4"
    generated = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c",
            "copy",
            str(source_path),
        ],
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert generated.returncode == 0, generated.stderr.decode(errors="replace")
    return source_path.read_bytes()


def _video_only_source_bytes(tmp_path, *, name: str) -> bytes:
    audio_source = tmp_path / f"{name}-with-audio.mp4"
    audio_source.write_bytes(_source_video_bytes(tmp_path))
    video_only_source = tmp_path / f"{name}-video-only.mp4"
    stripped = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(audio_source),
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "copy",
            str(video_only_source),
        ],
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert stripped.returncode == 0, stripped.stderr.decode(errors="replace")
    return video_only_source.read_bytes()


def _synthetic_audio_video_bytes(
    tmp_path,
    *,
    name: str,
    tone_hz: int,
    tone_volume: float = 0.35,
    secondary_tone_hz: int | None = None,
    secondary_tone_volume: float = 0.035,
    audio_duration: float = 4.0,
) -> bytes:
    source_path = tmp_path / f"{name}.mp4"
    audio_inputs = [
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={tone_hz}:sample_rate=48000:duration={audio_duration},"
        f"volume={tone_volume}",
    ]
    audio_map = "4:a:0"
    filter_complex = "[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[v]"
    if secondary_tone_hz is not None:
        audio_inputs.extend(
            [
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={secondary_tone_hz}:sample_rate=48000:"
                f"duration={audio_duration},volume={secondary_tone_volume}",
            ]
        )
        filter_complex += (
            ";[4:a:0][5:a:0]amix=inputs=2:duration=first:"
            "dropout_transition=0[a]"
        )
        audio_map = "[a]"
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
            "color=c=red:s=160x120:r=15:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x120:r=15:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=160x120:r=15:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=yellow:s=160x120:r=15:d=1",
            *audio_inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-map",
            audio_map,
            "-c:v",
            "mpeg4",
            "-q:v",
            "8",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-t",
            "4",
            str(source_path),
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert generated.returncode == 0, generated.stderr.decode(errors="replace")
    return source_path.read_bytes()


def _decoded_audio_rms_and_tone(
    renderer: FFmpegLocalClipRenderer,
    output_path,
    *,
    start_seconds: float,
    duration_seconds: float = 0.75,
) -> tuple[float, dict[int, float]]:
    decoded = subprocess.run(
        [
            renderer.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(output_path),
            "-ss",
            f"{start_seconds:.6f}",
            "-t",
            f"{duration_seconds:.6f}",
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-f",
            "s16le",
            "pipe:1",
        ],
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert decoded.returncode == 0, decoded.stderr.decode(errors="replace")
    samples = [sample[0] for sample in struct.iter_unpack("<h", decoded.stdout)]
    assert samples
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    powers: dict[int, float] = {}
    for frequency in (440, 880):
        coefficient = 2 * math.cos(2 * math.pi * frequency / 48_000)
        previous = previous_two = 0.0
        for sample in samples:
            current = sample + coefficient * previous - previous_two
            previous_two, previous = previous, current
        powers[frequency] = (
            previous * previous
            + previous_two * previous_two
            - coefficient * previous * previous_two
        )
    return rms, powers


def test_v2_audio_timeline_maps_sources_gain_and_explicit_silence(tmp_path):
    source_a_bytes = _synthetic_audio_video_bytes(
        tmp_path,
        name="tone-440",
        tone_hz=440,
    )
    source_b_bytes = _synthetic_audio_video_bytes(
        tmp_path,
        name="tone-880",
        tone_hz=880,
    )
    storage = LocalTemporaryContentStore(tmp_path / "v2-audio-store")
    source_a = storage.stage_clip_source("v2-a", source_a_bytes)
    source_b = storage.stage_clip_source("v2-b", source_b_bytes)
    output = storage.create_clip_staging_path("v2-audio-timeline")
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory
    )
    plan = ContentCreativeEditPlanV2(
        project_id="v2-audio-timeline",
        revision=1,
        creative_direction="Synthetic explicit audio timeline.",
        sources=(
            ContentEditPlanSource(
                source_asset_id="audio-a",
                content_sha256=hashlib.sha256(source_a_bytes).hexdigest(),
                duration_seconds=4,
            ),
            ContentEditPlanSource(
                source_asset_id="audio-b",
                content_sha256=hashlib.sha256(source_b_bytes).hexdigest(),
                duration_seconds=4,
            ),
        ),
        segments=(
            {
                "source_asset_id": "audio-a",
                "source_in_seconds": 0,
                "source_out_seconds": 2,
                "timeline_order": 1,
                "selection_method": "evenly_spaced_sample",
                "transition_before": "start",
                "audio": ContentEditPlanAudio(
                    mode="source_audio",
                    source_asset_id="audio-b",
                    source_in_seconds=0,
                    source_out_seconds=2,
                    gain_db=-24,
                    timing_policy="trim_pad_to_video_segment",
                ),
            },
            {
                "source_asset_id": "audio-b",
                "source_in_seconds": 0,
                "source_out_seconds": 2,
                "timeline_order": 2,
                "selection_method": "evenly_spaced_sample",
                "transition_before": "hard_cut",
                "audio": ContentEditPlanAudio(
                    mode="source_audio",
                    source_asset_id="audio-a",
                    source_in_seconds=0,
                    source_out_seconds=2,
                    gain_db=0,
                    timing_policy="trim_pad_to_video_segment",
                ),
            },
            {
                "source_asset_id": "audio-a",
                "source_in_seconds": 2,
                "source_out_seconds": 4,
                "timeline_order": 3,
                "selection_method": "evenly_spaced_sample",
                "transition_before": "hard_cut",
                "audio": ContentEditPlanAudio(
                    mode="source_audio",
                    source_asset_id="audio-a",
                    source_in_seconds=2,
                    source_out_seconds=4,
                    gain_db=6,
                    timing_policy="trim_pad_to_video_segment",
                ),
            },
            {
                "source_asset_id": "audio-b",
                "source_in_seconds": 2,
                "source_out_seconds": 4,
                "timeline_order": 4,
                "selection_method": "evenly_spaced_sample",
                "transition_before": "hard_cut",
                "audio": ContentEditPlanAudio(
                    mode="silence",
                    source_asset_id=None,
                    source_in_seconds=None,
                    source_out_seconds=None,
                    gain_db=0,
                    timing_policy="trim_pad_to_video_segment",
                ),
            },
        ),
    )

    source_infos, output_info = renderer.render_creative_edit_plan(
        {"audio-a": source_a, "audio-b": source_b},
        plan=plan,
        staged_output=output,
    )
    audio_stream_probe = subprocess.run(
        [
            renderer.ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(output),
        ],
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert audio_stream_probe.returncode == 0
    assert len(audio_stream_probe.stdout.splitlines()) == 1
    assert source_infos["audio-a"].has_audio is True
    assert source_infos["audio-b"].has_audio is True
    assert output_info.has_audio is True
    assert output_info.audio_duration_seconds == pytest.approx(8, abs=0.25)
    assert output_info.video_duration_seconds == pytest.approx(8, abs=0.25)
    assert abs(
        output_info.audio_duration_seconds - output_info.video_duration_seconds
    ) <= 0.05
    assert output_info.audio_peak_dbfs is not None
    assert output_info.audio_peak_dbfs <= -1.0

    low_gain_rms, low_gain_tones = _decoded_audio_rms_and_tone(
        renderer,
        output,
        start_seconds=0.5,
    )
    unity_gain_rms, unity_gain_tones = _decoded_audio_rms_and_tone(
        renderer,
        output,
        start_seconds=2.5,
    )
    boosted_gain_rms, boosted_gain_tones = _decoded_audio_rms_and_tone(
        renderer,
        output,
        start_seconds=4.5,
    )
    silence_rms, _ = _decoded_audio_rms_and_tone(
        renderer,
        output,
        start_seconds=6.5,
    )
    assert low_gain_tones[880] > low_gain_tones[440] * 10
    assert unity_gain_tones[440] > unity_gain_tones[880] * 10
    assert boosted_gain_tones[440] > boosted_gain_tones[880] * 10
    assert 0.045 < low_gain_rms / unity_gain_rms < 0.09
    assert 1.6 < boosted_gain_rms / unity_gain_rms < 2.4
    assert silence_rms < 20
    storage.delete_clip_staging(source_a)
    storage.delete_clip_staging(source_b)
    storage.delete_clip_staging(output)


def test_v2_renderer_rejects_repeated_source_audio_intervals(tmp_path):
    repeated_audio = ContentEditPlanAudio(
        mode="source_audio",
        source_asset_id="audio-a",
        source_in_seconds=0,
        source_out_seconds=2,
        gain_db=0,
        timing_policy="trim_pad_to_video_segment",
    )
    plan = ContentCreativeEditPlanV2(
        project_id="repeated-audio-interval-test",
        revision=1,
        creative_direction="Reject repeated source audio intervals.",
        sources=(
            ContentEditPlanSource(
                source_asset_id="audio-a",
                content_sha256="0" * 64,
                duration_seconds=6,
                scene_boundary_seconds=(3,),
            ),
        ),
        segments=(
            {
                "source_asset_id": "audio-a",
                "source_in_seconds": 0,
                "source_out_seconds": 2,
                "timeline_order": 1,
                "selection_method": "distinct_detected_scene_interval",
                "transition_before": "start",
                "audio": repeated_audio,
            },
            {
                "source_asset_id": "audio-a",
                "source_in_seconds": 3,
                "source_out_seconds": 5,
                "timeline_order": 2,
                "selection_method": "distinct_detected_scene_interval",
                "selected_scene_cut_seconds": 3,
                "transition_before": "hard_cut",
                "audio": ContentEditPlanAudio(
                    mode="source_audio",
                    source_asset_id="audio-a",
                    source_in_seconds=3,
                    source_out_seconds=5,
                    gain_db=0,
                    timing_policy="trim_pad_to_video_segment",
                ),
            },
            {
                "source_asset_id": "audio-a",
                "source_in_seconds": 0,
                "source_out_seconds": 2,
                "timeline_order": 3,
                "selection_method": "repeated_detected_scene_interval",
                "transition_before": "hard_cut",
                "audio": repeated_audio,
            },
        ),
    )
    staging_directory = tmp_path / "staging"
    staging_directory.mkdir()
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=staging_directory
    )

    with pytest.raises(ValueError, match="repeats a source audio interval"):
        renderer.render_creative_edit_plan(
            {"audio-a": tmp_path / "source-a.media"},
            plan=plan,
            staged_output=tmp_path / "unused-output.mp4",
        )


def test_two_source_audio_modes_keep_scene_audio_and_continuous_bed_in_sync(
    tmp_path,
):
    source_a_bytes = _synthetic_audio_video_bytes(
        tmp_path,
        name="workflow-source-440-mixed",
        tone_hz=440,
        secondary_tone_hz=880,
        secondary_tone_volume=0.035,
    )
    source_b_bytes = _synthetic_audio_video_bytes(
        tmp_path,
        name="workflow-source-880",
        tone_hz=880,
    )
    storage = LocalTemporaryContentStore(tmp_path / "two-source-audio-store")
    source_a = storage.stage_clip_source("workflow-a", source_a_bytes)
    source_b = storage.stage_clip_source("workflow-b", source_b_bytes)
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory
    )
    sources = (
        ContentEditPlanSource(
            source_asset_id="source-a",
            content_sha256=hashlib.sha256(source_a_bytes).hexdigest(),
            duration_seconds=4,
        ),
        ContentEditPlanSource(
            source_asset_id="source-b",
            content_sha256=hashlib.sha256(source_b_bytes).hexdigest(),
            duration_seconds=4,
        ),
    )

    def timeline_audio(
        source_asset_id: str | None,
        *,
        source_start: float = 0,
        source_end: float = 2,
        bed_a: tuple[float, float] | None = None,
    ):
        if source_asset_id is not None:
            return ContentEditPlanAudio(
                mode="source_audio",
                source_asset_id=source_asset_id,
                source_in_seconds=source_start,
                source_out_seconds=source_end,
                gain_db=0,
                timing_policy="trim_pad_to_video_segment",
            )
        return ContentEditPlanAudio(
            mode="silence",
            source_asset_id=None,
            source_in_seconds=None,
            source_out_seconds=None,
            gain_db=0,
            timing_policy="trim_pad_to_video_segment",
            **(
                {
                    "bed_source_asset_id": "source-a",
                    "bed_source_in_seconds": bed_a[0],
                    "bed_source_out_seconds": bed_a[1],
                }
                if bed_a is not None
                else {}
            ),
        )

    source_audio_plan = ContentCreativeEditPlanV2(
        project_id="two-source-source-audio",
        revision=1,
        creative_direction="Keep each clip's own audio.",
        audio_policy="source_audio",
        sources=sources,
        segments=(
            {
                "source_asset_id": "source-a",
                "source_in_seconds": 0,
                "source_out_seconds": 2,
                "timeline_order": 1,
                "selection_method": "evenly_spaced_sample",
                "transition_before": "start",
                "audio": timeline_audio("source-a"),
            },
            {
                "source_asset_id": "source-b",
                "source_in_seconds": 0,
                "source_out_seconds": 2,
                "timeline_order": 2,
                "selection_method": "evenly_spaced_sample",
                "transition_before": "hard_cut",
                "audio": ContentEditPlanAudio(
                    mode="source_audio",
                    source_asset_id="source-b",
                    source_in_seconds=0,
                    source_out_seconds=2,
                    gain_db=0,
                    timing_policy="trim_pad_to_video_segment",
                ),
            },
        ),
    )
    source_output = storage.create_clip_staging_path("two-source-source-audio")
    source_info, source_result = renderer.render_creative_edit_plan(
        {"source-a": source_a, "source-b": source_b},
        plan=source_audio_plan,
        staged_output=source_output,
    )
    assert source_info["source-a"].has_audio is True
    assert source_info["source-b"].has_audio is True
    assert source_result.video_duration_seconds == pytest.approx(4, abs=0.25)
    assert source_result.audio_duration_seconds == pytest.approx(4, abs=0.25)
    _, first_source_tone = _decoded_audio_rms_and_tone(
        renderer,
        source_output,
        start_seconds=0.5,
    )
    _, second_source_tone = _decoded_audio_rms_and_tone(
        renderer,
        source_output,
        start_seconds=2.5,
    )
    assert first_source_tone[440] > first_source_tone[880] * 10
    assert second_source_tone[880] > second_source_tone[440] * 10
    _, first_scene_immediately_before_cut = _decoded_audio_rms_and_tone(
        renderer,
        source_output,
        start_seconds=1.92,
        duration_seconds=0.08,
    )
    _, second_scene_at_cut = _decoded_audio_rms_and_tone(
        renderer,
        source_output,
        start_seconds=2.00,
        duration_seconds=0.08,
    )
    _, second_scene_immediately_after_cut = _decoded_audio_rms_and_tone(
        renderer,
        source_output,
        start_seconds=2.08,
        duration_seconds=0.08,
    )
    assert (
        first_scene_immediately_before_cut[440]
        > first_scene_immediately_before_cut[880] * 10
    )
    assert second_scene_at_cut[880] > second_scene_at_cut[440] * 10
    assert (
        second_scene_immediately_after_cut[880]
        > second_scene_immediately_after_cut[440] * 10
    )

    dialogue_plan = ContentCreativeEditPlanV2.model_validate(
        source_audio_plan.model_dump(mode="python")
        | {"audio_policy": "dialogue_first"}
    )
    dialogue_output = storage.create_clip_staging_path(
        "two-source-dialogue-first"
    )
    _, dialogue_result = renderer.render_creative_edit_plan(
        {"source-a": source_a, "source-b": source_b},
        plan=dialogue_plan,
        staged_output=dialogue_output,
    )
    assert dialogue_result.audio_duration_seconds == pytest.approx(4, abs=0.25)
    _, first_dialogue_tone = _decoded_audio_rms_and_tone(
        renderer,
        dialogue_output,
        start_seconds=0.5,
    )
    _, second_dialogue_tone = _decoded_audio_rms_and_tone(
        renderer,
        dialogue_output,
        start_seconds=2.5,
    )
    assert first_dialogue_tone[440] > first_dialogue_tone[880] * 10
    assert second_dialogue_tone[880] > second_dialogue_tone[440] * 10

    bed_plan = ContentCreativeEditPlanV2(
        project_id="two-source-continuous-bed",
        revision=1,
        creative_direction="Use source-a as a continuous user-supplied bed.",
        audio_policy="continuous_bed",
        sources=sources,
        segments=(
            {
                "source_asset_id": "source-a",
                "source_in_seconds": 0,
                "source_out_seconds": 2,
                "timeline_order": 1,
                "selection_method": "evenly_spaced_sample",
                "transition_before": "start",
                "audio": timeline_audio(None, bed_a=(0, 2)),
            },
            {
                "source_asset_id": "source-b",
                "source_in_seconds": 0,
                "source_out_seconds": 2,
                "timeline_order": 2,
                "selection_method": "evenly_spaced_sample",
                "transition_before": "hard_cut",
                "audio": ContentEditPlanAudio(
                    mode="source_audio",
                    source_asset_id="source-b",
                    source_in_seconds=0,
                    source_out_seconds=2,
                    gain_db=0,
                    timing_policy="trim_pad_to_video_segment",
                    bed_source_asset_id="source-a",
                    bed_source_in_seconds=2,
                    bed_source_out_seconds=4,
                ),
            },
        ),
    )
    bed_output = storage.create_clip_staging_path("two-source-continuous-bed")
    _, bed_result = renderer.render_creative_edit_plan(
        {"source-a": source_a, "source-b": source_b},
        plan=bed_plan,
        staged_output=bed_output,
    )
    assert bed_result.video_duration_seconds == pytest.approx(4, abs=0.25)
    assert bed_result.audio_duration_seconds == pytest.approx(4, abs=0.25)
    assert bed_result.audio_peak_dbfs is not None
    assert bed_result.audio_peak_dbfs <= -1.0
    _, first_bed_tone = _decoded_audio_rms_and_tone(
        renderer,
        bed_output,
        start_seconds=0.5,
    )
    _, second_bed_tone = _decoded_audio_rms_and_tone(
        renderer,
        bed_output,
        start_seconds=2.5,
    )
    assert first_bed_tone[440] > first_bed_tone[880] * 10
    assert second_bed_tone[440] > 0
    assert second_bed_tone[880] > second_bed_tone[440]
    for path in (
        source_a,
        source_b,
        source_output,
        dialogue_output,
        bed_output,
    ):
        storage.delete_clip_staging(path)


def test_v2_short_audio_is_padded_and_peak_protected(tmp_path):
    source_bytes = _synthetic_audio_video_bytes(
        tmp_path,
        name="short-loud-tone",
        tone_hz=440,
        tone_volume=0.9,
        audio_duration=0.5,
    )
    storage = LocalTemporaryContentStore(tmp_path / "v2-short-audio-store")
    source = storage.stage_clip_source("v2-short", source_bytes)
    output = storage.create_clip_staging_path("v2-short-audio")
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory
    )
    plan = ContentCreativeEditPlanV2(
        project_id="v2-short-audio",
        revision=1,
        creative_direction="Pad short audio and constrain decoded peaks.",
        sources=(
            ContentEditPlanSource(
                source_asset_id="short",
                content_sha256=hashlib.sha256(source_bytes).hexdigest(),
                duration_seconds=4,
            ),
        ),
        segments=(
            {
                "source_asset_id": "short",
                "source_in_seconds": 0,
                "source_out_seconds": 2,
                "timeline_order": 1,
                "selection_method": "evenly_spaced_sample",
                "transition_before": "start",
                "audio": ContentEditPlanAudio(
                    mode="source_audio",
                    source_asset_id="short",
                    source_in_seconds=0,
                    source_out_seconds=2,
                    gain_db=6,
                    timing_policy="trim_pad_to_video_segment",
                ),
            },
            {
                "source_asset_id": "short",
                "source_in_seconds": 2,
                "source_out_seconds": 4,
                "timeline_order": 2,
                "selection_method": "evenly_spaced_sample",
                "transition_before": "hard_cut",
                "audio": ContentEditPlanAudio(
                    mode="silence",
                    source_asset_id=None,
                    source_in_seconds=None,
                    source_out_seconds=None,
                    gain_db=0,
                    timing_policy="trim_pad_to_video_segment",
                ),
            },
        ),
    )

    source_infos, output_info = renderer.render_creative_edit_plan(
        {"short": source},
        plan=plan,
        staged_output=output,
    )
    assert source_infos["short"].audio_duration_seconds == pytest.approx(0.5, abs=0.15)
    assert output_info.audio_duration_seconds == pytest.approx(4, abs=0.25)
    assert output_info.audio_peak_dbfs is not None
    assert output_info.audio_peak_dbfs <= -1.0
    early_rms, _ = _decoded_audio_rms_and_tone(
        renderer,
        output,
        start_seconds=0.1,
    )
    padded_rms, _ = _decoded_audio_rms_and_tone(
        renderer,
        output,
        start_seconds=1.0,
    )
    intentional_silence_rms, _ = _decoded_audio_rms_and_tone(
        renderer,
        output,
        start_seconds=2.5,
    )
    assert early_rms > 100
    assert padded_rms < 30
    assert intentional_silence_rms < 20
    storage.delete_clip_staging(source)
    storage.delete_clip_staging(output)


def test_v2_audio_plan_rejects_video_only_source_before_ffmpeg(
    tmp_path,
    monkeypatch,
):
    source_with_audio = tmp_path / "source-with-audio.mp4"
    source_with_audio.write_bytes(_source_video_bytes(tmp_path))
    video_only_path = tmp_path / "source-video-only.mp4"
    stripped = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source_with_audio),
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "copy",
            str(video_only_path),
        ],
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert stripped.returncode == 0, stripped.stderr.decode(errors="replace")
    video_bytes = video_only_path.read_bytes()
    storage = LocalTemporaryContentStore(tmp_path / "v2-video-only-store")
    source = storage.stage_clip_source("v2-video-only", video_bytes)
    output = storage.create_clip_staging_path("v2-video-only-output")
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory
    )
    plan = ContentCreativeEditPlanV2(
        project_id="v2-video-only",
        revision=1,
        creative_direction="Reject audio selection absent from actual media.",
        sources=(
            ContentEditPlanSource(
                source_asset_id="video-only",
                content_sha256=hashlib.sha256(video_bytes).hexdigest(),
                duration_seconds=8,
            ),
        ),
        segments=(
            {
                "source_asset_id": "video-only",
                "source_in_seconds": 0,
                "source_out_seconds": 2,
                "timeline_order": 1,
                "selection_method": "evenly_spaced_sample",
                "transition_before": "start",
                "audio": ContentEditPlanAudio(
                    mode="source_audio",
                    source_asset_id="video-only",
                    source_in_seconds=0,
                    source_out_seconds=2,
                    gain_db=0,
                    timing_policy="trim_pad_to_video_segment",
                ),
            },
            {
                "source_asset_id": "video-only",
                "source_in_seconds": 2,
                "source_out_seconds": 4,
                "timeline_order": 2,
                "selection_method": "evenly_spaced_sample",
                "transition_before": "hard_cut",
                "audio": ContentEditPlanAudio(
                    mode="silence",
                    source_asset_id=None,
                    source_in_seconds=None,
                    source_out_seconds=None,
                    gain_db=0,
                    timing_policy="trim_pad_to_video_segment",
                ),
            },
        ),
    )

    def must_not_render(*args, **kwargs):
        pytest.fail("Invalid actual audio-source metadata reached FFmpeg.")

    monkeypatch.setattr(
        "ultron.application.content.clip_renderer.subprocess.run",
        must_not_render,
    )
    with pytest.raises(ValueError, match="Explicit audio source or timing"):
        renderer.render_creative_edit_plan(
            {"video-only": source},
            plan=plan,
            staged_output=output,
        )
    storage.delete_clip_staging(source)
    storage.delete_clip_staging(output)


def test_multi_source_render_preserves_audio_and_silences_video_only_segments(
    tmp_path,
):
    source_bytes = _source_video_bytes(tmp_path)
    with_audio_path = tmp_path / "source-with-audio.mp4"
    with_audio_path.write_bytes(source_bytes)
    video_only_path = tmp_path / "source-video-only.mp4"
    video_only = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(with_audio_path),
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "copy",
            str(video_only_path),
        ],
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert video_only.returncode == 0, video_only.stderr.decode(errors="replace")

    storage = LocalTemporaryContentStore(tmp_path / "managed-content")
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory
    )
    audio_source = storage.stage_clip_source("audio-source", source_bytes)
    silent_source = storage.stage_clip_source("silent-source", video_only_path.read_bytes())
    output = storage.create_clip_staging_path("mixed-audio-policy")
    source_infos, output_info = renderer.render_multi_source_timeline(
        {"audio": audio_source, "silent": silent_source},
        segments=(
            SourceEditSegment(0, 2, "audio"),
            SourceEditSegment(0, 2, "silent"),
        ),
        staged_output=output,
    )
    explicit_silence_output = storage.create_clip_staging_path(
        "explicit-video-only-silence"
    )
    _, explicit_silence_info = renderer.render_multi_source_timeline(
        {"audio": audio_source, "silent": silent_source},
        segments=(
            SourceEditSegment(0, 2, "audio"),
            SourceEditSegment(0, 2, "silent"),
        ),
        staged_output=explicit_silence_output,
        audio_decisions=(
            ContentEditPlanAudio(
                mode="source_audio",
                source_asset_id="audio",
                source_in_seconds=0,
                source_out_seconds=2,
                gain_db=0,
                timing_policy="trim_pad_to_video_segment",
            ),
            ContentEditPlanAudio(
                mode="silence",
                source_asset_id=None,
                source_in_seconds=None,
                source_out_seconds=None,
                gain_db=0,
                timing_policy="trim_pad_to_video_segment",
            ),
        ),
        audio_policy="source_audio",
    )
    assert source_infos["audio"].has_audio is True
    assert source_infos["silent"].has_audio is False
    assert renderer.inspect_video_source(silent_source).has_video is True
    with pytest.raises(ValueError, match="both video and audio"):
        renderer.inspect_source_video(silent_source)
    assert output_info.has_audio is True
    assert explicit_silence_info.has_audio is True
    assert explicit_silence_info.video_duration_seconds == pytest.approx(4, abs=0.25)
    assert explicit_silence_info.audio_duration_seconds == pytest.approx(4, abs=0.25)

    def peak_between(start_seconds: float, media_path=output) -> int:
        extracted = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                str(start_seconds),
                "-i",
                str(media_path),
                "-t",
                "0.5",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "8000",
                "-f",
                "s16le",
                "pipe:1",
            ],
            capture_output=True,
            check=False,
            timeout=10,
        )
        assert extracted.returncode == 0, extracted.stderr.decode(errors="replace")
        samples = struct.unpack(f"<{len(extracted.stdout) // 2}h", extracted.stdout)
        return max((abs(sample) for sample in samples), default=0)

    assert peak_between(0.5) > 500
    assert peak_between(2.5) < 100
    assert peak_between(0.5, explicit_silence_output) > 500
    assert peak_between(2.5, explicit_silence_output) < 100


def test_source_video_to_short_clip_uses_registered_original_footage(
    tmp_path,
    monkeypatch,
):
    source_bytes = _source_video_bytes(tmp_path)
    managed_storage = LocalTemporaryContentStore(tmp_path / "managed-content")
    media_renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=managed_storage.clip_staging_directory
    )
    staged_source = managed_storage.stage_clip_source(
        "scene-analysis-test",
        source_bytes,
    )
    try:
        scene_boundaries = media_renderer.detect_scene_changes(staged_source)
    finally:
        managed_storage.delete_clip_staging(staged_source)
    monkeypatch.setattr(lifecycle, "MEDIA", source_bytes)
    monkeypatch.setattr(
        lifecycle,
        "MEDIA_SHA256",
        hashlib.sha256(source_bytes).hexdigest(),
    )
    harness, asset, _, storage, transcript, candidate, rendering = (
        _authorized_pipeline(
            tmp_path,
            monkeypatch,
            media_bytes=source_bytes,
            media_renderer=media_renderer,
            scene_boundary_seconds=scene_boundaries,
        )
    )
    queued = _create_render(
        harness,
        rendering,
        candidate,
        _derivative_use(asset, candidate),
    )
    completed = _run_render(harness, rendering, queued.render_id)
    manifest = rendering.get_clip_review_manifest(
        completed.render_id,
        context=harness.context(
            "content_read_clip_review_manifest",
            {"render_id": completed.render_id},
        ),
    )
    output = rendering.get_clip_output_bytes(
        completed.render_id,
        context=harness.context(
            "content_read_clip_render_output",
            {"render_id": completed.render_id},
        ),
    )

    assert completed.state == ProcessingJobState.SUCCEEDED
    assert transcript.asset_sha256 == hashlib.sha256(source_bytes).hexdigest()
    assert manifest["provenance"]["assetSha256"] == transcript.asset_sha256
    assert manifest["provenance"]["sourceIntervalSeconds"] == [
        candidate.start_seconds,
        candidate.end_seconds,
    ]
    assert manifest["editTimeline"]["version"] == "cre-source-edl-v1"
    timeline_segments = manifest["editTimeline"]["segments"]
    assert len(timeline_segments) == 4
    assert timeline_segments[0]["sourceInSeconds"] == 0.5
    assert timeline_segments[1]["sourceInSeconds"] == pytest.approx(1.0, abs=0.1)
    assert timeline_segments[2]["sourceInSeconds"] == 2.0
    assert timeline_segments[3]["sourceInSeconds"] == pytest.approx(2.75, abs=0.1)
    assert manifest["editTimeline"]["durationSeconds"] == pytest.approx(2.5, abs=0.1)
    assert manifest["technicalValidation"]["video"]["dimensions"] == "720x1280"
    quality = manifest["technicalValidation"]["qualityInspection"]
    assert quality["status"] in {"PASSED", "DEFECTS_DETECTED"}
    assert isinstance(quality["blackFrames"], int)
    assert isinstance(quality["frozenFrames"], int)
    assert manifest["technicalValidation"]["audio"]["sourceAudioUsed"] is True
    assert hashlib.sha256(output).hexdigest() == completed.output_sha256
    assert b"ftyp" in output[:16]
    decoded = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-xerror",
            "-i",
            "pipe:0",
            "-f",
            "null",
            "-",
        ],
        input=output,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert decoded.returncode == 0, decoded.stderr.decode(errors="replace")
    assert storage.read_clip_output(
        completed.output_key,
        completed.output_sha256,
    ) == output


def test_edit_timeline_renders_ordered_source_segments(tmp_path):
    storage = LocalTemporaryContentStore(tmp_path / "storage")
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory
    )
    fixture = tmp_path / "timeline-colors.mp4"
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
            "color=c=red:s=160x120:r=15:d=2",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x120:r=15:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000:duration=4",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-map",
            "2:a:0",
            "-c:v",
            "mpeg4",
            "-q:v",
            "8",
            "-c:a",
            "aac",
            "-shortest",
            str(fixture),
        ],
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert generated.returncode == 0, generated.stderr.decode(errors="replace")
    source = storage.stage_clip_source("timeline-order-test", fixture.read_bytes())
    output = storage.create_clip_staging_path("timeline-order-test")
    try:
        _, info = renderer.render_edit_timeline(
            source,
            segments=(
                SourceEditSegment(0.25, 1.25),
                SourceEditSegment(2.75, 3.75),
            ),
            staged_output=output,
        )
        quality = renderer.inspect_render_quality(output)
        assert quality["status"] in {"PASSED", "DEFECTS_DETECTED"}
        assert isinstance(quality["blackFrames"], int)
        assert isinstance(quality["frozenFrames"], int)
        colors = []
        for timestamp in (0.25, 1.25):
            sample = subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    str(timestamp),
                    "-i",
                    str(output),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=1:1",
                    "-pix_fmt",
                    "rgb24",
                    "-f",
                    "rawvideo",
                    "pipe:1",
                ],
                capture_output=True,
                check=False,
                timeout=10,
            )
            assert sample.returncode == 0, sample.stderr.decode(errors="replace")
            assert len(sample.stdout) == 3
            colors.append(tuple(sample.stdout))
    finally:
        storage.delete_clip_staging(source)
        storage.delete_clip_staging(output)

    assert info.duration_seconds == pytest.approx(2.0, abs=0.25)
    assert colors[0][0] > colors[0][2] * 2
    assert colors[1][2] > colors[1][0] * 2


def test_multi_source_render_preserves_out_of_order_non_overlapping_intervals(
    tmp_path,
):
    source_bytes = _source_video_bytes(tmp_path)
    storage = LocalTemporaryContentStore(tmp_path / "out-of-order-storage")
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory,
    )
    source = storage.stage_clip_source("out-of-order-source", source_bytes)
    output = storage.create_clip_staging_path("out-of-order-output")

    _, output_info = renderer.render_multi_source_timeline(
        {"source": source},
        segments=(
            SourceEditSegment(3.0, 4.0, "source"),
            SourceEditSegment(1.0, 2.0, "source"),
            SourceEditSegment(0.0, 1.0, "source"),
        ),
        staged_output=output,
    )
    colors = []
    for timestamp in (0.3, 1.3, 2.3):
        sample = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                str(timestamp),
                "-i",
                str(output),
                "-frames:v",
                "1",
                "-vf",
                "scale=1:1",
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "pipe:1",
            ],
            capture_output=True,
            check=False,
            timeout=10,
        )
        assert sample.returncode == 0, sample.stderr.decode(errors="replace")
        assert len(sample.stdout) == 3
        colors.append(tuple(sample.stdout))

    assert output_info.duration_seconds == pytest.approx(3.0, abs=0.1)
    assert colors[0][1] > colors[0][0] * 2
    assert colors[1][2] > colors[1][0] * 2
    assert colors[2][0] > colors[2][1] * 2


def test_render_quality_revision_removes_detected_leading_black_frames(tmp_path):
    storage = LocalTemporaryContentStore(tmp_path / "storage")
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory
    )
    fixture = tmp_path / "quality-revision-source.mp4"
    generated = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=black:s=160x120:r=30:d=1",
            "-f", "lavfi", "-i", "testsrc2=s=160x120:r=30:d=3",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-c:v", "mpeg4", "-q:v", "8", "-an",
            str(fixture),
        ],
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert generated.returncode == 0, generated.stderr.decode(errors="replace")
    staged_source = storage.stage_clip_source("quality-revision-test", fixture.read_bytes())
    output = storage.create_clip_staging_path("quality-revision-test")
    try:
        _, info = renderer.render(
            staged_source,
            start_seconds=0,
            end_seconds=4,
            staged_output=output,
            vertical=False,
        )
        quality = renderer.inspect_render_quality(output)
        assert quality["status"] == "PASSED"
        assert renderer.last_quality_revision_count == 1
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1", str(output),
            ],
            capture_output=True,
            check=False,
            timeout=10,
        )
        assert probe.returncode == 0
        assert float(probe.stdout) == pytest.approx(3.0, abs=0.25)

        clean_fixture = tmp_path / "quality-clean-source.mp4"
        clean = subprocess.run(
            [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc2=s=160x120:r=30:d=1",
                "-c:v", "mpeg4", "-q:v", "8", "-an", str(clean_fixture),
            ],
            capture_output=True,
            check=False,
            timeout=20,
        )
        assert clean.returncode == 0, clean.stderr.decode(errors="replace")
        clean_source = storage.stage_clip_source(
            "quality-clean-source", clean_fixture.read_bytes()
        )
        clean_output = storage.create_clip_staging_path("quality-clean-source")
        try:
            renderer.render(
                clean_source,
                start_seconds=0,
                end_seconds=1,
                staged_output=clean_output,
                vertical=False,
            )
            clean_quality = renderer.inspect_render_quality(clean_output)
            assert clean_quality["revisionCount"] == 0
        finally:
            storage.delete_clip_staging(clean_source)
            storage.delete_clip_staging(clean_output)
    finally:
        storage.delete_clip_staging(staged_source)
        storage.delete_clip_staging(output)


def test_authorized_clip_is_rendered_decoded_and_persisted(
    tmp_path,
    monkeypatch,
):
    h, asset, _, storage, transcript, candidate, rendering = _authorized_pipeline(
        tmp_path,
        monkeypatch,
    )
    use = _derivative_use(asset, candidate)
    queued = _create_render(h, rendering, candidate, use)
    assert queued.state == ProcessingJobState.QUEUED

    completed = _run_render(h, rendering, queued.render_id)
    assert completed.state == ProcessingJobState.SUCCEEDED
    assert completed.output_key is not None
    assert completed.output_sha256 is not None
    assert completed.output_byte_size is not None
    assert completed.transcript_id == transcript.transcript_id
    assert completed.asset_id == asset.asset_id
    assert completed.asset_sha256 == asset.content_sha256
    assert completed.candidate_id == candidate.candidate_id
    assert completed.start_seconds == candidate.start_seconds
    assert completed.end_seconds == candidate.end_seconds
    assert completed.source_duration_seconds == transcript.duration_seconds
    assert completed.output_duration_seconds is not None
    assert abs(completed.output_duration_seconds - candidate.duration_seconds) <= 0.75
    assert completed.has_audio is True
    assert completed.has_video is True
    assert completed.quality_output_sha256 == completed.output_sha256
    assert completed.quality_status == "PASSED"
    assert completed.quality_revision_count == 0

    payload = {"render_id": completed.render_id}
    clip = rendering.get_clip_output_bytes(
        completed.render_id,
        context=h.context("content_read_clip_render_output", payload),
    )
    assert len(clip) == completed.output_byte_size
    assert hashlib.sha256(clip).hexdigest() == completed.output_sha256
    assert storage.read_clip_output(completed.output_key, completed.output_sha256) == clip

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,width,height",
            "-of",
            "json",
            "pipe:0",
        ],
        input=clip,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert probe.returncode == 0
    info = json.loads(probe.stdout)
    assert [stream["codec_type"] for stream in info["streams"]] == ["video", "audio"]
    assert [stream["codec_name"] for stream in info["streams"]] == ["h264", "aac"]
    assert info["streams"][0]["width"] == 720
    assert info["streams"][0]["height"] == 1280
    assert abs(float(info["format"]["duration"]) - candidate.duration_seconds) <= 0.75

    manifest = rendering.get_clip_review_manifest(
        completed.render_id,
        context=h.context(
            "content_read_clip_review_manifest",
            {"render_id": completed.render_id},
        ),
    )
    assert manifest["previewOnly"] is True
    assert manifest["publication"] == {
        "status": "NOT_ATTEMPTED_BY_RENDER",
        "livePublishingEnabled": False,
    }
    assert manifest["humanReview"] == {
        "required": True,
        "status": "REQUIRED",
        "releaseApproval": "SEPARATE_WORKFLOW_NOT_IMPLIED_BY_RENDER",
    }
    assert manifest["technicalValidation"]["status"] == "PASSED"
    assert manifest["technicalValidation"]["qualityInspection"]["status"] == "PASSED"
    assert manifest["technicalValidation"]["qualityInspection"]["revisionCount"] == 0
    assert manifest["technicalValidation"]["outputSha256"] == completed.output_sha256
    assert manifest["technicalValidation"]["video"]["dimensions"] == "720x1280"
    assert manifest["provenance"]["rightsRevalidatedAtRender"] is True
    assert [
        segment["text"]
        for segment in manifest["transcript"]["segments"]
    ] == [
        segment.text
        for segment in transcript.segments
        if segment.segment_id in candidate.transcript_segment_ids
    ]
    assert not tuple((storage._root / ".staging").glob(".captions-*.ass"))

    decoded = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-xerror",
            "-i",
            "pipe:0",
            "-f",
            "null",
            "-",
        ],
        input=clip,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert decoded.returncode == 0
    assert not tuple((storage._root / ".staging").glob(".captions-*.ass"))
    assert [
        event.to_state
        for event in h.store.list_clip_render_events(queued.render_id)
    ] == [
        ProcessingJobState.QUEUED,
        ProcessingJobState.RUNNING,
        ProcessingJobState.SUCCEEDED,
    ]


def test_render_request_is_deduplicated_and_conflicting_reuse_is_rejected(
    tmp_path,
    monkeypatch,
):
    h, asset, _, _, _, candidate, rendering = _authorized_pipeline(
        tmp_path,
        monkeypatch,
    )
    use = _derivative_use(asset, candidate)
    first = _create_render(
        h,
        rendering,
        candidate,
        use,
        command_id="stable-render-command",
    )
    replay = _create_render(
        h,
        rendering,
        candidate,
        use,
        command_id="stable-render-command",
    )
    replay_with_new_command = _create_render(
        h,
        rendering,
        candidate,
        use,
        command_id="another-render-command",
    )
    assert replay.render_id == first.render_id
    assert replay_with_new_command.render_id == first.render_id
    assert h.store._connection.execute(
        "SELECT COUNT(*) FROM content_clip_renders"
    ).fetchone()[0] == 1

    wrong_use = use.model_copy(update={"territory": "CA"})
    with pytest.raises(PermissionError, match="Current rights"):
        _create_render(h, rendering, candidate, wrong_use)
    with pytest.raises(ValueError, match="different content"):
        _create_render(
            h,
            rendering,
            candidate,
            wrong_use,
            command_id="stable-render-command",
        )


def test_analysis_rights_alone_do_not_authorize_derivative_rendering(
    tmp_path,
    monkeypatch,
):
    h, asset, _, _, _, candidate, rendering = _authorized_pipeline(
        tmp_path,
        monkeypatch,
        rights_actions=frozenset({RightsAction.ANALYZE}),
    )
    with pytest.raises(PermissionError, match="Current rights"):
        _create_render(
            h,
            rendering,
            candidate,
            _derivative_use(asset, candidate),
        )
    assert h.store._connection.execute(
        "SELECT COUNT(*) FROM content_clip_renders"
    ).fetchone()[0] == 0


def test_successful_state_requires_output_metadata_and_valid_transition(
    tmp_path,
    monkeypatch,
):
    h, asset, _, _, _, candidate, rendering = _authorized_pipeline(
        tmp_path,
        monkeypatch,
    )
    queued = _create_render(
        h,
        rendering,
        candidate,
        _derivative_use(asset, candidate),
    )
    with pytest.raises(sqlite3.IntegrityError, match="invalid clip render state"):
        h.store._connection.execute(
            "UPDATE content_clip_renders SET state = ? WHERE render_id = ?",
            (ProcessingJobState.UNCERTAIN.value, queued.render_id),
        )
    h.store._connection.execute(
        "UPDATE content_clip_renders SET state = ? WHERE render_id = ?",
        (ProcessingJobState.RUNNING.value, queued.render_id),
    )
    with pytest.raises(sqlite3.IntegrityError, match="requires validated output"):
        h.store._connection.execute(
            "UPDATE content_clip_renders SET state = ? WHERE render_id = ?",
            (ProcessingJobState.SUCCEEDED.value, queued.render_id),
        )


def test_expired_or_revoked_derivative_rights_prevent_renderer_dispatch(
    tmp_path,
    monkeypatch,
):
    h, asset, grant, _, _, candidate, rendering = _authorized_pipeline(
        tmp_path,
        monkeypatch,
    )
    use = _derivative_use(asset, candidate)
    queued = _create_render(h, rendering, candidate, use)
    called = False

    def should_not_render(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("renderer dispatched without current rights")

    monkeypatch.setattr(rendering.renderer, "render", should_not_render)
    h.invoke(
        "content_revoke_rights_grant",
        {"grant_id": grant.grant_id, "reason": "revoked before render"},
        h.service.revoke_rights_grant,
        grant_id=grant.grant_id,
        reason="revoked before render",
    )
    with pytest.raises(PermissionError, match="pre-render authorization"):
        _run_render(h, rendering, queued.render_id)
    assert not called
    failed = h.store.get_clip_render(queued.render_id)
    assert failed is not None and failed.state == ProcessingJobState.FAILED
    assert failed.output_key is None


def test_expired_rights_are_revalidated_before_render(
    tmp_path,
    monkeypatch,
):
    h, asset, _, _, _, candidate, rendering = _authorized_pipeline(
        tmp_path,
        monkeypatch,
    )
    use = _derivative_use(asset, candidate)
    queued = _create_render(h, rendering, candidate, use)
    called = False

    def should_not_render(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("renderer dispatched with expired rights")

    monkeypatch.setattr(rendering.renderer, "render", should_not_render)

    class FutureDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(days=2)

    monkeypatch.setattr(
        "ultron.application.content.policy.datetime",
        FutureDateTime,
    )
    with pytest.raises(PermissionError, match="pre-render authorization"):
        _run_render(h, rendering, queued.render_id)
    assert not called
    failed = h.store.get_clip_render(queued.render_id)
    assert failed is not None and failed.state == ProcessingJobState.FAILED
    assert failed.output_key is None


def test_failed_render_cleans_partial_staging_and_is_not_success(
    tmp_path,
    monkeypatch,
):
    h, asset, _, storage, _, candidate, rendering = _authorized_pipeline(
        tmp_path,
        monkeypatch,
    )
    use = _derivative_use(asset, candidate)
    queued = _create_render(h, rendering, candidate, use)

    def fail_after_partial(
        content,
        *,
        start_seconds,
        end_seconds,
        staged_output,
        vertical,
        captions,
    ):
        staged_output.write_bytes(b"partial")
        raise RuntimeError("injected renderer failure")

    monkeypatch.setattr(rendering.renderer, "render", fail_after_partial)
    with pytest.raises(RuntimeError, match="injected renderer failure"):
        _run_render(h, rendering, queued.render_id)

    failed = h.store.get_clip_render(queued.render_id)
    assert failed is not None and failed.state == ProcessingJobState.FAILED
    assert failed.output_key is None
    assert not tuple((storage._root / ".staging").glob(".clip-*.mp4"))
    assert h.store._connection.execute(
        "SELECT COUNT(*) FROM content_clip_renders WHERE output_key IS NOT NULL"
    ).fetchone()[0] == 0


def test_renderer_rejects_malformed_unsupported_and_out_of_range_media(tmp_path):
    storage = LocalTemporaryContentStore(tmp_path / "storage")
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory
    )
    output = storage.create_clip_staging_path("test-render")
    source = storage.stage_clip_source("test-render", _wav_bytes(2.0))
    outside_source = tmp_path / "source.wav"
    outside_source.write_bytes(_wav_bytes(2.0))
    with pytest.raises(ValueError, match="managed clip staging"):
        renderer.inspect(outside_source)
    with pytest.raises(ValueError, match="finite and positive"):
        renderer.render(
            source,
            start_seconds=-1,
            end_seconds=1,
            staged_output=output,
        )
    with pytest.raises(ValueError, match="finite and positive"):
        renderer.render(
            source,
            start_seconds=1,
            end_seconds=1,
            staged_output=output,
        )
    with pytest.raises(ValueError, match="exceeds the configured duration limit"):
        renderer.render(
            source,
            start_seconds=0,
            end_seconds=61,
            staged_output=output,
        )
    with pytest.raises(ValueError, match="exceeds source media duration"):
        renderer.render(
            source,
            start_seconds=1,
            end_seconds=3,
            staged_output=output,
        )
    oversized = storage.stage_clip_source("oversized-render", b"small")
    oversized.write_bytes(b"x" * (storage.MAX_INPUT_BYTES + 1))
    with pytest.raises(ValueError, match="non-empty and at most 32 MiB"):
        renderer.inspect(oversized)
    unsupported = storage.stage_clip_source(
        "unsupported-render", b"unsupported media bytes"
    )
    with pytest.raises(ValueError, match="probe"):
        renderer.render(
            unsupported,
            start_seconds=0,
            end_seconds=1,
            staged_output=output,
        )


def test_vertical_renderer_requires_valid_transcript_captions(tmp_path):
    storage = LocalTemporaryContentStore(tmp_path / "storage")
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory
    )
    source = storage.stage_clip_source("vertical-caption-test", _wav_bytes(2.0))
    output = storage.create_clip_staging_path("vertical-caption-test")

    with pytest.raises(ValueError, match="requires transcript captions"):
        renderer.render(
            source,
            start_seconds=0,
            end_seconds=2,
            staged_output=output,
            vertical=True,
        )
    with pytest.raises(ValueError, match="timing or text is invalid"):
        renderer._write_captions(
            (RenderCaption(start_seconds=0, end_seconds=3, text="Out of range"),),
            start_seconds=0,
            end_seconds=2,
        )
    assert not tuple(storage.clip_staging_directory.glob(".captions-*.ass"))


def test_caption_layout_wraps_without_changing_words_and_rejects_overflow(tmp_path):
    storage = LocalTemporaryContentStore(tmp_path / "storage")
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory
    )
    original = (
        "A clear explanation makes difficult ideas easier to understand and "
        "apply in everyday life."
    )
    caption_path = renderer._write_captions(
        (RenderCaption(start_seconds=0, end_seconds=2, text=original),),
        start_seconds=0,
        end_seconds=2,
    )
    assert caption_path is not None
    dialogue = next(
        line
        for line in caption_path.read_text(encoding="utf-8").splitlines()
        if line.startswith("Dialogue: ")
    )
    wrapped_lines = dialogue.split(",", 9)[-1].split(r"\N")
    assert " ".join(wrapped_lines) == original
    assert all(
        len(line) <= renderer.MAX_CAPTION_LINE_CHARACTERS for line in wrapped_lines
    )
    measured_lines, maximum_width = renderer._caption_layout(original)
    assert len(measured_lines) <= renderer.MAX_CAPTION_LINES
    assert maximum_width <= renderer.CAPTION_SAFE_MAX_WIDTH
    caption_path.unlink()

    with pytest.raises(ValueError, match="readable layout limit"):
        renderer._write_captions(
            (
                RenderCaption(
                    start_seconds=0,
                    end_seconds=2,
                    text="word " * 80,
                ),
            ),
            start_seconds=0,
            end_seconds=2,
        )
    with pytest.raises(ValueError, match="timing or text is invalid"):
        renderer._write_captions(
            (
                RenderCaption(start_seconds=0, end_seconds=1, text="First"),
                RenderCaption(start_seconds=0.9, end_seconds=1.5, text="Overlap"),
            ),
            start_seconds=0,
            end_seconds=2,
        )
    with pytest.raises(ValueError, match="timing or text is invalid"):
        renderer._write_captions(
            (
                RenderCaption(
                    start_seconds=0.0001,
                    end_seconds=0.0002,
                    text="Below frame timing resolution",
                ),
            ),
            start_seconds=0,
            end_seconds=2,
        )
    assert not tuple(storage.clip_staging_directory.glob(".captions-*.ass"))


@pytest.mark.parametrize("scene_image", ["missing", "wrong_dimensions"])
def test_narrated_renderer_rejects_missing_or_malformed_scene_images(
    tmp_path,
    scene_image,
):
    staging = tmp_path / "staging"
    staging.mkdir()
    renderer = FFmpegLocalClipRenderer(managed_staging_directory=staging)
    script_id = "12345678-1234-5678-1234-567812345678"
    scene_path = staging / f".clip-script-{script_id}-scene-01-{'a' * 32}.png"
    output_path = staging / f".clip-script-{script_id}-output-{'b' * 32}.mp4"
    output_path.touch()
    if scene_image == "wrong_dimensions":
        Image.new("RGB", (10, 10)).save(scene_path, format="PNG")
    with pytest.raises(ValueError):
        renderer.render_narrated_script_preview(
            (scene_path,),
            (1.0,),
            staging / f".clip-script-{script_id}-audio-{'c' * 32}.wav",
            script_id=script_id,
            duration_seconds=1.0,
            captions=(RenderCaption(0.0, 1.0, "Scene caption"),),
            staged_output=output_path,
        )


def test_vertical_audio_clip_seeks_and_burns_relative_captions(tmp_path):
    storage = LocalTemporaryContentStore(tmp_path / "storage")
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory
    )
    source = storage.stage_clip_source("caption-seek-test", _tone_wav_bytes(4.0))
    output = storage.create_clip_staging_path("caption-seek-test")
    _, rendered = renderer.render(
        source,
        start_seconds=1,
        end_seconds=3,
        staged_output=output,
        vertical=True,
        captions=(
            RenderCaption(
                start_seconds=1,
                end_seconds=2,
                text="Caption appears in the first output second.",
            ),
        ),
    )
    assert rendered.has_video and rendered.has_audio
    assert renderer._audio_peak(output) > 0

    frame_hashes = []
    for timestamp in ("0.5", "1.5"):
        frame = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                timestamp,
                "-i",
                str(output),
                "-frames:v",
                "1",
                "-f",
                "framemd5",
                "-",
            ],
            capture_output=True,
            check=False,
            timeout=10,
        )
        assert frame.returncode == 0
        frame_hashes.append(
            next(line for line in frame.stdout.decode().splitlines() if not line.startswith("#"))
        )
    assert frame_hashes[0] != frame_hashes[1]

    def crop_pixels(timestamp: str, crop: str) -> bytes:
        frame = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                timestamp,
                "-i",
                str(output),
                "-vf",
                f"crop={crop}",
                "-frames:v",
                "1",
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "pipe:1",
            ],
            capture_output=True,
            check=False,
            timeout=10,
        )
        assert frame.returncode == 0
        return frame.stdout

    caption_region = crop_pixels("0.5", "592:360:64:900")
    top_safe_region = crop_pixels("0.5", "720:200:0:0")
    caption_bright_pixels = sum(
        1
        for red, green, blue in struct.iter_unpack("BBB", caption_region)
        if red > 220 and green > 220 and blue > 220
    )
    top_bright_pixels = sum(
        1
        for red, green, blue in struct.iter_unpack("BBB", top_safe_region)
        if red > 220 and green > 220 and blue > 220
    )
    assert caption_bright_pixels > 100
    assert top_bright_pixels == 0
    assert not tuple(storage.clip_staging_directory.glob(".captions-*.ass"))


def test_caption_staging_is_removed_when_ffmpeg_times_out(tmp_path, monkeypatch):
    storage = LocalTemporaryContentStore(tmp_path / "storage")
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory
    )
    source = storage.stage_clip_source("caption-timeout-test", _wav_bytes(2.0))
    output = storage.create_clip_staging_path("caption-timeout-test")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], timeout=1)

    monkeypatch.setattr(
        "ultron.application.content.clip_renderer.subprocess.run",
        timeout,
    )
    with pytest.raises(ClipRenderTimeout):
        renderer.render(
            source,
            start_seconds=0,
            end_seconds=2,
            staged_output=output,
            vertical=True,
            captions=(
                RenderCaption(
                    start_seconds=0,
                    end_seconds=1,
                    text="A caption with a valid interval.",
                ),
            ),
        )
    assert not tuple(storage.clip_staging_directory.glob(".captions-*.ass"))


def test_probe_output_is_bounded(tmp_path):
    storage = LocalTemporaryContentStore(tmp_path / "storage")
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory
    )
    with pytest.raises(ValueError, match="metadata exceeds its output limit"):
        renderer._run_bounded_stdout(
            [sys.executable, "-c", "print('x' * 100000)"],
            timeout=5,
            max_output_bytes=32,
        )


def test_interrupted_clip_render_recovers_uncertain_and_is_not_retried(
    tmp_path,
    monkeypatch,
):
    h, asset, _, storage, _, candidate, rendering = _authorized_pipeline(
        tmp_path,
        monkeypatch,
    )
    use = _derivative_use(asset, candidate)
    queued = _create_render(h, rendering, candidate, use)
    h.store._connection.execute(
        "UPDATE content_clip_renders SET state = ? WHERE render_id = ?",
        (ProcessingJobState.RUNNING.value, queued.render_id),
    )
    staged_source = storage.stage_clip_source(queued.render_id, _wav_bytes())
    staged_output = storage.create_clip_staging_path(queued.render_id)
    staged_output.write_bytes(b"render still in progress")
    orphan_key, _, _ = storage.put_clip_output(queued.render_id, staged_output)
    database = h.store.database_identity
    h.store.close()

    reopened = ContentStore(database)
    recovered = reopened.get_clip_render(queued.render_id)
    assert recovered is not None and recovered.state == ProcessingJobState.UNCERTAIN
    assert reopened.list_clip_render_events(queued.render_id)[-1].to_state == (
        ProcessingJobState.UNCERTAIN
    )
    rights = type(h.service)(
        store=reopened,
        authority=h.authority,
        reviewer_principals=h.service.reviewer_principals,
        content_storage=storage,
        processor=h.service.processor,
    )
    resumed = ContentClipRenderingService(content_rights=rights)
    rights.recover_processing_storage()
    assert staged_source.exists()
    assert staged_output.exists()
    assert (storage._root / "outputs" / orphan_key).exists()
    with pytest.raises(PermissionError, match="not available"):
        _run_render(h, resumed, queued.render_id)
    reopened.close()


def test_schema_five_migrates_additively_to_clip_render_schema(tmp_path, monkeypatch):
    h, asset, _, _ = _harness(tmp_path, monkeypatch)
    project = ContentEditProject(
        project_id="schema-five-plan-table-migration",
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script="Synthetic schema migration compatibility test.",
    )
    h.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        h.service.create_edit_project,
        command_id="create-schema-five-plan-table-migration",
        project=project,
    )
    segments = (
        ContentEditSegment(
            source_asset_id=asset.asset_id,
            source_in_seconds=0,
            source_out_seconds=2,
            timeline_order=1,
        ),
        ContentEditSegment(
            source_asset_id=asset.asset_id,
            source_in_seconds=4,
            source_out_seconds=6,
            timeline_order=2,
        ),
    )
    h.store.replace_edit_segments(project.project_id, "alice", segments)
    h.store._connection.execute(
        "UPDATE content_schema_meta SET schema_version = 5 WHERE singleton = 1"
    )
    database = h.store.database_identity
    h.store.close()

    reopened = ContentStore(database)
    assert reopened.get_asset(asset.asset_id) == asset
    assert reopened._connection.execute(
        "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
    ).fetchone()[0] == 7
    assert reopened._connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name = 'content_clip_renders'"
    ).fetchone() is not None
    assert reopened._connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name = 'content_edit_plan_revisions'"
    ).fetchone() is not None
    assert reopened.get_edit_project(project.project_id, "alice") == project
    assert reopened.get_edit_segments(project.project_id, "alice") == segments
    reopened.close()


def test_renderer_timeout_is_uncertain_not_automatically_retried(
    tmp_path,
    monkeypatch,
):
    h, asset, _, storage, _, candidate, rendering = _authorized_pipeline(
        tmp_path,
        monkeypatch,
    )
    use = _derivative_use(asset, candidate)
    queued = _create_render(h, rendering, candidate, use)

    def uncertain_render(*args, **kwargs):
        raise ClipRenderTimeout("injected renderer timeout")

    monkeypatch.setattr(rendering.renderer, "render", uncertain_render)
    with pytest.raises(ClipRenderTimeout):
        _run_render(h, rendering, queued.render_id)
    uncertain = h.store.get_clip_render(queued.render_id)
    assert uncertain is not None and uncertain.state == ProcessingJobState.UNCERTAIN
    assert uncertain.output_key is None
    assert not tuple((storage._root / ".staging").glob(".clip-*.mp4"))


def test_interrupted_render_is_uncertain_and_staging_is_cleaned(
    tmp_path,
    monkeypatch,
):
    h, asset, _, storage, _, candidate, rendering = _authorized_pipeline(
        tmp_path,
        monkeypatch,
    )
    queued = _create_render(
        h,
        rendering,
        candidate,
        _derivative_use(asset, candidate),
    )

    def interrupt_render(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(rendering.renderer, "render", interrupt_render)
    with pytest.raises(KeyboardInterrupt):
        _run_render(h, rendering, queued.render_id)

    interrupted = h.store.get_clip_render(queued.render_id)
    assert interrupted is not None
    assert interrupted.state == ProcessingJobState.UNCERTAIN
    assert not tuple((storage._root / ".staging").glob(".clip-*.mp4"))
    assert not tuple((storage._root / ".staging").glob(".clip-*.media"))


def test_source_video_audio_extraction_produces_bounded_pcm_wave(tmp_path):
    storage = LocalTemporaryContentStore(tmp_path / "storage")
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory
    )
    fixture = tmp_path / "authorized-extraction-fixture.mp4"
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
            "testsrc=size=160x120:rate=15:duration=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000:duration=4",
            "-c:v",
            "mpeg4",
            "-q:v",
            "8",
            "-c:a",
            "aac",
            "-b:a",
            "32k",
            "-shortest",
            str(fixture),
        ],
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert generated.returncode == 0, generated.stderr.decode(errors="replace")
    source = storage.stage_clip_source("audio-extract-test", fixture.read_bytes())
    try:
        info = renderer.inspect_source_video(source)
        audio = renderer.extract_pcm_audio(source)
    finally:
        storage.delete_clip_staging(source)

    with wave.open(BytesIO(audio), "rb") as extracted:
        assert extracted.getcomptype() == "NONE"
        assert extracted.getframerate() == 16_000
        assert extracted.getnchannels() == 1
        assert extracted.getsampwidth() == 2
        assert extracted.getnframes() / extracted.getframerate() == pytest.approx(4, abs=0.1)
    assert info.has_video and info.has_audio
    assert len(audio) <= renderer.MAX_EXTRACTED_AUDIO_BYTES


def test_scene_change_detection_finds_a_hard_cut(tmp_path):
    storage = LocalTemporaryContentStore(tmp_path / "storage")
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory
    )
    fixture = tmp_path / "hard-cut-fixture.mp4"
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
            "color=c=red:s=160x120:r=15:d=2",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x120:r=15:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000:duration=4",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-map",
            "2:a:0",
            "-c:v",
            "mpeg4",
            "-q:v",
            "8",
            "-c:a",
            "aac",
            "-shortest",
            str(fixture),
        ],
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert generated.returncode == 0, generated.stderr.decode(errors="replace")
    source = storage.stage_clip_source("hard-cut-test", fixture.read_bytes())
    try:
        cuts = renderer.detect_scene_changes(source)
    finally:
        storage.delete_clip_staging(source)

    assert len(cuts) == 1
    assert cuts[0] == pytest.approx(2.0, abs=0.15)


def test_scene_change_detection_accepts_video_only_source_and_finds_cuts(tmp_path):
    storage = LocalTemporaryContentStore(tmp_path / "storage")
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory,
    )
    source = storage.stage_clip_source(
        "video-only-scene-detection-test",
        _video_only_source_bytes(tmp_path, name="video-only-scene-detection"),
    )
    try:
        info = renderer.inspect_video_source(source)
        cuts = renderer.detect_scene_changes(source)
    finally:
        storage.delete_clip_staging(source)

    assert info.has_video
    assert not info.has_audio
    assert len(cuts) == 2
    assert cuts[0] == pytest.approx(1.0, abs=0.2)
    assert cuts[1] == pytest.approx(2.75, abs=0.2)


def test_video_only_source_remains_rejected_by_audio_extraction(tmp_path):
    storage = LocalTemporaryContentStore(tmp_path / "storage")
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory,
    )
    source = storage.stage_clip_source(
        "video-only-audio-extraction-test",
        _video_only_source_bytes(tmp_path, name="video-only-audio-extraction"),
    )
    try:
        with pytest.raises(ValueError, match="both video and audio"):
            renderer.inspect_source_video(source)
        with pytest.raises(ValueError, match="both video and audio"):
            renderer.extract_pcm_audio(source)
    finally:
        storage.delete_clip_staging(source)


def test_audio_only_media_is_not_accepted_as_source_footage(tmp_path):
    storage = LocalTemporaryContentStore(tmp_path / "storage")
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory
    )
    source = storage.stage_clip_source("audio-only-test", _tone_wav_bytes())
    try:
        with pytest.raises(ValueError, match="both video and audio"):
            renderer.inspect_source_video(source)
    finally:
        storage.delete_clip_staging(source)

    malformed = storage.stage_clip_source(
        "malformed-audio-test",
        b"not a supported media container",
    )
    try:
        with pytest.raises(ValueError, match="probe"):
            renderer.extract_pcm_audio(malformed)
    finally:
        storage.delete_clip_staging(malformed)


def test_audio_video_render_validates_stream_duration_and_sync(tmp_path):
    storage = LocalTemporaryContentStore(tmp_path / "storage")
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory
    )
    fixture = tmp_path / "authorized-audio-video-fixture.mp4"
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
            "testsrc=size=160x120:rate=15:duration=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000:duration=4",
            "-c:v",
            "mpeg4",
            "-q:v",
            "8",
            "-c:a",
            "aac",
            "-b:a",
            "32k",
            "-shortest",
            str(fixture),
        ],
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert generated.returncode == 0, generated.stderr.decode(errors="replace")
    source = fixture.read_bytes()
    assert len(source) <= 1024 * 1024
    source_path = storage.stage_clip_source("av-test", source)
    staged = storage.create_clip_staging_path("av-test")

    source_info, output_info = renderer.render(
        source_path,
        start_seconds=1,
        end_seconds=3,
        staged_output=staged,
    )
    assert source_info.has_video and source_info.has_audio
    assert output_info.has_video and output_info.has_audio
    assert abs(output_info.duration_seconds - 2.0) <= 0.75
    assert output_info.audio_duration_seconds is not None
    assert output_info.video_duration_seconds is not None
    assert abs(
        output_info.audio_duration_seconds - output_info.video_duration_seconds
    ) <= 0.25


def test_render_multi_source_timeline_applies_executable_style_profile(tmp_path):
    storage = LocalTemporaryContentStore(tmp_path / "storage")
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory
    )
    fixture = tmp_path / "style-profile-source.mp4"
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
            "testsrc=size=320x180:rate=12:duration=6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:sample_rate=16000:duration=6",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "32k",
            "-shortest",
            str(fixture),
        ],
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert generated.returncode == 0, generated.stderr.decode(errors="replace")
    source_path = storage.stage_clip_source("style-profile-source", fixture.read_bytes())
    staged_output = storage.create_clip_staging_path("style-profile-output")
    segments = (
        SourceEditSegment(source_in_seconds=0.0, source_out_seconds=2.0, source_asset_id="source-1"),
        SourceEditSegment(source_in_seconds=2.0, source_out_seconds=4.0, source_asset_id="source-1"),
    )
    source_infos, output_info = renderer.render_multi_source_timeline(
        {"source-1": source_path},
        segments=segments,
        staged_output=staged_output,
        style_id="futuristic_science",
    )
    assert "source-1" in source_infos
    assert output_info.has_video and output_info.has_audio
    assert output_info.duration_seconds > 0


def test_caption_timestamps_map_across_trimmed_segments_and_render_in_distinct_styles(
    tmp_path,
):
    storage = LocalTemporaryContentStore(tmp_path / "storage")
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory
    )
    fixture = tmp_path / "caption-timeline-source.mp4"
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
            "testsrc=size=320x180:rate=15:duration=6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000:duration=6",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "32k",
            "-shortest",
            str(fixture),
        ],
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert generated.returncode == 0, generated.stderr.decode(errors="replace")
    source = storage.stage_clip_source("caption-timeline-source", fixture.read_bytes())
    segments = (
        SourceEditSegment(0.0, 2.0, "source"),
        SourceEditSegment(4.0, 6.0, "source"),
    )
    source_caption = RenderCaption(
        start_seconds=0.5,
        end_seconds=1.1,
        text="Known phrase timing",
        presentation="cinematic_documentary",
        font_size=32,
        max_line_length=24,
        max_lines=2,
        safe_area_margin_px=72,
    )
    second_source_caption = RenderCaption(
        start_seconds=4.25,
        end_seconds=4.85,
        text="Trimmed segment caption",
        presentation="cinematic_documentary",
        font_size=32,
        max_line_length=24,
        max_lines=2,
        safe_area_margin_px=72,
    )
    first_mapped = _map_caption_to_output_timeline(
        source_caption,
        source_in_seconds=0.0,
        source_out_seconds=2.0,
        output_offset_seconds=0.0,
        require_full_interval=True,
    )
    second_mapped = _map_caption_to_output_timeline(
        second_source_caption,
        source_in_seconds=4.0,
        source_out_seconds=6.0,
        output_offset_seconds=2.0,
        require_full_interval=True,
    )
    assert first_mapped is not None
    assert (first_mapped.start_seconds, first_mapped.end_seconds) == pytest.approx(
        (0.5, 1.1)
    )
    assert second_mapped is not None
    assert (second_mapped.start_seconds, second_mapped.end_seconds) == pytest.approx(
        (2.25, 2.85)
    )
    assert (
        _map_caption_to_output_timeline(
            source_caption,
            source_in_seconds=1.2,
            source_out_seconds=2.0,
            output_offset_seconds=0.0,
            require_full_interval=False,
        )
        is None
    )

    def render(name, captions=()):
        output = storage.create_clip_staging_path(name)
        decorated_segments = (
            replace(
                segments[0],
                captions=tuple(captions[:1]),
            ),
            replace(
                segments[1],
                captions=tuple(captions[1:]),
            ),
        )
        _, info = renderer.render_multi_source_timeline(
            {"source": source},
            segments=decorated_segments,
            staged_output=output,
            vertical=True,
        )
        assert info.video_width == 720
        assert info.video_height == 1280
        assert info.video_codec == "h264"
        assert info.audio_codec == "aac"
        assert info.video_fps == pytest.approx(30, abs=0.1)
        assert info.video_duration_seconds == pytest.approx(4.0, abs=0.15)
        assert info.audio_duration_seconds is not None
        assert abs(info.audio_duration_seconds - info.video_duration_seconds) <= 0.25
        return output

    baseline = render("caption-baseline")
    cinematic = render("caption-cinematic", (source_caption, second_source_caption))
    viral_captions = (
        replace(
            source_caption,
            presentation="high_retention_viral",
            font_size=44,
            safe_area_margin_px=48,
            emphasis="strong",
            emphasis_text="phrase",
        ),
        replace(
            second_source_caption,
            presentation="high_retention_viral",
            font_size=44,
            safe_area_margin_px=48,
            emphasis="strong",
            emphasis_text="caption",
        ),
    )
    viral = render("caption-viral", viral_captions)

    def frame(path, timestamp, name):
        image_path = tmp_path / name
        result = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-ss",
                str(timestamp),
                "-frames:v",
                "1",
                "-y",
                str(image_path),
            ],
            capture_output=True,
            check=False,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        with Image.open(image_path) as image:
            return image.convert("RGB").copy()

    baseline_first = frame(baseline, 0.75, "caption-base-first.png")
    cinematic_first = frame(cinematic, 0.75, "caption-cinematic-first.png")
    viral_first = frame(viral, 0.75, "caption-viral-first.png")
    baseline_second = frame(baseline, 2.5, "caption-base-second.png")
    cinematic_second = frame(cinematic, 2.5, "caption-cinematic-second.png")
    crop = lambda image: image.crop((0, 850, 720, 1280))
    assert ImageChops.difference(
        crop(baseline_first),
        crop(cinematic_first),
    ).getbbox()
    assert ImageChops.difference(
        crop(baseline_second),
        crop(cinematic_second),
    ).getbbox()
    assert ImageChops.difference(
        crop(cinematic_first),
        crop(viral_first),
    ).getbbox()
    assert cinematic_first.size == (720, 1280)


def test_caption_and_word_directives_reject_invalid_times_and_layout():
    valid_caption = ContentEditPlanCaption(
        transcript_id="fixture-transcript",
        transcript_segment_ids=("fixture-segment",),
        start_seconds=0.5,
        end_seconds=1.0,
        text="Known words",
        presentation="educational_storytelling",
    )
    assert valid_caption.safe_area_margin_px >= 24
    with pytest.raises(ValueError):
        ContentEditPlanCaption(
            transcript_id="fixture-transcript",
            transcript_segment_ids=("fixture-segment",),
            start_seconds=1.0,
            end_seconds=1.0,
            text="Invalid timing",
        )
    with pytest.raises(ValueError):
        ContentEditPlanCaption(
            transcript_id="fixture-transcript",
            transcript_segment_ids=("fixture-segment",),
            start_seconds=0.5,
            end_seconds=1.0,
            text="Missing emphasis",
            emphasis="strong",
            emphasis_text="not present",
        )
    with pytest.raises(ValueError):
        ContentEditPlanCaption(
            transcript_id="fixture-transcript",
            transcript_segment_ids=("fixture-segment",),
            start_seconds=0.5,
            end_seconds=1.0,
            text="Unsafe margin",
            safe_area_margin_px=12,
        )
    with pytest.raises(ValueError):
        TranscriptWord(start_seconds=1.0, end_seconds=0.9, text="backwards")


def test_autoplan_applies_style_and_vertical_render_profile(tmp_path, monkeypatch):
    fixture = tmp_path / "autoplan-style-source.mp4"
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
            "testsrc=size=320x180:rate=12:duration=6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:sample_rate=16000:duration=6",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "32k",
            "-shortest",
            str(fixture),
        ],
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert generated.returncode == 0, generated.stderr.decode(errors="replace")
    harness, asset, _, storage = _harness(
        tmp_path,
        monkeypatch,
        media_bytes=fixture.read_bytes(),
        rights_actions=frozenset({RightsAction.ANALYZE, RightsAction.EDIT, RightsAction.CREATE_DERIVATIVE}),
    )
    _register_analysis_scope(harness, asset)
    renderer = FFmpegLocalClipRenderer(managed_staging_directory=storage.clip_staging_directory)
    rendering = ContentClipRenderingService(content_rights=harness.service, renderer=renderer)
    project = ContentEditProject(
        project_id="autoplan-style-plan",
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script="Create a futuristic science vertical short.",
    )
    harness.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        harness.service.create_edit_project,
        project=project,
        command_id="create-autoplan-style-plan",
    )
    result = harness.invoke(
        "content_autoplan_edit_project",
        {"project_id": project.project_id, "instruction_length": len(project.script)},
        rendering.autoplan_edit_project,
        project_id=project.project_id,
        instruction=project.script,
        command_id="plan-autoplan-style-plan",
    )
    plan = harness.store.get_latest_edit_plan(project.project_id, "alice")
    assert plan.render_style_id == "futuristic_science"
    assert plan.aspect_ratio == "9:16"
    assert result["styleId"] == "futuristic_science"


def _render_application_style_workflow(
    tmp_path,
    monkeypatch,
    *,
    instruction: str,
    style_id: str | None,
    output_name: str,
):
    source_path = Path("/home/ultron/Videos/CRE_Import/WhatsApp Video 2026-10-28 at 1.45.55 PM.mp4")
    source_bytes = source_path.read_bytes()
    monkeypatch.setattr(lifecycle, "MEDIA", source_bytes)
    monkeypatch.setattr(lifecycle, "MEDIA_SHA256", hashlib.sha256(source_bytes).hexdigest())

    harness = lifecycle.Harness(tmp_path / f"{output_name}.db")
    storage = LocalTemporaryContentStore(tmp_path / f"{output_name}-managed-content")
    harness.service = ContentRightsService(
        store=harness.store,
        authority=harness.authority,
        reviewer_principals=harness.service.reviewer_principals,
        content_storage=storage,
        processor=SyntheticContentProcessor(),
    )
    asset = lifecycle._register_asset(harness)
    _register_analysis_scope(harness, asset)
    _create_restricted_test_permit(harness, asset)
    project = ContentEditProject(
        project_id=f"{output_name}-project",
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script=instruction,
    )
    harness.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        harness.service.create_edit_project,
        project=project,
        command_id=f"create-{output_name}",
    )

    rendering = ContentClipRenderingService(content_rights=harness.service)
    plan_result = harness.invoke(
        "content_autoplan_edit_project",
        {"project_id": project.project_id, "instruction_length": len(instruction)},
        rendering.autoplan_edit_project,
        project_id=project.project_id,
        instruction=instruction,
        command_id=f"plan-{output_name}",
    )
    plan = harness.store.get_latest_edit_plan(project.project_id, "alice")
    assert plan is not None
    assert plan.render_style_id == style_id
    assert plan.aspect_ratio == "9:16"

    rights_use = lifecycle._rights_use(
        actions=frozenset({RightsAction.EDIT, RightsAction.CREATE_DERIVATIVE}),
    ).model_copy(
        update={
            "asset_id": asset.asset_id,
            "expected_asset_sha256": asset.content_sha256,
            "duration_seconds": math.ceil(plan.target_duration_seconds),
        }
    )
    render_result = harness.invoke(
        "content_render_edit_project",
        {"project_id": project.project_id, "asset_ids": [asset.asset_id], "vertical": True},
        rendering.render_edit_project,
        project_id=project.project_id,
        rights_uses={asset.asset_id: rights_use},
        vertical=True,
        command_id=f"render-{output_name}",
    )
    artifact_bytes = storage.read_clip_output(
        str(render_result["artifactId"]),
        str(render_result["sha256"]),
    )
    output_path = tmp_path / f"{output_name}.mp4"
    output_path.write_bytes(artifact_bytes)
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(output_path),
        ],
        capture_output=True,
        check=False,
        timeout=45,
    )
    assert probe.returncode == 0, probe.stderr.decode(errors="replace")
    media = json.loads(probe.stdout)
    video_streams = [stream for stream in media["streams"] if stream["codec_type"] == "video"]
    audio_streams = [stream for stream in media["streams"] if stream["codec_type"] == "audio"]
    assert len(video_streams) == 1
    assert len(audio_streams) == 1
    video = video_streams[0]
    audio = audio_streams[0]
    assert (int(video["width"]), int(video["height"])) == (720, 1280)
    assert abs(
        float(video.get("duration") or media["format"]["duration"]) - float(audio.get("duration") or media["format"]["duration"])
    ) <= 0.25
    return plan_result, render_result, output_path, media


def test_application_workflow_handles_generic_and_explicit_styles_and_generates_vertical_outputs(tmp_path, monkeypatch):
    generic_instruction = "Create a vertical short for social posting."
    generic_result, _, _, _ = _render_application_style_workflow(
        tmp_path,
        monkeypatch,
        instruction=generic_instruction,
        style_id=None,
        output_name="generic-vertical-short",
    )
    assert generic_result["styleId"] is None

    explicit_styles = (
        ("Make a cinematic documentary vertical short.", "cinematic_documentary", "cinematic-documentary"),
        ("Create a high-retention viral vertical short.", "high_retention_viral", "high-retention-viral"),
        ("Create a futuristic science vertical short.", "futuristic_science", "futuristic-science"),
    )
    outputs = []
    for instruction, expected_style, output_name in explicit_styles:
        outputs.append(
            _render_application_style_workflow(
                tmp_path,
                monkeypatch,
                instruction=instruction,
                style_id=expected_style,
                output_name=output_name,
            )
        )
    assert len(outputs) == 3
    assert {output[0]["styleId"] for output in outputs} == {
        "cinematic_documentary",
        "high_retention_viral",
        "futuristic_science",
    }


def test_graphic_decisions_require_emphasis_and_exact_transcript_evidence():
    transcript = TranscriptRecord(
        transcript_id="graphics-transcript",
        job_id="graphics-job",
        asset_id="graphics-source",
        asset_sha256=hashlib.sha256(b"graphics-source").hexdigest(),
        target_context=ContentTargetContext.YOUTUBE_SHORTS,
        duration_seconds=4.0,
        engine_id="fixture-engine",
        engine_version="1",
        raw_output_sha256="b" * 64,
        segments=(
            TranscriptSegment(
                segment_id="phrase-1",
                start_seconds=0.4,
                end_seconds=1.6,
                text="A documented phrase supports this explanation.",
                confidence=0.92,
            ),
        ),
    )
    selected = _graphic_directives_for_interval(
        transcript,
        source_asset_id="graphics-source",
        source_in_seconds=0.0,
        source_out_seconds=2.0,
        timeline_order=2,
        narrative_purpose="emphasis",
        style_id="educational_storytelling",
    )

    assert len(selected) == 1
    assert selected[0].content_text == transcript.segments[0].text
    assert selected[0].evidence_id == transcript.segments[0].segment_id
    assert selected[0].entry_animation == "slide_up"
    assert _graphic_directives_for_interval(
        None,
        source_asset_id="graphics-source",
        source_in_seconds=0.0,
        source_out_seconds=2.0,
        timeline_order=2,
        narrative_purpose="emphasis",
        style_id="educational_storytelling",
    ) == ()
    assert _graphic_directives_for_interval(
        transcript,
        source_asset_id="graphics-source",
        source_in_seconds=0.0,
        source_out_seconds=2.0,
        timeline_order=2,
        narrative_purpose="development",
        style_id="educational_storytelling",
    ) == ()


def test_persisted_graphics_render_animated_style_treatments_with_sync(tmp_path):
    storage = LocalTemporaryContentStore(tmp_path / "graphic-storage")
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory
    )
    fixture = tmp_path / "graphic-source.mp4"
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
            "testsrc=size=320x180:rate=15:duration=6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000:duration=6",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "32k",
            "-shortest",
            str(fixture),
        ],
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert generated.returncode == 0, generated.stderr.decode(errors="replace")
    source_bytes = fixture.read_bytes()
    asset = SimpleNamespace(
        asset_id="graphic-source",
        content_sha256=hashlib.sha256(source_bytes).hexdigest(),
    )
    source_path = storage.stage_clip_source("graphic-source", source_bytes)
    base_data = _structured_plan_v2("graphic-project", asset).model_dump(mode="json")
    base_data["schema_version"] = "cre-creative-plan-v3"
    base_data["render_style_id"] = "minimalist_storytelling"
    base_data["segments"][0]["motion"] = ContentEditPlanMotion(
        direction="slow_zoom_in",
        intensity=1.1,
        duration_seconds=1.2,
        easing="ease_out",
    ).model_dump(mode="json")
    base_data["segments"][0]["captions"] = [
        ContentEditPlanCaption(
            transcript_id="graphics-transcript",
            transcript_segment_ids=("transcript-segment-1",),
            start_seconds=0.65,
            end_seconds=1.1,
            text="A documented phrase",
            kind="phrase",
            presentation="educational_storytelling",
        ).model_dump(mode="json")
    ]
    base_data["segments"][1].update(
        {
            "transition_before": "cross_dissolve",
            "transition_duration_seconds": 0.2,
            "transition": ContentEditPlanTransition(
                kind="cross_dissolve",
                duration_seconds=0.2,
                from_timeline_order=1,
                to_timeline_order=2,
                decision_reason="Persisted transition compatibility fixture.",
                evidence=("observed_scene_boundary", "editing_style"),
            ).model_dump(mode="json"),
        }
    )
    evidence_text = "A documented phrase supports this explanation."
    treatments = (
        (
            "documentary",
            "contextual_label",
            "cinematic_documentary",
            "fade",
        ),
        (
            "viral",
            "animated_callout",
            "high_retention_viral",
            "scale",
        ),
        (
            "educational",
            "educational_overlay",
            "educational_storytelling",
            "slide_up",
        ),
    )
    rendered_regions: dict[str, Image.Image] = {}
    plans_by_name: dict[str, ContentCreativeEditPlanV3] = {}

    def frame_at(path: Path, timestamp: float) -> Image.Image:
        decoded = subprocess.run(
            [
                renderer.ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "pipe:1",
            ],
            capture_output=True,
            check=False,
            timeout=20,
        )
        assert decoded.returncode == 0, decoded.stderr.decode(errors="replace")
        assert len(decoded.stdout) == 720 * 1280 * 3
        image = Image.frombytes("RGB", (720, 1280), decoded.stdout)
        return image.crop((40, 100, 680, 360))

    baseline_data = dict(base_data)
    baseline_data["segments"] = [
        dict(segment) for segment in base_data["segments"]
    ]
    baseline = ContentCreativeEditPlanV3.model_validate(baseline_data)
    baseline_path = storage.create_clip_staging_path("graphics-baseline")
    _, baseline_info = renderer.render_creative_edit_plan(
        {"graphic-source": source_path},
        plan=baseline,
        staged_output=baseline_path,
        vertical=True,
    )
    baseline_region = frame_at(baseline_path, 0.95)

    for name, graphic_type, profile, animation in treatments:
        plan_data = dict(base_data)
        plan_data["segments"] = [
            dict(segment) for segment in base_data["segments"]
        ]
        graphic = ContentEditPlanGraphic(
            graphic_type=graphic_type,
            segment_order=1,
            start_seconds=0.4,
            end_seconds=1.6,
            x=0.08,
            y=0.1,
            width=0.84,
            height=0.16,
            visual_hierarchy="primary",
            typography_profile=profile,
            entry_animation=animation,
            exit_animation="fade",
            entry_duration_seconds=0.3,
            exit_duration_seconds=0.2,
            easing="ease_out",
            font_size=32,
            content_text=evidence_text,
            evidence_transcript_id="graphics-transcript",
            evidence_id="transcript-segment-1",
            evidence_asset_id="graphic-source",
            evidence_text=evidence_text,
            editorial_rationale="Persisted verbatim phrase used for this visual treatment.",
        )
        plan_data["segments"][0]["graphics"] = [
            graphic.model_dump(mode="json")
        ]
        plan = ContentCreativeEditPlanV3.model_validate(plan_data)
        output = storage.create_clip_staging_path(f"graphics-{name}")
        _, info = renderer.render_creative_edit_plan(
            {"graphic-source": source_path},
            plan=plan,
            staged_output=output,
            vertical=True,
        )
        assert info.video_width == 720
        assert info.video_height == 1280
        assert info.video_codec == "h264"
        assert info.audio_codec == "aac"
        assert info.video_fps == pytest.approx(30, abs=0.1)
        assert info.video_duration_seconds == pytest.approx(3.8, abs=0.15)
        assert info.audio_duration_seconds is not None
        assert abs(info.audio_duration_seconds - info.video_duration_seconds) <= 0.25
        early_region = frame_at(output, 0.45)
        settled_region = frame_at(output, 0.95)
        assert ImageChops.difference(early_region, settled_region).getbbox() is not None
        assert ImageChops.difference(baseline_region, settled_region).getbbox() is not None
        rendered_regions[name] = settled_region
        plans_by_name[name] = plan

    repeat_path = storage.create_clip_staging_path("graphics-documentary-repeat")
    _, repeat_info = renderer.render_creative_edit_plan(
        {"graphic-source": source_path},
        plan=plans_by_name["documentary"],
        staged_output=repeat_path,
        vertical=True,
    )
    assert repeat_info.video_duration_seconds == pytest.approx(3.8, abs=0.15)
    assert frame_at(repeat_path, 0.95).tobytes() == rendered_regions[
        "documentary"
    ].tobytes()

    assert baseline_info.video_width == 720
    assert len(
        {
            hashlib.sha256(region.tobytes()).hexdigest()
            for region in rendered_regions.values()
        }
    ) == 3
