from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from ultron.application.content.store import ContentStore


def _read_schema_version(database: Path) -> int:
    connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
    try:
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite integrity check failed.")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("SQLite foreign-key check failed.")
        row = connection.execute(
            "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
        ).fetchone()
        if row is None:
            raise RuntimeError("Content schema metadata is missing.")
        return int(row[0])
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Explicitly migrate a backed-up CRE content database from schema "
            "v7, v8, or v9 to v10."
        )
    )
    parser.add_argument("database", type=Path, help="Existing content SQLite database")
    parser.add_argument(
        "--backup",
        required=True,
        type=Path,
        help="Existing separate backup copy made before this migration",
    )
    args = parser.parse_args()
    database = args.database
    backup = args.backup
    if (
        database.is_symlink()
        or backup.is_symlink()
        or not database.is_file()
        or not backup.is_file()
        or database.resolve() == backup.resolve()
    ):
        parser.error("Database and a separate, existing regular-file backup are required.")

    try:
        database_version = _read_schema_version(database)
        backup_version = _read_schema_version(backup)
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        parser.error(f"Database and backup must be valid content databases: {exc}")
    if database_version not in {
        ContentStore.SCHEMA_VERSION,
        8,
        ContentStore.PUBLISHING_ATTEMPT_SCHEMA_VERSION,
        ContentStore.PRIVATE_RENDER_OPERATION_SCHEMA_VERSION,
    }:
        parser.error("The database must be schema v7, v8, v9, or v10.")
    if backup_version != database_version:
        parser.error("The backup schema version must match the database before migration.")

    store = ContentStore(database, recover_interrupted_jobs=False)
    try:
        if database_version < ContentStore.PUBLISHING_ATTEMPT_SCHEMA_VERSION:
            version = store.migrate_publishing_attempt_schema()
        else:
            version = database_version
        if version < ContentStore.PRIVATE_RENDER_OPERATION_SCHEMA_VERSION:
            version = store.migrate_private_render_operation_schema()
        else:
            store.migrate_private_render_operation_schema()
        print(f"CRE private render operation schema is now v{version}.")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
