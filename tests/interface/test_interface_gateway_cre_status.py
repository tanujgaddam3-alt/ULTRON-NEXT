import asyncio
import base64
import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ultron.application.content.service import ContentRightsService
from ultron.application.content.status import (
    ContentOwnerMapping,
    ContentStatusService,
)
from ultron.application.content.script_preview import OriginalScriptPreviewService
from ultron.application.content.clip_renderer import FFmpegLocalClipRenderer
from ultron.application.content.intelligence_service import ContentIntelligenceService
from ultron.application.content.processing import LocalTemporaryContentStore
from ultron.application.content.rendering import ContentClipRenderingService
from ultron.application.content.store import ContentStore
from ultron.application.runtime.interface_gateway import InterfaceGateway
from ultron.application.runtime.operation_store import SQLiteOperationStore
from ultron.domain.content import ContentPlatform, RightsAction
from ultron.domain.content_intelligence import EngineTranscript, EngineTranscriptSegment
from ultron.security.identity.authenticator import WebSocketAuthenticator
from tests.application.test_content_rendering import (
    _source_video_bytes,
    _video_only_source_bytes,
)
from tests.interface.auth_test_utils import (
    authenticate_test_websocket,
    make_test_authenticator,
)


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send(self, message):
        self.messages.append(json.loads(message))


def _gateway(database, *, principal="test-operator"):
    store = ContentStore(database)
    authenticator = make_test_authenticator(principal)
    status_service = ContentStatusService(
        content_rights=ContentRightsService(
            store=store,
            authority=authenticator,
        ),
        owner_mapping=ContentOwnerMapping(
            principal=authenticator.principal,
            owner_id=authenticator.principal,
        ),
    )
    staging_directory = database.parent / "script-preview-staging"
    staging_directory.mkdir(mode=0o700, exist_ok=True)
    script_preview_service = OriginalScriptPreviewService(
        store=store,
        staging_directory=staging_directory,
    )
    container = SimpleNamespace(
        event_bus=SimpleNamespace(),
        runtime=SimpleNamespace(),
        executor=SimpleNamespace(),
        skills=SimpleNamespace(),
        interface_approval_authorizer=authenticator,
        cre_status_service=status_service,
        cre_script_preview_service=script_preview_service,
    )
    gateway = InterfaceGateway(
        container,
        operation_store=SQLiteOperationStore(":memory:"),
    )
    return gateway, store, authenticator


def _configured_source_gateway(database, *, principal="source-owner"):
    store = ContentStore(database)
    authenticator = make_test_authenticator(principal)
    storage = LocalTemporaryContentStore(database.parent / "managed-content")
    rights = ContentRightsService(
        store=store,
        authority=authenticator,
        content_storage=storage,
    )
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory,
    )
    clip_service = ContentClipRenderingService(
        content_rights=rights,
        renderer=renderer,
    )
    container = SimpleNamespace(
        event_bus=SimpleNamespace(),
        runtime=SimpleNamespace(),
        executor=SimpleNamespace(),
        skills=SimpleNamespace(),
        interface_approval_authorizer=authenticator,
        cre_clip_rendering_service=clip_service,
    )
    return (
        InterfaceGateway(
            container,
            operation_store=SQLiteOperationStore(":memory:"),
        ),
        store,
        authenticator,
        clip_service,
        storage,
    )


def test_authenticated_cre_status_request_returns_owner_scoped_dto(tmp_path):
    gateway, store, authenticator = _gateway(tmp_path / "content.db")
    websocket = FakeWebSocket()
    authenticate_test_websocket(authenticator, websocket)
    before = store._connection.total_changes

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_STATUS_REQUEST",
                "correlationId": "cre-status-1",
                "payload": {"limit": 10},
            },
        )
    )

    response = websocket.messages[-1]
    assert response["action"] == "CRE_STATUS_RESULT"
    assert response["correlationId"] == "cre-status-1"
    assert response["success"] is True
    assert response["payload"]["status"] == "AVAILABLE"
    assert response["payload"]["counts"]["total"] == 0
    assert response["payload"]["publishing"]["mode"] == "MOCK_ONLY"
    assert "ownerId" not in response["payload"]
    assert store._connection.total_changes == before
    store.close()
    gateway.operation_store.close()


def test_unauthenticated_and_client_selected_owner_requests_are_denied(tmp_path):
    gateway, store, authenticator = _gateway(tmp_path / "content.db")
    websocket = FakeWebSocket()

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_STATUS_REQUEST",
                "correlationId": "unauthenticated",
                "payload": {},
            },
        )
    )
    assert websocket.messages[-1]["status"] == "AUTHENTICATION_REQUIRED"

    authenticate_test_websocket(authenticator, websocket)
    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_STATUS_REQUEST",
                "correlationId": "client-owner",
                "payload": {"ownerId": "another-owner"},
            },
        )
    )
    assert websocket.messages[-1]["status"] == "INVALID_REQUEST"
    assert websocket.messages[-1]["success"] is False
    store.close()
    gateway.operation_store.close()


def test_cre_status_missing_service_reports_unavailable():
    authenticator = make_test_authenticator()
    container = SimpleNamespace(
        event_bus=SimpleNamespace(),
        runtime=SimpleNamespace(),
        executor=SimpleNamespace(),
        skills=SimpleNamespace(),
        interface_approval_authorizer=authenticator,
    )
    gateway = InterfaceGateway(
        container,
        operation_store=SQLiteOperationStore(":memory:"),
    )
    websocket = FakeWebSocket()
    authenticate_test_websocket(authenticator, websocket)

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_STATUS_REQUEST",
                "correlationId": "no-cre-service",
                "payload": {},
            },
        )
    )

    assert websocket.messages[-1]["status"] == "UNAVAILABLE"
    assert websocket.messages[-1]["success"] is False
    gateway.operation_store.close()


