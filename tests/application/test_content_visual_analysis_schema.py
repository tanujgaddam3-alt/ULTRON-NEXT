from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ultron.application.content.status import _verify_existing_database
from ultron.application.content.store import ContentStore
from tests.application.schema_test_utils import set_legacy_schema_version


def _insert_project_and_asset(store: ContentStore) -> None:
    now = store._now()
    with store._transaction() as connection:
        connection.execute(
            """
            INSERT INTO content_assets (
                asset_id, owner_id, category, content_sha256, provenance_json,
                storage_key, asset_state, created_at
            ) VALUES ('schema-asset', 'test-owner', 'footage', ?, '{}',
                      'synthetic-schema-asset', 'ready_for_analysis', ?)
            """,
            ("a" * 64, now),
        )
        connection.execute(
            """
            INSERT INTO content_edit_projects (
                project_id, owner_id, source_asset_ids_json, script, created_at
            ) VALUES ('schema-project', 'test-owner', '["schema-asset"]',
                      'synthetic schema test', ?)
            """,
            (now,),
        )
        connection.execute(
            """
            INSERT INTO content_edit_project_sources (
                project_id, source_asset_id, source_order
            ) VALUES ('schema-project', 'schema-asset', 1)
            """
        )


def _version(store: ContentStore) -> int:
    return store._connection.execute(
        "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
    ).fetchone()[0]


def test_fresh_and_existing_supported_base_schema_do_not_implicitly_upgrade(tmp_path):
    database = tmp_path / "isolated-fresh.db"
    store = ContentStore(database)
    assert _version(store) == ContentStore.SCHEMA_VERSION == 10
    store.close()

    reopened = ContentStore(database)
    assert _version(reopened) == 10
    assert reopened._connection.execute(
        """
        SELECT COUNT(*) FROM sqlite_master
        WHERE type = 'table' AND name = 'content_publishing_attempts'
        """
    ).fetchone()[0] == 1
    assert reopened._connection.execute(
        """
        SELECT COUNT(*) FROM sqlite_master
        WHERE type = 'table' AND name = 'content_private_render_operations'
        """
    ).fetchone()[0] == 1
    reopened.close()


def test_fresh_v10_schema_is_complete_and_initialization_is_idempotent(tmp_path):
    database = tmp_path / "fresh-v10.db"
    store = ContentStore(database)
    assert _version(store) == 10
    ContentStore.verify_complete_runtime_schema(store._connection)
    assert store._connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert store._connection.execute("PRAGMA foreign_key_check").fetchall() == []
    initial_inventory = store._connection.execute(
        """
        SELECT type, name, tbl_name, sql FROM sqlite_master
        WHERE type IN ('table', 'index', 'trigger')
          AND name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall()
    store.close()

    reopened = ContentStore(database)
    repeated_inventory = reopened._connection.execute(
        """
        SELECT type, name, tbl_name, sql FROM sqlite_master
        WHERE type IN ('table', 'index', 'trigger')
          AND name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall()
    assert [tuple(row) for row in repeated_inventory] == [
        tuple(row) for row in initial_inventory
    ]
    reopened.close()


def test_failed_fresh_initialization_rolls_back_schema_version_and_ddl(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "interrupted-fresh.db"
    original = ContentStore._create_private_render_operation_tables

    def fail_after_partial_create(connection):
        connection.execute(
            "CREATE TABLE interrupted_initialization (id INTEGER PRIMARY KEY)"
        )
        raise RuntimeError("synthetic initialization interruption")

    monkeypatch.setattr(
        ContentStore,
        "_create_private_render_operation_tables",
        staticmethod(fail_after_partial_create),
    )
    with pytest.raises(RuntimeError, match="synthetic initialization interruption"):
        ContentStore(database)

    connection = sqlite3.connect(database)
    try:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchall() == []
    finally:
        connection.close()

    monkeypatch.setattr(
        ContentStore,
        "_create_private_render_operation_tables",
        staticmethod(original),
    )
    recovered = ContentStore(database)
    assert _version(recovered) == 10
    recovered.close()


def test_concurrent_fresh_initialization_serializes_complete_v10_schema(tmp_path):
    database = tmp_path / "concurrent-fresh.db"

    def initialize():
        store = ContentStore(database)
        try:
            return _version(store)
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: initialize(), range(4))) == [10] * 4

    store = ContentStore(database)
    ContentStore.verify_complete_runtime_schema(store._connection)
    assert store._connection.execute("PRAGMA foreign_key_check").fetchall() == []
    store.close()


