from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

from ultron.application.content.service import ContentRightsService
from ultron.application.content.status import (
    ContentOwnerMapping,
    ContentStatusService,
    create_content_runtime,
    default_content_database_path,
    resolve_content_database_path,
)
from ultron.application.content.store import ContentStore
from ultron.application.content.store import ContentSchemaVersionMismatch
from ultron.domain.content import (
    AcquisitionMethod,
    AssetCategory,
    SourceAsset,
    SourceProvenance,
)
from ultron.security.identity.authorization import (
    AuthorizationContext,
    resource_fingerprint,
)
from ultron.security.identity.authenticator import WebSocketAuthenticator


OPERATOR_PRINCIPAL = "gateway-operator"


def _status_service(
    store: ContentStore,
    *,
    script_preview_configured: bool = False,
) -> ContentStatusService:
    authenticator = WebSocketAuthenticator("T" * 43, OPERATOR_PRINCIPAL)
    rights = ContentRightsService(store=store, authority=authenticator)
    return ContentStatusService(
        content_rights=rights,
        owner_mapping=ContentOwnerMapping(
            principal=OPERATOR_PRINCIPAL,
            owner_id=OPERATOR_PRINCIPAL,
        ),
        script_preview_configured=script_preview_configured,
    )


def test_cre_owner_mapping_is_explicit_and_matches_existing_owner_contract():
    mapping = ContentOwnerMapping(
        principal=OPERATOR_PRINCIPAL,
        owner_id=OPERATOR_PRINCIPAL,
    )

    assert mapping.owner_for(OPERATOR_PRINCIPAL) == OPERATOR_PRINCIPAL
    assert mapping.owner_for("another-principal") is None
    with pytest.raises(ValueError):
        ContentOwnerMapping(principal="", owner_id=OPERATOR_PRINCIPAL)
    with pytest.raises(ValueError):
        ContentOwnerMapping(
            principal="same-identity",
            owner_id="separate-owner",
        )


def test_empty_cre_status_is_truthful_and_read_only(tmp_path):
    store = ContentStore(tmp_path / "content.db")
    try:
        service = _status_service(store)
        before = store._connection.total_changes
        status = service.for_principal("gateway-operator")

        assert status["status"] == "AVAILABLE"
        assert status["counts"] == {
            "total": 0,
            "processing": {},
            "rendering": {},
            "publishing": {},
            "pendingApprovals": 0,
        }
        assert status["jobs"] == []
        assert status["lifecycle"] == []
        assert status["events"] == []
        assert status["services"]["sourceAssets"] == ()
        assert status["analytics"] == {
            "available": False,
            "reason": "NOT_INSTRUMENTED",
        }
        assert status["publishing"]["mode"] == "MOCK_ONLY"
        assert store._connection.total_changes == before
    finally:
        store.close()


def test_source_asset_projection_is_owner_scoped_and_never_exposes_storage_key(tmp_path):
    store = ContentStore(tmp_path / "content.db")
    try:
        provenance = json.dumps(
            {
                "acquisition_method": "owner_upload",
                "acquired_at": datetime.now(UTC).isoformat(),
                "source_reference": "https://example.org/source-video",
                "provider": None,
                "provider_record_id": None,
                "notes": "",
            }
        )
        for owner_id, asset_id in (
            (OPERATOR_PRINCIPAL, "owned-source"),
            ("another-owner", "private-source"),
        ):
            store._connection.execute(
                """
                INSERT INTO content_assets (
                    asset_id, owner_id, category, content_sha256,
                    provenance_json, storage_key, asset_state, created_at
                ) VALUES (?, ?, 'footage', ?, ?, ?, 'active', ?)
                """,
                (
                    asset_id,
                    owner_id,
                    "a" * 64,
                    provenance,
                    f"opaque-{asset_id}-storage-key",
                    datetime.now(UTC).isoformat(),
                ),
            )
        store._connection.execute(
            """
            INSERT INTO ownership_claims (
                claim_id, asset_id, claimant_id, rights_holder,
                authority_description, created_at
            ) VALUES ('claim-1', 'owned-source', ?, 'Creator',
                      'Operator-provided claim', ?)
            """,
            (OPERATOR_PRINCIPAL, datetime.now(UTC).isoformat()),
        )
        records = _status_service(store).for_principal(OPERATOR_PRINCIPAL)[
            "services"
        ]["sourceAssets"]
        assert len(records) == 1
        assert records[0]["assetId"] == "owned-source"
        assert records[0]["rightsStatus"] == "REVIEW_REQUIRED"
        assert records[0]["claims"][0]["reviewStatus"] == "REVIEW_REQUIRED"
        assert "storageKey" not in records[0]
        assert "opaque-owned-source-storage-key" not in repr(records)
        assert records[0]["provenance"]["source_reference"] == (
            "https://example.org/source-video"
        )
    finally:
        store.close()