@pytest.mark.parametrize(
    ("action", "expected_result_action"),
    [
        ("CRE_STATUS_REQUEST", "CRE_STATUS_RESULT"),
        ("CRE_SCRIPT_PREVIEW_REQUEST", "CRE_SCRIPT_PREVIEW_RESULT"),
        ("CRE_PROJECT_RENDER_REQUEST", "CRE_PROJECT_RENDER_RESULT"),
    ],
)
def test_schema_mismatch_disables_all_cre_endpoints_without_fabricated_data(
    action,
    expected_result_action,
):
    authenticator = make_test_authenticator()
    gateway = InterfaceGateway(
        SimpleNamespace(
            event_bus=SimpleNamespace(),
            runtime=SimpleNamespace(),
            executor=SimpleNamespace(),
            skills=SimpleNamespace(),
            interface_approval_authorizer=authenticator,
            cre_unavailable_reason="SCHEMA_VERSION_MISMATCH",
        ),
        operation_store=SQLiteOperationStore(":memory:"),
    )
    websocket = FakeWebSocket()
    authenticate_test_websocket(authenticator, websocket)

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": action,
                "correlationId": "cre-degraded",
                "payload": {},
            },
        )
    )

    response = websocket.messages[-1]
    assert response["action"] == expected_result_action
    assert response["success"] is False
    assert response["status"] == "UNAVAILABLE"
    assert response["subsystem"] == "CRE"
    assert response["reason"] == "SCHEMA_VERSION_MISMATCH"
    assert response["execution"] == "DISABLED"
    assert "counts" not in response
    assert "jobs" not in response
    gateway.operation_store.close()


def test_source_video_upload_requires_configured_managed_storage(tmp_path):
    authenticator = make_test_authenticator()
    gateway = InterfaceGateway(
        SimpleNamespace(
            event_bus=SimpleNamespace(),
            runtime=SimpleNamespace(),
            executor=SimpleNamespace(),
            skills=SimpleNamespace(),
            interface_approval_authorizer=authenticator,
        ),
        operation_store=SQLiteOperationStore(":memory:"),
    )
    websocket = FakeWebSocket()
    authenticate_test_websocket(authenticator, websocket)

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_SOURCE_VIDEO_REQUEST",
                "correlationId": "source-video-not-configured",
                "payload": {
                    "videoBase64": "YQ==",
                    "fileName": "source.mp4",
                    "targetContext": "youtube_shorts",
                    "accountId": "channel-1",
                    "territory": "US",
                },
            },
        )
    )

    response = websocket.messages[-1]
    assert response["action"] == "CRE_SOURCE_VIDEO_RESULT"
    assert response["success"] is False
    assert response["status"] == "NOT_CONFIGURED"
    assert "storage" in response["error"]
    gateway.operation_store.close()


def test_browser_source_upload_registers_video_only_footage_with_owner_and_hash(
    tmp_path,
):
    gateway, store, authenticator, _, _ = _configured_source_gateway(
        tmp_path / "video-only-upload.db",
    )
    websocket = FakeWebSocket()
    authenticate_test_websocket(authenticator, websocket)
    source_bytes = _video_only_source_bytes(
        tmp_path,
        name="gateway-browser-video-only",
    )

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_SOURCE_VIDEO_REQUEST",
                "correlationId": "browser-video-only",
                "payload": {
                    "videoBase64": base64.b64encode(source_bytes).decode("ascii"),
                    "fileName": "silent-source.mp4",
                    "mediaType": "video/mp4",
                    "targetContext": "youtube_shorts",
                    "accountId": "private-local-owner",
                    "territory": "US",
                },
            },
        )
    )

    response = websocket.messages[-1]
    asset_summary = response["payload"]["asset"]
    asset = store.get_asset(asset_summary["assetId"])
    assert response["status"] == "SOURCE_REGISTERED"
    assert response["success"] is True
    assert asset is not None
    assert asset.owner_id == authenticator.principal
    assert asset.content_sha256 == hashlib.sha256(source_bytes).hexdigest()
    assert asset_summary["sizeBytes"] == len(source_bytes)
    assert asset_summary["rightsStatus"] == "PENDING_REVIEW"
    store.close()
    gateway.operation_store.close()


def test_authorized_folder_registration_registers_video_only_footage(
    tmp_path,
):
    gateway, store, authenticator, _, _ = _configured_source_gateway(
        tmp_path / "video-only-folder.db",
    )
    import_directory = tmp_path / "Videos" / "CRE_Import"
    import_directory.mkdir(parents=True)
    source_path = import_directory / "silent-folder-source.mp4"
    source_bytes = _video_only_source_bytes(
        tmp_path,
        name="gateway-folder-video-only",
    )
    source_path.write_bytes(source_bytes)
    gateway.cre_import_directory = import_directory
    websocket = FakeWebSocket()
    authenticate_test_websocket(authenticator, websocket)

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_FOLDER_IMPORT_SCAN_REQUEST",
                "correlationId": "scan-video-only-folder",
                "payload": {},
            },
        )
    )
    scan_response = websocket.messages[-1]
    assert scan_response["status"] == "IMPORT_SCAN_READY"
    selected = scan_response["payload"]["files"][0]
    assert selected["status"] == "ELIGIBLE"
    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_FOLDER_IMPORT_REGISTER_REQUEST",
                "correlationId": "register-video-only-folder",
                "payload": {
                    "fileId": selected["fileId"],
                    "targetContext": "youtube_shorts",
                    "accountId": "private-local-owner",
                    "territory": "US",
                },
            },
        )
    )

    response = websocket.messages[-1]
    asset_summary = response["payload"]["asset"]
    asset = store.get_asset(asset_summary["assetId"])
    assert response["status"] == "SOURCE_REGISTERED"
    assert response["success"] is True
    assert asset is not None
    assert asset.owner_id == authenticator.principal
    assert asset.content_sha256 == hashlib.sha256(source_bytes).hexdigest()
    assert asset_summary["sizeBytes"] == len(source_bytes)
    store.close()
    gateway.operation_store.close()