def test_incomplete_schema_marked_v10_is_rejected_without_repair(tmp_path):
    database = tmp_path / "incomplete-v10.db"
    store = ContentStore(database)
    store._connection.execute(
        "DROP TRIGGER content_private_render_operation_identity_immutable"
    )
    store.close()
    before_bytes = database.read_bytes()

    with pytest.raises(RuntimeError, match="incomplete or inconsistent"):
        ContentStore(database)

    assert database.read_bytes() == before_bytes


def test_schema_migrations_require_deterministic_order_and_are_idempotent(tmp_path):
    store = ContentStore(tmp_path / "isolated-sequence.db")
    set_legacy_schema_version(store, 7)
    _insert_project_and_asset(store)

    with pytest.raises(RuntimeError, match="schema-v9"):
        store.migrate_private_render_operation_schema()
    assert store.migrate_publishing_attempt_schema() == 9
    assert store.migrate_publishing_attempt_schema() == 9
    assert _version(store) == 9
    assert store.migrate_private_render_operation_schema() == 10
    assert store.migrate_private_render_operation_schema() == 10
    assert store.migrate_visual_analysis_schema() == 2
    assert store.migrate_visual_analysis_schema() == 2
    assert _version(store) == ContentStore.RUNTIME_SCHEMA_VERSION == 10

    _insert_render_operation(store)
    operation_before = tuple(
        store._connection.execute(
            "SELECT operation_id, owner_id, project_id, state, input_snapshot_json "
            "FROM content_private_render_operations WHERE operation_id = 'render-op-1'"
        ).fetchone()
    )
    event_count_before = store._connection.execute(
        "SELECT COUNT(*) FROM content_private_render_operation_events "
        "WHERE operation_id = 'render-op-1'"
    ).fetchone()[0]

    assert store.migrate_visual_analysis_schema() == 2
    operation_after = tuple(
        store._connection.execute(
            "SELECT operation_id, owner_id, project_id, state, input_snapshot_json "
            "FROM content_private_render_operations WHERE operation_id = 'render-op-1'"
        ).fetchone()
    )
    assert operation_after == operation_before
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_private_render_operation_events "
        "WHERE operation_id = 'render-op-1'"
    ).fetchone()[0] == event_count_before
    assert store._connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert store._connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    for kind, name in (
        ("table", "content_visual_analyses"),
        ("table", "content_visual_analysis_events"),
        ("index", "content_visual_analyses_owner_cache_lookup_idx"),
        ("trigger", "content_visual_analysis_events_no_update"),
        ("trigger", "content_visual_analysis_events_no_delete"),
        ("trigger", "content_visual_analyses_cache_source_valid"),
    ):
        assert store._connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = ? AND name = ?",
            (kind, name),
        ).fetchone()[0] == 1
    assert store.get_edit_project("schema-project", "test-owner") is not None
    store.close()


def _visual_analysis_store(database: Path) -> ContentStore:
    store = ContentStore(database)
    store.migrate_publishing_attempt_schema()
    store.migrate_private_render_operation_schema()
    store.migrate_visual_analysis_schema()
    return store


def test_visual_identity_trigger_definition_is_valid_and_effective(tmp_path):
    store = _visual_analysis_store(tmp_path / "visual-trigger-valid.db")
    _insert_project_and_asset(store)
    store._connection.execute(
        """
        INSERT INTO content_visual_analyses (
            analysis_id, owner_id, project_id, asset_id, asset_sha256,
            idempotency_key, request_fingerprint, cache_fingerprint,
            request_json, state, created_at, updated_at
        ) VALUES (
            'analysis-1', 'test-owner', 'schema-project', 'schema-asset', ?,
            'idem-1', ?, ?, '{}', 'queued', ?, ?
        )
        """,
        ("a" * 64, "b" * 64, "c" * 64, store._now(), store._now()),
    )

    with pytest.raises(sqlite3.IntegrityError, match="identity is immutable"):
        store._connection.execute(
            "UPDATE content_visual_analyses SET owner_id = 'other-owner' "
            "WHERE analysis_id = 'analysis-1'"
        )

    assert store.migrate_visual_analysis_schema() == 2
    store.close()


@pytest.mark.parametrize(
    "trigger_name",
    [
        "content_visual_analysis_events_no_update",
        "content_visual_analysis_events_no_delete",
        "content_visual_analyses_identity_immutable",
        "content_visual_analyses_fingerprints_valid",
        "content_visual_analyses_cache_source_valid",
    ],
)
def test_visual_migration_rejects_same_named_noop_triggers(tmp_path, trigger_name):
    store = _visual_analysis_store(tmp_path / f"{trigger_name}.db")
    store._connection.execute(f"DROP TRIGGER {trigger_name}")
    store._connection.execute(
        f"""
        CREATE TRIGGER {trigger_name}
        BEFORE UPDATE ON content_visual_analyses
        BEGIN
            SELECT 1;
        END
        """
    )

    with pytest.raises(RuntimeError, match="invalid definition"):
        store.migrate_visual_analysis_schema()
    store.close()


