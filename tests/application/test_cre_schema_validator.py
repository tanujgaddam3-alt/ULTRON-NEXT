from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts import cre_schema_validator as validator
from ultron.application.content.store import ContentStore


def _fixture_at_version(path: Path, version: int) -> None:
    store = ContentStore(path, recover_interrupted_jobs=False)
    if version >= 9:
        assert store.migrate_publishing_attempt_schema() == 9
    if version >= 10:
        assert store.migrate_private_render_operation_schema() == 10
    store.close()


def _database_bytes(path: Path) -> bytes:
    return path.read_bytes()


@pytest.mark.parametrize("version", [7, 9, 10])
def test_baseline_matches_source_manifest_and_is_read_only(tmp_path, version):
    database = tmp_path / f"cre-v{version}.sqlite"
    _fixture_at_version(database, version)
    before = _database_bytes(database)

    result = validator.inspect_cre_fixture(database)

    assert result["status"] == "PASS"
    assert result["creSchemaVersion"] == version
    assert result["schemaObjects"]["matches"] is True
    assert result["schemaObjects"]["expectedManifest"] == sorted(
        result["schemaObjects"]["expectedManifest"],
        key=lambda item: (item["type"], item["name"]),
    )
    layer_by_name = {
        item["name"]: item["introducedOrChangedAt"]
        for item in result["schemaObjects"]["expectedManifest"]
    }
    assert layer_by_name["content_edit_plan_revisions"] == "initialization-v7"
    if version >= 9:
        assert layer_by_name["content_publishing_attempts"] == "v7-to-v9"
    if version >= 10:
        assert (
            layer_by_name["content_private_render_operations"]
            == "v9-to-v10"
        )
    assert result["tableCounts"]
    assert result["relationships"]
    assert all(item["orphanRows"] == 0 for item in result["relationships"])
    assert _database_bytes(database) == before


@pytest.mark.parametrize("version", [7, 9, 10])
def test_recovery_candidate_validates_requested_version_without_runtime_gate(
    tmp_path,
    version,
):
    database = tmp_path / f"candidate-v{version}.sqlite"
    _fixture_at_version(database, version)
    before = _database_bytes(database)

    result = validator.validate_recovery_candidate(
        database,
        expected_schema_version=version,
        expected_user_version=0,
    )

    assert result["status"] == "PASS"
    assert result["creSchemaVersion"] == version
    assert result["runtimeV10PreflightUsed"] is False
    assert _database_bytes(database) == before