@pytest.mark.parametrize(
    ("file_name", "media_type", "source_bytes"),
    [
        ("unsupported.avi", "video/x-msvideo", b"synthetic unsupported media"),
        ("corrupt.mp4", "video/mp4", b"not a media container"),
    ],
    ids=["unsupported-type", "corrupt-media"],
)
def test_source_upload_rejects_unsupported_and_corrupt_media(
    tmp_path,
    file_name,
    media_type,
    source_bytes,
):
    gateway, store, authenticator, _, _ = _configured_source_gateway(
        tmp_path / f"{file_name}.db",
    )
    websocket = FakeWebSocket()
    authenticate_test_websocket(authenticator, websocket)
    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_SOURCE_VIDEO_REQUEST",
                "correlationId": f"reject-{file_name}",
                "payload": {
                    "videoBase64": base64.b64encode(source_bytes).decode("ascii"),
                    "fileName": file_name,
                    "mediaType": media_type,
                    "targetContext": "youtube_shorts",
                    "accountId": "private-local-owner",
                    "territory": "US",
                },
            },
        )
    )
    assert websocket.messages[-1]["success"] is False
    assert websocket.messages[-1]["status"] == "INVALID_SOURCE"
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_assets",
    ).fetchone()[0] == 0
    store.close()
    gateway.operation_store.close()


def test_source_upload_rejects_oversized_input_before_media_probe(
    tmp_path,
    monkeypatch,
):
    import ultron.application.runtime.interface_gateway as gateway_module

    monkeypatch.setattr(gateway_module, "MAX_SOURCE_BYTES", 1024 * 1024)
    gateway, store, authenticator, _, _ = _configured_source_gateway(
        tmp_path / "oversized-source.db",
    )
    websocket = FakeWebSocket()
    authenticate_test_websocket(authenticator, websocket)
    source_bytes = b"x" * (1024 * 1024 + 1)
    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_SOURCE_VIDEO_REQUEST",
                "correlationId": "reject-oversized-source",
                "payload": {
                    "videoBase64": base64.b64encode(source_bytes).decode("ascii"),
                    "fileName": "oversized.mp4",
                    "mediaType": "video/mp4",
                    "targetContext": "youtube_shorts",
                    "accountId": "private-local-owner",
                    "territory": "US",
                },
            },
        )
    )
    assert websocket.messages[-1]["success"] is False
    assert websocket.messages[-1]["status"] == "INVALID_SOURCE"
    assert "at most 1 MiB" in websocket.messages[-1]["error"]
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_assets",
    ).fetchone()[0] == 0
    store.close()
    gateway.operation_store.close()


