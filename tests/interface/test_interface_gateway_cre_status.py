import asyncio
import base64
import hashlib
import json
import math
import subprocess
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ultron.application.content.service import ContentRightsService
from ultron.application.content.status import (
    ContentOwnerMapping,
    ContentStatusService,
)
from ultron.application.content.acquisition import ContentAcquisitionService
from ultron.application.content.discovery import ContentDiscoveryService
from ultron.application.content.nasa import (
    NASAImagesAcquisitionAdapter,
    NASAVideoCandidate,
)
from ultron.application.content.provider_network import ProviderSearchResult
from ultron.application.content.script_preview import OriginalScriptPreviewService
from ultron.application.content.clip_renderer import FFmpegLocalClipRenderer
from ultron.application.content.intelligence_service import ContentIntelligenceService
from ultron.application.content.processing import LocalTemporaryContentStore
from ultron.application.content.rendering import ContentClipRenderingService
from ultron.application.content.sources import SourceDocument
from ultron.application.content.store import ContentStore
from ultron.application.runtime.interface_gateway import InterfaceGateway
from ultron.application.runtime.operation_store import SQLiteOperationStore
from ultron.domain.content import ContentPlatform, RightsAction
from ultron.domain.content_acquisition import AcquisitionIntendedUse
from ultron.domain.content import ContentEditProject, ContentTargetContext, RightsUse
from ultron.domain.content_intelligence import (
    EngineTranscript,
    EngineTranscriptSegment,
    HighlightConfig,
    TranscriptionConfig,
)
from ultron.domain.content_owner_processing import OwnerLocalProcessingOperation
from ultron.security.identity.authenticator import WebSocketAuthenticator
from tests.application.test_content_rendering import (
    _source_video_bytes,
    _video_only_source_bytes,
)
from tests.application.test_content_intelligence import FakeTranscriptionEngine
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
    store.migrate_owner_local_processing_schema()
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


def _create_scored_opportunity(store, *, key):
    store.migrate_source_identity_schema()
    store.migrate_acquisition_request_schema()
    discovery = ContentDiscoveryService(store)
    url = f"https://example.com/authorized/{key}"
    opportunity = discovery.discover(
        topic=f"Authorized source {key}",
        niche="education",
        target_audience="learners",
        target_language="en",
        content_format="short",
        source_documents=(
            SourceDocument(
                canonical_url=url,
                title=f"Authorized source {key}",
                summary="Persisted source observation.",
                published_at=datetime.now(UTC),
                publisher="Example",
                source_identity=f"rss_atom:url-sha256:{hashlib.sha256(url.encode()).hexdigest()}",
                provider="rss_atom",
                provider_content_id=key,
                original_url=url,
            ),
        ),
    )
    researched, _ = discovery.research(
        opportunity,
        summary="Evidence-backed test opportunity.",
    )
    discovery.persist_score(researched)
    source_id = store.list_content_evidence(opportunity.opportunity_id)[0].source_id
    return opportunity, source_id


def _create_upload_request(gateway, store, websocket, *, key):
    opportunity, source_id = _create_scored_opportunity(store, key=key)
    parameters = {
        "opportunity_id": opportunity.opportunity_id,
        "source_id": source_id,
        "intended_use": AcquisitionIntendedUse.EDITORIAL_DEVELOPMENT.value,
        "idempotency_key": key,
    }
    context = gateway._cre_authorization_context(
        websocket,
        f"select-{key}",
        action="content_request_acquisition",
        parameters=parameters,
    )
    request = ContentAcquisitionService(
        store=store,
        authority=gateway.approval_authorizer,
    ).select_opportunity(
        opportunity_id=opportunity.opportunity_id,
        source_id=source_id,
        intended_use=AcquisitionIntendedUse.EDITORIAL_DEVELOPMENT,
        idempotency_key=key,
        context=context,
    )
    return request.request_id


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


