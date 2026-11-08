from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests.application.test_content_processing import _harness
from tests.application.schema_test_utils import set_legacy_schema_version
from ultron.application.content.rendering import ContentClipRenderingService
from ultron.application.content.store import ContentStore, IdempotencyConflictError
from ultron.application.runtime.interface_gateway import InterfaceGateway
from ultron.application.runtime.operation_store import SQLiteOperationStore
from ultron.domain.content import (
    ContentCreativeEditPlan,
    ContentCreativeEditPlanSegmentV2,
    ContentCreativeEditPlanV2,
    ContentCreativeEditPlanV3,
    ContentEditPlanAudio,
    ContentEditPlanSegment,
    ContentEditPlanSource,
    ContentEditProject,
    ContentEditSegment,
    PrivateRenderOperationState,
)
from ultron.security.identity.authorization import AuthorizationContext, resource_fingerprint
from ultron.security.identity.authenticator import WebSocketAuthenticator


class _FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send(self, message):
        self.messages.append(json.loads(message))


class _DropFirstResponseWebSocket(_FakeWebSocket):
    def __init__(self):
        super().__init__()
        self.drop_first_response = True

    async def send(self, message):
        if self.drop_first_response:
            self.drop_first_response = False
            return
        await super().send(message)


def _migrate_to_v10(store: ContentStore) -> None:
    store.migrate_publishing_attempt_schema()
    store.migrate_private_render_operation_schema()


def _project(harness, asset, project_id: str = "private-render-project"):
    project = ContentEditProject(
        project_id=project_id,
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script="Synthetic private render lifecycle test.",
    )
    harness.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        harness.service.create_edit_project,
        project=project,
        command_id=f"create-{project_id}",
    )
    harness.store.replace_edit_segments(
        project.project_id,
        "alice",
        (
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
        ),
    )
    return project


def _operation_context(harness, action: str, payload: dict) -> AuthorizationContext:
    return harness.context(action, payload)


def _submit(harness, rendering, operation_id: str, project_id: str, *, vertical=False):
    payload = {
        "operation_id": operation_id,
        "project_id": project_id,
        "vertical": vertical,
    }
    return rendering.submit_private_project_render_operation(
        operation_id,
        project_id,
        vertical=vertical,
        context=_operation_context(
            harness,
            "content_submit_private_project_render",
            payload,
        ),
    )


def _fake_render(renderer, output=b"synthetic-rendered-mp4"):
    def render_multi_source_timeline(
        source_paths,
        *,
        segments,
        staged_output,
        vertical,
    ):
        staged_output.write_bytes(output)
        return None, SimpleNamespace(
            duration_seconds=4.0,
            video_width=640,
            video_height=360,
            has_audio=False,
            audio_sample_rate=None,
            audio_channels=None,
            audio_duration_seconds=None,
            audio_peak_dbfs=None,
        )

    renderer.render_multi_source_timeline = render_multi_source_timeline


