import hashlib
import json
import sqlite3
import stat
import zipfile
from datetime import UTC, datetime, timedelta

import pytest

import scripts.cre_recovery_evidence as recovery_evidence
from scripts.cre_recovery_evidence import (
    RecoveryEvidenceError,
    create_recovery_evidence,
)


def _create_database(path):
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE content_schema_meta "
        "(singleton INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL)"
    )
    connection.execute(
        "INSERT INTO content_schema_meta(singleton, schema_version) VALUES (1, 7)"
    )
    connection.execute("PRAGMA user_version=0")
    connection.execute(
        "CREATE TABLE artifact_records "
        "(artifact_id TEXT PRIMARY KEY, output_sha256 TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO artifact_records VALUES (?, ?)",
        ("artifact-1", hashlib.sha256(b"rendered artifact").hexdigest()),
    )
    connection.commit()
    connection.close()


def _capture(source, content, destination, *, retention_count=3, retention_days=30):
    return create_recovery_evidence(
        source,
        content,
        destination,
        expected_cre_schema_version=7,
        expected_user_version=0,
        retention_count=retention_count,
        retention_days=retention_days,
        minimum_free_bytes=1,
    )


def test_recovery_snapshot_restores_database_and_managed_artifacts(tmp_path):
    source = tmp_path / "source.sqlite"
    content = tmp_path / "content"
    destination = tmp_path / "durable-backups"
    _create_database(source)
    (content / "outputs").mkdir(parents=True)
    (content / "outputs" / "artifact-1").write_bytes(b"rendered artifact")
    destination.mkdir(mode=0o700)

    evidence = _capture(source, content, destination)
    snapshot = destination / evidence["snapshotId"]
    persisted = json.loads(
        (snapshot / "RECOVERY-EVIDENCE.json").read_text(encoding="utf-8")
    )

    assert evidence["outcome"] == "VERIFIED"
    assert persisted["outcome"] == "VERIFIED"
    assert persisted["backup"]["integrity"] == "ok"
    assert persisted["backup"]["foreignKeyViolations"] == 0
    assert persisted["backup"]["recordsSha256"]
    assert persisted["restoreTest"]["outcome"] == "RESTORED_AND_VERIFIED"
    assert persisted["restoreTest"]["creSchemaVersion"] == 7
    assert persisted["managedContent"]["fileCount"] == 1
    assert persisted["managedContent"]["restoredAndVerified"] is True
    assert persisted["retentionPolicy"]["approvalStatus"] == "PENDING_OPERATOR_APPROVAL"
    assert persisted["retentionPolicy"]["automaticDeletion"] is False
    assert stat.S_IMODE(snapshot.stat().st_mode) == 0o700
    assert stat.S_IMODE((snapshot / "production.sqlite").stat().st_mode) == 0o600

    archive_path = snapshot / "RECOVERY-EVIDENCE.zip"
    sidecar = archive_path.with_suffix(".zip.sha256")
    archive_hash, archive_name = sidecar.read_text(encoding="ascii").strip().split()
    assert archive_name == archive_path.name
    assert archive_hash == hashlib.sha256(archive_path.read_bytes()).hexdigest()
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {
            "RECOVERY-EVIDENCE.json",
            "RECOVERY-EVIDENCE.json.sha256",
            "DATABASE-BACKUP-MANIFEST.json",
            "MANAGED-CONTENT-INVENTORY.json",
        }
        backup_manifest = json.loads(
            archive.read("DATABASE-BACKUP-MANIFEST.json")
        )
        inventory = json.loads(
            archive.read("MANAGED-CONTENT-INVENTORY.json")
        )
    assert backup_manifest["outcome"] == "VERIFIED"
    assert inventory["files"][0]["sha256"] == hashlib.sha256(
        b"rendered artifact"
    ).hexdigest()
    restored_file = (
        snapshot / "restore-test" / "managed-content" / "outputs" / "artifact-1"
    )
    assert restored_file.read_bytes() == b"rendered artifact"


def test_retention_capacity_refuses_without_deleting_existing_snapshot(tmp_path):
    source = tmp_path / "source.sqlite"
    content = tmp_path / "content"
    destination = tmp_path / "durable-backups"
    _create_database(source)
    content.mkdir()
    destination.mkdir(mode=0o700)
    first = _capture(source, content, destination, retention_count=1)
    first_directory = destination / first["snapshotId"]
    before = sorted(path.name for path in first_directory.iterdir())

    with pytest.raises(RecoveryEvidenceError, match="retention count is full"):
        _capture(source, content, destination, retention_count=1)

    assert sorted(path.name for path in first_directory.iterdir()) == before
    assert len(list(destination.iterdir())) == 1


def test_retention_age_refuses_without_deleting_expired_snapshot(tmp_path, monkeypatch):
    class FixedDateTime(datetime):
        current = datetime(2026, 1, 1, tzinfo=UTC)

        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(cls.current.timestamp(), tz or UTC)

    source = tmp_path / "source.sqlite"
    content = tmp_path / "content"
    destination = tmp_path / "durable-backups"
    _create_database(source)
    content.mkdir()
    destination.mkdir(mode=0o700)
    monkeypatch.setattr(recovery_evidence, "datetime", FixedDateTime)

    first = _capture(source, content, destination, retention_days=30)
    first_directory = destination / first["snapshotId"]
    before = sorted(path.name for path in first_directory.iterdir())
    FixedDateTime.current += timedelta(days=31)

    with pytest.raises(RecoveryEvidenceError, match="outside the configured retention duration"):
        _capture(source, content, destination, retention_days=30)

    assert sorted(path.name for path in first_directory.iterdir()) == before
    assert len(list(destination.iterdir())) == 1


def test_recovery_snapshot_rejects_wrong_schema_and_unsafe_destination(tmp_path):
    source = tmp_path / "source.sqlite"
    content = tmp_path / "content"
    destination = tmp_path / "durable-backups"
    _create_database(source)
    content.mkdir()
    destination.mkdir(mode=0o755)

    with pytest.raises(RecoveryEvidenceError, match="mode 0700"):
        _capture(source, content, destination)
    destination.chmod(0o700)

    with pytest.raises(RecoveryEvidenceError, match="schema"):
        create_recovery_evidence(
            source,
            content,
            destination,
            expected_cre_schema_version=10,
            expected_user_version=0,
            retention_count=3,
            retention_days=30,
            minimum_free_bytes=1,
        )
    assert not list(destination.iterdir())


def test_recovery_snapshots_use_distinct_ids_without_overwriting(tmp_path):
    source = tmp_path / "source.sqlite"
    content = tmp_path / "content"
    destination = tmp_path / "durable-backups"
    _create_database(source)
    content.mkdir()
    destination.mkdir(mode=0o700)

    first = _capture(source, content, destination)
    second = _capture(source, content, destination)
    assert first["snapshotId"] != second["snapshotId"]
    assert first["snapshotId"] in {path.name for path in destination.iterdir()}
    assert second["snapshotId"] in {path.name for path in destination.iterdir()}
    assert (destination / first["snapshotId"] / "production.sqlite").exists()
    assert (destination / second["snapshotId"] / "production.sqlite").exists()
