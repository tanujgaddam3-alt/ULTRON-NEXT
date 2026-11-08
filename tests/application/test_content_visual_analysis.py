from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import uuid4

import pytest

from tests.application.schema_test_utils import set_legacy_schema_version
from tests.application.test_content_lifecycle import Harness
from tests.application.test_content_rights import (
    RightsHarness,
    add_grant,
    make_terms,
)
from ultron.application.content.service import ContentRightsService
from ultron.application.content.store import (
    ContentStore,
    VisualAnalysisConflictError,
)
from ultron.application.content.visual_analysis import (
    VisualAnalysisLifecycleService,
    VisualAnalysisWorker,
)
from ultron.domain.content_visual_analysis import (
    VisualAnalysisRequest,
    VisualAnalysisRetentionPolicy,
    VisualAnalysisState,
)
from ultron.domain.content_visual_intelligence import (
    VisualFrameAnalysis,
    VisualFrameEvidence,
    VisualInferenceDevice,
    VisualInferenceResources,
    VisualVideoAnalysis,
)
from ultron.domain.content import (
    AcquisitionMethod,
    AssetCategory,
    ContentPlatform,
    ContentEditProject,
    ContentTargetContext,
    OwnershipClaim,
    RightsAction,
    RightsUse,
    SourceAccessPermissionState,
    SourceAsset,
    SourceProvenance,
    SourceReferenceRecord,
)
from ultron.domain.content_owner_processing import (
    OwnerLocalProcessingAuthorization,
    OwnerLocalProcessingOperation,
)
from ultron.security.identity.authorization import (
    AuthorizationContext,
    resource_fingerprint,
)
from ultron.security.identity.authenticator import WebSocketAuthenticator


_MEDIA = b"synthetic test source bytes"
_MEDIA_SHA256 = hashlib.sha256(_MEDIA).hexdigest()
_MODEL_DIGEST = "a" * 64
_TOKEN = "S" * 43


def _authorized_service(store: ContentStore, principal: str):
    authority = WebSocketAuthenticator(_TOKEN, principal)
    websocket = object()
    assert authority.authenticate(websocket, _TOKEN) is not None
    content_rights = ContentRightsService(store=store, authority=authority)
    return (
        VisualAnalysisLifecycleService(
            store=store,
            authority=authority,
            content_rights=content_rights,
        ),
        authority,
        websocket,
    )


def _worker(store: ContentStore) -> VisualAnalysisWorker:
    authority = WebSocketAuthenticator(_TOKEN, "alice")
    return VisualAnalysisWorker(
        store=store,
        content_rights=ContentRightsService(store=store, authority=authority),
    )


def _context(authority, websocket, action: str, payload: dict) -> AuthorizationContext:
    operation = {
        "operationId": str(uuid4()),
        "correlationId": str(uuid4()),
        "requestFingerprint": hashlib.sha256(str(uuid4()).encode()).hexdigest(),
    }
    grant = authority.authorize(
        websocket=websocket,
        operation=operation,
        purpose="content_visual_analysis",
        request_correlation_id=str(uuid4()),
        action=action,
        resource_fingerprint=resource_fingerprint(action, payload),
    )
    assert grant is not None
    return AuthorizationContext.from_grant(grant)


def _create(service, authority, websocket, request):
    payload = request.model_dump(mode="json")
    return service.create(
        _context(
            authority,
            websocket,
            "content_visual_analysis_create",
            payload,
        ),
        request,
    )


def _get(service, authority, websocket, analysis_id):
    payload = {"analysis_id": analysis_id}
    return service.get(
        _context(
            authority,
            websocket,
            "content_visual_analysis_status",
            payload,
        ),
        analysis_id,
    )


def _events(service, authority, websocket, analysis_id):
    payload = {"analysis_id": analysis_id}
    return service.events(
        _context(
            authority,
            websocket,
            "content_visual_analysis_events",
            payload,
        ),
        analysis_id,
    )


def _store(tmp_path, *, migrate_visual: bool = True) -> ContentStore:
    store = ContentStore(tmp_path / "disposable-cre.db")
    store.migrate_publishing_attempt_schema()
    store.migrate_private_render_operation_schema()
    store.migrate_owner_local_processing_schema()
    if migrate_visual:
        assert store.migrate_visual_analysis_schema() == 2
        assert store.migrate_visual_analysis_schema() == 2
    return store


