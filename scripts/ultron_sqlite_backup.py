#!/usr/bin/env python3
"""Create and verify protected SQLite online backups without copying live files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence


_NAME = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


class BackupError(RuntimeError):
    """A SQLite backup could not be safely created, verified, or restored."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _readonly_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve(strict=True).as_uri()}?mode=ro", uri=True)


def _metadata(connection: sqlite3.Connection) -> dict:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()
    if integrity is None or integrity[0] != "ok":
        raise BackupError("SQLite integrity_check did not return ok.")
    schema_rows = connection.execute(
        """
        SELECT type, name, tbl_name, COALESCE(sql, '')
        FROM sqlite_schema
        WHERE name NOT LIKE 'sqlite_%'
        ORDER BY type, name, tbl_name
        """
    ).fetchall()
    schema_bytes = json.dumps(
        schema_rows, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return {
        "integrity": integrity[0],
        "userVersion": connection.execute("PRAGMA user_version").fetchone()[0],
        "applicationId": connection.execute("PRAGMA application_id").fetchone()[0],
        "journalMode": connection.execute("PRAGMA journal_mode").fetchone()[0],
        "schemaSha256": hashlib.sha256(schema_bytes).hexdigest(),
    }


def _require_secure_directory(path: Path, *, create: bool) -> None:
    if create:
        path.mkdir(mode=0o700, parents=False, exist_ok=False)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise BackupError("Backup directory must be owned by this user and mode 0700.")


def _write_private_json(path: Path, data: dict) -> None:
    encoded = (json.dumps(data, sort_keys=True, indent=2) + "\n").encode("utf-8")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def backup_database(
    source: Path,
    destination_directory: Path,
    name: str,
    *,
    expected_user_version: int | None = None,
) -> dict:
    if not _NAME.fullmatch(name) or name in {".", ".."}:
        raise BackupError("Backup name must be a simple filename.")
    source = source.resolve(strict=True)
    _require_secure_directory(destination_directory, create=True)
    destination = destination_directory / f"{name}.sqlite"
    manifest_path = destination_directory / f"{name}.manifest.json"
    if destination.exists() or manifest_path.exists():
        raise BackupError("Backup output already exists.")

    source_connection = _readonly_connection(source)
    temporary_path: Path | None = None
    try:
        source_before = _metadata(source_connection)
        if (
            expected_user_version is not None
            and source_before["userVersion"] != expected_user_version
        ):
            raise BackupError("Source user_version does not match the approved value.")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{name}.", suffix=".tmp", dir=destination_directory
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        backup_connection = sqlite3.connect(temporary_path)
        try:
            source_connection.backup(backup_connection)
            backup_connection.commit()
        finally:
            backup_connection.close()
        source_after = _metadata(source_connection)
        if source_after != source_before:
            raise BackupError("Source metadata changed during the online backup.")
    except sqlite3.Error as error:
        raise BackupError("SQLite online backup failed.") from error
    finally:
        source_connection.close()

    try:
        with temporary_path.open("rb") as stream:
            os.fsync(stream.fileno())
        os.chmod(temporary_path, 0o600)
        backup_connection = sqlite3.connect(
            f"{temporary_path.resolve().as_uri()}?mode=ro", uri=True
        )
        try:
            backup_metadata = _metadata(backup_connection)
        finally:
            backup_connection.close()
        if any(
            backup_metadata[key] != source_before[key]
            for key in ("userVersion", "applicationId", "schemaSha256", "integrity")
        ):
            raise BackupError("Backup schema or integrity does not match the source.")
        os.link(temporary_path, destination)
        temporary_path.unlink()
        temporary_path = None
        stat_result = destination.stat()
        manifest = {
            "format": 1,
            "createdAt": datetime.now(UTC).isoformat(),
            "source": str(source),
            "sourceMetadata": source_before,
            "backupMetadata": backup_metadata,
            "backup": {
                "file": destination.name,
                "size": stat_result.st_size,
                "sha256": _sha256_file(destination),
                "mode": stat.S_IMODE(stat_result.st_mode),
            },
            "outcome": "VERIFIED",
        }
        _write_private_json(manifest_path, manifest)
        return manifest
    except (OSError, sqlite3.Error) as error:
        destination.unlink(missing_ok=True)
        raise BackupError("Backup output could not be securely finalized.") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def verify_backup(backup_path: Path, manifest_path: Path) -> dict:
    try:
        parent_info = backup_path.parent.lstat()
        if (
            not stat.S_ISDIR(parent_info.st_mode)
            or parent_info.st_uid != os.getuid()
            or stat.S_IMODE(parent_info.st_mode) != 0o700
        ):
            raise BackupError("Backup directory must be owned by this user and mode 0700.")
        info = backup_path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise BackupError("Backup must be an owned regular file.")
        manifest_info = manifest_path.lstat()
        if (
            not stat.S_ISREG(manifest_info.st_mode)
            or manifest_info.st_uid != os.getuid()
            or stat.S_IMODE(manifest_info.st_mode) != 0o600
        ):
            raise BackupError("Backup manifest must be an owned mode-0600 file.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except BackupError:
        raise
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise BackupError("Backup or manifest cannot be read.") from error
    backup_record = manifest.get("backup", {})
    if (
        manifest.get("format") != 1
        or manifest.get("outcome") != "VERIFIED"
        or backup_record.get("file") != backup_path.name
        or backup_record.get("size") != info.st_size
        or backup_record.get("sha256") != _sha256_file(backup_path)
        or backup_record.get("mode") != stat.S_IMODE(info.st_mode)
    ):
        raise BackupError("Backup does not match its protected manifest.")
    connection = _readonly_connection(backup_path)
    try:
        metadata = _metadata(connection)
    except sqlite3.Error as error:
        raise BackupError("Backup database cannot be opened or checked.") from error
    finally:
        connection.close()
    if metadata != manifest.get("backupMetadata"):
        raise BackupError("Backup schema or integrity metadata does not match.")
    return manifest


def restore_backup(
    backup_path: Path,
    manifest_path: Path,
    destination: Path,
    *,
    expected_user_version: int | None = None,
) -> dict:
    manifest = verify_backup(backup_path, manifest_path)
    source_metadata = manifest["backupMetadata"]
    if (
        expected_user_version is not None
        and source_metadata["userVersion"] != expected_user_version
    ):
        raise BackupError("Backup user_version does not match the approved value.")
    if destination.exists():
        raise BackupError("Restore destination already exists.")
    backup_connection = _readonly_connection(backup_path)
    destination_connection: sqlite3.Connection | None = None
    try:
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(
            destination,
            os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.close(descriptor)
        destination_connection = sqlite3.connect(destination)
        backup_connection.backup(destination_connection)
        destination_connection.commit()
        restored = _metadata(destination_connection)
        if any(
            restored[key] != source_metadata[key]
            for key in ("userVersion", "applicationId", "schemaSha256", "integrity")
        ):
            raise BackupError("Restored database failed integrity or schema verification.")
    except (OSError, sqlite3.Error) as error:
        destination.unlink(missing_ok=True)
        raise BackupError("Database restoration failed.") from error
    except BackupError:
        destination.unlink(missing_ok=True)
        raise
    finally:
        backup_connection.close()
        if destination_connection is not None:
            destination_connection.close()
    os.chmod(destination, 0o600)
    return {
        "destination": str(destination),
        "size": destination.stat().st_size,
        "sha256": _sha256_file(destination),
        "metadata": restored,
        "outcome": "RESTORED_AND_VERIFIED",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--source", type=Path, required=True)
    backup.add_argument("--destination-dir", type=Path, required=True)
    backup.add_argument("--name", required=True)
    backup.add_argument("--expected-user-version", type=int)
    verify = commands.add_parser("verify")
    verify.add_argument("--backup", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    restore = commands.add_parser("restore")
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--manifest", type=Path, required=True)
    restore.add_argument("--destination", type=Path, required=True)
    restore.add_argument("--expected-user-version", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.action == "backup":
        result = backup_database(
            arguments.source,
            arguments.destination_dir,
            arguments.name,
            expected_user_version=arguments.expected_user_version,
        )
    elif arguments.action == "verify":
        result = verify_backup(arguments.backup, arguments.manifest)
    else:
        result = restore_backup(
            arguments.backup,
            arguments.manifest,
            arguments.destination,
            expected_user_version=arguments.expected_user_version,
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