def test_script_preview_status_distinguishes_script_preview_from_media_rendering(
    tmp_path,
):
    store = ContentStore(tmp_path / "content.db")
    try:
        status = _status_service(
            store,
            script_preview_configured=True,
        ).for_principal(OPERATOR_PRINCIPAL)
        assert status["services"]["rendering"] == "LOCAL_SCRIPT_PREVIEW_ONLY"
        assert status["services"]["scriptPreview"] == "WIRED_LOCAL_NARRATED_PREVIEW"
        assert status["services"]["processing"] == "NOT_CONFIGURED"
        assert status["publishing"]["mode"] == "MOCK_ONLY"
    finally:
        store.close()


def test_status_queries_are_owner_scoped_bounded_and_minimal(tmp_path):
    store = ContentStore(tmp_path / "content.db")
    now = datetime.now(UTC).isoformat()
    try:
        for asset_id, owner_id in (
            ("owner-asset-1", OPERATOR_PRINCIPAL),
            ("owner-asset-2", OPERATOR_PRINCIPAL),
            ("other-asset", "other-owner"),
        ):
            store._connection.execute(
                """
                INSERT INTO content_assets (
                    asset_id, owner_id, category, content_sha256,
                    provenance_json, storage_key, asset_state, created_at
                ) VALUES (?, ?, 'footage', ?, ?, NULL, 'active', ?)
                """,
                (
                    asset_id,
                    owner_id,
                    "a" * 64,
                    json.dumps(
                        {
                            "acquisition_method": "owner_upload",
                            "acquired_at": now,
                            "source_reference": None,
                            "provider": None,
                            "provider_record_id": None,
                            "notes": "",
                        }
                    ),
                    now,
                ),
            )
        for job_id, asset_id, owner_id in (
            ("owned-job-1", "owner-asset-1", OPERATOR_PRINCIPAL),
            ("owned-job-2", "owner-asset-2", OPERATOR_PRINCIPAL),
            ("other-job", "other-asset", "other-owner"),
        ):
            store._connection.execute(
                """
                INSERT INTO content_processing_jobs (
                    job_id, owner_id, session_id, command_id, asset_id,
                    asset_sha256, target_context, rights_use_json, state,
                    created_at, updated_at
                ) VALUES (?, ?, 'private-session', 'command', ?, ?, 'context',
                          '{}', 'completed', ?, ?)
                """,
                (job_id, owner_id, asset_id, "a" * 64, now, now),
            )
        store._connection.execute(
            """
            INSERT INTO content_processing_events (
                job_id, from_state, to_state, actor, reason, created_at
            ) VALUES ('owned-job-1', NULL, 'completed', 'private-actor',
                      'private-detail', ?)
            """,
            (now,),
        )
        store._connection.execute(
            """
            INSERT INTO content_processing_events (
                job_id, from_state, to_state, actor, reason, created_at
            ) VALUES ('other-job', NULL, 'completed', 'other-actor',
                      'other-detail', ?)
            """,
            (now,),
        )
        before = store._connection.total_changes

        status = _status_service(store).for_principal(
            "gateway-operator",
            limit=1,
        )

        assert status["counts"]["total"] == 2
        assert len(status["jobs"]) == 1
        assert status["jobs"][0]["id"].startswith("owned-job-")
        assert all(
            "session" not in str(item).lower()
            and "actor" not in str(item).lower()
            and "reason" not in str(item).lower()
            for item in [*status["jobs"], *status["events"]]
        )
        assert len(status["events"]) == 1
        assert store._connection.total_changes == before
    finally:
        store.close()


def test_status_denies_unmapped_principal_and_invalid_limits(tmp_path):
    store = ContentStore(tmp_path / "content.db")
    try:
        service = _status_service(store)
        with pytest.raises(PermissionError):
            service.for_principal("another-principal")
        with pytest.raises(ValueError):
            service.for_principal("gateway-operator", limit=0)
        with pytest.raises(ValueError):
            service.for_principal("gateway-operator", limit=True)
    finally:
        store.close()