def test_source_upload_stays_pending_and_asset_rights_review_uses_distinct_principal(
    tmp_path,
):
    database = tmp_path / "staged-content.db"
    store = ContentStore(database)
    owner_token = "O" * 43
    reviewer_token = "R" * 43
    authenticator = WebSocketAuthenticator(
        owner_token,
        "source-owner",
        additional_credentials={"source-reviewer": reviewer_token},
    )
    storage = LocalTemporaryContentStore(tmp_path / "managed-content")
    rights = ContentRightsService(
        store=store,
        authority=authenticator,
        reviewer_principals=frozenset({"source-reviewer"}),
        content_storage=storage,
    )
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory
    )
    clip_service = ContentClipRenderingService(
        content_rights=rights,
        renderer=renderer,
    )
    class FixtureTranscriptionEngine:
        engine_id = "fixture-local-engine"
        engine_version = "1"
        calls = 0

        def transcribe(
            self,
            audio_bytes,
            *,
            language,
            max_segments,
            max_segment_characters,
            max_text_characters,
        ):
            self.calls += 1
            return EngineTranscript(
                segments=(
                    EngineTranscriptSegment(
                        start_seconds=0.0,
                        end_seconds=3.5,
                        text="The measured result changes when we apply this method.",
                    ),
                    EngineTranscriptSegment(
                        start_seconds=3.5,
                        end_seconds=8.0,
                        text="The evidence explains why the difference matters.",
                    ),
                ),
                language="en",
                raw_output_sha256=hashlib.sha256(b"fixture transcript").hexdigest(),
            )

    fixture_engine = FixtureTranscriptionEngine()
    intelligence = ContentIntelligenceService(
        content_rights=rights,
        transcription_engine=fixture_engine,
        media_renderer=renderer,
    )
    gateway = InterfaceGateway(
        SimpleNamespace(
            event_bus=SimpleNamespace(),
            runtime=SimpleNamespace(),
            executor=SimpleNamespace(),
            skills=SimpleNamespace(),
            interface_approval_authorizer=authenticator,
            cre_status_service=ContentStatusService(
                content_rights=rights,
                owner_mapping=ContentOwnerMapping("source-owner", "source-owner"),
            ),
            cre_clip_rendering_service=clip_service,
            cre_intelligence_service=intelligence,
        ),
        operation_store=SQLiteOperationStore(":memory:"),
    )
    owner_socket = FakeWebSocket()
    reviewer_socket = FakeWebSocket()
    authenticator.authenticate(owner_socket, owner_token)
    authenticator.authenticate(reviewer_socket, reviewer_token)
    video_bytes = _source_video_bytes(tmp_path)

    asyncio.run(
        gateway.handle(
            owner_socket,
            {
                "action": "CRE_SOURCE_VIDEO_REQUEST",
                "correlationId": "register-source",
                "payload": {
                    "videoBase64": base64.b64encode(video_bytes).decode("ascii"),
                    "fileName": "authorized-source.mp4",
                    "targetContext": "youtube_shorts",
                    "accountId": "channel-1",
                    "territory": "US",
                },
            },
        )
    )
    upload = owner_socket.messages[-1]
    asset = upload["payload"]["asset"]
    assert upload["status"] == "SOURCE_REGISTERED"
    assert asset["rightsStatus"] == "PENDING_REVIEW"
    assert store.get_asset(asset["assetId"]).content_sha256 == hashlib.sha256(video_bytes).hexdigest()
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_processing_jobs"
    ).fetchone()[0] == 0

    evidence_bytes = b"fixture rights agreement evidence"
    terms = {
        "platform": ContentPlatform.YOUTUBE.value,
        "account_scope": "channel-1",
        "territories": ["US"],
        "permitted_actions": [
            RightsAction.ANALYZE.value,
            RightsAction.CREATE_DERIVATIVE.value,
        ],
        "valid_from": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        "valid_until": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
        "attribution_required": False,
        "attribution_text": None,
        "max_duration_seconds": 300,
    }
    asyncio.run(
        gateway.handle(
            owner_socket,
            {
                "action": "CRE_SOURCE_RIGHTS_REQUEST",
                "correlationId": "submit-source-rights",
                "payload": {
                    "step": "submit_claim",
                    "assetId": asset["assetId"],
                    "targetContext": "youtube_shorts",
                    "accountId": "channel-1",
                    "territory": "US",
                    "rightsHolder": "Authorized Rights Holder",
                    "authorityDescription": "Owner's documented editing license",
                    "evidenceKind": "signed_license",
                    "evidenceReference": "https://rights.example/license/1",
                    "evidenceBase64": base64.b64encode(evidence_bytes).decode("ascii"),
                    "terms": terms,
                },
            },
        )
    )
    submitted = owner_socket.messages[-1]
    assert submitted["success"] is True
    ids = submitted["payload"]
    assert ids["assetSha256"] == asset["sha256"]
    assert ids["rightsStatus"] == "PENDING_INDEPENDENT_REVIEW"

    review_request = {
        "step": "review_initial",
        "evidenceId": ids["evidenceId"],
        "grantorId": ids["grantorId"],
        "decision": "verified",
        "reason": "Fixture review performed by the separately authenticated reviewer.",
    }
    asyncio.run(
        gateway.handle(
            owner_socket,
            {
                "action": "CRE_SOURCE_RIGHTS_REQUEST",
                "correlationId": "owner-cannot-review",
                "payload": review_request,
            },
        )
    )
    assert owner_socket.messages[-1]["success"] is False
    assert owner_socket.messages[-1]["status"] == "RIGHTS_OR_AUTHORIZATION_DENIED"

    asyncio.run(
        gateway.handle(
            reviewer_socket,
            {
                "action": "CRE_SOURCE_RIGHTS_REQUEST",
                "correlationId": "review-source-rights",
                "payload": review_request,
            },
        )
    )
    assert reviewer_socket.messages[-1]["success"] is True
    assert reviewer_socket.messages[-1]["payload"]["decision"] == "verified"
    assert store.evidence_decision(ids["evidenceId"]).value == "verified"

    def rights_step(socket, correlation, payload):
        asyncio.run(
            gateway.handle(
                socket,
                {
                    "action": "CRE_SOURCE_RIGHTS_REQUEST",
                    "correlationId": correlation,
                    "payload": payload,
                },
            )
        )
        return socket.messages[-1]

    authority_response = rights_step(
        owner_socket,
        "register-source-authority",
        {
            "step": "register_authority",
            "assetId": asset["assetId"],
            "claimId": ids["claimId"],
            "evidenceId": ids["evidenceId"],
            "grantorId": ids["grantorId"],
            "rightsHolder": "Authorized Rights Holder",
            "relationship": "licensing representative",
            "scopeDescription": "Short-form editing rights for the registered source.",
            "evidenceReference": "https://rights.example/license/1",
            "evidenceSha256": ids["evidenceSha256"],
        },
    )
    assert authority_response["success"] is True
    authority_id = authority_response["payload"]["authorityId"]
    authority_review = rights_step(
        reviewer_socket,
        "review-source-authority",
        {
            "step": "review_authority",
            "authorityId": authority_id,
            "decision": "verified",
            "evidenceSha256": ids["evidenceSha256"],
            "reason": "Fixture asset-specific authority review.",
        },
    )
    assert authority_review["success"] is True

    agreement_response = rights_step(
        owner_socket,
        "create-source-agreement",
        {
            "step": "create_agreement",
            "agreementBase64": base64.b64encode(b"fixture signed agreement").decode("ascii"),
            "evidenceReference": "https://rights.example/agreement/1",
            "terms": terms,
            "grantorId": ids["grantorId"],
            "authorityId": authority_id,
            "assetId": asset["assetId"],
            "rightsHolder": "Authorized Rights Holder",
        },
    )
    assert agreement_response["success"] is True
    agreement_id = agreement_response["payload"]["agreementId"]
    agreement_review = rights_step(
        reviewer_socket,
        "review-source-agreement",
        {
            "step": "review_agreement",
            "agreementId": agreement_id,
            "agreementVersion": 1,
            "decision": "verified",
            "reason": "Fixture independent agreement review.",
        },
    )
    assert agreement_review["success"] is True

    grant_response = rights_step(
        owner_socket,
        "record-source-grant",
        {
            "step": "record_grant",
            "assetId": asset["assetId"],
            "claimId": ids["claimId"],
            "evidenceId": ids["evidenceId"],
            "agreementId": agreement_id,
            "agreementVersion": 1,
        },
    )
    assert grant_response["success"] is True
    assert grant_response["payload"]["assetSha256"] == asset["sha256"]

    asyncio.run(
        gateway.handle(
            owner_socket,
            {
                "action": "CRE_SOURCE_ASSET_STATUS_REQUEST",
                "correlationId": "asset-rights-status",
                "payload": {
                    "assetId": asset["assetId"],
                    "targetContext": "youtube_shorts",
                    "accountId": "channel-1",
                    "territory": "US",
                },
            },
        )
    )
    current_status = owner_socket.messages[-1]
    assert current_status["success"] is True
    assert current_status["payload"]["analysisRightsStatus"] == "cleared"

    resume_payload = {
        "assetId": asset["assetId"],
        "targetContext": "youtube_shorts",
        "accountId": "channel-1",
        "territory": "US",
    }
    for correlation in ("resume-source-once", "resume-source-again"):
        asyncio.run(
            gateway.handle(
                owner_socket,
                {
                    "action": "CRE_SOURCE_VIDEO_RESUME_REQUEST",
                    "correlationId": correlation,
                    "payload": resume_payload,
                },
            )
        )
        assert owner_socket.messages[-1]["success"] is True
        assert owner_socket.messages[-1]["status"] == "HIGHLIGHTS_READY"
        assert owner_socket.messages[-1]["payload"]["asset"]["assetId"] == asset["assetId"]
        assert owner_socket.messages[-1]["payload"]["asset"]["sha256"] == asset["sha256"]
        visual = owner_socket.messages[-1]["payload"]["visualAnalysis"]
        assert visual["method"] == "ffmpeg_scene_change_detection"
        assert visual["semanticVideoUnderstanding"] is False
        assert isinstance(visual["sceneChangeSeconds"], list)
        assert len(visual["sceneChangeSeconds"]) == 2
        candidate = owner_socket.messages[-1]["payload"]["candidates"][0]
        actual_inside = sum(
            candidate["startSeconds"] < cut < candidate["endSeconds"]
            for cut in visual["sceneChangeSeconds"]
        )
        assert candidate["visualEvidence"]["sceneChangeCount"] == actual_inside
    assert fixture_engine.calls == 1
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_processing_jobs WHERE asset_id = ?",
        (asset["assetId"],),
    ).fetchone()[0] == 1
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_transcripts WHERE asset_id = ?",
        (asset["assetId"],),
    ).fetchone()[0] == 1
    store.close()
    gateway.operation_store.close()