@pytest.mark.parametrize(
    ("database_setup", "message"),
    [
        ("missing", "metadata table is missing or malformed"),
        ("malformed", "metadata table is missing or malformed"),
        ("bad-row", "metadata row is missing or unrecognized"),
    ],
)
def test_validator_rejects_missing_or_malformed_metadata_without_mutation(
    tmp_path,
    database_setup,
    message,
):
    database = tmp_path / f"{database_setup}.sqlite"
    connection = sqlite3.connect(database)
    if database_setup == "malformed":
        connection.execute(
            "CREATE TABLE content_schema_meta "
            "(singleton INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO content_schema_meta VALUES (1, 10)"
        )
    elif database_setup == "bad-row":
        connection.execute(
            """
            CREATE TABLE content_schema_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                schema_version INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO content_schema_meta VALUES (1, 8)"
        )
    connection.commit()
    connection.close()
    before = _database_bytes(database)

    with pytest.raises(validator.ValidationError, match=message):
        validator.inspect_cre_fixture(database)

    assert _database_bytes(database) == before


def test_schema_object_definition_drift_is_reported_without_mutation(tmp_path):
    database = tmp_path / "drift.sqlite"
    _fixture_at_version(database, 10)
    connection = sqlite3.connect(database)
    connection.execute(
        "DROP TRIGGER content_private_render_operation_identity_immutable"
    )
    connection.execute(
        """
        CREATE TRIGGER content_private_render_operation_identity_immutable
        BEFORE UPDATE OF owner_id ON content_private_render_operations
        BEGIN
            SELECT 1;
        END
        """
    )
    connection.commit()
    connection.close()
    before = _database_bytes(database)

    result = validator.inspect_cre_fixture(database)

    assert result["status"] == "FAIL"
    assert result["schemaObjects"]["definitionMismatches"] == [
        {
            "type": "trigger",
            "name": "content_private_render_operation_identity_immutable",
        }
    ]
    assert _database_bytes(database) == before


def test_foreign_key_violations_are_aggregated_and_reported(tmp_path):
    database = tmp_path / "foreign-key-violation.sqlite"
    _fixture_at_version(database, 10)
    connection = sqlite3.connect(database)
    connection.execute(
        """
        INSERT INTO content_private_render_operations (
            operation_id, owner_id, project_id, idempotency_key,
            request_fingerprint, input_snapshot_json, state, created_at, updated_at
        ) VALUES ('orphan', 'fixture-owner', 'missing-project', 'idem',
                  ?, '{}', 'queued', 'now', 'now')
        """,
        ("a" * 64,),
    )
    connection.commit()
    connection.close()
    before = _database_bytes(database)

    result = validator.inspect_cre_fixture(database)

    assert result["status"] == "FAIL"
    assert result["checks"]["foreignKeyViolationCount"] == 1
    relationship = next(
        item
        for item in result["relationships"]
        if item["childTable"] == "content_private_render_operations"
    )
    assert relationship["orphanRows"] == 1
    assert _database_bytes(database) == before


def test_integrity_failure_is_reported_without_changing_fixture(tmp_path, monkeypatch):
    database = tmp_path / "integrity-fixture.sqlite"
    _fixture_at_version(database, 10)
    before = _database_bytes(database)
    original_open = validator._open_read_only

    class IntegrityFailureConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, query, *args):
            if query == "PRAGMA integrity_check":
                class Result:
                    def fetchall(self):
                        return [("synthetic integrity failure",)]

                return Result()
            return self.connection.execute(query, *args)

        def close(self):
            self.connection.close()

    monkeypatch.setattr(
        validator,
        "_open_read_only",
        lambda path: IntegrityFailureConnection(original_open(path)),
    )

    result = validator.validate_recovery_candidate(
        database,
        expected_schema_version=10,
    )

    assert result["status"] == "FAIL"
    assert result["checks"]["integrityCheck"] == "failed"
    assert _database_bytes(database) == before


def test_user_version_mismatch_is_reported(tmp_path):
    database = tmp_path / "user-version.sqlite"
    _fixture_at_version(database, 7)
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA user_version = 3")
    connection.commit()
    connection.close()
    before = _database_bytes(database)

    result = validator.validate_recovery_candidate(
        database,
        expected_schema_version=7,
        expected_user_version=0,
    )

    assert result["status"] == "FAIL"
    assert result["sqliteUserVersion"] == 3
    assert _database_bytes(database) == before


def test_configured_production_path_is_rejected_before_open(monkeypatch):
    monkeypatch.setattr(
        validator,
        "_open_read_only",
        lambda path: pytest.fail("production path must be rejected before opening"),
    )

    with pytest.raises(validator.ValidationError, match="production"):
        validator.inspect_cre_fixture(validator.CONFIGURED_CRE_DATABASE)


def test_symlink_alias_is_rejected_before_open(tmp_path, monkeypatch):
    alias = tmp_path / "production-alias.sqlite"
    alias.symlink_to(validator.CONFIGURED_CRE_DATABASE)
    monkeypatch.setattr(
        validator,
        "_open_read_only",
        lambda path: pytest.fail("symlink alias must be rejected before opening"),
    )

    with pytest.raises(validator.ValidationError, match="symbolic link"):
        validator.inspect_cre_fixture(alias)


def test_non_temporary_path_is_rejected(tmp_path, monkeypatch):
    database = tmp_path / "fixture.sqlite"
    _fixture_at_version(database, 7)
    monkeypatch.setattr(
        validator.tempfile,
        "gettempdir",
        lambda: str(Path.home()),
    )
    with pytest.raises(validator.ValidationError, match="temporary directory"):
        validator.inspect_cre_fixture(database)