def _register_owner_asset_with_operations(
    harness: Harness,
    *,
    asset_id: str,
    operations: frozenset[OwnerLocalProcessingOperation],
) -> SourceAsset:
    asset_sha256 = hashlib.sha256(_MEDIA).hexdigest()
    now = datetime.now(UTC)
    source_reference_value = f"urn:ultron:owner-created:{asset_sha256}"
    asset = SourceAsset(
        asset_id=asset_id,
        owner_id=harness.authority.principal,
        category=AssetCategory.FOOTAGE,
        content_sha256=asset_sha256,
        provenance=SourceProvenance(
            acquisition_method=AcquisitionMethod.OWNER_CREATED,
            acquired_at=now,
            original_filename=f"{asset_id}.mp4",
            transfer_channel="direct_test_upload",
            source_reference=source_reference_value,
        ),
        storage_key=f"{asset_id}-input",
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
        duration_seconds=10,
    )
    source_reference = SourceReferenceRecord(
        intake_id=f"{asset_id}-intake",
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
    payload = {
        "asset": asset.model_dump(mode="json"),
        "source_reference": source_reference.model_dump(mode="json"),
        "owner_local_processing_operations": sorted(
            operation.value for operation in operations
        ),
    }
    harness.service.register_asset(
        asset,
        source_stream=BytesIO(_MEDIA),
        context=harness.context("content_register_owner_created_asset", payload),
        command_id=f"register-{asset_id}",
        source_reference=source_reference,
        owner_local_processing_operations=operations,
    )
    return asset


def test_visual_analysis_request_requires_exact_owner_asset_authorization(tmp_path):
    database = tmp_path / "owner-visual-analysis.db"
    harness = Harness(database)
    harness.store.migrate_owner_local_processing_schema()
    harness.store.migrate_visual_analysis_schema()
    permitted_asset = _register_owner_asset_with_operations(
        harness,
        asset_id="owner-visual-permitted",
        operations=frozenset({OwnerLocalProcessingOperation.ANALYZE_VIDEO}),
    )
    limited_asset = _register_owner_asset_with_operations(
        harness,
        asset_id="owner-visual-limited",
        operations=frozenset({OwnerLocalProcessingOperation.TRANSCRIBE}),
    )
    assert permitted_asset.content_sha256 == limited_asset.content_sha256
    for asset in (permitted_asset, limited_asset):
        project = ContentEditProject(
            project_id=f"project-{asset.asset_id}",
            owner_id=asset.owner_id,
            source_asset_ids=(asset.asset_id,),
            script="Owner local visual-analysis authorization test.",
        )
        harness.invoke(
            "content_create_edit_project",
            project.model_dump(mode="json"),
            harness.service.create_edit_project,
            project=project,
            command_id=f"create-{project.project_id}",
        )
    service = VisualAnalysisLifecycleService(
        store=harness.store,
        authority=harness.authority,
        content_rights=ContentRightsService(
            store=harness.store,
            authority=harness.authority,
        ),
    )

    def request(asset: SourceAsset, key: str) -> VisualAnalysisRequest:
        return VisualAnalysisRequest(
            analysis_id=str(uuid4()),
            owner_id=asset.owner_id,
            project_id=f"project-{asset.asset_id}",
            asset_id=asset.asset_id,
            asset_sha256=asset.content_sha256,
            idempotency_key=key,
            provider_id="local-model",
            model_id="test-vision",
            model_version=_MODEL_DIGEST,
            prompt_version="owner-local-v1",
            schema_version="visual-v1",
            sampler_version="frame-sampler-v1",
            timestamps_seconds=(0.5,),
        )

    accepted, created = _create(
        service,
        harness.authority,
        harness.websocket,
        request(permitted_asset, "permitted-visual-request"),
    )
    assert created is True
    assert accepted.state.value == "queued"
    with pytest.raises(PermissionError):
        _create(
            service,
            harness.authority,
            harness.websocket,
            request(limited_asset, "limited-visual-request"),
        )
    harness.close()


def test_visual_analysis_fails_closed_without_local_authorization_or_rights(tmp_path):
    store = _store(tmp_path)
    asset = _insert_project_and_asset(
        store,
        authorize_owner_local=False,
        acquisition_method=AcquisitionMethod.AUTHORIZED_PROVIDER,
    )
    service, authority, websocket = _authorized_service(store, "alice")
    worker = _worker(store)
    request = _request()
    with pytest.raises(PermissionError):
        _create(service, authority, websocket, request)
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_visual_analyses"
    ).fetchone()[0] == 0

    rights_harness = RightsHarness(store.database_identity, store=store)
    claim = OwnershipClaim(
        claim_id="visual-analysis-independent-rights-claim",
        asset_id=asset.asset_id,
        claimant_id=asset.owner_id,
        rights_holder="Studio North",
        authority_description="Verified rights holder",
    )
    rights_harness.invoke(
        "content_add_ownership_claim",
        claim.model_dump(mode="json"),
        rights_harness.service.add_ownership_claim,
        claim=claim,
    )
    add_grant(
        rights_harness,
        asset,
        claim,
        make_terms(
            actions=frozenset({RightsAction.ANALYZE}),
            account_scope="private-owner",
            max_duration_seconds=10,
        ),
    )

    accepted, created = _create(service, authority, websocket, request)
    assert created is True
    assert accepted.state is VisualAnalysisState.QUEUED
    worker.start()
    assert worker.claim(accepted.analysis_id, "alice") is True
    worker.complete(accepted.analysis_id, "alice", _result(request))
    assert store.get_visual_analysis(
        accepted.analysis_id,
        "alice",
    ).state is VisualAnalysisState.COMPLETED
    store.close()