def test_private_render_persists_completed_operation_and_recovers_artifact(
    tmp_path,
    monkeypatch,
):
    harness, asset, _, storage = _harness(tmp_path, monkeypatch)
    _migrate_to_v10(harness.store)
    project = _project(harness, asset)
    rendering = ContentClipRenderingService(content_rights=harness.service)
    rendered = []
    _fake_render(rendering.renderer)
    original = rendering.renderer.render_multi_source_timeline

    def counted_render(*args, **kwargs):
        rendered.append(True)
        return original(*args, **kwargs)

    rendering.renderer.render_multi_source_timeline = counted_render
    operation_id = str(uuid4())

    submitted = _submit(harness, rendering, operation_id, project.project_id)
    assert submitted["state"] == "completed"
    assert rendered == [True]
    saved = harness.store.get_private_render_operation(operation_id, "alice")
    assert saved is not None
    assert saved.state is PrivateRenderOperationState.COMPLETED
    assert saved.artifact_byte_count == len(b"synthetic-rendered-mp4")
    assert saved.artifact_sha256 == hashlib.sha256(
        b"synthetic-rendered-mp4"
    ).hexdigest()

    recovered = _submit(harness, rendering, operation_id, project.project_id)
    assert recovered["state"] == "completed"
    artifact = rendering.get_private_render_artifact(
        operation_id,
        context=_operation_context(
            harness,
            "content_read_private_project_render_artifact",
            {"operation_id": operation_id},
        ),
    )
    assert base64.b64decode(artifact["base64"], validate=True) == (
        b"synthetic-rendered-mp4"
    )
    assert artifact["sizeBytes"] == len(b"synthetic-rendered-mp4")
    assert hashlib.sha256(base64.b64decode(artifact["base64"])).hexdigest() == (
        artifact["sha256"]
    )
    assert rendered == [True]

    database = harness.store.database_identity
    harness.store.close()
    reopened = ContentStore(database)
    operation = reopened.get_private_render_operation(operation_id, "alice")
    assert operation is not None
    assert operation.state is PrivateRenderOperationState.COMPLETED
    assert operation.artifact_key == saved.artifact_key
    assert storage.read_clip_output(
        operation.artifact_key,
        operation.artifact_sha256,
    ) == b"synthetic-rendered-mp4"
    reopened.close()


def test_private_render_idempotency_conflict_and_owner_scoping(
    tmp_path,
    monkeypatch,
):
    harness, asset, _, _ = _harness(tmp_path, monkeypatch)
    _migrate_to_v10(harness.store)
    project = _project(harness, asset)
    other_project = _project(harness, asset, "private-render-project-two")
    rendering = ContentClipRenderingService(content_rights=harness.service)
    calls = []

    def render(source_paths, *, segments, staged_output, vertical):
        calls.append(True)
        staged_output.write_bytes(b"synthetic-render")
        return None, SimpleNamespace(
            duration_seconds=4.0,
            video_width=640,
            video_height=360,
            has_audio=False,
            audio_sample_rate=None,
            audio_channels=None,
            audio_duration_seconds=None,
            audio_peak_dbfs=None,
        )

    rendering.renderer.render_multi_source_timeline = render
    operation_id = str(uuid4())
    assert _submit(harness, rendering, operation_id, project.project_id)["state"] == "completed"
    assert _submit(harness, rendering, operation_id, project.project_id)["state"] == "completed"
    assert calls == [True]
    with pytest.raises(IdempotencyConflictError):
        _submit(harness, rendering, operation_id, other_project.project_id)

    other_authority = WebSocketAuthenticator("L" * 43, "mallory")
    websocket = object()
    assert other_authority.authenticate(websocket, "L" * 43) is not None
    other_service = type(harness.service)(
        store=harness.store,
        authority=other_authority,
        content_storage=harness.service.content_storage,
    )
    other_rendering = ContentClipRenderingService(content_rights=other_service)
    for action, parameters, lookup in (
        (
            "content_read_private_project_render",
            {"operation_id": operation_id},
            other_rendering.get_private_render_operation,
        ),
        (
            "content_read_private_project_render_artifact",
            {"operation_id": operation_id},
            other_rendering.get_private_render_artifact,
        ),
    ):
        grant = other_authority.authorize(
            websocket=websocket,
            operation={
                "operationId": str(uuid4()),
                "correlationId": str(uuid4()),
                "requestFingerprint": hashlib.sha256(b"test").hexdigest(),
            },
            purpose="content_lifecycle",
            request_correlation_id=str(uuid4()),
            action=action,
            resource_fingerprint=resource_fingerprint(action, parameters),
        )
        assert grant is not None
        with pytest.raises(PermissionError, match="unavailable"):
            lookup(
                operation_id,
                context=AuthorizationContext.from_grant(grant),
            )
    harness.store.close()