def test_visual_migration_rejects_a_missing_required_trigger(tmp_path):
    store = _visual_analysis_store(tmp_path / "visual-trigger-missing.db")
    store._connection.execute(
        "DROP TRIGGER content_visual_analyses_identity_immutable"
    )

    with pytest.raises(RuntimeError, match="incomplete|invalid definition"):
        store.migrate_visual_analysis_schema()
    store.close()


def _insert_render_operation(store: ContentStore) -> None:
    now = store._now()
    with store._transaction() as connection:
        connection.execute(
            """
            INSERT INTO content_private_render_operations (
                operation_id, owner_id, project_id, idempotency_key,
                request_fingerprint, input_snapshot_json, state, created_at,
                updated_at
            ) VALUES (
                'render-op-1', 'test-owner', 'schema-project', 'render-idem-1',
                ?, '{}', 'queued', ?, ?
            )
            """,
            ("b" * 64, now, now),
        )
        connection.execute(
            """
            INSERT INTO content_private_render_operation_events (
                operation_id, from_state, to_state, actor, reason, created_at
            ) VALUES ('render-op-1', NULL, 'queued', 'test-owner',
                      'synthetic migration record', ?)
            """,
            (now,),
        )


def test_runtime_gate_rejects_older_schema_without_migrating_it(tmp_path):
    database = tmp_path / "isolated-old.db"
    store = ContentStore(database)
    with store._transaction() as connection:
        connection.execute(
            "UPDATE content_schema_meta SET schema_version = 6 WHERE singleton = 1"
        )
    store.close()

    with pytest.raises(RuntimeError, match="current runtime version"):
        _verify_existing_database(database)
    connection = sqlite3.connect(database)
    try:
        assert connection.execute(
            "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
        ).fetchone()[0] == 6
    finally:
        connection.close()


def test_runtime_gate_requires_an_explicitly_provisioned_database(tmp_path):
    with pytest.raises(RuntimeError, match="explicitly initialized and migrated"):
        _verify_existing_database(tmp_path / "not-created.db")


def test_runtime_gate_rejects_database_without_schema_metadata(tmp_path):
    database = tmp_path / "missing-runtime-metadata.db"
    sqlite3.connect(database).close()
    before_bytes = database.read_bytes()

    with pytest.raises(RuntimeError):
        _verify_existing_database(database)

    assert database.read_bytes() == before_bytes


@pytest.mark.parametrize(
    "table_sql, rows",
    [
        (
            "CREATE TABLE content_schema_meta "
            "(singleton INTEGER, schema_version INTEGER)",
            [(1, 10)],
        ),
        (
            "CREATE TABLE content_schema_meta ("
            "singleton INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL)",
            [(1, 10)],
        ),
        (
            "CREATE TABLE content_schema_meta ("
            "singleton INTEGER PRIMARY KEY CHECK(singleton = 1), "
            "schema_version INTEGER NOT NULL, extra TEXT)",
            [(1, 10, "unexpected")],
        ),
        (
            "CREATE TABLE content_schema_meta (singleton, schema_version)",
            [(1, 10), (2, 10)],
        ),
        (
            "CREATE TABLE content_schema_meta ("
            "singleton INTEGER PRIMARY KEY CHECK(singleton = 1), "
            "schema_version INTEGER NOT NULL)",
            [(1, "10")],
        ),
    ],
)
def test_runtime_gate_rejects_malformed_v10_metadata_without_mutation(
    tmp_path,
    table_sql,
    rows,
):
    database = tmp_path / "malformed-runtime-metadata.db"
    connection = sqlite3.connect(database)
    connection.execute(table_sql)
    placeholders = ", ".join("?" for _ in rows[0])
    connection.executemany(
        f"INSERT INTO content_schema_meta VALUES ({placeholders})",
        rows,
    )
    connection.commit()
    connection.close()
    before_bytes = database.read_bytes()

    with pytest.raises(RuntimeError):
        _verify_existing_database(database)

    assert database.read_bytes() == before_bytes
    verify = sqlite3.connect(database)
    try:
        assert verify.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall() == [("content_schema_meta",)]
    finally:
        verify.close()


def test_runtime_gate_rejects_base_schema_until_explicit_migrations_complete(
    tmp_path,
):
    database = tmp_path / "isolated-base.db"
    store = ContentStore(database)
    set_legacy_schema_version(store, 7)
    store.close()
    with pytest.raises(RuntimeError, match="current runtime version"):
        _verify_existing_database(database)

    store = ContentStore(database)
    assert store.migrate_publishing_attempt_schema() == 9
    store.close()
    with pytest.raises(RuntimeError, match="current runtime version"):
        _verify_existing_database(database)

    store = ContentStore(database)
    assert store.migrate_private_render_operation_schema() == 10
    assert store.migrate_source_identity_schema() == 1
    store.close()
    before_bytes = database.read_bytes()
    _verify_existing_database(database)
    assert database.read_bytes() == before_bytes


