from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ultron.application.content.processing import LocalTemporaryContentStore
from ultron.application.content.production import (
    ContentMediaProductionService,
    FFmpegSceneCardRenderer,
    NarrationUnavailable,
)
from ultron.application.content.service import ContentRightsService
from ultron.application.content.status import (
    ContentOwnerMapping,
    ContentStatusService,
    create_content_runtime,
)
from ultron.application.content.store import ContentStore
from ultron.application.content.workflow import ContentWorkflowRuntime
from ultron.application.content.distribution import DryRunPlatformAdapter
from ultron.domain.content_distribution import DistributionPlatform
from ultron.domain.content_production import MediaProductionState
from ultron.domain.content_strategy import ProductionReadiness
from ultron.security.identity.authenticator import WebSocketAuthenticator
from tests.application.test_content_package_e2e import (
    _complete_with_provider,
    _pending_package,
)


class _DeterministicRenderer:
    timeout_seconds = 30

    def render(self, plan, staged_output):
        staged_output.write_bytes(b"deterministic test media")
        return {"name": "deterministic-test-renderer", "version": "1"}

    def validate(self, plan, staged_output):
        return {
            "passed": True,
            "results": (
                {"id": "test_media", "passed": True},
                {"id": "scene_sequence", "passed": True},
            ),
            "failure_reasons": (),
            "duration_seconds": sum(scene.duration_seconds for scene in plan.scenes),
            "width": plan.rendering_configuration["width"],
            "height": plan.rendering_configuration["height"],
            "video_codec": "h264",
        }

    def renderer_information(self):
        return {"name": "deterministic-test-renderer", "version": "1"}


def _production_service(tmp_path, *, real_renderer=False):
    database = tmp_path / "media-production.db"
    store, opportunity, workflow, pending, angle = _pending_package(database)
    package = _complete_with_provider(
        store,
        opportunity,
        workflow,
        pending,
        angle,
        classification="fictional",
    )
    storage = LocalTemporaryContentStore(tmp_path / "media")
    renderer = (
        FFmpegSceneCardRenderer(storage, timeout_seconds=180)
        if real_renderer
        else _DeterministicRenderer()
    )
    return database, store, package, storage, renderer


def test_media_production_persists_and_recovers_exact_script_artifact(tmp_path):
    database, store, package, storage, renderer = _production_service(tmp_path)
    service = ContentMediaProductionService(store, storage, renderer)
    plan = service.create_plan(package.package_id)

    assert plan.state.value == "planned"
    assert plan.script_artifact_id == package.script_artifact_id
    assert plan.script_version == package.script_artifact_version
    assert len(plan.scenes) >= 3
    assert tuple(scene.sequence for scene in plan.scenes) == tuple(
        range(1, len(plan.scenes) + 1)
    )
    assert plan.asset_references
    assert all(
        "scene card" in asset["label"].lower()
        for asset in store.list_media_scene_assets(plan.job_id)
    )
    assert store.list_content_workflow_tasks()[-1].task_type == "render_media_package"

    same_plan = service.create_plan(package.package_id)
    assert same_plan.job_id == plan.job_id
    artifact = service.execute(plan.job_id)
    assert service.execute(plan.job_id) == artifact
    assert artifact.validation_passed is True
    assert artifact.script_sha256 == plan.script_sha256
    assert artifact.output_sha256 == hashlib.sha256(
        storage.read_clip_output(
            artifact.storage_reference.rsplit("/", 1)[-1],
            artifact.output_sha256,
        )
    ).hexdigest()
    assert store.get_media_validation_report_for_job(plan.job_id).passed
    assert store.get_media_production_plan(plan.job_id).state.value == "completed"
    assert [event["state"] for event in store.list_media_production_stage_events(plan.job_id)][
        -3:
    ] == ["rendering", "validating", "completed"]

    store.close()
    reopened = ContentStore(database)
    recovered_plan = reopened.get_media_production_plan(plan.job_id)
    recovered_artifact = reopened.get_media_artifact(artifact.artifact_id)
    assert recovered_plan is not None and recovered_plan.state.value == "completed"
    assert recovered_artifact == artifact
    assert storage.read_clip_output(
        recovered_artifact.storage_reference.rsplit("/", 1)[-1],
        recovered_artifact.output_sha256,
    )
    reopened.close()