def test_runtime_allowlists_only_the_distinct_configured_gateway_principal(
    tmp_path,
    monkeypatch,
):
    database_path = tmp_path / "data" / "ultron_cre.db"
    monkeypatch.delenv("ULTRON_CRE_DATABASE", raising=False)
    monkeypatch.setattr(
        "ultron.application.content.status.default_content_database_path",
        lambda: database_path,
    )
    monkeypatch.delenv("ULTRON_CRE_FASTER_WHISPER_MODEL", raising=False)
    monkeypatch.delenv("ULTRON_CRE_EDITORIAL_OLLAMA_MODEL", raising=False)
    test_store = ContentStore(database_path)
    test_store.migrate_publishing_attempt_schema()
    test_store.migrate_private_render_operation_schema()
    test_store.close()
    authenticator = WebSocketAuthenticator(
        "O" * 43,
        "asset-owner",
        additional_credentials={"rights-reviewer": "R" * 43},
    )

    runtime = create_content_runtime(authenticator)
    try:
        assert runtime.rights.reviewer_principals == frozenset({"rights-reviewer"})
        assert runtime.intelligence is None
    finally:
        runtime.close()


def test_runtime_enables_only_configured_local_ollama_editorial_ranking(
    tmp_path,
    monkeypatch,
):
    database_path = tmp_path / "data" / "ultron_cre.db"

    class FakeTranscriptionEngine:
        engine_id = "test-local"
        engine_version = "1"

        def transcribe(self, samples, sample_rate_hz):
            return ()

    monkeypatch.setattr(
        "ultron.application.content.status.FasterWhisperLocalEngine",
        lambda model_directory: FakeTranscriptionEngine(),
    )
    monkeypatch.setenv("ULTRON_CRE_FASTER_WHISPER_MODEL", "/configured/local/model")
    monkeypatch.setenv("ULTRON_CRE_EDITORIAL_OLLAMA_MODEL", "qwen2.5:7b")
    test_store = ContentStore(database_path)
    test_store.migrate_publishing_attempt_schema()
    test_store.migrate_private_render_operation_schema()
    test_store.close()

    runtime = create_content_runtime(
        WebSocketAuthenticator("S" * 43, OPERATOR_PRINCIPAL),
        database_path=database_path,
        content_storage_root=tmp_path / "content",
    )
    try:
        intelligence = runtime.intelligence
        assert intelligence is not None
        provider = intelligence.local_editorial_provider
        assert provider is not None
        assert provider.model == "qwen2.5:7b"
        assert provider.local_only is True
        assert provider._session.trust_env is False
        assert provider.url == "http://localhost:11434/api/generate"
        assert provider.timeout == 20
        assert provider.num_predict == 384
        assert provider.json_mode is True
    finally:
        runtime.close()


def test_runtime_rejects_editorial_model_without_local_transcription(tmp_path, monkeypatch):
    monkeypatch.delenv("ULTRON_CRE_FASTER_WHISPER_MODEL", raising=False)
    monkeypatch.setenv("ULTRON_CRE_EDITORIAL_OLLAMA_MODEL", "qwen2.5:7b")

    with pytest.raises(RuntimeError, match="requires the configured local"):
        create_content_runtime(
            WebSocketAuthenticator("S" * 43, OPERATOR_PRINCIPAL),
            database_path=tmp_path / "must-not-be-created.db",
        )

    assert not (tmp_path / "must-not-be-created.db").exists()


def test_content_database_path_defaults_to_project_database_when_unset(
    tmp_path,
    monkeypatch,
):
    database_path = tmp_path / "data" / "ultron_cre.db"
    database_path.parent.mkdir()
    database_path.touch()
    monkeypatch.delenv("ULTRON_CRE_DATABASE", raising=False)
    monkeypatch.setattr(
        "ultron.application.content.status.default_content_database_path",
        lambda: database_path,
    )

    assert resolve_content_database_path() == database_path.resolve()


def test_content_database_path_accepts_existing_absolute_override(tmp_path, monkeypatch):
    database_path = tmp_path / "backup-copy" / "cre-v7.sqlite"
    database_path.parent.mkdir()
    database_path.touch()
    monkeypatch.setenv("ULTRON_CRE_DATABASE", str(database_path))

    assert resolve_content_database_path() == database_path.resolve()