def test_visual_analysis_rejects_hash_mismatch_revocation_and_expiry(
    tmp_path,
    monkeypatch,
):
    store = _store(tmp_path)
    _insert_project_and_asset(store)
    service, authority, websocket = _authorized_service(store, "alice")
    authorization = store.get_owner_local_processing_authorization_for_asset(
        owner_id="alice",
        asset_id="asset-1",
        asset_sha256=_MEDIA_SHA256,
    )
    assert authorization is not None
    with pytest.raises(ValueError, match="digest does not match"):
        _create(
            service,
            authority,
            websocket,
            _request().model_copy(update={"asset_sha256": "b" * 64}),
        )

    with store._transaction() as connection:
        store.revoke_owner_local_processing_authorization_in_transaction(
            connection,
            authorization_id=authorization.authorization_id,
            owner_id="alice",
            reason="visual authorization regression",
        )
    with pytest.raises(PermissionError):
        _create(
            service,
            authority,
            websocket,
            _request(idempotency_key="revoked-authorization"),
        )
    store.close()

    expired_directory = tmp_path / "expired"
    expired_directory.mkdir()
    expired_store = _store(expired_directory)
    _insert_project_and_asset(expired_store)
    expired_service, expired_authority, expired_websocket = _authorized_service(
        expired_store,
        "alice",
    )
    expired_authorization = (
        expired_store.get_owner_local_processing_authorization_for_asset(
            owner_id="alice",
            asset_id="asset-1",
            asset_sha256=_MEDIA_SHA256,
        )
    )
    assert expired_authorization is not None
    expired_now = expired_authorization.expires_at + timedelta(seconds=1)
    monkeypatch.setattr(expired_store, "_now", lambda: expired_now.isoformat())
    with pytest.raises(PermissionError, match="expired"):
        _create(
            expired_service,
            expired_authority,
            expired_websocket,
            _request(idempotency_key="expired-authorization"),
        )
    expired_store.close()


def test_visual_analysis_claim_and_completion_recheck_revocation_atomically(
    tmp_path,
    monkeypatch,
):
    store = _store(tmp_path)
    _insert_project_and_asset(store)
    service, authority, websocket = _authorized_service(store, "alice")
    request = _request()
    queued, _ = _create(service, authority, websocket, request)
    worker = _worker(store)
    worker.start()
    authorization = store.get_owner_local_processing_authorization_for_asset(
        owner_id="alice",
        asset_id="asset-1",
        asset_sha256=_MEDIA_SHA256,
    )
    assert authorization is not None
    original_claim = store.claim_visual_analysis

    def revoke_before_claim(
        analysis_id,
        owner_id,
        *,
        worker_token,
        authorization_check,
    ):
        with store._transaction() as connection:
            store.revoke_owner_local_processing_authorization_in_transaction(
                connection,
                authorization_id=authorization.authorization_id,
                owner_id="alice",
                reason="revoke before claim",
            )
        return original_claim(
            analysis_id,
            owner_id,
            worker_token=worker_token,
            authorization_check=authorization_check,
        )

    monkeypatch.setattr(store, "claim_visual_analysis", revoke_before_claim)
    with pytest.raises(PermissionError):
        worker.claim(queued.analysis_id, "alice")
    denied = store.get_visual_analysis(queued.analysis_id, "alice")
    assert denied is not None
    assert denied.state is VisualAnalysisState.FAILED
    assert denied.failure_code == "AUTHORIZATION_DENIED"
    assert denied.result is None
    assert [
        event.to_state
        for event in store.list_visual_analysis_events(
            queued.analysis_id,
            "alice",
        )
    ] == [VisualAnalysisState.QUEUED, VisualAnalysisState.FAILED]
    store.close()

    completion_directory = tmp_path / "completion"
    completion_directory.mkdir()
    completion_store = _store(completion_directory)
    _insert_project_and_asset(completion_store)
    completion_service, completion_authority, completion_websocket = (
        _authorized_service(completion_store, "alice")
    )
    completion_request = _request()
    record, _ = _create(
        completion_service,
        completion_authority,
        completion_websocket,
        completion_request,
    )
    completion_worker = _worker(completion_store)
    completion_worker.start()
    assert completion_worker.claim(record.analysis_id, "alice")
    completion_authorization = (
        completion_store.get_owner_local_processing_authorization_for_asset(
            owner_id="alice",
            asset_id="asset-1",
            asset_sha256=_MEDIA_SHA256,
        )
    )
    assert completion_authorization is not None
    with completion_store._transaction() as connection:
        completion_store.revoke_owner_local_processing_authorization_in_transaction(
            connection,
            authorization_id=completion_authorization.authorization_id,
            owner_id="alice",
            reason="revoke before completion",
        )
    with pytest.raises(PermissionError):
        completion_worker.complete(
            record.analysis_id,
            "alice",
            _result(completion_request),
        )
    failed = completion_store.get_visual_analysis(record.analysis_id, "alice")
    assert failed is not None
    assert failed.state is VisualAnalysisState.FAILED
    assert failed.result is None
    completion_store.close()