def test_expired_authenticated_session_cannot_read_cre_status(tmp_path):
    gateway, store, authenticator = _gateway(tmp_path / "content.db")
    websocket = FakeWebSocket()
    authenticate_test_websocket(authenticator, websocket)
    authenticator._sessions[websocket]["expiresAt"] = time.monotonic() - 1

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_STATUS_REQUEST",
                "correlationId": "expired-session",
                "payload": {},
            },
        )
    )

    assert websocket.messages[-1]["status"] == "AUTHENTICATION_REQUIRED"
    assert websocket.messages[-1]["success"] is False
    store.close()
    gateway.operation_store.close()


def test_authenticated_script_preview_returns_unverified_review_manifest(tmp_path):
    gateway, store, authenticator = _gateway(tmp_path / "content.db")
    websocket = FakeWebSocket()
    authenticate_test_websocket(authenticator, websocket)

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_SCRIPT_PREVIEW_REQUEST",
                "correlationId": "preview-request-1",
                "payload": {
                    "script": "An original educational script.",
                    "references": [
                        {
                            "label": "Reference 1",
                            "url": "https://example.org/research",
                        }
                    ],
                },
            },
        )
    )

    response = websocket.messages[-1]
    assert response["success"] is True
    assert response["action"] == "CRE_SCRIPT_PREVIEW_RESULT"
    assert response["correlationId"] == "preview-request-1"
    assert response["payload"]["references"]["state"] == "UNVERIFIED"
    assert response["payload"]["review"]["humanReviewState"] == "PENDING"
    assert response["payload"]["review"]["approvalState"] == "NOT_GRANTED"
    assert response["payload"]["review"]["publicationState"] == "NOT_PUBLISHED"
    assert response["payload"]["preview"]["audioIncluded"] is True
    preview = response["payload"]
    assert preview["productionManifest"]["sceneVisualEvidence"]["state"] == "VERIFIED"
    assert preview["sceneVisualEvidence"]["state"] == "VERIFIED"
    assert preview["productionManifest"]["scenes"]
    for scene in preview["productionManifest"]["scenes"]:
        asset = scene["visualAsset"]
        assert scene["assetState"] == "GENERATED"
        assert asset["validationStatus"] == "TECHNICAL_CHECKS_PASSED"
        assert asset["sourceScriptSha256"] == preview["scriptSha256"]
        assert base64.b64decode(asset["dataBase64"]).startswith(b"\x89PNG\r\n\x1a\n")
        assert len(asset["sha256"]) == 64

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_SCRIPT_PREVIEW_REVIEW_REQUEST",
                "correlationId": "preview-review-1",
                "payload": {
                    "scriptId": preview["scriptId"],
                    "artifactSha256": preview["preview"]["sha256"],
                    "editorialVerdict": "READY_FOR_FACTUAL_REVIEW",
                    "narrationAssessment": "INTELLIGIBLE",
                    "pacingAssessment": "APPROPRIATE",
                    "captionAssessment": "SYNCHRONIZED",
                    "sceneAssessment": "COHERENT",
                    "factualReviewAttested": True,
                    "notes": "Test-only operator attestation note.",
                },
            },
        )
    )
    review_response = websocket.messages[-1]
    assert review_response["success"] is True
    assert review_response["action"] == "CRE_SCRIPT_PREVIEW_REVIEW_RESULT"
    review = review_response["payload"]["review"]
    assert review["humanReviewState"] == "RECORDED"
    assert review["factualReviewState"] == "OPERATOR_ATTESTED_COMPLETE"
    assert review["editorial"]["selfReview"] is True
    assert review["rightsState"] == "NOT_EVALUATED"
    assert review["approvalState"] == "NOT_GRANTED"
    assert review["publicationState"] == "NOT_PUBLISHED"

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_STATUS_REQUEST",
                "correlationId": "cre-status-audio-evidence",
                "payload": {"limit": 10},
            },
        )
    )
    status = websocket.messages[-1]
    assert status["success"] is True
    assert status["payload"]["scriptPreviews"]["state"] == "AVAILABLE"
    assert [item["scriptId"] for item in status["payload"]["scriptPreviews"]["items"]] == [
        preview["scriptId"]
    ]
    assert (
        status["payload"]["scriptPreviews"]["items"][0]["audioEvidence"]["status"]
        == "VERIFIED"
    )
    visual_status = status["payload"]["scriptPreviews"]["items"][0][
        "sceneVisualEvidence"
    ]
    assert visual_status["state"] == "VERIFIED"
    assert visual_status["statusScope"] == "CURRENT_READBACK"
    assert visual_status["scenes"][0]["asset"]["sha256"] == (
        preview["productionManifest"]["scenes"][0]["visualAsset"]["sha256"]
    )
    assert "dataBase64" not in visual_status["scenes"][0]["asset"]
    assert (
        gateway.cre_script_preview_service.list_audio_evidence(
            principal="another-owner"
        )["items"]
        == []
    )

    other_gateway, other_store, other_authenticator = _gateway(
        tmp_path / "content.db",
        principal="another-owner",
    )
    other_websocket = FakeWebSocket()
    authenticate_test_websocket(other_authenticator, other_websocket)
    asyncio.run(
        other_gateway.handle(
            other_websocket,
            {
                "action": "CRE_STATUS_REQUEST",
                "correlationId": "other-owner-status",
                "payload": {"limit": 10},
            },
        )
    )
    other_status = other_websocket.messages[-1]
    assert other_status["success"] is True
    assert other_status["payload"]["scriptPreviews"]["items"] == []
    with pytest.raises(PermissionError, match="outside the authenticated account"):
        other_gateway.cre_script_preview_service._response(
            preview["scriptId"],
            "another-owner",
        )
    store.close()
    gateway.operation_store.close()
    other_store.close()
    other_gateway.operation_store.close()