@pytest.mark.parametrize(
    ("path_kind", "message"),
    [
        ("missing", "does not exist or is invalid"),
        ("symlink", "must not be a symbolic link"),
        ("relative", "must be absolute"),
        ("directory", "not a regular file"),
    ],
)
def test_content_database_path_rejects_invalid_overrides(
    tmp_path,
    monkeypatch,
    path_kind,
    message,
):
    database_path = tmp_path / "ultron_cre.db"
    if path_kind == "symlink":
        target = tmp_path / "target.sqlite"
        target.touch()
        database_path.symlink_to(target)
        configured_path = database_path
    elif path_kind == "relative":
        configured_path = Path("relative/ultron_cre.db")
    elif path_kind == "directory":
        database_path.mkdir()
        configured_path = database_path
    else:
        configured_path = tmp_path / "missing.sqlite"
    monkeypatch.setenv("ULTRON_CRE_DATABASE", str(configured_path))

    with pytest.raises(RuntimeError, match=message):
        resolve_content_database_path()


def test_content_runtime_accepts_v10_database_at_override_path(tmp_path, monkeypatch):
    database_path = tmp_path / "backup-copy" / "cre-v7.sqlite"
    database_path.parent.mkdir()
    store = ContentStore(database_path)
    store.migrate_publishing_attempt_schema()
    store.migrate_private_render_operation_schema()
    store.close()
    monkeypatch.setenv("ULTRON_CRE_DATABASE", str(database_path))
    monkeypatch.delenv("ULTRON_CRE_FASTER_WHISPER_MODEL", raising=False)

    runtime = create_content_runtime(
        WebSocketAuthenticator("T" * 43, OPERATOR_PRINCIPAL),
        database_path=resolve_content_database_path(),
    )
    try:
        assert Path(runtime.store.database_identity) == database_path.resolve()
        assert runtime.status.for_principal(OPERATOR_PRINCIPAL)["status"] == "AVAILABLE"
    finally:
        runtime.close()


def test_real_content_runtime_initializes_and_operates_on_fresh_v10_database(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ULTRON_CRE_WORKER_ENABLED", "false")
    monkeypatch.setenv("ULTRON_CRE_YOUTUBE_PUBLISHING_ENABLED", "false")
    monkeypatch.setenv("ULTRON_CRE_PUBLISHING_DRY_RUN_ENABLED", "false")
    database_path = tmp_path / "isolated" / "data" / "ultron_cre.db"
    storage_root = tmp_path / "isolated" / "content"
    token = "V" * 43
    authenticator = WebSocketAuthenticator(token, "fixture-operator")
    runtime = create_content_runtime(
        authenticator,
        database_path=database_path,
        content_storage_root=storage_root,
    )
    try:
        assert Path(runtime.store.database_identity) == database_path.resolve()
        runtime_status = runtime.status.for_principal("fixture-operator")
        assert runtime_status["status"] == "AVAILABLE"
        assert runtime_status["publishing"]["mode"] == "MOCK_ONLY"
        assert runtime.workflow_runtime is None
        assert runtime.youtube_publishing is None
        assert (database_path.parent / "ultron_cre_script_staging").is_dir()
        assert storage_root.is_dir()
        assert runtime.store._connection.execute(
            "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
        ).fetchone()[0] == 10

        payload_bytes = b"isolated runtime write fixture"
        digest = sha256(payload_bytes).hexdigest()
        asset = SourceAsset(
            asset_id="isolated-runtime-asset",
            owner_id="fixture-operator",
            category=AssetCategory.FOOTAGE,
            content_sha256=digest,
            provenance=SourceProvenance(
                acquisition_method=AcquisitionMethod.OWNER_UPLOAD,
                acquired_at=datetime.now(UTC),
                source_reference="upload:isolated-runtime-test",
            ),
            storage_key="asset:isolated-runtime-test",
        )
        websocket = object()
        assert authenticator.authenticate(websocket, token) is not None
        operation_id = str(uuid4())
        correlation_id = str(uuid4())
        action = "content_register_asset"
        asset_payload = asset.model_dump(mode="json")
        grant = authenticator.authorize(
            websocket=websocket,
            operation={
                "operationId": operation_id,
                "correlationId": correlation_id,
                "requestFingerprint": sha256(operation_id.encode()).hexdigest(),
            },
            purpose="content_lifecycle",
            request_correlation_id=correlation_id,
            action=action,
            resource_fingerprint=resource_fingerprint(action, asset_payload),
        )
        assert grant is not None
        registered = runtime.rights.register_asset(
            asset,
            source_stream=BytesIO(payload_bytes),
            context=AuthorizationContext.from_grant(grant),
            command_id=str(uuid4()),
        )
        assert registered == asset
        assert runtime.store.get_asset(asset.asset_id) == asset
    finally:
        runtime.close()

    reopened = ContentStore(database_path)
    try:
        assert reopened.get_asset("isolated-runtime-asset") == asset
        ContentStore.verify_complete_runtime_schema(reopened._connection)
    finally:
        reopened.close()


def test_v7_runtime_mismatch_is_classified_and_leaves_fixture_unchanged(
    tmp_path,
    monkeypatch,
):
    database_path = tmp_path / "data" / "ultron_cre.db"
    monkeypatch.delenv("ULTRON_CRE_DATABASE", raising=False)
    database_path.parent.mkdir()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE content_schema_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                schema_version INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO content_schema_meta(singleton, schema_version) VALUES (1, 7)"
        )
    before_bytes = database_path.read_bytes()
    monkeypatch.setattr(
        "ultron.application.content.status.default_content_database_path",
        lambda: database_path,
    )

    with pytest.raises(ContentSchemaVersionMismatch) as error:
        create_content_runtime(
            WebSocketAuthenticator("T" * 43, OPERATOR_PRINCIPAL)
        )

    assert error.value.actual_version == 7
    assert error.value.expected_version == 10
    assert ContentStore.SCHEMA_VERSION == 10
    assert ContentStore.PUBLISHING_ATTEMPT_SCHEMA_VERSION == 9
    assert ContentStore.RUNTIME_SCHEMA_VERSION == 10
    assert database_path.read_bytes() == before_bytes
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT schema_version FROM content_schema_meta"
        ).fetchone() == (7,)