def test_concurrent_duplicate_private_render_is_claimed_once(tmp_path, monkeypatch):
    harness, asset, _, _ = _harness(tmp_path, monkeypatch)
    _migrate_to_v10(harness.store)
    project = _project(harness, asset)
    rendering = ContentClipRenderingService(content_rights=harness.service)
    entered_renderer = threading.Event()
    release_renderer = threading.Event()
    calls = []

    def blocking_render(source_paths, *, segments, staged_output, vertical):
        calls.append(True)
        entered_renderer.set()
        assert release_renderer.wait(timeout=5)
        staged_output.write_bytes(b"concurrent-synthetic-render")
        return None, SimpleNamespace(
            duration_seconds=4.0,
            video_width=640,
            video_height=360,
            has_audio=False,
            audio_sample_rate=None,
            audio_channels=None,
            audio_duration_seconds=None,
            audio_peak_dbfs=None,
        )

    rendering.renderer.render_multi_source_timeline = blocking_render
    operation_id = str(uuid4())
    result = []
    failure = []

    def first_submission():
        try:
            result.append(_submit(harness, rendering, operation_id, project.project_id))
        except Exception as exc:
            failure.append(exc)

    worker = threading.Thread(target=first_submission)
    worker.start()
    assert entered_renderer.wait(timeout=5)
    duplicate = _submit(harness, rendering, operation_id, project.project_id)
    assert duplicate["state"] == "rendering"
    assert calls == [True]
    release_renderer.set()
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert not failure
    assert result[0]["state"] == "completed"
    assert calls == [True]
    harness.store.close()


def test_stale_private_render_is_reconciled_without_rerender(tmp_path, monkeypatch):
    harness, asset, _, _ = _harness(tmp_path, monkeypatch)
    _migrate_to_v10(harness.store)
    project = _project(harness, asset)
    operation_id = str(uuid4())
    with harness.store._transaction() as connection:
        harness.store.create_private_render_operation_in_transaction(
            connection,
            operation_id=operation_id,
            owner_id="alice",
            project_id=project.project_id,
            request_fingerprint=hashlib.sha256(b"fingerprint").hexdigest(),
            input_snapshot_json=json.dumps({"projectId": project.project_id}),
        )
    assert harness.store.claim_private_render_operation(operation_id, "alice")
    database = harness.store.database_identity
    harness.store.close()

    reopened = ContentStore(database)
    operation = reopened.get_private_render_operation(operation_id, "alice")
    assert operation is not None
    assert operation.state is PrivateRenderOperationState.RECONCILIATION_REQUIRED
    assert operation.failure_code == "PROCESS_INTERRUPTED"
    assert reopened.list_private_render_operation_events(
        operation_id,
        "alice",
    )[-1].to_state is PrivateRenderOperationState.RECONCILIATION_REQUIRED
    reopened.close()


def test_renderer_failure_and_artifact_corruption_never_report_false_success(
    tmp_path,
    monkeypatch,
):
    harness, asset, _, storage = _harness(tmp_path, monkeypatch)
    _migrate_to_v10(harness.store)
    project = _project(harness, asset, "private-render-failure-project")
    rendering = ContentClipRenderingService(content_rights=harness.service)
    failed_id = str(uuid4())

    def fail_renderer(*args, **kwargs):
        raise RuntimeError("synthetic renderer failure")

    rendering.renderer.render_multi_source_timeline = fail_renderer
    failed_response = _submit(harness, rendering, failed_id, project.project_id)
    assert failed_response["state"] == "failed"
    failed = harness.store.get_private_render_operation(failed_id, "alice")
    assert failed is not None
    assert failed.state is PrivateRenderOperationState.FAILED
    assert failed.artifact_key is None

    _fake_render(rendering.renderer)
    original_put = storage.put_clip_output

    def fail_artifact_persistence(*args, **kwargs):
        raise OSError("synthetic artifact persistence failure")

    storage.put_clip_output = fail_artifact_persistence
    failed_artifact_id = str(uuid4())
    persistence_failure = _submit(
        harness,
        rendering,
        failed_artifact_id,
        project.project_id,
    )
    assert persistence_failure["state"] == "failed"
    storage.put_clip_output = original_put

    rendering = ContentClipRenderingService(content_rights=harness.service)
    _fake_render(rendering.renderer)
    completed_id = str(uuid4())
    assert _submit(harness, rendering, completed_id, project.project_id)["state"] == "completed"
    completed = harness.store.get_private_render_operation(completed_id, "alice")
    assert completed is not None
    artifact_path = storage._path(completed.artifact_key, namespace="outputs")
    artifact_path.write_bytes(b"corrupted synthetic artifact")
    with pytest.raises(ValueError):
        rendering.get_private_render_artifact(
            completed_id,
            context=_operation_context(
                harness,
                "content_read_private_project_render_artifact",
                {"operation_id": completed_id},
            ),
        )
    still_completed = harness.store.get_private_render_operation(
        completed_id,
        "alice",
    )
    assert still_completed is not None
    assert still_completed.state is PrivateRenderOperationState.COMPLETED
    harness.store.close()