def test_authenticated_cre_folder_scan_reports_validation_without_exposing_paths(tmp_path):
    gateway, store, authenticator = _gateway(tmp_path / "folder-scan.db")
    import_directory = tmp_path / "Videos" / "CRE_Import"
    import_directory.mkdir(parents=True)
    gateway.cre_import_directory = import_directory
    valid_file = import_directory / "sample.mp4"
    valid_file.write_bytes(b"fixture video bytes")
    (import_directory / "empty.webm").write_bytes(b"")
    oversized_file = import_directory / "oversized.mkv"
    with oversized_file.open("wb") as stream:
        stream.truncate(32 * 1024 * 1024 + 1)
    (import_directory / "unsupported.avi").write_bytes(b"avi fixture")
    outside_file = tmp_path / "outside.mp4"
    outside_file.write_bytes(b"outside")
    (import_directory / "linked.mp4").symlink_to(outside_file)
    websocket = FakeWebSocket()
    authenticate_test_websocket(authenticator, websocket)

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_FOLDER_IMPORT_SCAN_REQUEST",
                "correlationId": "folder-scan",
                "payload": {},
            },
        )
    )

    response = websocket.messages[-1]
    assert response["action"] == "CRE_FOLDER_IMPORT_SCAN_RESULT"
    assert response["success"] is True
    assert response["status"] == "IMPORT_SCAN_READY"
    files = {item["fileName"]: item for item in response["payload"]["files"]}
    assert set(files) == {"sample.mp4", "empty.webm", "oversized.mkv", "unsupported.avi"}
    assert files["sample.mp4"]["status"] == "ELIGIBLE"
    assert files["sample.mp4"]["validation"].startswith("Supported format and size")
    assert files["empty.webm"]["status"] == "INVALID"
    assert files["oversized.mkv"]["status"] == "INVALID"
    assert files["unsupported.avi"]["status"] == "INVALID"
    assert all("path" not in item and "directory" not in item for item in files.values())
    assert all(str(tmp_path) not in json.dumps(item) for item in files.values())
    store.close()
    gateway.operation_store.close()