def test_authenticated_discovery_selection_creates_recoverable_acquisition_request(
    tmp_path,
):
    gateway, store, authenticator, _, _ = _configured_source_gateway(
        tmp_path / "discovery-selection.db",
    )
    websocket = FakeWebSocket()
    authenticate_test_websocket(authenticator, websocket)
    opportunity, source_id = _create_scored_opportunity(
        store,
        key="gateway-opportunity-selection",
    )

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_ACQUISITION_OPPORTUNITIES_REQUEST",
                "correlationId": "list-selectable-opportunities",
                "payload": {},
            },
        )
    )
    listed = websocket.messages[-1]
    assert listed["success"] is True
    assert listed["status"] == "ACQUISITION_OPPORTUNITIES_AVAILABLE"
    assert listed["payload"]["opportunities"] == [
        {
            "opportunityId": opportunity.opportunity_id,
            "topic": opportunity.topic,
            "niche": opportunity.niche,
            "targetAudience": opportunity.target_audience,
            "targetLanguage": opportunity.target_language,
            "contentFormat": opportunity.content_format,
            "sourceId": source_id,
            "sourceIdentity": store.list_content_opportunity_sources(
                opportunity.opportunity_id,
            )[0].source_identity,
            "sourceReference": "https://example.com/authorized/gateway-opportunity-selection",
            "sourceTitle": opportunity.topic,
            "provider": "rss_atom",
        }
    ]

    selection = {
        "opportunityId": opportunity.opportunity_id,
        "sourceId": source_id,
        "intendedUse": AcquisitionIntendedUse.EDITORIAL_DEVELOPMENT.value,
        "idempotencyKey": "gateway-selection-once",
    }
    for correlation_id in ("select-opportunity", "retry-opportunity-selection"):
        asyncio.run(
            gateway.handle(
                websocket,
                {
                    "action": "CRE_ACQUISITION_SELECT_REQUEST",
                    "correlationId": correlation_id,
                    "payload": selection,
                },
            )
        )
        response = websocket.messages[-1]
        assert response["success"] is True
        assert response["status"] == "ACQUISITION_REQUEST_SELECTED"

    request_id = websocket.messages[-1]["payload"]["requestId"]
    request = store.get_content_acquisition_request(request_id)
    assert request.owner_id == authenticator.principal
    assert request.source_id == source_id
    assert request.intended_use is AcquisitionIntendedUse.EDITORIAL_DEVELOPMENT
    assert request.state.value == "awaiting_adapter"
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_acquisition_requests WHERE opportunity_id = ?",
        (opportunity.opportunity_id,),
    ).fetchone()[0] == 1

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_ACQUISITION_REQUESTS_REQUEST",
                "correlationId": "list-owner-acquisition-requests",
                "payload": {},
            },
        )
    )
    listed_requests = websocket.messages[-1]["payload"]["requests"]
    assert len(listed_requests) == 1
    assert listed_requests[0]["requestId"] == request_id
    assert listed_requests[0]["state"] == "awaiting_adapter"
    store.close()
    gateway.operation_store.close()