def test_media_production_worker_uses_existing_permission_gated_runtime(tmp_path):
    _, store, package, storage, renderer = _production_service(tmp_path)
    service = ContentMediaProductionService(store, storage, renderer)
    plan = service.create_plan(package.package_id)
    runtime = ContentWorkflowRuntime(
        store,
        worker_id="media-worker",
        poll_interval_seconds=0.01,
        permissions=frozenset({"content.produce"}),
        media_production_service=service,
    )

    assert runtime.enabled is False
    assert "render_media_package" in runtime.status()["handlers"]
    runtime.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            task = store.get_content_workflow_task(plan.task_id)
            if task.state in {"completed", "failed"}:
                break
            time.sleep(0.01)
        assert store.get_content_workflow_task(plan.task_id).state == "completed"
        assert store.get_media_production_plan(plan.job_id).state.value == "completed"
    finally:
        runtime.stop()
        store.close()


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="Local FFmpeg and ffprobe are required for the runtime integration test.",
)
def test_content_runtime_factory_renders_and_recovers_managed_media(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "cre.db"
    store, opportunity, workflow, pending, angle = _pending_package(database)
    package = _complete_with_provider(
        store,
        opportunity,
        workflow,
        pending,
        angle,
        classification="fictional",
    )
    script_artifact = store.get_script_artifact(package.script_artifact_id)
    assert script_artifact is not None
    store.migrate_publishing_attempt_schema()
    store.migrate_private_render_operation_schema()
    store.close()
    monkeypatch.delenv("ULTRON_CRE_WORKER_ENABLED", raising=False)
    monkeypatch.delenv("ULTRON_CRE_PUBLISHING_DRY_RUN_ENABLED", raising=False)
    authenticator = WebSocketAuthenticator("T" * 43, principal="operator")
    managed_root = database.parent / "ultron_cre_content"

    runtime = create_content_runtime(authenticator, database)
    assert runtime.workflow_runtime is None
    assert runtime.youtube_publishing is None
    assert runtime.youtube_oauth is None
    assert runtime.publication_preparation.store is runtime.store
    assert (
        runtime.status.for_principal("operator")["publishing"]["publicDispatchEnabled"]
        is False
    )
    assert runtime.content_storage.clip_staging_directory.parent == managed_root.resolve()
    assert runtime.media_production.narration_status()["available"] is False
    plan = runtime.media_production.create_plan(
        package.package_id,
        configuration={
            "width": 180,
            "height": 320,
            "frame_rate": 5,
            "aspect_ratio": "9:16",
            "scene_duration_seconds": 0.5,
        },
    )
    subtitles_before_render = runtime.store.list_media_subtitle_artifacts(plan.job_id)
    assert {item.subtitle_format for item in subtitles_before_render} == {"srt", "ass"}
    assert all(
        item.script_artifact_id == plan.script_artifact_id
        and item.script_version == plan.script_version
        and item.script_sha256 == plan.script_sha256
        for item in subtitles_before_render
    )
    artifact = runtime.media_production.execute(plan.job_id)
    report = runtime.store.get_media_validation_report_for_job(plan.job_id)
    assert artifact.validation_passed is True
    assert report is not None and report.passed
    assert artifact.script_artifact_id == plan.script_artifact_id == package.script_artifact_id
    assert artifact.script_version == plan.script_version == package.script_artifact_version
    assert artifact.script_sha256 == plan.script_sha256 == script_artifact.content_sha256
    media_path = Path(artifact.storage_reference)
    assert media_path.is_file()
    assert media_path.parent == (managed_root / "outputs").resolve()
    assert hashlib.sha256(media_path.read_bytes()).hexdigest() == artifact.output_sha256
    assert artifact.audio_metadata["track"] is None
    probe = subprocess.run(
        [
            shutil.which("ffprobe") or "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=format_name,duration,size:stream=codec_type,codec_name,width,height,r_frame_rate",
            "-of",
            "json",
            str(media_path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    probe_data = json.loads(probe.stdout)
    video_stream = next(
        stream for stream in probe_data["streams"] if stream["codec_type"] == "video"
    )
    assert probe_data["format"]["format_name"].startswith("mov")
    assert int(probe_data["format"]["size"]) == artifact.file_size
    assert video_stream["codec_name"] == "h264"
    assert (video_stream["width"], video_stream["height"]) == (180, 320)
    assert video_stream["r_frame_rate"] == "5/1"
    assert not any(stream["codec_type"] == "audio" for stream in probe_data["streams"])
    for subtitle in subtitles_before_render:
        subtitle_path = Path(subtitle.storage_reference)
        assert subtitle_path.is_file()
        assert subtitle_path.parent == (managed_root / "outputs").resolve()
        assert runtime.content_storage.read_output(
            subtitle_path.name,
            subtitle.content_sha256,
            max_bytes=runtime.content_storage.MAX_CLIP_OUTPUT_BYTES,
        )
    duplicate = runtime.media_production.execute(plan.job_id)
    assert duplicate == artifact
    runtime.close()

    monkeypatch.setenv("ULTRON_CRE_PUBLISHING_DRY_RUN_ENABLED", "true")
    reopened = create_content_runtime(authenticator, database)
    try:
        assert reopened.workflow_runtime is None
        assert type(
            reopened.publication_preparation.adapters.get(
                DistributionPlatform.YOUTUBE
            )
        ) is DryRunPlatformAdapter
        recovered_plan = reopened.store.get_media_production_plan(plan.job_id)
        recovered_job = reopened.store.get_content_workflow_task(plan.task_id)
        recovered_artifact = reopened.store.get_media_artifact(artifact.artifact_id)
        recovered_subtitles = reopened.store.list_media_subtitle_artifacts(plan.job_id)
        recovered_report = reopened.store.get_media_validation_report_for_job(plan.job_id)
        assert recovered_plan is not None
        assert recovered_plan.state is MediaProductionState.COMPLETED
        assert recovered_job is not None and recovered_job.state == "pending"
        assert recovered_artifact == artifact
        assert recovered_artifact.script_artifact_id == package.script_artifact_id
        assert recovered_artifact.script_version == package.script_artifact_version
        assert recovered_artifact.script_sha256 == script_artifact.content_sha256
        assert recovered_report is not None and recovered_report.passed
        assert recovered_subtitles == subtitles_before_render
        assert all(
            item.script_artifact_id == recovered_plan.script_artifact_id
            and item.script_version == recovered_plan.script_version
            and item.script_sha256 == recovered_plan.script_sha256
            for item in recovered_subtitles
        )
        recovered_bytes = reopened.content_storage.read_clip_output(
            media_path.name,
            recovered_artifact.output_sha256,
        )
        assert hashlib.sha256(recovered_bytes).hexdigest() == recovered_artifact.output_sha256
        assert all(Path(item.storage_reference).is_file() for item in recovered_subtitles)
        decoded = subprocess.run(
            [
                shutil.which("ffmpeg") or "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-v",
                "error",
                "-xerror",
                "-i",
                str(media_path),
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert decoded.returncode == 0, decoded.stderr
        assert reopened.media_production.execute(plan.job_id) == recovered_artifact
        assert reopened.store.get_media_artifact_for_job(plan.job_id) == recovered_artifact
    finally:
        reopened.close()


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="Local FFmpeg and ffprobe are required for the worker integration test.",
)
@pytest.mark.parametrize(
    ("permissions", "expected_state"),
    ((None, "failed"), ("content.produce", "completed")),
)
def test_content_runtime_worker_enforces_media_permission(
    tmp_path,
    monkeypatch,
    permissions,
    expected_state,
):
    database = tmp_path / "cre.db"
    store, opportunity, workflow, pending, angle = _pending_package(database)
    package = _complete_with_provider(
        store,
        opportunity,
        workflow,
        pending,
        angle,
        classification="fictional",
    )
    store.migrate_publishing_attempt_schema()
    store.migrate_private_render_operation_schema()
    store.close()
    monkeypatch.setenv("ULTRON_CRE_WORKER_ENABLED", "true")
    monkeypatch.setenv("ULTRON_CRE_WORKER_POLL_SECONDS", "0.01")
    monkeypatch.setenv("ULTRON_CRE_WORKER_PERMISSIONS", permissions or "")
    authenticator = WebSocketAuthenticator("T" * 43, principal="operator")
    runtime = create_content_runtime(authenticator, database)
    try:
        worker = runtime.workflow_runtime
        assert worker is not None and worker.enabled
        assert "render_media_package" in worker.status()["handlers"]
        plan = runtime.media_production.create_plan(
            package.package_id,
            configuration={
                "width": 180,
                "height": 320,
                "frame_rate": 5,
                "aspect_ratio": "9:16",
                "scene_duration_seconds": 0.25,
            },
        )
        deadline = time.monotonic() + 30
        task = runtime.store.get_content_workflow_task(plan.task_id)
        while task.state not in {"completed", "failed"} and time.monotonic() < deadline:
            time.sleep(0.02)
            task = runtime.store.get_content_workflow_task(plan.task_id)
        assert task.state == expected_state
        if expected_state == "failed":
            assert runtime.store.get_media_artifact_for_job(plan.job_id) is None
            assert runtime.store.get_media_production_plan(plan.job_id).state is MediaProductionState.PLANNED
        else:
            artifact = runtime.store.get_media_artifact_for_job(plan.job_id)
            assert artifact is not None and artifact.validation_passed
            assert Path(artifact.storage_reference).is_file()
    finally:
        runtime.close()


def test_media_production_rejects_missing_or_ineligible_package(tmp_path):
    _, store, package, storage, renderer = _production_service(tmp_path)
    service = ContentMediaProductionService(store, storage, renderer)

    with pytest.raises(ValueError, match="existing content package"):
        service.create_plan("not-a-package")

    incomplete = package.model_copy(
        update={"readiness": ProductionReadiness.NEEDS_FACTUAL_REVIEW}
    )
    store.save_content_package(incomplete)
    with pytest.raises(ValueError, match="production-ready"):
        service.create_plan(package.package_id)
    store.close()


def test_media_production_rejects_missing_script_and_invalid_script_hash(
    tmp_path,
    monkeypatch,
):
    _, store, package, storage, renderer = _production_service(tmp_path)
    service = ContentMediaProductionService(store, storage, renderer)
    original_get = store.get_script_artifact
    monkeypatch.setattr(store, "get_script_artifact", lambda _artifact_id: None)
    with pytest.raises(ValueError, match="missing or has an invalid hash"):
        service.create_plan(package.package_id)

    artifact = original_get(package.script_artifact_id)
    monkeypatch.setattr(
        store,
        "get_script_artifact",
        lambda _artifact_id: artifact.model_copy(
            update={"content_sha256": "0" * 64}
        ),
    )
    with pytest.raises(ValueError, match="invalid hash"):
        service.create_plan(package.package_id)
    store.close()


def test_script_revision_creates_a_distinct_production_identity(tmp_path):
    _, store, package, storage, renderer = _production_service(tmp_path)
    service = ContentMediaProductionService(store, storage, renderer)
    old_plan = service.create_plan(package.package_id)
    old_script = store.get_script_artifact(package.script_artifact_id)
    new_content = json.dumps(
        {
            **json.loads(old_script.content),
            "closing": "A revised close.",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    new_script = store.create_script_artifact(
        artifact_id="revised-script-artifact",
        workflow_id=package.workflow_id,
        content=new_content,
        provenance={"source": "test"},
        state="approved",
    )
    revised_package = package.model_copy(
        update={
            "script_artifact_id": new_script.artifact_id,
            "script_artifact_version": new_script.version,
            "script_artifact_sha256": new_script.content_sha256,
        }
    )
    store.save_content_package(revised_package)
    readiness = store.get_latest_readiness_decision(package.package_id)
    store.save_readiness_decision(
        readiness.model_copy(
            update={
                "decision_id": "revised-readiness-decision",
                "script_artifact_id": new_script.artifact_id,
                "script_artifact_version": new_script.version,
                "script_artifact_sha256": new_script.content_sha256,
                "decided_at": datetime.now(UTC),
            }
        )
    )

    new_plan = service.create_plan(package.package_id)
    assert new_plan.job_id != old_plan.job_id
    assert new_plan.script_version == old_plan.script_version + 1
    assert store.get_media_production_plan(old_plan.job_id) == old_plan
    store.close()


@pytest.mark.parametrize(
    ("profile", "width", "height", "frame_rate"),
    (
        ("vertical", 1080, 1920, 30),
        ("landscape", 1920, 1080, 30),
        ("square", 1080, 1080, 30),
        ("preview", 360, 640, 15),
    ),
)
def test_rendering_profiles_apply_expected_dimensions_and_frame_rates(
    tmp_path,
    profile,
    width,
    height,
    frame_rate,
):
    _, store, package, storage, renderer = _production_service(tmp_path)
    service = ContentMediaProductionService(store, storage, renderer)
    plan = service.create_plan(
        package.package_id,
        configuration={
            "profile": profile,
            "scene_duration_seconds": 1.0,
            "video_bitrate": "2M",
            "quality": 23,
            "pixel_format": "yuv420p",
            "container": "matroska",
            "transition": "fade",
            "transition_seconds": 0.5,
            "audio_bitrate": "96k",
            "audio_sample_rate": 44100,
        },
    )
    assert plan.rendering_configuration["profile"] == profile
    assert plan.rendering_configuration["width"] == width
    assert plan.rendering_configuration["height"] == height
    assert plan.rendering_configuration["frame_rate"] == frame_rate
    assert plan.rendering_configuration["video_bitrate"] == "2M"
    assert plan.rendering_configuration["quality"] == 23
    assert plan.rendering_configuration["container"] == "matroska"
    assert plan.rendering_configuration["transition_seconds"] == 0.5
    assert all(scene.duration_seconds == 1.0 for scene in plan.scenes)
    store.close()


def test_subtitles_are_timed_persisted_bound_and_retrievable(tmp_path):
    database, store, package, storage, renderer = _production_service(tmp_path)
    service = ContentMediaProductionService(store, storage, renderer)
    plan = service.create_plan(package.package_id)
    subtitle_artifacts = store.list_media_subtitle_artifacts(plan.job_id)

    assert {artifact.subtitle_format for artifact in subtitle_artifacts} == {"srt", "ass"}
    assert all(
        artifact.script_artifact_id == plan.script_artifact_id
        and artifact.script_version == plan.script_version
        and artifact.script_sha256 == plan.script_sha256
        and artifact.timing_method == "scene-duration-weighted-text-chunks-v1"
        for artifact in subtitle_artifacts
    )
    assert {
        Path(artifact.storage_reference).suffix for artifact in subtitle_artifacts
    } == {".srt", ".ass"}
    assert all(
        segment.end_seconds > segment.start_seconds
        for segment in plan.subtitle_segments
    )
    assert tuple(segment.sequence for segment in plan.subtitle_segments) == tuple(
        range(1, len(plan.subtitle_segments) + 1)
    )
    srt = next(item for item in subtitle_artifacts if item.subtitle_format == "srt")
    ass = next(item for item in subtitle_artifacts if item.subtitle_format == "ass")
    srt_content = storage.read_output(
        Path(srt.storage_reference).name,
        srt.content_sha256,
        max_bytes=storage.MAX_CLIP_OUTPUT_BYTES,
    ).decode()
    ass_content = storage.read_output(
        Path(ass.storage_reference).name,
        ass.content_sha256,
        max_bytes=storage.MAX_CLIP_OUTPUT_BYTES,
    ).decode()
    assert " --> " in srt_content
    assert "[Events]" in ass_content and "Dialogue:" in ass_content

    broken_segment = plan.subtitle_segments[0].model_copy(
        update={"end_seconds": plan.subtitle_segments[0].start_seconds}
    )
    with pytest.raises(ValueError, match="ordering or duration"):
        store.create_media_subtitle_artifact(
            srt.model_copy(
                update={
                    "segments": (broken_segment, *plan.subtitle_segments[1:]),
                    "artifact_id": "broken-subtitle-timing",
                }
            )
        )

    store.close()
    reopened = ContentStore(database)
    recovered = reopened.list_media_subtitle_artifacts(plan.job_id)
    assert recovered == subtitle_artifacts
    assert all(Path(item.storage_reference).is_file() for item in recovered)
    reopened.close()


def test_narration_is_explicitly_unavailable_and_required_audio_is_rejected(tmp_path):
    _, store, package, storage, renderer = _production_service(tmp_path)
    service = ContentMediaProductionService(store, storage, renderer)
    plan = service.create_plan(package.package_id)

    status = service.narration_status()
    assert status["available"] is False
    assert status["provider"] is None
    assert "remote service" in status["reason"]
    with pytest.raises(NarrationUnavailable, match="active application environment lacks"):
        service.generate_narration(plan.job_id, voice="en-US-test")
    with pytest.raises(NarrationUnavailable, match="narration_enabled=false"):
        service.create_plan(
            package.package_id,
            configuration={"narration_enabled": True, "voice": "en-US-test"},
        )
    with pytest.raises(ValueError, match="Required audio"):
        service.create_plan(
            package.package_id,
            configuration={"audio_required": True},
        )
    store.close()


def test_read_only_content_status_projects_persisted_media_production(tmp_path):
    _, store, package, storage, renderer = _production_service(tmp_path)
    service = ContentMediaProductionService(store, storage, renderer)
    plan = service.create_plan(package.package_id)
    token = "T" * 43
    authority = WebSocketAuthenticator(token, principal="operator")
    status_service = ContentStatusService(
        content_rights=ContentRightsService(store=store, authority=authority),
        owner_mapping=ContentOwnerMapping(principal="operator", owner_id="operator"),
    )

    projected = status_service.for_principal("operator")
    package_projection = next(
        item
        for item in projected["services"]["contentPackages"]
        if item["packageId"] == package.package_id
    )
    production_job = package_projection["productionJobs"][0]
    assert production_job["jobId"] == plan.job_id
    assert production_job["state"] == "planned"
    assert production_job["currentStage"] == "planned"
    assert production_job["outputArtifactReference"] is None
    store.close()


def test_persisted_artifact_hash_mismatch_is_marked_uncertain(tmp_path):
    _, store, package, storage, renderer = _production_service(tmp_path)
    service = ContentMediaProductionService(store, storage, renderer)
    plan = service.create_plan(package.package_id)
    artifact = service.execute(plan.job_id)
    Path(artifact.storage_reference).write_bytes(b"tampered test artifact")

    with pytest.raises(ValueError, match="SHA-256"):
        service.execute(plan.job_id)
    uncertain = store.get_media_production_plan(plan.job_id)
    assert uncertain.state.value == "uncertain"
    assert uncertain.failure_class == "integrity"
    store.close()


def test_missing_ffmpeg_is_a_failed_production_not_success(tmp_path):
    _, store, package, storage, _ = _production_service(tmp_path)
    renderer = FFmpegSceneCardRenderer(
        storage,
        ffmpeg="ultron-test-missing-ffmpeg-binary",
        ffprobe="ultron-test-missing-ffprobe-binary",
    )
    service = ContentMediaProductionService(store, storage, renderer)
    plan = service.create_plan(package.package_id)

    with pytest.raises(RuntimeError, match="unavailable"):
        service.execute(plan.job_id)
    failed = store.get_media_production_plan(plan.job_id)
    assert failed.state.value == "failed"
    assert failed.failure_class == "missing_dependency"
    assert store.get_media_artifact_for_job(plan.job_id) is None
    store.close()


def test_worker_recovery_validates_output_saved_before_artifact_commit(tmp_path):
    _, store, package, storage, renderer = _production_service(tmp_path)
    service = ContentMediaProductionService(store, storage, renderer)
    plan = service.create_plan(package.package_id)
    store.update_media_production_plan(
        plan.job_id,
        state=MediaProductionState.PREPARING,
        current_stage="preparing",
        retry_count=1,
    )
    store.update_media_production_plan(
        plan.job_id,
        state=MediaProductionState.RENDERING,
        current_stage="rendering",
    )
    store.update_media_production_plan(
        plan.job_id,
        state=MediaProductionState.VALIDATING,
        current_stage="validating",
    )
    staged = storage.create_clip_staging_path(plan.job_id)
    staged.write_bytes(b"persisted-before-database-commit")
    storage.put_clip_output(plan.job_id, staged)
    storage.delete_clip_staging(staged)

    artifact = service.execute(plan.job_id)
    assert artifact.validation_passed
    assert store.get_media_production_plan(plan.job_id).state.value == "completed"
    store.close()


def test_concurrent_duplicate_execution_creates_one_artifact(tmp_path):
    _, store, package, storage, renderer = _production_service(tmp_path)
    service = ContentMediaProductionService(store, storage, renderer)
    plan = service.create_plan(package.package_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _index: service.execute(plan.job_id), range(2)))

    assert results[0] == results[1]
    assert store.get_media_artifact_for_job(plan.job_id) == results[0]
    store.close()


@pytest.mark.parametrize(
    ("failure", "expected_state"),
    (("render", "failed"), ("timeout", "uncertain"), ("validation", "failed")),
)
def test_media_production_failures_are_persisted_and_classified(
    tmp_path,
    failure,
    expected_state,
):
    class FailingRenderer(_DeterministicRenderer):
        def render(self, plan, staged_output):
            if failure == "timeout":
                raise TimeoutError("injected local render timeout")
            if failure == "render":
                raise RuntimeError("injected render failure")
            return super().render(plan, staged_output)

        def validate(self, plan, staged_output):
            if failure == "validation":
                return {
                    "passed": False,
                    "results": ({"id": "container_mp4", "passed": False},),
                    "failure_reasons": ("container_mp4",),
                }
            return super().validate(plan, staged_output)

    _, store, package, storage, _ = _production_service(tmp_path)
    service = ContentMediaProductionService(store, storage, FailingRenderer())
    plan = service.create_plan(package.package_id)
    expected_error = TimeoutError if failure == "timeout" else Exception
    with pytest.raises(expected_error):
        service.execute(plan.job_id)
    failed_plan = store.get_media_production_plan(plan.job_id)
    assert failed_plan.state.value == expected_state
    assert failed_plan.failure_class is not None
    if failure == "validation":
        report = store.get_media_validation_report_for_job(plan.job_id)
        assert report is not None and not report.passed
    store.close()


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="Local FFmpeg and ffprobe are required for the MP4 integration test.",
)
def test_local_ffmpeg_renders_and_validates_playable_mp4(tmp_path):
    database, store, package, storage, renderer = _production_service(
        tmp_path,
        real_renderer=True,
    )
    service = ContentMediaProductionService(store, storage, renderer)
    plan = service.create_plan(
        package.package_id,
        configuration={
            "width": 180,
            "height": 320,
            "frame_rate": 5,
            "aspect_ratio": "9:16",
        },
    )
    artifact = service.execute(plan.job_id)

    assert artifact.media_format == "mp4"
    assert artifact.video_codec == "h264"
    assert artifact.width == 180
    assert artifact.height == 320
    assert artifact.duration_seconds > 0
    assert artifact.validation_passed
    assert artifact.renderer_information["name"] == "ffmpeg"
    assert artifact.rendering_profile == "custom"
    assert artifact.video_metadata["frame_rate"] == 5
    assert artifact.video_metadata["pixel_format"] == "yuv420p"
    assert artifact.video_metadata["configuration"] == plan.rendering_configuration
    assert artifact.audio_metadata["track"] is None
    assert artifact.audio_metadata["narration"]["available"] is False
    assert len(artifact.subtitle_artifact_ids) == 2
    validation = store.get_media_validation_report_for_job(plan.job_id)
    assert validation is not None and validation.passed
    assert any(
        result["id"] == "burned_captions" and result["passed"]
        for result in validation.results
    )
    assert any(
        result["id"] == "burned_caption_visible" and result["passed"]
        for result in validation.results
    )
    store.close()
    reopened = ContentStore(database)
    persisted = reopened.get_media_artifact(artifact.artifact_id)
    assert persisted == artifact
    recovered_bytes = storage.read_clip_output(
        persisted.storage_reference.rsplit("/", 1)[-1],
        persisted.output_sha256,
    )
    assert hashlib.sha256(recovered_bytes).hexdigest() == persisted.output_sha256
    assert reopened.list_media_subtitle_artifacts(plan.job_id)
    reopened.close()


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="Local FFmpeg and ffprobe are required for audio/container integration.",
)
def test_local_audio_is_normalized_synchronized_and_matroska_validated(tmp_path):
    database, store, package, storage, renderer = _production_service(
        tmp_path,
        real_renderer=True,
    )
    service = ContentMediaProductionService(store, storage, renderer)
    base_configuration = {
        "width": 180,
        "height": 320,
        "frame_rate": 5,
        "aspect_ratio": "9:16",
        "scene_duration_seconds": 0.5,
        "container": "matroska",
    }
    timing_plan = service.create_plan(
        package.package_id,
        configuration=base_configuration,
    )
    audio_duration = sum(scene.duration_seconds for scene in timing_plan.scenes)
    wave_path = tmp_path / "generated-tone.wav"
    subprocess.run(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={audio_duration:.6f}",
            "-ar",
            "24000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(wave_path),
        ],
        check=True,
        capture_output=True,
    )
    wave = wave_path.read_bytes()
    staged_audio = storage.stage_clip_source(f"{package.package_id}-tone", wave)
    configuration = {
        **base_configuration,
        "audio_path": str(staged_audio),
        "audio_required": True,
        "audio_sample_rate": 24000,
        "audio_bitrate": "64k",
    }
    plan = service.create_plan(package.package_id, configuration=configuration)
    try:
        artifact = service.execute(plan.job_id)
        assert artifact.media_format == "mkv"
        assert Path(artifact.storage_reference).suffix == ".mkv"
        assert artifact.audio_metadata["track"]["codec"] == "aac"
        assert artifact.audio_metadata["track"]["sample_rate"] == 24000
        validation = store.get_media_validation_report_for_job(plan.job_id)
        assert validation is not None and validation.passed
        assert any(
            result["id"] == "audio_video_sync" and result["passed"]
            for result in validation.results
        )
        assert any(
            result["id"] == "audio_sample_rate" and result["passed"]
            for result in validation.results
        )
        decoded = subprocess.run(
            [
                shutil.which("ffmpeg") or "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-v",
                "error",
                "-xerror",
                "-i",
                artifact.storage_reference,
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert decoded.returncode == 0, decoded.stderr
    finally:
        storage.delete_clip_staging(staged_audio)
        store.close()