def test_read_only_runtime_preflight_accepts_disposable_v10_without_content_store(monkeypatch, tmp_path):
    database_path = tmp_path / "cre-test.db"
    store = ContentStore(database_path)
    store.migrate_publishing_attempt_schema()
    store.migrate_private_render_operation_schema()
    store.close()
    before = database_path.read_bytes()

    monkeypatch.setattr(
        ContentStore,
        "__init__",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("ContentStore must not be instantiated")
        ),
    )
    from ultron.application.content.status import verify_cre_runtime_database_read_only

    verify_cre_runtime_database_read_only(database_path)
    assert database_path.read_bytes() == before


def test_read_only_runtime_preflight_rejects_v7_clearly(tmp_path):
    database_path = tmp_path / "cre-v7.db"
    store = ContentStore(database_path)
    store._connection.execute(
        "UPDATE content_schema_meta SET schema_version = 7 WHERE singleton = 1"
    )
    store.close()
    before = database_path.read_bytes()
    from ultron.application.content.status import verify_cre_runtime_database_read_only

    with pytest.raises(Exception) as error:
        verify_cre_runtime_database_read_only(database_path)

    assert "does not match" in str(error.value) or "current runtime" in str(error.value)
    assert database_path.read_bytes() == before


def test_read_only_runtime_preflight_missing_path_and_relative_path(tmp_path):
    from ultron.application.content.status import verify_cre_runtime_database_read_only

    with pytest.raises(RuntimeError, match="does not exist"):
        verify_cre_runtime_database_read_only(tmp_path / "missing.db")
    with pytest.raises(RuntimeError, match="must be absolute"):
        verify_cre_runtime_database_read_only(Path("relative.db"))


def test_expected_runtime_schema_creates_content_runtime_in_fixture(tmp_path, monkeypatch):
    database_path = tmp_path / "data" / "ultron_cre.db"
    monkeypatch.delenv("ULTRON_CRE_DATABASE", raising=False)
    database_path.parent.mkdir()
    store = ContentStore(database_path)
    store.migrate_publishing_attempt_schema()
    store.migrate_private_render_operation_schema()
    store.close()
    monkeypatch.setattr(
        "ultron.application.content.status.default_content_database_path",
        lambda: database_path,
    )

    runtime = create_content_runtime(
        WebSocketAuthenticator("T" * 43, OPERATOR_PRINCIPAL)
    )
    try:
        assert runtime.status.for_principal(OPERATOR_PRINCIPAL)["status"] == "AVAILABLE"
    finally:
        runtime.close()


def test_market_data_dependency_contract_matches_installed_yfinance():
    from importlib.metadata import metadata, version
    from pathlib import Path

    requirements = (
        Path(__file__).resolve().parents[2] / "requirements.txt"
    ).read_text(encoding="utf-8")
    yfinance_websockets = [
        requirement
        for requirement in metadata("yfinance").get_all("Requires-Dist", [])
        if requirement.lower().startswith("websockets")
    ]

    assert "websockets>=13.0" in requirements
    assert yfinance_websockets == ["websockets>=13.0"]
    assert tuple(int(part) for part in version("websockets").split(".")[:1]) >= (13,)
    from websockets.sync.client import connect  # noqa: F401
    import yfinance  # noqa: F401
