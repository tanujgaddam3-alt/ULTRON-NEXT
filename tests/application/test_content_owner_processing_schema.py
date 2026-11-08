from __future__ import annotations

import pytest

from tests.application.test_content_lifecycle import Harness, _register_asset
from tests.application.test_content_visual_analysis import _insert_project_and_asset
from ultron.application.content.store import ContentStore


def test_owner_local_processing_migration_is_additive_repeatable_and_rollback_safe(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "owner-processing-schema.db"
    harness = Harness(database)
    asset = _register_asset(harness)
    store = harness.store
    before_schema_version = store._connection.execute(
        "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
    ).fetchone()[0]
    before_asset_count = store._connection.execute(
        "SELECT COUNT(*) FROM content_assets"
    ).fetchone()[0]
    original_verify = store._verify_owner_local_processing_expiration_schema

    def fail_verification(connection):
        raise RuntimeError("simulated owner-processing migration verification failure")

    monkeypatch.setattr(
        store,
        "_verify_owner_local_processing_expiration_schema",
        fail_verification,
    )
    with pytest.raises(RuntimeError, match="simulated"):
        store.migrate_owner_local_processing_schema()
    assert store._connection.execute(
        """
        SELECT COUNT(*) FROM sqlite_master
        WHERE type = 'table' AND name LIKE 'content_owner_local_processing_%'
        """
    ).fetchone()[0] == 0
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_assets"
    ).fetchone()[0] == before_asset_count
    assert store._connection.execute("PRAGMA foreign_key_check").fetchall() == []

    monkeypatch.setattr(
        store,
        "_verify_owner_local_processing_expiration_schema",
        original_verify,
    )
    assert store.migrate_owner_local_processing_schema() == 1
    assert store.migrate_owner_local_processing_schema() == 1
    assert store._connection.execute(
        "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
    ).fetchone()[0] == before_schema_version
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_assets"
    ).fetchone()[0] == before_asset_count
    assert store.get_asset(asset.asset_id) == asset
    assert store._connection.execute("PRAGMA foreign_key_check").fetchall() == []
    harness.close()

    reopened = ContentStore(database)
    try:
        assert reopened.get_asset(asset.asset_id) == asset
        assert reopened._connection.execute(
            "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
        ).fetchone()[0] == before_schema_version
        assert reopened._connection.execute(
            "SELECT schema_version FROM content_owner_local_processing_schema_meta "
            "WHERE singleton = 1"
        ).fetchone()[0] == 1
        assert reopened._connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        reopened.close()


def test_legacy_owner_authorizations_receive_durable_365_day_expiration(tmp_path):
    database = tmp_path / "owner-processing-expiration-backfill.db"
    store = ContentStore(database)
    store.migrate_publishing_attempt_schema()
    store.migrate_private_render_operation_schema()
    store.migrate_owner_local_processing_schema()
    asset = _insert_project_and_asset(store)
    authorization = store.get_owner_local_processing_authorization_for_asset(
        owner_id="alice",
        asset_id="asset-1",
        asset_sha256=asset.content_sha256,
    )
    assert authorization is not None
    authorized_at = authorization.authorized_at

    with store._transaction() as connection:
        connection.execute(
            "DROP TABLE content_owner_local_processing_expirations"
        )
        connection.execute(
            "DROP TABLE content_owner_local_processing_expiration_schema_meta"
        )
    store.close()

    reopened = ContentStore(database)
    try:
        assert reopened._table_exists(
            reopened._connection,
            "content_owner_local_processing_expiration_schema_meta",
        ) is False
        assert reopened.migrate_owner_local_processing_schema() == 1
        migrated = reopened.get_owner_local_processing_authorization_for_asset(
            owner_id="alice",
            asset_id="asset-1",
            asset_sha256=asset.content_sha256,
        )
        assert migrated is not None
        assert migrated.authorized_at == authorized_at
        assert migrated.expires_at == (
            authorized_at
            + reopened.OWNER_LOCAL_PROCESSING_AUTHORIZATION_TTL
        )
        assert reopened._connection.execute(
            "SELECT COUNT(*) FROM content_owner_local_processing_events"
        ).fetchone()[0] == 1
        assert reopened._connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert reopened.migrate_owner_local_processing_schema() == 1
    finally:
        reopened.close()