def test_cre_folder_scan_requires_auth_and_reports_missing_or_empty_directory(tmp_path):
    gateway, store, authenticator = _gateway(tmp_path / "folder-state.db")
    import_directory = tmp_path / "Videos" / "CRE_Import"
    gateway.cre_import_directory = import_directory
    websocket = FakeWebSocket()

    async def scan(correlation_id):
        await gateway.handle(
            websocket,
            {
                "action": "CRE_FOLDER_IMPORT_SCAN_REQUEST",
                "correlationId": correlation_id,
                "payload": {},
            },
        )
        return websocket.messages[-1]

    assert asyncio.run(scan("folder-unauthenticated"))["status"] == "AUTHENTICATION_REQUIRED"
    authenticate_test_websocket(authenticator, websocket)
    missing = asyncio.run(scan("folder-missing"))
    assert missing["status"] == "DIRECTORY_MISSING"
    assert "CRE_Import" in missing["error"]
    import_directory.mkdir(parents=True)
    empty = asyncio.run(scan("folder-empty"))
    assert empty["status"] == "DIRECTORY_EMPTY"
    assert empty["success"] is False
    assert "No supported video files" in empty["error"]
    store.close()
    gateway.operation_store.close()


def test_cre_folder_scan_accepts_only_the_exact_configured_directory_and_rejects_symlink_escape(
    tmp_path,
):
    gateway, store, authenticator = _gateway(tmp_path / "folder-paths.db")
    import_directory = tmp_path / "Videos" / "CRE_Import"
    import_directory.mkdir(parents=True)
    (import_directory / "allowed.mp4").write_bytes(b"fixture video")
    gateway.cre_import_directory = import_directory
    websocket = FakeWebSocket()
    authenticate_test_websocket(authenticator, websocket)

    async def scan(correlation_id, directory):
        await gateway.handle(
            websocket,
            {
                "action": "CRE_FOLDER_IMPORT_SCAN_REQUEST",
                "correlationId": correlation_id,
                "payload": {"directory": directory},
            },
        )
        return websocket.messages[-1]

    allowed = asyncio.run(scan("configured-path", str(import_directory)))
    assert allowed["status"] == "IMPORT_SCAN_READY"
    assert [entry["fileName"] for entry in allowed["payload"]["files"]] == ["allowed.mp4"]
    for correlation_id, directory in (
        ("outside-path", str(tmp_path)),
        ("traversal-path", str(import_directory.parent / ".." / "outside")),
        ("relative-path", "Videos/CRE_Import"),
    ):
        rejected = asyncio.run(scan(correlation_id, directory))
        assert rejected["status"] == "UNAUTHORIZED_DIRECTORY"
        assert rejected["success"] is False
        assert rejected["payload"] is None

    outside_directory = tmp_path / "outside"
    outside_directory.mkdir()
    (outside_directory / "secret.mp4").write_bytes(b"outside")
    symlink_directory = import_directory.parent / "CRE_Import_link"
    symlink_directory.symlink_to(outside_directory, target_is_directory=True)
    gateway.cre_import_directory = symlink_directory
    symlink_result = asyncio.run(scan("symlink-path", str(symlink_directory)))
    assert symlink_result["success"] is False
    assert symlink_result["status"] == "DIRECTORY_INACCESSIBLE"
    assert symlink_result["payload"] is None

    store.close()
    gateway.operation_store.close()


def test_cre_folder_registration_reopens_only_current_scanned_file_and_delegates_to_source_pipeline(
    tmp_path,
    monkeypatch,
):
    gateway, store, authenticator = _gateway(tmp_path / "folder-register.db")
    import_directory = tmp_path / "Videos" / "CRE_Import"
    import_directory.mkdir(parents=True)
    source_path = import_directory / "registered.mp4"
    source_bytes = b"folder video source bytes"
    source_path.write_bytes(source_bytes)
    gateway.cre_import_directory = import_directory
    websocket = FakeWebSocket()
    authenticate_test_websocket(authenticator, websocket)
    captured = []

    async def existing_source_registration(target_websocket, source_message):
        captured.append((target_websocket, source_message))

    monkeypatch.setattr(gateway, "_handle_cre_source_video", existing_source_registration)
    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_FOLDER_IMPORT_SCAN_REQUEST",
                "correlationId": "scan-before-register",
                "payload": {},
            },
        )
    )
    file_id = websocket.messages[-1]["payload"]["files"][0]["fileId"]
    source_path.write_bytes(b"changed source bytes")
    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_FOLDER_IMPORT_REGISTER_REQUEST",
                "correlationId": "stale-folder-file",
                "payload": {
                    "fileId": file_id,
                    "targetContext": "youtube_shorts",
                    "accountId": "channel-1",
                    "territory": "US",
                },
            },
        )
    )
    assert websocket.messages[-1]["status"] == "INVALID_SOURCE"
    assert captured == []

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_FOLDER_IMPORT_SCAN_REQUEST",
                "correlationId": "rescan-before-register",
                "payload": {},
            },
        )
    )
    file_id = websocket.messages[-1]["payload"]["files"][0]["fileId"]
    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_FOLDER_IMPORT_REGISTER_REQUEST",
                "correlationId": "register-folder-file",
                "payload": {
                    "fileId": file_id,
                    "targetContext": "youtube_shorts",
                    "accountId": "channel-1",
                    "territory": "US",
                },
            },
        )
    )
    assert len(captured) == 1
    target_websocket, source_message = captured[0]
    assert target_websocket is websocket
    assert source_message["action"] == "CRE_SOURCE_VIDEO_REQUEST"
    assert source_message["correlationId"] == "register-folder-file"
    source_payload = source_message["payload"]
    assert base64.b64decode(source_payload["videoBase64"]) == b"changed source bytes"
    assert source_payload["fileName"] == "registered.mp4"
    assert source_payload["targetContext"] == "youtube_shorts"
    assert set(source_payload) == {
        "videoBase64",
        "fileName",
        "targetContext",
        "accountId",
        "territory",
    }

    outside_file = tmp_path / "outside.mp4"
    outside_file.write_bytes(b"outside")
    (import_directory / "escape.mp4").symlink_to(outside_file)
    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_FOLDER_IMPORT_SCAN_REQUEST",
                "correlationId": "scan-symlink",
                "payload": {},
            },
        )
    )
    assert all(
        item["fileName"] != "escape.mp4"
        for item in websocket.messages[-1]["payload"]["files"]
    )
    store.close()
    gateway.operation_store.close()


