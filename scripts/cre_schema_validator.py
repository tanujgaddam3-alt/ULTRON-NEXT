"""Fixture-only, read-only CRE schema and recovery-candidate validation."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from ultron.application.content.store import ContentStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIGURED_CRE_DATABASE = PROJECT_ROOT / "data" / "ultron_cre.db"
SUPPORTED_SCHEMA_VERSIONS = (7, 9, 10)
SCHEMA_LAYERS = (
    (7, "initialization-v7"),
    (9, "v7-to-v9"),
    (10, "v9-to-v10"),
)


class ValidationError(RuntimeError):
    """Raised when a supplied fixture cannot be safely inspected."""


def _safe_fixture_path(database: str | Path) -> Path:
    supplied = Path(database).expanduser()
    if not supplied.is_absolute():
        raise ValidationError("Database path must be an explicit absolute fixture path.")

    production_path = CONFIGURED_CRE_DATABASE
    supplied_absolute = Path(os.path.abspath(supplied))
    production_absolute = Path(os.path.abspath(production_path))
    if supplied_absolute == production_absolute:
        raise ValidationError("Configured production CRE database path is forbidden.")
    if supplied.is_symlink():
        raise ValidationError("Fixture database path must not be a symbolic link.")

    try:
        supplied_resolved = supplied.resolve(strict=False)
        production_resolved = production_path.resolve(strict=False)
        resolved = supplied.resolve(strict=True)
        temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValidationError("Fixture database path is unavailable.") from error
    if supplied_resolved == production_resolved:
        raise ValidationError("Configured production CRE database path is forbidden.")
    if not resolved.is_file():
        raise ValidationError("Fixture database path must be an existing regular file.")
    if not resolved.is_relative_to(temporary_root):
        raise ValidationError("Database path must be inside the system temporary directory.")
    if resolved == production_resolved:
        raise ValidationError("Configured production CRE database path is forbidden.")
    return resolved


def _open_read_only(database: Path) -> sqlite3.Connection:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"{database.as_uri()}?mode=ro",
            uri=True,
            timeout=5.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection
    except sqlite3.Error as error:
        if connection is not None:
            connection.close()
        raise ValidationError("Fixture database could not be opened read-only.") from error


def _canonical_sql(sql: str | None) -> str | None:
    if sql is None:
        return None

    result: list[str] = []
    quote: str | None = None
    pending_space = False
    index = 0
    while index < len(sql):
        character = sql[index]
        if quote is not None:
            result.append(character)
            if quote == "]" and character == "]":
                quote = None
            elif quote != "]" and character == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    result.append(sql[index + 1])
                    index += 1
                else:
                    quote = None
        elif character in ("'", '"', "`"):
            if pending_space and result and result[-1] != " ":
                result.append(" ")
            pending_space = False
            quote = character
            result.append(character)
        elif character == "[":
            if pending_space and result and result[-1] != " ":
                result.append(" ")
            pending_space = False
            quote = "]"
            result.append(character)
        elif character.isspace():
            pending_space = True
        else:
            if pending_space and result and result[-1] != " ":
                result.append(" ")
            pending_space = False
            result.append(character)
        index += 1
    return "".join(result).strip()


def _schema_snapshot(connection: sqlite3.Connection) -> dict[tuple[str, str], dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE type IN ('table', 'index', 'trigger')
          AND name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    )
    return {
        (row["type"], row["name"]): {
            "type": row["type"],
            "name": row["name"],
            "table": row["tbl_name"],
            "sql": row["sql"],
        }
        for row in rows
    }


@lru_cache(maxsize=1)
def _expected_schema_snapshots() -> dict[int, dict[tuple[str, str], dict[str, Any]]]:
    """Build expected DDL from current initialization and migration source in memory."""
    store = ContentStore(":memory:", recover_interrupted_jobs=False)
    try:
        connection = store._connection
        snapshots = {7: _schema_snapshot(connection)}
        if store.migrate_publishing_attempt_schema() != 9:
            raise ValidationError("Source schema generator did not reach schema v9.")
        snapshots[9] = _schema_snapshot(connection)
        if store.migrate_private_render_operation_schema() != 10:
            raise ValidationError("Source schema generator did not reach schema v10.")
        snapshots[10] = _schema_snapshot(connection)
        return snapshots
    finally:
        store.close()


def _expected_manifest(version: int) -> list[dict[str, Any]]:
    snapshots = _expected_schema_snapshots()
    target = snapshots[version]
    stage_by_key: dict[tuple[str, str], str] = {}
    previous: dict[tuple[str, str], dict[str, Any]] = {}
    for layer_version, layer_name in SCHEMA_LAYERS:
        current = snapshots[layer_version]
        for key, definition in current.items():
            if (
                key not in previous
                or _canonical_sql(previous[key]["sql"])
                != _canonical_sql(definition["sql"])
            ):
                stage_by_key[key] = layer_name
        previous = current
        if layer_version == version:
            break
    return [
        {
            **target[key],
            "canonicalSql": _canonical_sql(target[key]["sql"]),
            "introducedOrChangedAt": stage_by_key[key],
        }
        for key in sorted(target)
    ]


def _compare_schema(
    actual: dict[tuple[str, str], dict[str, Any]],
    expected_manifest: list[dict[str, Any]],
) -> dict[str, Any]:
    expected = {
        (item["type"], item["name"]): item
        for item in expected_manifest
    }
    actual_keys = set(actual)
    expected_keys = set(expected)
    definition_mismatches = []
    for key in sorted(actual_keys & expected_keys):
        if _canonical_sql(actual[key]["sql"]) != expected[key]["canonicalSql"]:
            definition_mismatches.append({"type": key[0], "name": key[1]})
    return {
        "matches": (
            actual_keys == expected_keys
            and not definition_mismatches
        ),
        "missing": [
            {"type": kind, "name": name}
            for kind, name in sorted(expected_keys - actual_keys)
        ],
        "unexpected": [
            {"type": kind, "name": name}
            for kind, name in sorted(actual_keys - expected_keys)
        ],
        "definitionMismatches": definition_mismatches,
    }


def _read_schema_version(connection: sqlite3.Connection) -> int:
    definition = connection.execute(
        """
        SELECT sql FROM sqlite_master
        WHERE type = 'table' AND name = 'content_schema_meta'
        """
    ).fetchone()
    expected_sql = _canonical_sql(ContentStore._CONTENT_SCHEMA_META_DDL)
    if definition is None or _canonical_sql(definition["sql"]) != expected_sql:
        raise ValidationError("CRE schema metadata table is missing or malformed.")

    columns = [
        (row["name"], row["type"].upper(), row["notnull"], row["pk"])
        for row in connection.execute("PRAGMA table_info(content_schema_meta)")
    ]
    if columns != [
        ("singleton", "INTEGER", 0, 1),
        ("schema_version", "INTEGER", 1, 0),
    ]:
        raise ValidationError("CRE schema metadata columns are malformed.")
    rows = connection.execute(
        "SELECT singleton, schema_version FROM content_schema_meta"
    ).fetchall()
    if (
        len(rows) != 1
        or type(rows[0]["singleton"]) is not int
        or rows[0]["singleton"] != 1
        or type(rows[0]["schema_version"]) is not int
        or rows[0]["schema_version"] not in SUPPORTED_SCHEMA_VERSIONS
    ):
        raise ValidationError("CRE schema metadata row is missing or unrecognized.")
    return rows[0]["schema_version"]


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _foreign_key_relationships(
    connection: sqlite3.Connection,
) -> list[dict[str, Any]]:
    table_rows = connection.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    relationships: list[dict[str, Any]] = []
    for table_row in table_rows:
        child_table = table_row["name"]
        foreign_keys = connection.execute(
            f"PRAGMA foreign_key_list({_quote_identifier(child_table)})"
        ).fetchall()
        groups: dict[int, list[sqlite3.Row]] = {}
        for foreign_key in foreign_keys:
            groups.setdefault(foreign_key["id"], []).append(foreign_key)
        for key_id, rows in sorted(groups.items()):
            ordered = sorted(rows, key=lambda row: row["seq"])
            parent_table = ordered[0]["table"]
            child_columns = [row["from"] for row in ordered]
            parent_columns = [row["to"] for row in ordered]
            if any(column is None for column in parent_columns):
                primary_keys = {
                    row["pk"]: row["name"]
                    for row in connection.execute(
                        f"PRAGMA table_info({_quote_identifier(parent_table)})"
                    )
                    if row["pk"]
                }
                parent_columns = [
                    primary_keys.get(index + 1)
                    for index in range(len(parent_columns))
                ]
            if any(column is None for column in child_columns + parent_columns):
                raise ValidationError(
                    f"Cannot resolve foreign-key columns for {child_table}."
                )

            child_ref = _quote_identifier(child_table)
            parent_ref = _quote_identifier(parent_table)
            applicable = " AND ".join(
                f"c.{_quote_identifier(column)} IS NOT NULL"
                for column in child_columns
            )
            matching = " AND ".join(
                f"p.{_quote_identifier(parent_column)} = "
                f"c.{_quote_identifier(child_column)}"
                for child_column, parent_column in zip(
                    child_columns,
                    parent_columns,
                    strict=True,
                )
            )
            row = connection.execute(
                f"""
                SELECT
                    COUNT(*) AS child_count,
                    COALESCE(SUM(CASE WHEN {applicable} THEN 1 ELSE 0 END), 0)
                        AS applicable_count,
                    COALESCE(SUM(CASE WHEN {applicable} AND EXISTS (
                        SELECT 1 FROM {parent_ref} p WHERE {matching}
                    ) THEN 1 ELSE 0 END), 0) AS matched_count
                FROM {child_ref} c
                """
            ).fetchone()
            relationships.append(
                {
                    "childTable": child_table,
                    "parentTable": parent_table,
                    "childColumns": child_columns,
                    "parentColumns": parent_columns,
                    "childRows": row["child_count"],
                    "applicableRows": row["applicable_count"],
                    "matchedRows": row["matched_count"],
                    "orphanRows": row["applicable_count"] - row["matched_count"],
                    "foreignKeyId": key_id,
                }
            )
    return sorted(
        relationships,
        key=lambda item: (
            item["childTable"],
            item["foreignKeyId"],
            item["parentTable"],
        ),
    )


def _table_counts(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    tables = connection.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    )
    return [
        {
            "table": row["name"],
            "rows": connection.execute(
                f"SELECT COUNT(*) FROM {_quote_identifier(row['name'])}"
            ).fetchone()[0],
        }
        for row in tables
    ]


def _integrity_and_foreign_key_checks(
    connection: sqlite3.Connection,
) -> dict[str, Any]:
    integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
    integrity_ok = len(integrity_rows) == 1 and integrity_rows[0][0] == "ok"
    foreign_key_violations = sum(
        1 for _ in connection.execute("PRAGMA foreign_key_check")
    )
    return {
        "integrityCheck": "ok" if integrity_ok else "failed",
        "integrityFailureCount": 0 if integrity_ok else len(integrity_rows),
        "foreignKeyViolationCount": foreign_key_violations,
    }


def inspect_cre_fixture(database: str | Path) -> dict[str, Any]:
    """Return a record-free aggregate baseline for an explicit temporary fixture."""
    path = _safe_fixture_path(database)
    connection = _open_read_only(path)
    try:
        version = _read_schema_version(connection)
        expected_manifest = _expected_manifest(version)
        actual = _schema_snapshot(connection)
        schema_result = _compare_schema(actual, expected_manifest)
        table_counts = _table_counts(connection)
        relationships = _foreign_key_relationships(connection)
        checks = _integrity_and_foreign_key_checks(connection)
        status = (
            "PASS"
            if schema_result["matches"]
            and checks["integrityCheck"] == "ok"
            and checks["foreignKeyViolationCount"] == 0
            and all(item["orphanRows"] == 0 for item in relationships)
            else "FAIL"
        )
        return {
            "status": status,
            "scope": "temporary-fixture-only; read-only; aggregate data only",
            "creSchemaVersion": version,
            "sqliteUserVersion": connection.execute("PRAGMA user_version").fetchone()[0],
            "schemaObjects": {
                **schema_result,
                "expectedManifest": expected_manifest,
            },
            "tableCounts": table_counts,
            "relationships": relationships,
            "checks": checks,
        }
    except sqlite3.Error as error:
        raise ValidationError("Fixture schema or aggregate checks could not be read.") from error
    finally:
        connection.close()


def validate_recovery_candidate(
    database: str | Path,
    *,
    expected_schema_version: int,
    expected_user_version: int = 0,
) -> dict[str, Any]:
    """Read-only checks for a separate temporary recovery candidate, never activation."""
    if expected_schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValidationError("Expected CRE schema version must be 7, 9, or 10.")
    if isinstance(expected_user_version, bool) or not isinstance(expected_user_version, int):
        raise ValidationError("Expected SQLite user_version must be an integer.")

    path = _safe_fixture_path(database)
    connection = _open_read_only(path)
    try:
        actual_schema_version = _read_schema_version(connection)
        actual_user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        checks = _integrity_and_foreign_key_checks(connection)
        schema_matches = actual_schema_version == expected_schema_version
        user_version_matches = actual_user_version == expected_user_version
        checks_pass = (
            checks["integrityCheck"] == "ok"
            and checks["foreignKeyViolationCount"] == 0
        )
        return {
            "status": (
                "PASS"
                if schema_matches and user_version_matches and checks_pass
                else "FAIL"
            ),
            "scope": "temporary-recovery-candidate-only; read-only; never activated",
            "creSchemaVersion": actual_schema_version,
            "expectedCreSchemaVersion": expected_schema_version,
            "sqliteUserVersion": actual_user_version,
            "expectedSqliteUserVersion": expected_user_version,
            "checks": checks,
            "runtimeV10PreflightUsed": False,
            "note": (
                "A schema-v7 recovery candidate is checked against expected "
                "v7 metadata, not the runtime-v10 preflight."
                if expected_schema_version == 7
                else "This validates metadata and SQLite integrity only; it does not activate the candidate."
            ),
        }
    except sqlite3.Error as error:
        raise ValidationError("Recovery candidate checks could not be read.") from error
    finally:
        connection.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only CRE schema validator for existing temporary test/fixture "
            "databases. Production paths are rejected."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)
    baseline = commands.add_parser(
        "baseline",
        help="emit sorted schema manifest and aggregate table/relationship checks",
    )
    baseline.add_argument("--database", required=True, help="absolute temporary fixture database")
    recovery = commands.add_parser(
        "recovery",
        help="validate metadata/integrity of a separate temporary recovery fixture",
    )
    recovery.add_argument("--database", required=True, help="absolute temporary candidate database")
    recovery.add_argument(
        "--expected-cre-schema-version",
        required=True,
        type=int,
        choices=SUPPORTED_SCHEMA_VERSIONS,
    )
    recovery.add_argument(
        "--expected-user-version",
        type=int,
        default=0,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "baseline":
            result = inspect_cre_fixture(arguments.database)
        else:
            result = validate_recovery_candidate(
                arguments.database,
                expected_schema_version=arguments.expected_cre_schema_version,
                expected_user_version=arguments.expected_user_version,
            )
    except ValidationError as error:
        print(json.dumps({"status": "ERROR", "error": str(error)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