def test_authenticated_gateway_search_persists_provider_opportunity(
    tmp_path,
    monkeypatch,
):
    gateway, store, authenticator, _, _ = _configured_source_gateway(
        tmp_path / "gateway-provider-discovery.db",
    )
    store.migrate_source_identity_schema()
    websocket = FakeWebSocket()
    authenticate_test_websocket(authenticator, websocket)
    candidate = NASAVideoCandidate(
        nasa_id="NASA_GATEWAY_TEST_001",
        title="Gateway solar observation",
        center="NASA",
        description="Provider discovery metadata.",
        metadata_url="https://images-api.nasa.gov/search?nasa_id=NASA_GATEWAY_TEST_001",
        canonical_url="https://images-api.nasa.gov/search?nasa_id=NASA_GATEWAY_TEST_001",
        asset_url="https://images-assets.nasa.gov/video/NASA_GATEWAY_TEST_001/source.mp4",
        copyright_notice=None,
        photographer=None,
    )
    adapter = NASAImagesAcquisitionAdapter()
    monkeypatch.setattr(
        adapter,
        "search_with_status",
        lambda *_args, **_kwargs: ProviderSearchResult(
            status=200,
            candidates=(candidate,),
        ),
    )
    gateway.cre_discovery_service = ContentDiscoveryService(
        store,
        provider_adapters={"nasa": adapter},
    )
    try:
        asyncio.run(
            gateway.handle(
                websocket,
                {
                    "action": "CRE_ACQUISITION_OPPORTUNITIES_REQUEST",
                    "correlationId": "provider-discovery",
                    "payload": {
                        "provider": "nasa",
                        "query": "solar observation",
                        "niche": "science",
                        "targetAudience": "learners",
                        "targetLanguage": "en",
                        "contentFormat": "short",
                        "pageSize": 5,
                    },
                },
            )
        )
        response = websocket.messages[-1]
        assert response["success"] is True
        assert response["status"] == "DISCOVERY_RESULTS_PERSISTED"
        assert response["payload"]["discovery"] == {
            "provider": "nasa",
            "query": "solar observation",
            "httpStatus": 200,
            "resultCount": 1,
            "persistedOpportunityCount": 1,
        }
        persisted = response["payload"]["opportunities"][0]
        assert persisted["acquisitionEligible"] is False
        opportunity = store.get_content_opportunity(persisted["opportunityId"])
        assert opportunity is not None
        assert opportunity.status.value == "discovered"
        source = store.list_content_opportunity_sources(opportunity.opportunity_id)[0]
        assert source.provider_content_id == candidate.nasa_id
        assert source.canonical_url == candidate.canonical_url
        assert source.source_identity == persisted["sourceIdentity"]
        assert store._connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert store._connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
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
                    "acquisitionRequestId": "request-1",
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
    request_id = _create_upload_request(
        gateway,
        store,
        websocket,
        key="video-only-upload",
    )
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
                    "acquisitionRequestId": request_id,
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
    assert asset_summary["acquisitionRequestId"] == request_id
    assert asset_summary["acquisitionState"] == "awaiting_rights"
    assert asset_summary["rightsStatus"] == "pending_asset_evaluation"
    assert store.get_owner_local_processing_authorization_for_asset(
        asset.asset_id,
        asset.content_sha256,
        authenticator.principal,
    ) is None
    with pytest.raises(PermissionError):
        with store._transaction() as connection:
            store.require_owner_local_processing_authorization_in_transaction(
                connection,
                owner_id=authenticator.principal,
                asset_id=asset.asset_id,
                asset_sha256=asset.content_sha256,
                operation=OwnerLocalProcessingOperation.TRANSCRIBE,
                actor=authenticator.principal,
            )
    initial_event_count = store._connection.execute(
        """
        SELECT COUNT(*) FROM content_acquisition_request_events
        WHERE request_id = ?
        """,
        (request_id,),
    ).fetchone()[0]

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_SOURCE_VIDEO_REQUEST",
                "correlationId": "browser-video-only-retry",
                "payload": {
                    "videoBase64": base64.b64encode(source_bytes).decode("ascii"),
                    "fileName": "silent-source.mp4",
                    "mediaType": "video/mp4",
                    "targetContext": "youtube_shorts",
                    "accountId": "private-local-owner",
                    "territory": "US",
                    "acquisitionRequestId": request_id,
                },
            },
        )
    )
    assert websocket.messages[-1]["success"] is True
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_assets WHERE asset_id = ?",
        (asset.asset_id,),
    ).fetchone()[0] == 1
    assert store._connection.execute(
        """
        SELECT COUNT(*) FROM content_acquisition_request_events
        WHERE request_id = ?
        """,
        (request_id,),
    ).fetchone()[0] == initial_event_count

    conflicting_bytes = _source_video_bytes(tmp_path)
    assert conflicting_bytes != source_bytes
    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_SOURCE_VIDEO_REQUEST",
                "correlationId": "browser-video-only-conflicting-retry",
                "payload": {
                    "videoBase64": base64.b64encode(conflicting_bytes).decode("ascii"),
                    "fileName": "silent-source.mp4",
                    "mediaType": "video/mp4",
                    "targetContext": "youtube_shorts",
                    "accountId": "private-local-owner",
                    "territory": "US",
                    "acquisitionRequestId": request_id,
                },
            },
        )
    )
    assert websocket.messages[-1]["success"] is False
    assert store.get_asset(asset.asset_id).content_sha256 == hashlib.sha256(
        source_bytes
    ).hexdigest()
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_assets",
    ).fetchone()[0] == 1
    store.close()
    gateway.operation_store.close()


