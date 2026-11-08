import hashlib
import sqlite3

import pytest

from scripts.ultron_sqlite_backup import (
    BackupError,
    backup_database,
    restore_backup,
    verify_backup,
)


def _create_wal_database(path):
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("PRAGMA user_version=7")
    connection.execute("PRAGMA application_id=123456")
    connection.execute(
        "CREATE TABLE evidence (id INTEGER PRIMARY KEY AUTOINCREMENT, payload BLOB NOT NULL)"
    )
    connection.executemany(
        "INSERT INTO evidence(payload) VALUES (?)",
        [(bytes([index]) * 4096,) for index in range(1, 7)],
    )
    connection.commit()
    return connection


def test_wal_online_backup_integrity_permissions_checksum_and_restore(tmp_path):
    source = tmp_path / "source.db"
    source_connection = _create_wal_database(source)
    content_root = tmp_path / "content-root"
    (content_root / "inputs").mkdir(parents=True)
    (content_root / "outputs").mkdir()
    (content_root / "inputs" / "source-video").write_bytes(b"source-artifact")
    (content_root / "outputs" / "rendered-video").write_bytes(b"rendered-artifact")
    wal_path = tmp_path / "source.db-wal"
    assert wal_path.exists() and wal_path.stat().st_size > 0
    before_source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    before_wal_hash = hashlib.sha256(wal_path.read_bytes()).hexdigest()

    backup_dir = tmp_path / "protected-backup"
    manifest = backup_database(
        source,
        backup_dir,
        "cre",
        expected_user_version=7,
        managed_content_root=content_root,
    )
    backup_path = backup_dir / "cre.sqlite"
    manifest_path = backup_dir / "cre.manifest.json"
    verified = verify_backup(backup_path, manifest_path)

    assert verified["outcome"] == "VERIFIED"
    assert verified["sourceMetadata"]["journalMode"].lower() == "wal"
    assert verified["sourceMetadata"]["userVersion"] == 7
    assert verified["sourceMetadata"]["applicationId"] == 123456
    assert verified["sourceMetadata"]["integrity"] == "ok"
    assert verified["backupMetadata"]["integrity"] == "ok"
    assert verified["backupMetadata"]["foreignKeyViolations"] == 0
    assert verified["backupMetadata"]["recordCount"] == 7
    assert len(verified["backupMetadata"]["recordsSha256"]) == 64
    assert len(verified["managedContent"]["files"]) == 2
    assert manifest["backup"]["size"] == backup_path.stat().st_size
    assert manifest["backup"]["sha256"] == hashlib.sha256(
        backup_path.read_bytes()
    ).hexdigest()
    assert backup_dir.stat().st_mode & 0o777 == 0o700
    assert backup_path.stat().st_mode & 0o777 == 0o600
    assert manifest_path.stat().st_mode & 0o777 == 0o600

    restore_path = tmp_path / "restored/separate.db"
    restored = restore_backup(
        backup_path,
        manifest_path,
        restore_path,
        expected_user_version=7,
        managed_content_destination=tmp_path / "restored/content",
    )
    assert restored["outcome"] == "RESTORED_AND_VERIFIED"
    assert restored["metadata"]["integrity"] == "ok"
    assert restored["metadata"]["recordsSha256"] == verified["backupMetadata"]["recordsSha256"]
    assert restored["managedContent"]["outcome"] == "RESTORED_AND_VERIFIED"
    assert (tmp_path / "restored/content/inputs/source-video").read_bytes() == b"source-artifact"
    assert (tmp_path / "restored/content/outputs/rendered-video").read_bytes() == b"rendered-artifact"
    assert restored["metadata"]["schemaSha256"] == manifest["backupMetadata"]["schemaSha256"]
    assert restore_path.stat().st_mode & 0o777 == 0o600
    restored_connection = sqlite3.connect(restore_path)
    try:
        assert restored_connection.execute(
            "SELECT count(*) FROM evidence"
        ).fetchone()[0] == 6
        assert restored_connection.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='evidence'"
        ).fetchone()[0] == 6
        assert restored_connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        restored_connection.close()

    assert hashlib.sha256(source.read_bytes()).hexdigest() == before_source_hash
    assert hashlib.sha256(wal_path.read_bytes()).hexdigest() == before_wal_hash
    source_connection.close()


def test_sqlite_backup_rejects_wrong_expected_version_without_mutating_source(tmp_path):
    source = tmp_path / "source.db"
    connection = _create_wal_database(source)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    with pytest.raises(BackupError, match="user_version"):
        backup_database(
            source,
            tmp_path / "rejected-backup",
            "wrong-version",
            expected_user_version=8,
        )

    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    connection.close()


def test_sqlite_backup_verification_rejects_tampered_backup(tmp_path):
    source = tmp_path / "source.db"
    connection = _create_wal_database(source)
    backup_dir = tmp_path / "protected-backup"
    backup_database(source, backup_dir, "evidence")
    backup = backup_dir / "evidence.sqlite"
    with backup.open("ab") as stream:
        stream.write(b"tamper")

    with pytest.raises(BackupError, match="manifest"):
        verify_backup(backup, backup_dir / "evidence.manifest.json")
    connection.close()


def test_sqlite_backup_rejects_foreign_key_violations(tmp_path):
    source = tmp_path / "foreign-key.db"
    connection = sqlite3.connect(source)
    connection.executescript(
        "CREATE TABLE parent (id INTEGER PRIMARY KEY);"
        "CREATE TABLE child (parent_id INTEGER REFERENCES parent(id));"
        "PRAGMA foreign_keys=OFF;"
        "INSERT INTO child(parent_id) VALUES (9);"
    )
    connection.commit()
    connection.close()

    with pytest.raises(BackupError, match="foreign_key_check"):
        backup_database(source, tmp_path / "rejected-backup", "invalid")


def test_sqlite_backup_verification_rejects_tampered_managed_content(tmp_path):
    source = tmp_path / "source.db"
    connection = _create_wal_database(source)
    content_root = tmp_path / "content"
    content_root.mkdir()
    (content_root / "artifact").write_bytes(b"verified artifact")
    backup_dir = tmp_path / "protected-backup"
    backup_database(source, backup_dir, "evidence", managed_content_root=content_root)

    snapshot = backup_dir / "evidence.content" / "artifact"
    snapshot.write_bytes(b"tampered")
    with pytest.raises(BackupError, match="Managed content snapshot"):
        verify_backup(
            backup_dir / "evidence.sqlite",
            backup_dir / "evidence.manifest.json",
        )
    connection.close()