def _insert_project_and_asset(
    store: ContentStore,
    *,
    authorize_owner_local: bool = True,
    operations: frozenset[OwnerLocalProcessingOperation] = frozenset(
        OwnerLocalProcessingOperation
    ),
    acquisition_method: AcquisitionMethod = AcquisitionMethod.OWNER_CREATED,
) -> SourceAsset:
    now = store._now()
    created_at = datetime.fromisoformat(now)
    source_reference = f"urn:ultron:test-source:{_MEDIA_SHA256}"
    provenance = SourceProvenance(
        acquisition_method=acquisition_method,
        acquired_at=created_at,
        original_filename="asset-1.mp4",
        transfer_channel="direct_test_upload",
        source_reference=source_reference,
    )
    rights_use = RightsUse(
        asset_id="asset-1",
        expected_asset_sha256=_MEDIA_SHA256,
        platform=ContentPlatform.YOUTUBE,
        account_id="private-owner",
        territory="US",
        actions=frozenset({RightsAction.ANALYZE}),
        commercial_intent=False,
        monetization_intent=False,
        duration_seconds=10,
    )
    asset = SourceAsset(
        asset_id="asset-1",
        owner_id="alice",
        category=AssetCategory.FOOTAGE,
        content_sha256=_MEDIA_SHA256,
        provenance=provenance,
        storage_key="synthetic-asset-1",
        created_at=created_at,
    )
    with store._transaction() as connection:
        connection.execute(
            """
            INSERT INTO content_assets (
                asset_id, owner_id, category, content_sha256, provenance_json,
                storage_key, asset_state, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "asset-1",
                "alice",
                "footage",
                _MEDIA_SHA256,
                store._canonical(provenance.model_dump(mode="json")),
                "synthetic-asset-1",
                asset.state.value,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO source_intakes (
                intake_id, asset_id, owner_id, source_reference,
                acquisition_method, provenance_json, evidence_reference,
                claimed_source_sha256, source_hash_status,
                access_permission_state, target_context, rights_use_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, 'unverified', ?, ?, ?, ?)
            """,
            (
                "asset-1-intake",
                "asset-1",
                "alice",
                source_reference,
                acquisition_method.value,
                store._canonical(provenance.model_dump(mode="json")),
                _MEDIA_SHA256,
                SourceAccessPermissionState.NOT_REQUIRED.value,
                ContentTargetContext.YOUTUBE_SHORTS.value,
                store._canonical(rights_use.model_dump(mode="json")),
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO content_edit_projects (
                project_id, owner_id, source_asset_ids_json, script, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("project-1", "alice", json.dumps(["asset-1"]), "synthetic script", now),
        )
        connection.execute(
            """
            INSERT INTO content_edit_project_sources (
                project_id, source_asset_id, source_order
            ) VALUES (?, ?, ?)
            """,
            ("project-1", "asset-1", 1),
        )
        if authorize_owner_local and acquisition_method is AcquisitionMethod.OWNER_CREATED:
            authorized_at = datetime.fromisoformat(now)
            authorization = OwnerLocalProcessingAuthorization(
                authorization_id=str(uuid4()),
                owner_id="alice",
                asset_id="asset-1",
                asset_sha256=_MEDIA_SHA256,
                operations=operations,
                authorized_at=authorized_at,
                expires_at=(
                    authorized_at
                    + store.OWNER_LOCAL_PROCESSING_AUTHORIZATION_TTL
                ),
            )
            store.create_owner_local_processing_authorization_in_transaction(
                connection,
                authorization,
                actor="alice",
            )
    return asset


def _request(
    *,
    owner_id: str = "alice",
    idempotency_key: str = "synthetic-operation-1",
    analysis_id: str | None = None,
    model_version: str = _MODEL_DIGEST,
    timestamps: tuple[float, ...] = (0.5,),
) -> VisualAnalysisRequest:
    return VisualAnalysisRequest(
        analysis_id=analysis_id or str(uuid4()),
        owner_id=owner_id,
        project_id="project-1",
        asset_id="asset-1",
        asset_sha256=_MEDIA_SHA256,
        idempotency_key=idempotency_key,
        provider_id="ollama",
        model_id="qwen3-vl:2b-instruct-q4_K_M",
        model_version=model_version,
        prompt_version="visual-frame-prompt-v1",
        schema_version="visual-observation-v1",
        sampler_version="timestamp-sampler-v1",
        timestamps_seconds=timestamps,
        context_tokens=1024,
        output_tokens=384,
    )


def _result(request: VisualAnalysisRequest) -> VisualVideoAnalysis:
    frame_bytes = b"synthetic-persistence-contract-frame"
    evidence = VisualFrameEvidence(
        frame_id="synthetic-persistence-test-frame",
        asset_id=request.asset_id,
        asset_sha256=request.asset_sha256,
        timestamp_seconds=request.timestamps_seconds[0],
        frame_sha256=hashlib.sha256(frame_bytes).hexdigest(),
        width=320,
        height=240,
    )
    frame = VisualFrameAnalysis(
        evidence=evidence,
        scene_description="Persistence contract test fixture, not model output.",
        raw_output_sha256=hashlib.sha256(b"test-fixture-response").hexdigest(),
        provider_id=request.provider_id,
        model_id=request.model_id,
        model_version=request.model_version,
        resources=VisualInferenceResources(
            duration_ms=1,
            device=VisualInferenceDevice.UNKNOWN,
        ),
    )
    return VisualVideoAnalysis(
        asset_id=request.asset_id,
        asset_sha256=request.asset_sha256,
        duration_seconds=1,
        frames=(frame,),
        cross_frame_reasoning_performed=False,
    )


def _crash_worker_after_claim(database: str, analysis_id: str) -> None:
    store = ContentStore(database)
    worker = _worker(store)
    worker.start()
    if not worker.claim(analysis_id, "alice"):
        os._exit(2)
    os._exit(0)


def test_visual_analysis_migration_is_explicit_idempotent_and_v10_only(tmp_path):
    store = ContentStore(tmp_path / "disposable-cre.db")
    set_legacy_schema_version(store, 9)
    _insert_project_and_asset(store, authorize_owner_local=False)

    with pytest.raises(RuntimeError, match="schema-v10"):
        store.migrate_visual_analysis_schema()
    with pytest.raises(RuntimeError, match="schema-v10 foundation"):
        store.create_visual_analysis(_request())
    assert store.migrate_private_render_operation_schema() == 10
    with pytest.raises(RuntimeError, match="explicit visual-analysis schema migration"):
        store.create_visual_analysis(_request())
    assert store.migrate_visual_analysis_schema() == 2
    assert store.migrate_visual_analysis_schema() == 2
    store.close()


def test_visual_analysis_is_owner_scoped_idempotent_and_stateful(tmp_path):
    store = _store(tmp_path)
    _insert_project_and_asset(store)
    alice, alice_authority, alice_websocket = _authorized_service(store, "alice")
    worker = _worker(store)
    request = _request()
    worker.start()

    created, inserted = _create(alice, alice_authority, alice_websocket, request)
    replayed, replay_inserted = _create(
        alice,
        alice_authority,
        alice_websocket,
        _request(analysis_id=str(uuid4())),
    )

    assert inserted is True
    assert replay_inserted is False
    assert replayed.analysis_id == created.analysis_id
    assert created.state == VisualAnalysisState.QUEUED
    assert (
        _get(alice, alice_authority, alice_websocket, created.analysis_id).state
        == VisualAnalysisState.QUEUED
    )
    assert _events(
        alice,
        alice_authority,
        alice_websocket,
        created.analysis_id,
    )[0].to_state == VisualAnalysisState.QUEUED

    bob, bob_authority, bob_websocket = _authorized_service(store, "bob")
    assert _get(bob, bob_authority, bob_websocket, created.analysis_id) is None
    assert _events(bob, bob_authority, bob_websocket, created.analysis_id) == []
    with pytest.raises(PermissionError, match="unavailable"):
        _create(
            bob,
            bob_authority,
            bob_websocket,
            _request(owner_id="bob"),
        )

    assert worker.claim(created.analysis_id, "alice") is True
    assert worker.claim(created.analysis_id, "alice") is False
    worker.complete(created.analysis_id, "alice", _result(request))
    completed = _get(alice, alice_authority, alice_websocket, created.analysis_id)
    assert completed.state == VisualAnalysisState.COMPLETED
    assert completed.result is not None
    assert (
        completed.result.frames[0].scene_description
        == "Persistence contract test fixture, not model output."
    )
    assert [
        event.to_state
        for event in _events(
            alice,
            alice_authority,
            alice_websocket,
            created.analysis_id,
        )
    ] == [
        VisualAnalysisState.QUEUED,
        VisualAnalysisState.ANALYZING,
        VisualAnalysisState.COMPLETED,
    ]

    database = store.database_identity
    store.close()
    reopened = ContentStore(database)
    recovered, recovered_authority, recovered_websocket = _authorized_service(
        reopened,
        "alice",
    )
    recovered_worker = _worker(reopened)
    recovered_worker.start()
    persisted = _get(
        recovered,
        recovered_authority,
        recovered_websocket,
        created.analysis_id,
    )
    assert persisted is not None
    assert persisted.result == completed.result
    assert recovered_worker.claim(created.analysis_id, "alice") is False
    reopened.close()


def test_visual_analysis_rejects_reused_key_with_changed_request(tmp_path):
    store = _store(tmp_path)
    _insert_project_and_asset(store)
    store.create_visual_analysis(_request())

    with pytest.raises(VisualAnalysisConflictError, match="conflicts"):
        store.create_visual_analysis(
            _request(model_version="b" * 64)
        )
    with pytest.raises(VisualAnalysisConflictError, match="conflicts"):
        store.create_visual_analysis(
            _request(timestamps=(0.25,))
        )
    store.close()


def test_visual_analysis_cache_fingerprint_tracks_model_and_sampling_versions():
    base = _request()
    assert base.cache_fingerprint() != _request(model_version="b" * 64).cache_fingerprint()
    assert base.cache_fingerprint() != _request(timestamps=(0.25,)).cache_fingerprint()
    assert base.cache_fingerprint() == _request(
        analysis_id=str(uuid4()),
    ).cache_fingerprint()
    assert base.cache_fingerprint() == _request(
        idempotency_key="a-different-operation",
    ).cache_fingerprint()
    assert base.request_fingerprint() != _request(
        idempotency_key="a-different-operation",
    ).request_fingerprint()
    with pytest.raises(ValueError, match="immutable SHA-256 digest"):
        _request(model_version="latest")


@pytest.mark.parametrize(
    "changes",
    [
        {"owner_id": "bob"},
        {"project_id": "different-project"},
        {"asset_id": "different-asset"},
        {"asset_sha256": "b" * 64},
        {"provider_id": "different-provider"},
        {"model_id": "different-model"},
        {"model_version": "b" * 64},
        {"prompt_version": "prompt-v2"},
        {"schema_version": "schema-v2"},
        {"sampler_version": "sampler-v2"},
        {"timestamps_seconds": (0.25,)},
        {"context_tokens": 2048},
        {"output_tokens": 512},
        {"image_dimension_limit": 768},
    ],
)
def test_visual_analysis_cache_fingerprint_invalidates_result_inputs(changes):
    base = _request()
    changed = base.model_copy(update=changes)
    assert changed.cache_fingerprint() != base.cache_fingerprint()


def test_unattested_cache_identity_never_skips_fresh_analysis(tmp_path):
    store = _store(tmp_path)
    _insert_project_and_asset(store)
    service, authority, websocket = _authorized_service(store, "alice")
    worker = _worker(store)
    worker.start()
    original_request = _request(idempotency_key="operation-original")
    original, created = _create(service, authority, websocket, original_request)
    assert created is True
    assert worker.claim(original.analysis_id, "alice") is True
    expected_result = _result(original_request)
    worker.complete(original.analysis_id, "alice", expected_result)

    cache_request = _request(idempotency_key="operation-cache-hit")
    cached, inserted = _create(service, authority, websocket, cache_request)
    assert inserted is True
    assert cached.analysis_id != original.analysis_id
    assert cached.state == VisualAnalysisState.QUEUED
    assert cached.cache_source_analysis_id is None
    assert cached.result is None
    assert worker.claim(cached.analysis_id, "alice") is True
    worker.complete(cached.analysis_id, "alice", expected_result)

    replayed, replay_inserted = _create(
        service,
        authority,
        websocket,
        cache_request.model_copy(update={"analysis_id": str(uuid4())}),
    )
    assert replay_inserted is False
    assert replayed.analysis_id == cached.analysis_id
    with pytest.raises(VisualAnalysisConflictError):
        _create(
            service,
            authority,
            websocket,
            cache_request.model_copy(update={"output_tokens": 512}),
        )
    store.close()


def test_visual_analysis_v1_extension_upgrades_atomically_to_v2(tmp_path):
    store = _store(tmp_path)
    _insert_project_and_asset(store)
    request = _request()
    record, inserted = store.create_visual_analysis(request)
    assert inserted is True
    with store._transaction() as connection:
        connection.execute(
            "DROP INDEX content_visual_analyses_owner_cache_lookup_idx"
        )
        connection.execute(
            "DROP TRIGGER content_visual_analyses_cache_source_valid"
        )
        connection.execute(
            "DROP TRIGGER content_visual_analyses_fingerprints_valid"
        )
        connection.execute(
            "DROP TRIGGER content_visual_analyses_identity_immutable"
        )
        connection.execute(
            "ALTER TABLE content_visual_analyses "
            "DROP COLUMN cache_source_analysis_id"
        )
        connection.execute(
            "ALTER TABLE content_visual_analyses DROP COLUMN cache_fingerprint"
        )
        connection.execute("ALTER TABLE content_visual_analysis_schema_meta "
                           "RENAME TO content_visual_analysis_schema_meta_v2")
        connection.execute(
            """
            CREATE TABLE content_visual_analysis_schema_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                schema_version INTEGER NOT NULL CHECK (schema_version = 1)
            )
            """
        )
        connection.execute(
            "INSERT INTO content_visual_analysis_schema_meta(singleton, schema_version) "
            "VALUES (1, 1)"
        )
        connection.execute("DROP TABLE content_visual_analysis_schema_meta_v2")

    assert store.migrate_visual_analysis_schema() == 2
    migrated = store.get_visual_analysis(record.analysis_id, "alice")
    assert migrated is not None
    assert migrated.request_fingerprint == request.request_fingerprint()
    assert migrated.cache_fingerprint == request.cache_fingerprint()
    assert migrated.state == VisualAnalysisState.QUEUED
    store.close()


def test_visual_analysis_worker_recovery_gates_claims_and_excludes_second_process(
    tmp_path,
):
    store = _store(tmp_path)
    _insert_project_and_asset(store)
    record, _ = store.create_visual_analysis(_request())
    with pytest.raises(RuntimeError, match="startup recovery"):
        store.claim_visual_analysis(record.analysis_id, "alice")

    worker = _worker(store)
    worker.start()
    assert worker.claim(record.analysis_id, "alice") is True
    second_same_store_worker = _worker(store)
    with pytest.raises(RuntimeError, match="Another visual-analysis worker"):
        second_same_store_worker.start()

    second_store = ContentStore(store.database_identity)
    second_store.migrate_visual_analysis_schema()
    second_worker = _worker(second_store)
    with pytest.raises(RuntimeError, match="Another visual-analysis worker"):
        second_worker.start()
    second_store.close()
    with pytest.raises(RuntimeError, match="cannot stop while analyses are claimed"):
        worker.stop()
    with pytest.raises(RuntimeError, match="cannot close while visual analyses are claimed"):
        store.close()
    assert worker.fail(
        record.analysis_id,
        "alice",
        failure_code="TEST_CLEANUP",
        failure_detail="Release the synthetic claimed operation.",
    )
    worker.stop()
    store.close()


def test_visual_analysis_retention_is_owner_scoped_dry_run_and_reference_safe(
    tmp_path,
):
    store = _store(tmp_path)
    _insert_project_and_asset(store)
    service, authority, websocket = _authorized_service(store, "alice")
    worker = _worker(store)
    worker.start()

    completed_request = _request(idempotency_key="retention-completed")
    completed, _ = _create(service, authority, websocket, completed_request)
    assert worker.claim(completed.analysis_id, "alice")
    worker.complete(completed.analysis_id, "alice", _result(completed_request))
    cached, _ = _create(
        service,
        authority,
        websocket,
        _request(idempotency_key="retention-cache-reference"),
    )
    active, _ = _create(
        service,
        authority,
        websocket,
        _request(idempotency_key="retention-active").model_copy(
            update={"sampler_version": "sampler-active"}
        ),
    )

    old_time = "2000-01-01T00:00:00+00:00"
    boundary_time = "2026-10-30T00:00:00+00:00"
    expired_unreferenced = _request(idempotency_key="retention-unreferenced")
    exact_boundary = _request(idempotency_key="retention-boundary")
    with store._transaction() as connection:
        connection.execute(
            "UPDATE content_visual_analyses SET updated_at = ? WHERE analysis_id = ?",
            (old_time, completed.analysis_id),
        )
        for request, timestamp in (
            (expired_unreferenced, old_time),
            (exact_boundary, boundary_time),
        ):
            connection.execute(
                """
                INSERT INTO content_visual_analyses (
                    analysis_id, owner_id, project_id, asset_id, asset_sha256,
                    idempotency_key, request_fingerprint, cache_fingerprint,
                    cache_source_analysis_id, request_json, state, failure_code,
                    failure_detail, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 'failed',
                          'SYNTHETIC_FAILURE', 'Synthetic retention fixture', ?, ?)
                """,
                (
                    request.analysis_id,
                    request.owner_id,
                    request.project_id,
                    request.asset_id,
                    request.asset_sha256,
                    request.idempotency_key,
                    request.request_fingerprint(),
                    request.cache_fingerprint(),
                    store._canonical(request),
                    timestamp,
                    timestamp,
                ),
            )

    policy = VisualAnalysisRetentionPolicy(
        owner_id="alice",
        completed_before="2026-10-30T00:00:00+00:00",
        failed_before="2026-10-30T00:00:00+00:00",
        reconciliation_required_before="2026-10-30T00:00:00+00:00",
    )
    plan = store.plan_visual_analysis_retention(policy)
    assert plan.dry_run is True
    assert plan.candidate_analysis_ids == (expired_unreferenced.analysis_id,)
    assert plan.audit_protected_analysis_ids == (completed.analysis_id,)
    assert plan.cache_referenced_analysis_ids == ()
    assert plan.active_analysis_ids == tuple(
        sorted((cached.analysis_id, active.analysis_id))
    )
    assert exact_boundary.analysis_id not in plan.candidate_analysis_ids
    assert cached.analysis_id not in plan.candidate_analysis_ids
    assert store.get_visual_analysis(expired_unreferenced.analysis_id, "alice") is not None
    assert store.plan_visual_analysis_retention(
        policy.model_copy(update={"owner_id": "bob"})
    ).candidate_analysis_ids == ()
    store.close()


def test_visual_analysis_application_boundary_rejects_wrong_authorized_action(
    tmp_path,
):
    store = _store(tmp_path)
    _insert_project_and_asset(store)
    service, authority, websocket = _authorized_service(store, "alice")
    request = _request()
    context = _context(
        authority,
        websocket,
        "unrelated_action",
        request.model_dump(mode="json"),
    )

    with pytest.raises(PermissionError, match="active-session"):
        service.create(context, request)
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_visual_analyses"
    ).fetchone()[0] == 0
    store.close()


def test_visual_analysis_rejects_unregistered_source_digest_and_wrong_project(
    tmp_path,
):
    store = _store(tmp_path)
    _insert_project_and_asset(store)

    with pytest.raises(ValueError, match="digest"):
        store.create_visual_analysis(
            _request().model_copy(update={"asset_sha256": "b" * 64})
        )
    with pytest.raises(PermissionError, match="unavailable"):
        store.create_visual_analysis(
            _request().model_copy(update={"project_id": "unrelated-project"})
        )
    store.close()


def test_interrupted_visual_analysis_requires_reconciliation_no_retry(tmp_path):
    store = _store(tmp_path)
    _insert_project_and_asset(store)
    service, authority, websocket = _authorized_service(store, "alice")
    worker = _worker(store)
    record, _ = _create(
        service,
        authority,
        websocket,
        _request(),
    )
    database = store.database_identity
    store.close()

    process = multiprocessing.Process(
        target=_crash_worker_after_claim,
        args=(database, record.analysis_id),
    )
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 0

    reopened = ContentStore(database)
    recovered, recovered_authority, recovered_websocket = _authorized_service(
        reopened,
        "alice",
    )
    recovered_worker = _worker(reopened)
    with pytest.raises(RuntimeError, match="startup recovery"):
        recovered_worker.claim(record.analysis_id, "alice")
    recovered_worker.start()
    state = _get(
        recovered,
        recovered_authority,
        recovered_websocket,
        record.analysis_id,
    )
    assert state.state == VisualAnalysisState.RECONCILIATION_REQUIRED
    assert recovered_worker.claim(record.analysis_id, "alice") is False
    assert [
        event.to_state
        for event in _events(
            recovered,
            recovered_authority,
            recovered_websocket,
            record.analysis_id,
        )
    ] == [
        VisualAnalysisState.QUEUED,
        VisualAnalysisState.ANALYZING,
        VisualAnalysisState.RECONCILIATION_REQUIRED,
    ]
    reopened.close()


def test_visual_analysis_completion_requires_matching_model_and_timestamps(tmp_path):
    store = _store(tmp_path)
    _insert_project_and_asset(store)
    service, authority, websocket = _authorized_service(store, "alice")
    worker = _worker(store)
    request = _request()
    worker.start()
    record, _ = _create(service, authority, websocket, request)
    assert worker.claim(record.analysis_id, "alice")
    wrong_version_request = request.model_copy(update={"model_version": "c" * 64})

    with pytest.raises(ValueError, match="does not match"):
        worker.complete(record.analysis_id, "alice", _result(wrong_version_request))
    assert (
        _get(service, authority, websocket, record.analysis_id).state
        == VisualAnalysisState.ANALYZING
    )
    assert worker.fail(
        record.analysis_id,
        "alice",
        failure_code="TEST_CLEANUP",
        failure_detail="Release the synthetic claimed operation.",
    )
    store.close()


def test_visual_analysis_failure_is_audited_and_terminal(tmp_path):
    store = _store(tmp_path)
    _insert_project_and_asset(store)
    service, authority, websocket = _authorized_service(store, "alice")
    worker = _worker(store)
    record, _ = _create(service, authority, websocket, _request())
    worker.start()

    assert worker.fail(
        record.analysis_id,
        "alice",
        failure_code="PROVIDER_ERROR",
        failure_detail="Synthetic test provider failure.",
    )
    failed = _get(service, authority, websocket, record.analysis_id)
    assert failed.state == VisualAnalysisState.FAILED
    assert failed.failure_code == "PROVIDER_ERROR"
    assert worker.claim(record.analysis_id, "alice") is False
    assert worker.fail(
        record.analysis_id,
        "alice",
        failure_code="PROVIDER_ERROR",
        failure_detail="Repeated failure.",
    ) is False
    store.close()


def test_visual_analysis_claim_is_atomic_and_events_are_append_only(tmp_path):
    store = _store(tmp_path)
    _insert_project_and_asset(store)
    service, authority, websocket = _authorized_service(store, "alice")
    worker = _worker(store)
    record, _ = _create(service, authority, websocket, _request())
    worker.start()
    with ThreadPoolExecutor(max_workers=4) as executor:
        outcomes = list(
            executor.map(
                    lambda _: worker.claim(record.analysis_id, "alice"),
                range(4),
            )
        )
    assert outcomes.count(True) == 1
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store._connection.execute(
            "UPDATE content_visual_analysis_events SET reason = 'tampered'"
        )
    assert [
        event.to_state
        for event in _events(service, authority, websocket, record.analysis_id)
    ] == [
        VisualAnalysisState.QUEUED,
        VisualAnalysisState.ANALYZING,
    ]
    assert worker.fail(
        record.analysis_id,
        "alice",
        failure_code="TEST_CLEANUP",
        failure_detail="Release the synthetic claimed operation.",
    )
    store.close()