def test_authenticated_original_owner_attestation_is_explicit_and_not_independent(
    tmp_path,
):
    gateway, store, authenticator, _, _ = _configured_source_gateway(
        tmp_path / "owner-attestation.db",
    )
    websocket = FakeWebSocket()
    authenticate_test_websocket(authenticator, websocket)
    source_bytes = _video_only_source_bytes(
        tmp_path,
        name="owner-attestation",
    )
    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_SOURCE_VIDEO_REQUEST",
                "correlationId": "owner-created-intake",
                "payload": {
                    "videoBase64": base64.b64encode(source_bytes).decode("ascii"),
                    "fileName": "Owner supplied original.mp4",
                    "mediaType": "video/mp4",
                    "targetContext": "youtube_shorts",
                    "accountId": "channel-1",
                    "territory": "US",
                    "localProcessingOperations": [
                        "transcribe",
                        "analyze_audio",
                        "analyze_video",
                        "detect_highlights",
                        "generate_edit_plan",
                        "render_private",
                    ],
                },
            },
        )
    )
    upload = websocket.messages[-1]
    assert upload["success"] is True, upload.get("error")
    asset = upload["payload"]["asset"]
    assert asset["sha256"] == hashlib.sha256(source_bytes).hexdigest()
    assert asset["fileName"] == "Owner supplied original.mp4"
    assert asset["transferChannel"] == "direct_websocket_upload"
    assert asset["rightsStatus"] == "local_processing_authorized"
    assert set(asset["ownerProcessingAuthorization"]["operations"]) == {
        "transcribe",
        "analyze_audio",
        "analyze_video",
        "detect_highlights",
        "generate_edit_plan",
        "render_private",
    }
    authorization = store.get_owner_local_processing_authorization(
        asset["ownerProcessingAuthorization"]["authorizationId"],
        authenticator.principal,
    )
    assert authorization is not None
    assert (
        datetime.fromisoformat(asset["ownerProcessingAuthorization"]["expiresAt"])
        == authorization.expires_at
    )
    assert authorization.expires_at - authorization.authorized_at == (
        store.OWNER_LOCAL_PROCESSING_AUTHORIZATION_TTL
    )

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_SOURCE_RIGHTS_REQUEST",
                "correlationId": "owner-attestation",
                "payload": {
                    "step": "self_attest_original",
                    "assetId": asset["assetId"],
                    "targetContext": "youtube_shorts",
                    "accountScope": "channel-1",
                    "territories": ["US"],
                    "ownershipDeclaration": True,
                    "ownershipBasis": (
                        "I created and own this original footage and have rights "
                        "to its audio and included material."
                    ),
                    "evidenceReference": None,
                    "limitations": "YouTube Shorts, US territory, up to 60 seconds.",
                    "maxDurationSeconds": 60,
                },
            },
        )
    )
    result = websocket.messages[-1]
    assert result["success"] is True, result
    assert result["payload"]["rightsState"] == "self_attested"
    assert result["payload"]["status"] == "active"
    assert result["payload"]["independentlyVerified"] is False
    stored = store.get_owner_rights_attestation(
        result["payload"]["attestationId"],
        authenticator.principal,
    )
    assert stored is not None
    assert stored.asset_sha256 == asset["sha256"]
    assert stored.transfer_channel == "direct_websocket_upload"
    store.close()
    gateway.operation_store.close()