@pytest.mark.parametrize("version", [0, 11])
def test_store_rejects_unsupported_schema_versions_without_repairing(
    tmp_path,
    version,
):
    database = tmp_path / f"isolated-version-{version}.db"
    store = ContentStore(database)
    with store._transaction() as connection:
        connection.execute(
            "UPDATE content_schema_meta SET schema_version = ? WHERE singleton = 1",
            (version,),
        )
    store.close()
    with pytest.raises(RuntimeError):
        ContentStore(database)


def test_missing_or_malformed_schema_metadata_is_rejected(tmp_path):
    missing_database = tmp_path / "missing-metadata.db"
    store = ContentStore(missing_database)
    store._connection.execute("DROP TABLE content_schema_meta")
    store.close()
    with pytest.raises(RuntimeError, match="metadata is missing"):
        ContentStore(missing_database)

    malformed_database = tmp_path / "malformed-metadata.db"
    connection = sqlite3.connect(malformed_database)
    connection.execute(
        "CREATE TABLE content_schema_meta (singleton INTEGER PRIMARY KEY)"
    )
    connection.execute(
        "INSERT INTO content_schema_meta(singleton) VALUES (1)"
    )
    connection.commit()
    connection.close()
    with pytest.raises(RuntimeError, match="metadata is malformed"):
        ContentStore(malformed_database)

    missing_row_database = tmp_path / "missing-version-row.db"
    connection = sqlite3.connect(missing_row_database)
    connection.execute(
        """
        CREATE TABLE content_schema_meta (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            schema_version INTEGER NOT NULL
        )
        """
    )
    connection.commit()
    connection.close()
    with pytest.raises(RuntimeError, match="missing its version row"):
        ContentStore(missing_row_database)


def test_failed_migration_rolls_back_partial_ddl_and_version(tmp_path, monkeypatch):
    store = ContentStore(tmp_path / "isolated-interrupted.db")
    set_legacy_schema_version(store, 7)
    assert store.migrate_publishing_attempt_schema() == 9

    def fail_after_partial_create(connection):
        connection.execute(
            "CREATE TABLE interrupted_render_migration (id INTEGER PRIMARY KEY)"
        )
        raise RuntimeError("synthetic migration interruption")

    monkeypatch.setattr(
        ContentStore,
        "_create_private_render_operation_tables",
        staticmethod(fail_after_partial_create),
    )
    with pytest.raises(RuntimeError, match="synthetic migration interruption"):
        store.migrate_private_render_operation_schema()
    assert _version(store) == 9
    assert store._connection.execute(
        """
        SELECT COUNT(*) FROM sqlite_master
        WHERE type = 'table' AND name = 'interrupted_render_migration'
        """
    ).fetchone()[0] == 0
    assert store._connection.execute(
        """
        SELECT COUNT(*) FROM sqlite_master
        WHERE type = 'table' AND name = 'content_private_render_operations'
        """
    ).fetchone()[0] == 0
    store.close()


def test_nested_store_transactions_use_savepoints_and_preserve_rollback_boundaries(
    tmp_path,
):
    store = ContentStore(tmp_path / "nested-transactions.db")
    store._connection.execute(
        "CREATE TABLE nested_transaction_probe (value TEXT PRIMARY KEY)"
    )

    with store._transaction() as connection:
        connection.execute(
            "INSERT INTO nested_transaction_probe(value) VALUES ('outer-before')"
        )
        with pytest.raises(RuntimeError, match="inner rollback"):
            with store._transaction() as nested:
                nested.execute(
                    "INSERT INTO nested_transaction_probe(value) VALUES ('inner')"
                )
                raise RuntimeError("inner rollback")
        connection.execute(
            "INSERT INTO nested_transaction_probe(value) VALUES ('outer-after')"
        )

    rows = store._connection.execute(
        "SELECT value FROM nested_transaction_probe ORDER BY value"
    ).fetchall()
    assert [row["value"] for row in rows] == ["outer-after", "outer-before"]

    with pytest.raises(RuntimeError, match="outer rollback"):
        with store._transaction() as connection:
            with store._transaction() as nested:
                nested.execute(
                    "INSERT INTO nested_transaction_probe(value) VALUES ('nested-commit')"
                )
            raise RuntimeError("outer rollback")

    assert store._connection.execute(
        "SELECT COUNT(*) FROM nested_transaction_probe "
        "WHERE value = 'nested-commit'"
    ).fetchone()[0] == 0
    store.close()