def test_cre_folder_register_rejects_client_supplied_paths_and_unauthenticated_requests(
    tmp_path,
):
    gateway, store, authenticator = _gateway(tmp_path / "folder-invalid.db")
    websocket = FakeWebSocket()
    request = {
        "action": "CRE_FOLDER_IMPORT_REGISTER_REQUEST",
        "correlationId": "folder-path-rejected",
        "payload": {
            "fileId": "a" * 64,
            "path": "/etc/passwd",
            "targetContext": "youtube_shorts",
            "accountId": "channel-1",
            "territory": "US",
        },
    }
    authenticate_test_websocket(authenticator, websocket)
    asyncio.run(gateway.handle(websocket, request))
    assert websocket.messages[-1]["status"] == "INVALID_REQUEST"
    unauthenticated_websocket = FakeWebSocket()
    request["payload"].pop("path")
    request["correlationId"] = "folder-unauthenticated-register"
    asyncio.run(gateway.handle(unauthenticated_websocket, request))
    assert unauthenticated_websocket.messages[-1]["status"] == "AUTHENTICATION_REQUIRED"
    store.close()
    gateway.operation_store.close()


def test_cre_folder_scan_rejects_import_directory_symlink(tmp_path):
    gateway, store, authenticator = _gateway(tmp_path / "folder-symlink.db")
    videos_directory = tmp_path / "Videos"
    videos_directory.mkdir()
    outside_directory = tmp_path / "outside"
    outside_directory.mkdir()
    (outside_directory / "secret.mp4").write_bytes(b"not in import root")
    (videos_directory / "CRE_Import").symlink_to(outside_directory, target_is_directory=True)
    gateway.cre_import_directory = videos_directory / "CRE_Import"
    websocket = FakeWebSocket()
    authenticate_test_websocket(authenticator, websocket)

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_FOLDER_IMPORT_SCAN_REQUEST",
                "correlationId": "folder-symlink-rejected",
                "payload": {},
            },
        )
    )

    response = websocket.messages[-1]
    assert response["status"] == "DIRECTORY_INACCESSIBLE"
    assert response["success"] is False
    assert response["payload"] is None
    store.close()
    gateway.operation_store.close()


def test_gateway_accepts_script_over_400_unicode_characters_through_request_path(tmp_path):
    gateway, store, authenticator = _gateway(tmp_path / "content.db")
    websocket = FakeWebSocket()
    authenticate_test_websocket(authenticator, websocket)

    class LongScriptPreviewService:
        def create_preview(self, *, principal, script, references, command_id):
            assert len(script) > 400
            assert len(script) <= 20000
            assert script.startswith("Café — 日本語 🌱")
            assert "\n\n" in script
            return {
                "scriptId": "long-script-preview",
                "scriptSha256": hashlib.sha256(script.encode("utf-8")).hexdigest(),
                "previewStage": "PROCESSING_REACHED",
            }

    gateway.cre_script_preview_service = LongScriptPreviewService()
    script = "Café — 日本語 🌱\n\n" + ("Paragraph content. " * 100)
    script = script[:1982]

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_SCRIPT_PREVIEW_REQUEST",
                "correlationId": "long-preview-request",
                "payload": {"script": script, "references": []},
            },
        )
    )

    response = websocket.messages[-1]
    assert response["success"] is True
    assert response["payload"]["previewStage"] == "PROCESSING_REACHED"
    assert response["payload"]["scriptSha256"] == hashlib.sha256(script.encode("utf-8")).hexdigest()
    store.close()
    gateway.operation_store.close()


def test_unauthenticated_script_preview_request_is_rejected(tmp_path):
    gateway, store, _ = _gateway(tmp_path / "content.db")
    websocket = FakeWebSocket()

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_SCRIPT_PREVIEW_REQUEST",
                "correlationId": "preview-unauthenticated",
                "payload": {"script": "Do not accept this.", "references": []},
            },
        )
    )

    response = websocket.messages[-1]
    assert response["success"] is False
    assert response["status"] == "AUTHENTICATION_REQUIRED"
    assert "payload" not in response
    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_SCRIPT_PREVIEW_REVIEW_REQUEST",
                "correlationId": "review-unauthenticated",
                "payload": {
                    "scriptId": "00000000-0000-0000-0000-000000000000",
                    "artifactSha256": "a" * 64,
                    "editorialVerdict": "READY_FOR_FACTUAL_REVIEW",
                    "narrationAssessment": "INTELLIGIBLE",
                    "pacingAssessment": "APPROPRIATE",
                    "captionAssessment": "SYNCHRONIZED",
                    "sceneAssessment": "COHERENT",
                    "factualReviewAttested": False,
                    "notes": "",
                },
            },
        )
    )
    review_response = websocket.messages[-1]
    assert review_response["action"] == "ERROR"
    assert review_response["success"] is False
    assert review_response["status"] == "AUTHENTICATION_REQUIRED"
    assert "payload" not in review_response
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_original_script_preview_reviews"
    ).fetchone()[0] == 0
    store.close()
    gateway.operation_store.close()