def test_owner_local_authorization_is_hash_scoped_revocable_and_local_only(tmp_path):
    gateway, store, authenticator, _, _ = _configured_source_gateway(
        tmp_path / "owner-local-authorization.db",
    )
    websocket = FakeWebSocket()
    authenticate_test_websocket(authenticator, websocket)
    source_bytes = _video_only_source_bytes(
        tmp_path,
        name="owner-local-authorization",
    )
    operations = [
        "transcribe",
        "analyze_audio",
        "analyze_video",
        "detect_highlights",
        "generate_edit_plan",
        "render_private",
    ]
    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_SOURCE_VIDEO_REQUEST",
                "correlationId": "owner-local-authorization",
                "payload": {
                    "videoBase64": base64.b64encode(source_bytes).decode("ascii"),
                    "fileName": "owner-video.mp4",
                    "mediaType": "video/mp4",
                    "targetContext": "youtube_shorts",
                    "accountId": "private-owner",
                    "territory": "US",
                    "localProcessingOperations": operations,
                },
            },
        )
    )
    upload = websocket.messages[-1]
    assert upload["success"] is True, upload.get("error")
    asset = upload["payload"]["asset"]
    authorization_id = asset["ownerProcessingAuthorization"]["authorizationId"]
    authorization = store.get_owner_local_processing_authorization(
        authorization_id,
        authenticator.principal,
    )
    assert authorization is not None
    assert authorization.asset_id == asset["assetId"]
    assert authorization.asset_sha256 == hashlib.sha256(source_bytes).hexdigest()
    assert authorization.operations == frozenset(
        OwnerLocalProcessingOperation(value) for value in operations
    )
    limited_operations = ["transcribe", "analyze_audio", "detect_highlights"]
    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_SOURCE_VIDEO_REQUEST",
                "correlationId": "owner-local-authorization-same-bytes",
                "payload": {
                    "videoBase64": base64.b64encode(source_bytes).decode("ascii"),
                    "fileName": "same-bytes-new-asset.mp4",
                    "mediaType": "video/mp4",
                    "targetContext": "youtube_shorts",
                    "accountId": "private-owner",
                    "territory": "US",
                    "localProcessingOperations": limited_operations,
                },
            },
        )
    )
    second_asset = websocket.messages[-1]["payload"]["asset"]
    assert websocket.messages[-1]["success"] is True
    assert second_asset["assetId"] != asset["assetId"]
    assert second_asset["sha256"] == asset["sha256"]
    with store._transaction() as connection:
        store.require_owner_local_processing_authorization_in_transaction(
            connection,
            owner_id=authenticator.principal,
            asset_id=asset["assetId"],
            asset_sha256=asset["sha256"],
            operation=OwnerLocalProcessingOperation.ANALYZE_VIDEO,
            actor=authenticator.principal,
        )
    with pytest.raises(PermissionError):
        with store._transaction() as connection:
            store.require_owner_local_processing_authorization_in_transaction(
                connection,
                owner_id=authenticator.principal,
                asset_id=second_asset["assetId"],
                asset_sha256=second_asset["sha256"],
                operation=OwnerLocalProcessingOperation.ANALYZE_VIDEO,
                actor=authenticator.principal,
            )

    with store._transaction() as connection:
        store.require_owner_local_processing_authorization_in_transaction(
            connection,
            owner_id=authenticator.principal,
            asset_id=asset["assetId"],
            asset_sha256=asset["sha256"],
            operation=OwnerLocalProcessingOperation.TRANSCRIBE,
            actor=authenticator.principal,
        )
    with pytest.raises(PermissionError):
        with store._transaction() as connection:
            store.require_owner_local_processing_authorization_in_transaction(
                connection,
                owner_id="another-owner",
                asset_id=asset["assetId"],
                asset_sha256=asset["sha256"],
                operation=OwnerLocalProcessingOperation.TRANSCRIBE,
                actor="another-owner",
            )
    with pytest.raises(PermissionError):
        with store._transaction() as connection:
            store.require_owner_local_processing_authorization_in_transaction(
                connection,
                owner_id=authenticator.principal,
                asset_id=asset["assetId"],
                asset_sha256="0" * 64,
                operation=OwnerLocalProcessingOperation.TRANSCRIBE,
                actor=authenticator.principal,
            )
    with pytest.raises(PermissionError):
        with store._transaction() as connection:
            store.require_owner_local_processing_authorization_in_transaction(
                connection,
                owner_id=authenticator.principal,
                asset_id="unrelated-asset",
                asset_sha256=asset["sha256"],
                operation=OwnerLocalProcessingOperation.TRANSCRIBE,
                actor=authenticator.principal,
            )
    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_SOURCE_RIGHTS_REQUEST",
                "correlationId": "owner-local-authorization-renew-wrong-hash",
                "payload": {
                    "step": "renew_local_processing_authorization",
                    "assetId": asset["assetId"],
                    "assetSha256": "0" * 64,
                    "operations": operations,
                    "reason": "Attempt to renew altered media.",
                },
            },
        )
    )
    assert websocket.messages[-1]["success"] is False
    assert (
        store.get_owner_local_processing_authorization(
            authorization_id,
            authenticator.principal,
        ).revoked_at
        is None
    )
    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_SOURCE_RIGHTS_REQUEST",
                "correlationId": "owner-local-authorization-renew-active",
                "payload": {
                    "step": "renew_local_processing_authorization",
                    "assetId": asset["assetId"],
                    "assetSha256": asset["sha256"],
                    "operations": operations,
                    "reason": "Owner renewed active local-processing permission.",
                },
            },
        )
    )
    active_renewal_result = websocket.messages[-1]
    assert active_renewal_result["success"] is True, active_renewal_result.get(
        "error"
    )
    active_renewed_id = active_renewal_result["payload"]["authorizationId"]
    assert active_renewed_id != authorization_id
    assert (
        store.get_owner_local_processing_authorization(
            authorization_id,
            authenticator.principal,
        ).revoked_at
        is not None
    )
    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_SOURCE_RIGHTS_REQUEST",
                "correlationId": "owner-local-authorization-revoke",
                "payload": {
                    "step": "revoke_local_processing_authorization",
                    "authorizationId": active_renewed_id,
                    "reason": "Owner withdrew local-processing permission.",
                },
            },
        )
    )
    revoked = websocket.messages[-1]
    assert revoked["success"] is True, revoked
    assert revoked["payload"]["revokedAt"] is not None
    assert store.get_owner_local_processing_authorization(
        active_renewed_id,
        authenticator.principal,
    ).revoked_at is not None
    with pytest.raises(PermissionError):
        with store._transaction() as connection:
            store.require_owner_local_processing_authorization_in_transaction(
                connection,
                owner_id=authenticator.principal,
                asset_id=asset["assetId"],
                asset_sha256=asset["sha256"],
                operation=OwnerLocalProcessingOperation.TRANSCRIBE,
                actor=authenticator.principal,
            )
    events = store.list_owner_local_processing_events(
        authorization_id,
        authenticator.principal,
    )
    assert [event["event_type"] for event in events] == [
        "authorized",
        "operation_authorized",
        "operation_authorized",
        "revoked",
    ]
    assert all("secret" not in str(event).lower() for event in events)
    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_SOURCE_RIGHTS_REQUEST",
                "correlationId": "owner-local-authorization-renew-invalid-scope",
                "payload": {
                    "step": "renew_local_processing_authorization",
                    "assetId": asset["assetId"],
                    "assetSha256": asset["sha256"],
                    "operations": ["public_publish"],
                    "reason": "Attempt to add a publishing scope.",
                },
            },
        )
    )
    assert websocket.messages[-1]["success"] is False
    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_SOURCE_RIGHTS_REQUEST",
                "correlationId": "owner-local-authorization-renew",
                "payload": {
                    "step": "renew_local_processing_authorization",
                    "assetId": asset["assetId"],
                    "assetSha256": asset["sha256"],
                    "operations": operations,
                    "reason": "Owner renewed private local-processing permission.",
                },
            },
        )
    )
    renewed_result = websocket.messages[-1]
    assert renewed_result["success"] is True, renewed_result.get("error")
    renewed_id = renewed_result["payload"]["authorizationId"]
    assert renewed_id not in {authorization_id, active_renewed_id}
    renewed = store.get_owner_local_processing_authorization(
        renewed_id,
        authenticator.principal,
    )
    assert renewed is not None
    assert renewed.asset_id == asset["assetId"]
    assert renewed.asset_sha256 == asset["sha256"]
    assert renewed.operations == frozenset(
        OwnerLocalProcessingOperation(value) for value in operations
    )
    assert renewed.expires_at - renewed.authorized_at == timedelta(days=365)
    renewed_events = store.list_owner_local_processing_events(
        renewed_id,
        authenticator.principal,
    )
    assert [event["event_type"] for event in renewed_events] == ["authorized"]
    assert "renewal" in renewed_events[0]["reason"]
    with store._transaction() as connection:
        store.require_owner_local_processing_authorization_in_transaction(
            connection,
            owner_id=authenticator.principal,
            asset_id=asset["assetId"],
            asset_sha256=asset["sha256"],
            operation=OwnerLocalProcessingOperation.TRANSCRIBE,
            actor=authenticator.principal,
        )
    with pytest.raises(ValueError):
        OwnerLocalProcessingOperation("public_publish")
    store.close()
    gateway.operation_store.close()