def test_artifact_transfer_timeout_preserves_completed_state(tmp_path, monkeypatch):
    harness, asset, _, storage = _harness(tmp_path, monkeypatch)
    _migrate_to_v10(harness.store)
    project = _project(harness, asset, "private-render-transfer-timeout-project")
    rendering = ContentClipRenderingService(content_rights=harness.service)
    _fake_render(rendering.renderer)
    operation_id = str(uuid4())
    assert _submit(harness, rendering, operation_id, project.project_id)["state"] == "completed"
    original_read = storage.read_clip_output

    def timeout_read(*args, **kwargs):
        raise TimeoutError("synthetic artifact transfer timeout")

    storage.read_clip_output = timeout_read
    with pytest.raises(TimeoutError, match="synthetic artifact transfer timeout"):
        rendering.get_private_render_artifact(
            operation_id,
            context=_operation_context(
                harness,
                "content_read_private_project_render_artifact",
                {"operation_id": operation_id},
            ),
        )
    operation = harness.store.get_private_render_operation(operation_id, "alice")
    assert operation is not None
    assert operation.state is PrivateRenderOperationState.COMPLETED

    storage.read_clip_output = original_read
    recovered = rendering.get_private_render_artifact(
        operation_id,
        context=_operation_context(
            harness,
            "content_read_private_project_render_artifact",
            {"operation_id": operation_id},
        ),
    )
    assert base64.b64decode(recovered["base64"], validate=True) == (
        b"synthetic-rendered-mp4"
    )
    harness.store.close()