def test_unauthenticated_owner_local_upload_is_rejected_before_registration(tmp_path):
    gateway, store, _, _, _ = _configured_source_gateway(
        tmp_path / "unauthenticated-owner-upload.db",
    )
    websocket = FakeWebSocket()
    source_bytes = _video_only_source_bytes(
        tmp_path,
        name="unauthenticated-owner-upload",
    )
    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_SOURCE_VIDEO_REQUEST",
                "correlationId": "unauthenticated-owner-upload",
                "payload": {
                    "videoBase64": base64.b64encode(source_bytes).decode("ascii"),
                    "fileName": "owner-video.mp4",
                    "mediaType": "video/mp4",
                    "targetContext": "youtube_shorts",
                    "accountId": "untrusted-client-owner",
                    "territory": "US",
                    "localProcessingOperations": ["transcribe"],
                },
            },
        )
    )
    assert websocket.messages[-1]["status"] == "AUTHENTICATION_REQUIRED"
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_assets"
    ).fetchone()[0] == 0
    store.close()
    gateway.operation_store.close()


def test_authenticated_owner_upload_transcribes_plans_and_renders_private(
    tmp_path,
):
    gateway, store, authenticator, clip_service, storage = _configured_source_gateway(
        tmp_path / "owner-local-workflow.db",
    )
    websocket = FakeWebSocket()
    authenticate_test_websocket(authenticator, websocket)
    source_bytes = _source_video_bytes(tmp_path)
    operations = [
        "transcribe",
        "analyze_audio",
        "analyze_video",
        "detect_highlights",
        "generate_edit_plan",
        "render_private",
    ]
    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "CRE_SOURCE_VIDEO_REQUEST",
                "correlationId": "owner-local-workflow-upload",
                "payload": {
                    "videoBase64": base64.b64encode(source_bytes).decode("ascii"),
                    "fileName": "owner-local-workflow.mp4",
                    "mediaType": "video/mp4",
                    "targetContext": "youtube_shorts",
                    "accountId": "private-owner",
                    "territory": "US",
                    "localProcessingOperations": operations,
                },
            },
        )
    )
    upload = websocket.messages[-1]
    assert upload["success"] is True, upload.get("error")
    asset_summary = upload["payload"]["asset"]
    asset = store.get_asset(asset_summary["assetId"])
    assert asset is not None
    assert asset.content_sha256 == hashlib.sha256(source_bytes).hexdigest()
    assert set(asset_summary["ownerProcessingAuthorization"]["operations"]) == set(
        operations
    )
    use = gateway._cre_target_use(
        asset_id=asset.asset_id,
        asset_sha256=asset.content_sha256,
        target=ContentTargetContext.YOUTUBE_SHORTS,
        account_id="private-owner",
        territory="US",
        action=RightsAction.ANALYZE,
        duration_seconds=math.ceil(asset_summary["durationSeconds"]),
    )
    intelligence = ContentIntelligenceService(
        content_rights=clip_service.content_rights,
        transcription_engine=FakeTranscriptionEngine(),
        media_renderer=clip_service.renderer,
    )
    request_sequence = 0

    def invoke(action, parameters, function, **kwargs):
        nonlocal request_sequence
        request_sequence += 1
        correlation_id = f"owner-local-workflow-{request_sequence}"
        context = gateway._cre_authorization_context(
            websocket,
            correlation_id,
            action=action,
            parameters=parameters,
        )
        return function(
            context=context,
            command_id=correlation_id,
            **kwargs,
        )

    config = TranscriptionConfig()
    transcription_payload = {
        "use": use.model_dump(mode="json"),
        "target_context": ContentTargetContext.YOUTUBE_SHORTS.value,
        "config": config.model_dump(mode="json"),
    }
    job = invoke(
        "content_create_transcription_job",
        transcription_payload,
        intelligence.create_transcription_job,
        use=use,
        target_context=ContentTargetContext.YOUTUBE_SHORTS,
        config=config,
    )
    transcript = invoke(
        "content_run_transcription_job",
        {"job_id": job.job_id},
        intelligence.run_transcription_job,
        job_id=job.job_id,
    )
    invoke(
        "content_read_transcript",
        {"transcript_id": transcript.transcript_id},
        intelligence.get_transcript,
        transcript_id=transcript.transcript_id,
    )
    highlight_config = HighlightConfig()
    highlights = invoke(
        "content_generate_highlights",
        {
            "transcript_id": transcript.transcript_id,
            "config": highlight_config.model_dump(mode="json"),
        },
        intelligence.generate_highlights,
        transcript_id=transcript.transcript_id,
        config=highlight_config,
    )
    project = ContentEditProject(
        project_id="owner-local-workflow-project",
        owner_id=authenticator.principal,
        source_asset_ids=(asset.asset_id,),
        script="Create a private local review edit.",
    )
    invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        clip_service.content_rights.create_edit_project,
        project=project,
    )
    instruction = "Create a short private review edit."
    plan = invoke(
        "content_autoplan_edit_project",
        {
            "project_id": project.project_id,
            "instruction_length": len(instruction),
        },
        clip_service.autoplan_edit_project,
        project_id=project.project_id,
        instruction=instruction,
    )
    render_use = RightsUse(
        asset_id=asset.asset_id,
        expected_asset_sha256=asset.content_sha256,
        platform=ContentPlatform.YOUTUBE,
        account_id="private-owner",
        territory="US",
        actions=frozenset({RightsAction.EDIT, RightsAction.CREATE_DERIVATIVE}),
        commercial_intent=False,
        monetization_intent=False,
        duration_seconds=math.ceil(plan["targetDurationSeconds"]),
    )
    rendered = invoke(
        "content_render_edit_project",
        {
            "project_id": project.project_id,
            "asset_ids": [asset.asset_id],
            "vertical": True,
        },
        clip_service.render_edit_project,
        project_id=project.project_id,
        rights_uses={asset.asset_id: render_use},
        vertical=True,
    )
    output_path = tmp_path / "owner-local-workflow-private.mp4"
    output_path.write_bytes(
        storage.read_clip_output(
            str(rendered["artifactId"]),
            str(rendered["sha256"]),
        )
    )
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
        rendered["durationSeconds"],
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
    assert transcript.asset_id == asset.asset_id
    assert highlights.transcript_id == transcript.transcript_id
    assert plan["projectId"] == project.project_id
    assert store._connection.execute(
        "SELECT COUNT(*) FROM rights_grants WHERE asset_id = ?",
        (asset.asset_id,),
    ).fetchone()[0] == 0
    assert store.get_latest_asset_license_record(
        asset.asset_id,
        authenticator.principal,
    ) is None
    store.close()
    gateway.operation_store.close()