@pytest.mark.parametrize(
    "plan_version",
    ["cre-creative-plan-v1", "cre-creative-plan-v2", "cre-creative-plan-v3"],
)
def test_durable_render_keeps_legacy_and_structured_plan_compatibility(
    tmp_path,
    monkeypatch,
    plan_version,
):
    harness, asset, _, _ = _harness(tmp_path, monkeypatch)
    _migrate_to_v10(harness.store)
    project = _project(
        harness,
        asset,
        f"private-render-{plan_version}-project",
    )
    source = ContentEditPlanSource(
        source_asset_id=asset.asset_id,
        content_sha256=asset.content_sha256,
        duration_seconds=4,
    )
    if plan_version == "cre-creative-plan-v1":
        plan = ContentCreativeEditPlan(
            project_id=project.project_id,
            revision=1,
            creative_direction="Synthetic v1 compatibility.",
            sources=(source,),
            segments=(
                ContentEditPlanSegment(
                    source_asset_id=asset.asset_id,
                    source_in_seconds=0,
                    source_out_seconds=2,
                    timeline_order=1,
                    selection_method="evenly_spaced_sample",
                    transition_before="start",
                ),
                ContentEditPlanSegment(
                    source_asset_id=asset.asset_id,
                    source_in_seconds=2,
                    source_out_seconds=4,
                    timeline_order=2,
                    selection_method="evenly_spaced_sample",
                    transition_before="hard_cut",
                ),
            ),
        )
    else:
        silence = ContentEditPlanAudio(
            mode="silence",
            source_asset_id=None,
            source_in_seconds=None,
            source_out_seconds=None,
            gain_db=0,
            timing_policy="trim_pad_to_video_segment",
        )
        plan_model = (
            ContentCreativeEditPlanV3
            if plan_version == "cre-creative-plan-v3"
            else ContentCreativeEditPlanV2
        )
        plan = plan_model(
            project_id=project.project_id,
            revision=1,
            creative_direction="Synthetic v2 compatibility.",
            sources=(source,),
            segments=(
                ContentCreativeEditPlanSegmentV2(
                    source_asset_id=asset.asset_id,
                    source_in_seconds=0,
                    source_out_seconds=2,
                    timeline_order=1,
                    selection_method="evenly_spaced_sample",
                    transition_before="start",
                    audio=silence,
                ),
                ContentCreativeEditPlanSegmentV2(
                    source_asset_id=asset.asset_id,
                    source_in_seconds=2,
                    source_out_seconds=4,
                    timeline_order=2,
                    selection_method="evenly_spaced_sample",
                    transition_before="hard_cut",
                    audio=silence,
                ),
            ),
        )
    with harness.store._transaction() as connection:
        harness.store.save_edit_plan_in_transaction(connection, "alice", plan)
    rendering = ContentClipRenderingService(content_rights=harness.service)
    observed_plan = []

    def render_plan(source_paths, *, plan, staged_output, vertical):
        observed_plan.append(plan)
        staged_output.write_bytes(b"synthetic-plan-render")
        return None, SimpleNamespace(
            duration_seconds=4.0,
            video_width=640,
            video_height=360,
            has_audio=False,
            audio_sample_rate=None,
            audio_channels=None,
            audio_duration_seconds=None,
            audio_peak_dbfs=None,
        )

    rendering.renderer.render_creative_edit_plan = render_plan
    operation_id = str(uuid4())
    result = _submit(harness, rendering, operation_id, project.project_id)
    assert result["state"] == "completed"
    assert result["result"]["planVersion"] == plan_version
    if plan_version == "cre-creative-plan-v1":
        assert "audioValidation" not in result["result"]
    else:
        assert result["result"]["audioValidation"]["status"] == "PASSED"
        assert result["result"]["audioValidation"]["outputPresent"] is False
        assert "base64" not in result["result"]["audioValidation"]
    assert observed_plan[0].schema_version == plan_version
    harness.store.close()


def test_v9_migration_is_repeatable_and_preserves_existing_project_data(
    tmp_path,
    monkeypatch,
):
    harness, asset, _, _ = _harness(tmp_path, monkeypatch)
    project = _project(harness, asset, "migration-preservation-project")
    set_legacy_schema_version(harness.store, 9)
    assert harness.store.migrate_publishing_attempt_schema() == 9
    assert harness.store.get_edit_project(project.project_id, "alice") == project
    assert harness.store.get_asset(asset.asset_id) == asset
    assert harness.store.migrate_private_render_operation_schema() == 10
    assert harness.store.migrate_private_render_operation_schema() == 10
    assert harness.store.get_edit_project(project.project_id, "alice") == project
    assert harness.store.get_asset(asset.asset_id) == asset
    database = harness.store.database_identity
    harness.store.close()

    reopened = ContentStore(database)
    assert reopened.private_render_operations_enabled
    assert reopened.get_edit_project(project.project_id, "alice") == project
    reopened._connection.execute(
        "UPDATE content_schema_meta SET schema_version = 11 WHERE singleton = 1"
    )
    reopened.close()
    with pytest.raises(RuntimeError, match="newer than this application supports"):
        ContentStore(database, recover_interrupted_jobs=False)


def test_explicit_migration_command_requires_and_uses_matching_backup(tmp_path):
    database = tmp_path / "content.db"
    backup = tmp_path / "content.db.pre-v10"
    store = ContentStore(database)
    set_legacy_schema_version(store, 9)
    assert store.migrate_publishing_attempt_schema() == 9
    store.close()
    shutil.copyfile(database, backup)

    repository = Path(__file__).resolve().parents[2]
    command = [
        sys.executable,
        "-m",
        "tools.migrate_cre_private_render_operations",
        str(database),
        "--backup",
        str(backup),
    ]
    migrated = subprocess.run(
        command,
        cwd=repository,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert migrated.returncode == 0, migrated.stderr.decode()
    assert b"now v10" in migrated.stdout
    reopened = ContentStore(database)
    assert reopened.private_render_operations_enabled
    reopened.close()

    mismatch = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.migrate_cre_private_render_operations",
            str(database),
            "--backup",
            str(backup),
        ],
        cwd=repository,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert mismatch.returncode != 0
    assert b"backup schema version must match" in mismatch.stderr