def test_source_upload_recovers_after_registration_transaction_failure(
    tmp_path,
    monkeypatch,
):
    gateway, store, authenticator, _, storage = _configured_source_gateway(
        tmp_path / "upload-recovery.db",
    )
    websocket = FakeWebSocket()
    authenticate_test_websocket(authenticator, websocket)
    request_id = _create_upload_request(
        gateway,
        store,
        websocket,
        key="upload-recovery",
    )
    source_bytes = _video_only_source_bytes(
        tmp_path,
        name="gateway-upload-recovery",
    )
    original_bind = store.bind_content_acquisition_asset_in_transaction
    calls = 0

    def fail_once(connection, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("Injected failure before acquisition commit.")
        return original_bind(connection, **kwargs)

    monkeypatch.setattr(
        store,
        "bind_content_acquisition_asset_in_transaction",
        fail_once,
    )
    upload_message = {
        "action": "CRE_SOURCE_VIDEO_REQUEST",
        "correlationId": "upload-recovery-first-attempt",
        "payload": {
            "videoBase64": base64.b64encode(source_bytes).decode("ascii"),
            "fileName": "recoverable-source.mp4",
            "mediaType": "video/mp4",
            "targetContext": "youtube_shorts",
            "accountId": "private-local-owner",
            "territory": "US",
            "acquisitionRequestId": request_id,
        },
    }
    asyncio.run(gateway.handle(websocket, upload_message))
    assert websocket.messages[-1]["success"] is False
    assert store.get_content_acquisition_request(request_id).asset_id is None
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_assets",
    ).fetchone()[0] == 0
    assert tuple((storage._root / "inputs").iterdir()) == ()

    monkeypatch.setattr(
        store,
        "bind_content_acquisition_asset_in_transaction",
        original_bind,
    )
    upload_message["correlationId"] = "upload-recovery-retry"
    asyncio.run(gateway.handle(websocket, upload_message))
    response = websocket.messages[-1]
    assert response["success"] is True
    asset_id = response["payload"]["asset"]["assetId"]
    asset = store.get_asset(asset_id)
    assert asset.content_sha256 == hashlib.sha256(source_bytes).hexdigest()
    assert store.get_content_acquisition_request(request_id).asset_id == asset_id
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_assets",
    ).fetchone()[0] == 1
    assert calls == 1
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
    request_id = _create_upload_request(
        gateway,
        store,
        websocket,
        key="folder-video-only-upload",
    )

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
                    "acquisitionRequestId": request_id,
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
    request_id = _create_upload_request(
        gateway,
        store,
        websocket,
        key=f"reject-{file_name}",
    )
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
                    "acquisitionRequestId": request_id,
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
    request_id = _create_upload_request(
        gateway,
        store,
        websocket,
        key="oversized-source-upload",
    )
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
                    "acquisitionRequestId": request_id,
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
    request_id = _create_upload_request(
        gateway,
        store,
        owner_socket,
        key="rights-review-source-upload",
    )
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
                    "acquisitionRequestId": request_id,
                },
            },
        )
    )
    upload = owner_socket.messages[-1]
    asset = upload["payload"]["asset"]
    assert upload["status"] == "SOURCE_REGISTERED"
    assert asset["acquisitionRequestId"] == request_id
    assert asset["acquisitionState"] == "awaiting_rights"
    assert asset["rightsStatus"] == "pending_asset_evaluation"
    request = store.get_content_acquisition_request(request_id)
    assert request.asset_id == asset["assetId"]
    assert request.state.value == "awaiting_rights"
    assert store.get_asset(asset["assetId"]).content_sha256 == hashlib.sha256(video_bytes).hexdigest()
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_processing_jobs"
    ).fetchone()[0] == 0

    asyncio.run(
        gateway.handle(
            owner_socket,
            {
                "action": "CRE_SOURCE_VIDEO_RESUME_REQUEST",
                "correlationId": "resume-before-intended-use-clearance",
                "payload": {
                    "assetId": asset["assetId"],
                    "targetContext": "youtube_shorts",
                    "accountId": "channel-1",
                    "territory": "US",
                },
            },
        )
    )
    assert owner_socket.messages[-1]["success"] is False
    assert fixture_engine.calls == 0
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_processing_jobs WHERE asset_id = ?",
        (asset["assetId"],),
    ).fetchone()[0] == 0

    evidence_bytes = b"fixture rights agreement evidence"
    terms = {
        "platform": ContentPlatform.YOUTUBE.value,
        "account_scope": "channel-1",
        "territories": ["US"],
        "permitted_actions": [
            RightsAction.ANALYZE.value,
            RightsAction.EDIT.value,
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
    assert submitted["success"] is True, submitted
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
    assert current_status["payload"]["intendedUseRightsStatus"] == "cleared"
    assert current_status["payload"]["acquisitionState"] == "completed"
    assert current_status["payload"]["acquisitionRightsStatus"] == "cleared"
    assert store.get_content_acquisition_request(request_id).state.value == "completed"
    asyncio.run(
        gateway.handle(
            owner_socket,
            {
                "action": "CRE_SOURCE_ASSET_STATUS_REQUEST",
                "correlationId": "asset-rights-status-retry",
                "payload": {
                    "assetId": asset["assetId"],
                    "targetContext": "youtube_shorts",
                    "accountId": "channel-1",
                    "territory": "US",
                },
            },
        )
    )
    assert owner_socket.messages[-1]["success"] is True
    assert owner_socket.messages[-1]["payload"]["acquisitionState"] == "completed"

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
                    "acquisitionRequestId": "selected-request-id",
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
                    "acquisitionRequestId": "selected-request-id",
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
        "acquisitionRequestId",
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
            "acquisitionRequestId": "selected-request-id",
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