def test_gateway_submit_status_and_artifact_operations_are_owner_authenticated(
    tmp_path,
    monkeypatch,
):
    harness, asset, _, _ = _harness(tmp_path, monkeypatch)
    _migrate_to_v10(harness.store)
    project = _project(harness, asset, "gateway-private-render-project")
    rendering = ContentClipRenderingService(content_rights=harness.service)
    _fake_render(rendering.renderer)
    render_calls = []
    render_once = rendering.renderer.render_multi_source_timeline

    def counted_render(*args, **kwargs):
        render_calls.append(True)
        return render_once(*args, **kwargs)

    rendering.renderer.render_multi_source_timeline = counted_render
    gateway = InterfaceGateway(
        SimpleNamespace(
            event_bus=SimpleNamespace(),
            runtime=SimpleNamespace(),
            executor=SimpleNamespace(),
            skills=SimpleNamespace(),
            interface_approval_authorizer=harness.authority,
            cre_clip_rendering_service=rendering,
        ),
        operation_store=SQLiteOperationStore(":memory:"),
    )
    websocket = _DropFirstResponseWebSocket()
    assert harness.authority.authenticate(websocket, "L" * 43) is not None
    operation_id = str(uuid4())

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_PRIVATE_PROJECT_RENDER_REQUEST",
                "correlationId": "private-render-submit",
                "payload": {
                    "operationId": operation_id,
                    "projectId": project.project_id,
                    "vertical": False,
                },
            },
        )
    )
    assert websocket.messages == []

    completed = harness.store.get_private_render_operation(operation_id, "alice")
    assert completed is not None and completed.result_json is not None
    result = json.loads(completed.result_json)
    # Model nested legacy result metadata without modifying the saved artifact.
    result["status"] = {"artifact": dict(result)}
    with harness.store._transaction() as connection:
        connection.execute(
            """
            UPDATE content_private_render_operations
            SET result_json = ?
            WHERE operation_id = ?
            """,
            (harness.store._canonical(result), operation_id),
        )

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_PRIVATE_RENDER_STATUS_REQUEST",
                "correlationId": "private-render-status",
                "payload": {"operationId": operation_id},
            },
        )
    )
    status = websocket.messages[-1]
    assert status["success"] is True
    assert status["payload"]["state"] == "completed"
    assert set(status["payload"]["result"]) == {
        "projectId",
        "planMode",
        "planVersion",
        "planRevision",
        "planSha256",
        "sourceAssetIds",
    }
    assert "status" not in status["payload"]["result"]

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_PRIVATE_RENDER_ARTIFACT_REQUEST",
                "correlationId": "private-render-artifact",
                "payload": {"operationId": operation_id},
            },
        )
    )
    artifact = websocket.messages[-1]
    assert artifact["success"] is True
    assert set(artifact["payload"]) == {
        "base64",
        "durationSeconds",
        "mimeType",
        "sha256",
        "sizeBytes",
    }
    assert artifact["payload"]["sizeBytes"] == len(b"synthetic-rendered-mp4")
    assert hashlib.sha256(
        base64.b64decode(artifact["payload"]["base64"], validate=True)
    ).hexdigest() == artifact["payload"]["sha256"]

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_PRIVATE_RENDER_ARTIFACT_REQUEST",
                "correlationId": "private-render-artifact-retry",
                "payload": {"operationId": operation_id},
            },
        )
    )
    retried_artifact = websocket.messages[-1]
    assert retried_artifact["success"] is True
    assert retried_artifact["payload"] == artifact["payload"]
    assert render_calls == [True]
    gateway.operation_store.close()
    harness.store.close()
