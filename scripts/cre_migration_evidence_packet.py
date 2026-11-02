"""Assemble operator-supplied CRE migration evidence without operating systems."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


MAX_EVIDENCE_BYTES = 1_048_576
MAX_EVIDENCE_ENTRIES = 100
MAX_EVIDENCE_AGE = timedelta(hours=24)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
EVIDENCE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}$")
EVIDENCE_FILE_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,199}\.json$")
CLASSIFICATIONS = {
    "PRODUCTION_CURRENT",
    "OPERATOR_ATTESTATION",
    "HISTORICAL_REHEARSAL",
    "TEST_FIXTURE",
    "OTHER",
}
REQUIRED_CATEGORIES = (
    "cre_schema_metadata",
    "schema_baseline",
    "writer_inventory",
    "process_identity",
    "open_descriptors",
    "restart_sources",
    "maintenance_hold",
    "backup_output_approval",
    "backup_destination_approval",
    "backup_verification",
    "migration_v7_to_v9_approval",
    "migration_v9_to_v10_approval",
    "rollback_approval",
    "service_control_approval",
    "external_writer_control_approval",
    "cutover_approval",
)
APPROVAL_CATEGORIES = {
    "backup_output_approval",
    "backup_destination_approval",
    "migration_v7_to_v9_approval",
    "migration_v9_to_v10_approval",
    "rollback_approval",
    "service_control_approval",
    "external_writer_control_approval",
    "cutover_approval",
}
APPROVAL_SCOPES = {
    "backup_output_approval": "backup-output-creation",
    "backup_destination_approval": "backup-output-destination",
    "migration_v7_to_v9_approval": "cre-v7-to-v9-migration",
    "migration_v9_to_v10_approval": "cre-v9-to-v10-migration",
    "rollback_approval": "restore-to-new-recovery-candidate",
    "service_control_approval": "gateway-service-control",
    "external_writer_control_approval": "external-writer-service-control",
    "cutover_approval": "gateway-cutover",
}
EVIDENCE_CATEGORIES = set(REQUIRED_CATEGORIES)
EVIDENCE_TIMESTAMP_FIELDS = (
    "verifiedAt",
    "capturedAt",
    "generatedAt",
    "recordedAt",
    "createdAt",
    "timestamp",
)


class PacketError(ValueError):
    """Raised when the input manifest or output target is unsafe or malformed."""


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PacketError("Duplicate JSON object key.")
        result[key] = value
    return result


def _read_bounded_regular_file(path: Path, *, label: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PacketError(f"{label} is unavailable or cannot be opened safely.") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise PacketError(f"{label} must be a regular file.")
        if metadata.st_size > MAX_EVIDENCE_BYTES:
            raise PacketError(f"{label} exceeds the 1 MiB size limit.")
        chunks: list[bytes] = []
        total = 0
        while total <= MAX_EVIDENCE_BYTES:
            chunk = os.read(descriptor, min(64 * 1024, MAX_EVIDENCE_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > MAX_EVIDENCE_BYTES:
            raise PacketError(f"{label} exceeds the 1 MiB size limit.")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _loads_unique_json(raw: bytes, *, label: str) -> Any:
    try:
        return json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise PacketError(f"{label} is not valid UTF-8 JSON.") from error


def _timestamp_from_document(
    category: str,
    document: dict[str, Any],
) -> tuple[str | None, str | None]:
    fields = (
        ("recordedAt",) + tuple(
            field for field in EVIDENCE_TIMESTAMP_FIELDS if field != "recordedAt"
        )
        if category in APPROVAL_CATEGORIES
        else EVIDENCE_TIMESTAMP_FIELDS
    )
    for field in fields:
        if field in document:
            value = document[field]
            return (value if isinstance(value, str) else None, field)
    return None, None


def _safe_enum(value: Any, allowed: set[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def _safe_int(value: Any) -> int | None:
    return value if type(value) is int else None


def _timestamp_for_report(value: Any) -> str | None:
    parsed = _parse_timestamp(value)
    return parsed.isoformat(timespec="seconds") if parsed is not None else None


def _summary(category: str, document: dict[str, Any]) -> dict[str, Any]:
    if category == "cre_schema_metadata":
        return {
            "creSchemaVersion": _safe_int(document.get("creSchemaVersion")),
            "sqliteUserVersion": _safe_int(document.get("sqliteUserVersion")),
            "assessment": _safe_enum(
                document.get("assessment", document.get("status")),
                {"PASS", "VERIFIED", "FAIL", "NOT VERIFIED", "INCOMPLETE"},
            ),
        }
    if category == "schema_baseline":
        table_counts = document.get("tableCounts")
        relationships = document.get("relationships")
        schema_objects = document.get("schemaObjects")
        checks = document.get("checks")
        table_rows = (
            [
                row.get("rows")
                for row in table_counts
                if isinstance(row, dict) and type(row.get("rows")) is int
            ]
            if isinstance(table_counts, list)
            else []
        )
        orphan_counts = (
            [
                row.get("orphanRows")
                for row in relationships
                if isinstance(row, dict) and type(row.get("orphanRows")) is int
            ]
            if isinstance(relationships, list)
            else []
        )
        return {
            "creSchemaVersion": _safe_int(document.get("creSchemaVersion")),
            "sqliteUserVersion": _safe_int(document.get("sqliteUserVersion")),
            "assessment": _safe_enum(
                document.get("assessment", document.get("status")),
                {"PASS", "VERIFIED", "FAIL", "NOT VERIFIED", "INCOMPLETE"},
            ),
            "schemaObjectsMatch": (
                schema_objects.get("matches")
                if isinstance(schema_objects, dict)
                and schema_objects.get("matches") is True
                else False
                if isinstance(schema_objects, dict)
                and schema_objects.get("matches") is False
                else None
            ),
            "tableCountEntries": len(table_counts) if isinstance(table_counts, list) else None,
            "aggregateRows": sum(table_rows)
            if isinstance(table_counts, list) and len(table_rows) == len(table_counts)
            else None,
            "relationshipEntries": len(relationships) if isinstance(relationships, list) else None,
            "aggregateOrphanRows": sum(orphan_counts)
            if isinstance(relationships, list)
            and len(orphan_counts) == len(relationships)
            else None,
            "integrityCheck": (
                _safe_enum(checks.get("integrityCheck"), {"ok", "failed"})
                if isinstance(checks, dict)
                else None
            ),
            "foreignKeyViolationCount": (
                _safe_int(checks.get("foreignKeyViolationCount"))
                if isinstance(checks, dict)
                else None
            ),
            "schemaObjectDiff": {
                key: len(schema_objects[key])
                if isinstance(schema_objects, dict)
                and isinstance(schema_objects.get(key), list)
                else None
                for key in ("missing", "unexpected", "definitionMismatches")
            },
        }
    if category == "writer_inventory":
        return {
            "assessment": _safe_enum(
                document.get("assessment", document.get("overallStatus")),
                {"PASS", "VERIFIED", "FAIL", "NOT VERIFIED", "INCOMPLETE"},
            ),
            "quiescenceStatus": _safe_enum(
                document.get("quiescenceStatus"),
                {"VERIFIED", "QUIESCED", "NOT VERIFIED", "UNKNOWN"},
            ),
            "snapshotOnly": (
                document.get("processSnapshot", {}).get("snapshotOnly")
                if isinstance(document.get("processSnapshot"), dict)
                and type(document.get("processSnapshot", {}).get("snapshotOnly")) is bool
                else None
            ),
            "writerCount": (
                len(document["writers"])
                if isinstance(document.get("writers"), list)
                else None
            ),
            "inventoryComplete": document.get("inventoryComplete") is True,
            "databaseTargetProvided": isinstance(document.get("databaseTarget"), str),
            "knownEntryPointCount": (
                len(document.get("knownWriterInventory", {}).get("knownEntryPoints", []))
                if isinstance(document.get("knownWriterInventory"), dict)
                and isinstance(
                    document.get("knownWriterInventory", {}).get("knownEntryPoints"),
                    list,
                )
                else None
            ),
        }
    if category == "process_identity":
        return {
            "assessment": _safe_enum(
                document.get(
                    "assessment",
                    document.get("managerState", {}).get("status")
                    if isinstance(document.get("managerState"), dict)
                    else None,
                ),
                {"PASS", "VERIFIED", "FAIL", "NOT VERIFIED", "INCOMPLETE", "CHECKED"},
            ),
            "processCount": (
                len(document["processes"])
                if isinstance(document.get("processes"), list)
                else len(document.get("managerState", {}).get("services", []))
                if isinstance(document.get("managerState"), dict)
                and isinstance(document.get("managerState", {}).get("services"), list)
                else None
            ),
            "verifiedIdentityCount": sum(
                1
                for process in document.get("processes", [])
                if isinstance(process, dict)
                and process.get("identityStatus") == "VERIFIED"
                and process.get("writerState") == "STOPPED"
            )
            if isinstance(document.get("processes"), list)
            else None,
        }
    if category == "open_descriptors":
        return {
            "assessment": _safe_enum(
                document.get(
                    "assessment",
                    document.get("processSnapshot", {}).get("status")
                    if isinstance(document.get("processSnapshot"), dict)
                    else None,
                ),
                {"PASS", "VERIFIED", "FAIL", "NOT VERIFIED", "INCOMPLETE", "CHECKED"},
            ),
            "matchingDescriptorCount": (
                len(document["matches"])
                if isinstance(document.get("matches"), list)
                else len(
                    document.get("processSnapshot", {}).get(
                        "databaseDescriptorMatches", []
                    )
                )
                if isinstance(document.get("processSnapshot"), dict)
                and isinstance(
                    document.get("processSnapshot", {}).get(
                        "databaseDescriptorMatches"
                    ),
                    list,
                )
                else None
            ),
            "databaseTargetProvided": isinstance(
                document.get("checkedDatabaseTarget"), str
            ),
            "requiredSidecarsChecked": (
                bool(
                    {"-wal", "-shm"}.issubset(set(document.get("checkedSidecars", [])))
                )
                if isinstance(document.get("checkedSidecars"), list)
                and all(
                    item in {"-wal", "-shm"}
                    for item in document.get("checkedSidecars", [])
                )
                else False
            ),
            "matches": (
                len(document["matches"])
                if isinstance(document.get("matches"), list)
                else None
            ),
        }
    if category == "restart_sources":
        return {
            "assessment": _safe_enum(
                document.get("assessment"),
                {"PASS", "VERIFIED", "FAIL", "NOT VERIFIED", "INCOMPLETE", "CHECKED"},
            ),
            "sourceCount": (
                len(document["sources"])
                if isinstance(document.get("sources"), list)
                else len(document.get("scheduledAndExternalSources", {}).get("sources", []))
                if isinstance(document.get("scheduledAndExternalSources"), dict)
                and isinstance(
                    document.get("scheduledAndExternalSources", {}).get("sources"),
                    list,
                )
                else None
            ),
            "sourceStatuses": [
                _safe_enum(item.get("restartStatus"), {"HELD", "DISABLED"})
                for item in document.get("sources", [])
                if isinstance(item, dict)
            ]
            if isinstance(document.get("sources"), list)
            else None,
        }
    if category == "maintenance_hold":
        return {
            "assessment": _safe_enum(
                document.get("assessment"),
                {"PASS", "VERIFIED", "FAIL", "NOT VERIFIED", "INCOMPLETE"},
            ),
            "holdStart": _timestamp_for_report(document.get("holdStart")),
            "holdEnd": _timestamp_for_report(document.get("holdEnd")),
            "ongoingObservation": document.get("ongoingObservation") is True,
        }
    if category == "backup_verification":
        backup = document.get("backup")
        metadata = document.get("backupMetadata")
        return {
            "outcome": _safe_enum(document.get("outcome"), {"VERIFIED", "FAILED"}),
            "verificationMethodMatch": (
                document.get("verificationMethod")
                == "scripts/ultron_sqlite_backup.py verify"
            ),
            "backupFileProvided": (
                isinstance(backup.get("file"), str) if isinstance(backup, dict) else False
            ),
            "backupSize": _safe_int(backup.get("size")) if isinstance(backup, dict) else None,
            "backupSha256": (
                backup.get("sha256") if isinstance(backup, dict) else None
            ),
            "backupUserVersion": _safe_int(metadata.get("userVersion"))
            if isinstance(metadata, dict)
            else None,
            "integrity": (
                _safe_enum(metadata.get("integrity"), {"ok", "failed"})
                if isinstance(metadata, dict)
                else None
            ),
            "sourceUserVersion": _safe_int(
                document.get("sourceMetadata", {}).get("userVersion")
            )
            if isinstance(document.get("sourceMetadata"), dict)
            else None,
        }
    if category in APPROVAL_CATEGORIES:
        return {
            "decisionClaim": _safe_enum(
                document.get("decision"),
                {"APPROVED", "DENIED", "PENDING"},
            ),
            "scopeMatchesRequiredAction": (
                document.get("scope") == APPROVAL_SCOPES[category]
            ),
            "recordedAt": _timestamp_for_report(document.get("recordedAt")),
            "destinationProvided": isinstance(document.get("destination"), str)
            if category == "backup_destination_approval"
            else False,
            "privacyRequirementsProvided": isinstance(document.get("privacyRequirements"), str)
            if category == "backup_destination_approval"
            else False,
            "retentionPeriodProvided": isinstance(document.get("retentionPeriod"), str)
            if category == "backup_destination_approval"
            else False,
        }
    return {}


def _validate_document(category: str, document: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if category in {
        "cre_schema_metadata",
        "schema_baseline",
        "writer_inventory",
        "open_descriptors",
    } and (
        not isinstance(document.get("databaseId"), str)
        or not EVIDENCE_ID_PATTERN.fullmatch(document["databaseId"])
    ):
        issues.append("Stable databaseId is missing or malformed.")
    if category == "cre_schema_metadata":
        if type(document.get("creSchemaVersion")) is not int:
            issues.append("CRE schema version is missing or not an integer.")
        if type(document.get("sqliteUserVersion")) is not int:
            issues.append("SQLite user_version is missing or not an integer.")
        if document.get("creSchemaVersion") != 7:
            issues.append("Pre-migration production CRE schema evidence must report v7.")
        if document.get("sqliteUserVersion") != 0:
            issues.append("Pre-migration SQLite user_version evidence must report 0.")
        if document.get("assessment", document.get("status")) not in ("PASS", "VERIFIED"):
            issues.append("CRE schema metadata evidence is not explicitly passing.")
    elif category == "schema_baseline":
        if document.get("creSchemaVersion") != 7:
            issues.append("Pre-migration baseline must report CRE schema v7.")
        if document.get("sqliteUserVersion") != 0:
            issues.append("Pre-migration baseline must report SQLite user_version 0.")
        if document.get("assessment", document.get("status")) not in ("PASS", "VERIFIED"):
            issues.append("Schema baseline is not explicitly passing.")
        schema_objects = document.get("schemaObjects")
        checks = document.get("checks")
        if not isinstance(schema_objects, dict) or schema_objects.get("matches") is not True:
            issues.append("Exact schema-object comparison is missing or failed.")
        if not isinstance(document.get("tableCounts"), list):
            issues.append("Aggregate table-count evidence is missing.")
        if not isinstance(document.get("relationships"), list):
            issues.append("Aggregate relationship evidence is missing.")
        if (
            not isinstance(checks, dict)
            or checks.get("integrityCheck") != "ok"
            or checks.get("foreignKeyViolationCount") != 0
        ):
            issues.append("Baseline integrity or foreign-key checks are missing or failed.")
    elif category == "writer_inventory":
        writers = document.get("writers")
        writer_ids = [
            writer.get("writerId")
            for writer in writers
            if isinstance(writer, dict) and isinstance(writer.get("writerId"), str)
        ] if isinstance(writers, list) else []
        if document.get("assessment") != "VERIFIED":
            issues.append("Writer inventory lacks an explicit VERIFIED operator assessment.")
        if document.get("overallStatus") == "NOT VERIFIED":
            issues.append("A point-in-time collector snapshot cannot prove writer quiescence.")
        if document.get("quiescenceStatus") not in ("VERIFIED", "QUIESCED"):
            issues.append("Writer quiescence is not explicitly evidenced.")
        if not isinstance(writers, list) or not writers or any(
            not isinstance(writer, dict)
            or not isinstance(writer.get("writerId"), str)
            or not isinstance(writer.get("identityEvidenceRef"), str)
            or writer.get("state") != "STOPPED"
            for writer in (writers if isinstance(writers, list) else [])
        ) or document.get("inventoryComplete") is not True:
            issues.append("Complete stopped-writer inventory with identity evidence is missing.")
        if len(writer_ids) != len(set(writer_ids)):
            issues.append("Writer inventory contains duplicate writer IDs.")
    elif category in {"process_identity", "open_descriptors", "restart_sources"}:
        if document.get("assessment") != "VERIFIED":
            issues.append(f"{category.replace('_', ' ').capitalize()} evidence is not explicitly verified.")
        if category == "process_identity":
            processes = document.get("processes")
            if not isinstance(processes, list) or not processes or any(
                not isinstance(process, dict)
                or type(process.get("pid")) is not int
                or type(process.get("startTicks")) is not int
                or type(process.get("processGroup")) is not int
                or not isinstance(process.get("executable"), str)
                or not isinstance(process.get("workingDirectory"), str)
                or not isinstance(process.get("writerId"), str)
                or process.get("identityStatus") != "VERIFIED"
                or process.get("writerState") != "STOPPED"
                for process in (processes if isinstance(processes, list) else [])
            ):
                issues.append("Verified process identities and stopped states are missing.")
        elif category == "open_descriptors":
            if (
                not isinstance(document.get("checkedDatabaseTarget"), str)
                or document.get("matches") != []
                or not {"-wal", "-shm"}.issubset(
                    set(document.get("checkedSidecars", []))
                    if isinstance(document.get("checkedSidecars"), list)
                    else set()
                )
            ):
                issues.append(
                    "Targeted database/WAL/SHM open-descriptor check is missing, failed, or found a descriptor."
                )
        else:
            sources = document.get("sources")
            if not isinstance(sources, list) or not sources or any(
                not isinstance(source, dict)
                or not isinstance(source.get("sourceId"), str)
                or not isinstance(source.get("evidenceRef"), str)
                or source.get("restartStatus") not in ("HELD", "DISABLED")
                for source in (sources if isinstance(sources, list) else [])
            ):
                issues.append("Restart-source inventory or verified maintenance holds are missing.")
    elif category == "maintenance_hold":
        if document.get("assessment") != "VERIFIED":
            issues.append("Maintenance hold is not explicitly verified.")
        if document.get("ongoingObservation") is not True:
            issues.append("Ongoing observation for the entire maintenance window is not evidenced.")
        if _parse_timestamp(document.get("holdStart")) is None:
            issues.append("Maintenance-hold start timestamp is missing or invalid.")
        if _parse_timestamp(document.get("holdEnd")) is None:
            issues.append("Maintenance-hold end timestamp is missing or invalid.")
    elif category == "backup_destination_approval":
        if (
            document.get("decision") != "APPROVED"
            or document.get("scope") != APPROVAL_SCOPES[category]
            or not isinstance(document.get("destination"), str)
            or not isinstance(document.get("privacyRequirements"), str)
            or not isinstance(document.get("retentionPeriod"), str)
        ):
            issues.append(
                "Backup destination approval, privacy requirements, or retention period is missing."
            )
    elif category == "backup_verification":
        backup = document.get("backup")
        metadata = document.get("backupMetadata")
        if (
            document.get("outcome") != "VERIFIED"
            or not isinstance(document.get("verifiedAt"), str)
            or document.get("verificationMethod")
            != "scripts/ultron_sqlite_backup.py verify"
            or not isinstance(backup, dict)
            or not isinstance(backup.get("file"), str)
            or type(backup.get("size")) is not int
            or not isinstance(backup.get("sha256"), str)
            or not SHA256_PATTERN.fullmatch(backup["sha256"])
            or not isinstance(metadata, dict)
            or metadata.get("integrity") != "ok"
            or metadata.get("userVersion") != 0
            or not isinstance(document.get("sourceMetadata"), dict)
            or document.get("sourceMetadata", {}).get("userVersion") != 0
            or not isinstance(document.get("sourceDatabaseId"), str)
            or not EVIDENCE_ID_PATTERN.fullmatch(document["sourceDatabaseId"])
        ):
            issues.append(
                "Backup verification manifest is missing, incomplete, or has unexpected SQLite user_version."
            )
    elif category in APPROVAL_CATEGORIES:
        if document.get("decision") != "APPROVED":
            issues.append("Separate approval evidence does not explicitly record APPROVED.")
        if document.get("scope") != APPROVAL_SCOPES[category]:
            issues.append("Approval scope does not match the required operation.")
    return issues


def _add_finding(
    findings: list[dict[str, str]],
    *,
    severity: str,
    category: str,
    reason: str,
) -> None:
    finding = {"severity": severity, "category": category, "reason": reason}
    if finding not in findings:
        findings.append(finding)


def build_packet(
    manifest_path: str | Path,
    output_path: str | Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    supplied_manifest = Path(manifest_path).expanduser()
    if not supplied_manifest.is_absolute():
        raise PacketError("Evidence manifest path must be absolute.")
    try:
        manifest = supplied_manifest.resolve(strict=True)
    except OSError as error:
        raise PacketError("Evidence manifest is unavailable.") from error
    output = Path(output_path).expanduser()
    if not output.is_absolute():
        raise PacketError("Packet output path must be absolute.")
    if not EVIDENCE_FILE_PATTERN.fullmatch(output.name):
        raise PacketError("Packet output must use a simple .json filename.")
    if output.exists() or output.is_symlink():
        raise PacketError("Packet output must be a new, non-existing file.")
    try:
        output_parent = output.parent.resolve(strict=True)
    except OSError as error:
        raise PacketError("Packet output directory must already exist.") from error
    output = output_parent / output.name
    if output == manifest:
        raise PacketError("Packet output must not overwrite the evidence manifest.")
    try:
        raw_manifest = _read_bounded_regular_file(
            supplied_manifest,
            label="Evidence manifest",
        )
        manifest_data = _loads_unique_json(raw_manifest, label="Evidence manifest")
    except OSError as error:
        raise PacketError("Evidence manifest is unavailable or invalid JSON.") from error
    if (
        not isinstance(manifest_data, dict)
        or manifest_data.get("format") != 1
        or not isinstance(manifest_data.get("evidence"), list)
    ):
        raise PacketError("Evidence manifest must use format 1 and an evidence list.")
    if len(manifest_data["evidence"]) > MAX_EVIDENCE_ENTRIES:
        raise PacketError("Evidence manifest exceeds the 100-entry limit.")

    generated_at = now or datetime.now(UTC)
    if generated_at.tzinfo is None:
        raise PacketError("Packet generation time must be timezone-aware.")
    generated_at = generated_at.astimezone(UTC)
    evidence_records: list[dict[str, Any]] = []
    findings: list[dict[str, str]] = []
    categories_seen: dict[str, int] = {}
    ids_seen: set[str] = set()
    files_seen: set[str] = set()
    evidence_id_categories: dict[str, str] = {}
    evidence_documents: dict[str, dict[str, Any]] = {}
    evidence_references: list[tuple[str, str, str, str | None]] = []
    database_observations: list[tuple[str, str]] = []
    version_observations: dict[str, list[tuple[str, int]]] = {
        "creSchemaVersion": [],
        "sqliteUserVersion": [],
    }

    for entry in manifest_data["evidence"]:
        if not isinstance(entry, dict):
            _add_finding(
                findings,
                severity="BLOCKED",
                category="manifest",
                reason="Evidence entry is not an object.",
            )
            continue
        category = entry.get("category")
        classification = entry.get("classification")
        expected_hash = entry.get("sha256")
        file_name = entry.get("file")
        evidence_id = entry.get("id")
        if not isinstance(category, str) or category not in EVIDENCE_CATEGORIES:
            _add_finding(
                findings,
                severity="BLOCKED",
                category="manifest",
                reason="Unknown or unsupported evidence category.",
            )
            continue
        categories_seen[category] = categories_seen.get(category, 0) + 1
        if (
            not isinstance(evidence_id, str)
            or not EVIDENCE_ID_PATTERN.fullmatch(evidence_id)
        ):
            _add_finding(
                findings,
                severity="BLOCKED",
                category=category,
                reason="Evidence ID is missing or malformed.",
            )
        elif evidence_id in ids_seen:
            _add_finding(
                findings,
                severity="BLOCKED",
                category=category,
                reason="Duplicate evidence ID.",
            )
        else:
            ids_seen.add(evidence_id)
            evidence_id_categories[evidence_id] = category
        record: dict[str, Any] = {
            "category": category,
            "fileName": file_name
            if isinstance(file_name, str)
            and EVIDENCE_FILE_PATTERN.fullmatch(file_name)
            else None,
            "classification": classification
            if isinstance(classification, str) and classification in CLASSIFICATIONS
            else None,
            "classificationValidation": "NOT_PERFORMED",
            "expectedSha256": expected_hash
            if isinstance(expected_hash, str)
            and SHA256_PATTERN.fullmatch(expected_hash)
            else None,
            "sha256": None,
            "timestamp": None,
            "timestampField": None,
            "freshness": "UNVERIFIED",
            "assessment": "UNVERIFIED",
            "structuralValidation": "NOT_PERFORMED",
            "independentVerification": "NOT_PERFORMED",
            "additionalTimestampChecks": {},
            "operatorApprovalStatus": (
                "SUPPLIED_UNVERIFIED" if category in APPROVAL_CATEGORIES else "NOT_APPLICABLE"
            ),
            "summary": {},
            "reasons": [],
        }
        try:
            if not isinstance(evidence_id, str) or not EVIDENCE_ID_PATTERN.fullmatch(evidence_id):
                raise PacketError("Evidence ID is missing or malformed.")
            if not isinstance(file_name, str) or not file_name:
                raise PacketError("Evidence file name is missing.")
            if not EVIDENCE_FILE_PATTERN.fullmatch(file_name):
                raise PacketError("Evidence filename must be a simple basename ending in .json.")
            if file_name in files_seen:
                _add_finding(
                    findings,
                    severity="BLOCKED",
                    category=category,
                    reason="Evidence file is reused by multiple manifest entries.",
                )
                raise PacketError("Evidence file is reused.")
            files_seen.add(file_name)
            if not isinstance(classification, str) or classification not in CLASSIFICATIONS:
                raise PacketError("Evidence classification is missing or unsupported.")
            if not isinstance(expected_hash, str) or not SHA256_PATTERN.fullmatch(expected_hash):
                raise PacketError("A pinned SHA-256 digest is required in the manifest.")
            supplied_path = manifest.parent / file_name
            evidence_bytes = _read_bounded_regular_file(
                supplied_path,
                label="Evidence file",
            )
            actual_hash = hashlib.sha256(evidence_bytes).hexdigest()
            record["sha256"] = actual_hash
            if actual_hash != expected_hash:
                record["reasons"].append("Evidence SHA-256 does not match the pinned manifest digest.")
                record["assessment"] = "TAMPERED"
                _add_finding(
                    findings,
                    severity="BLOCKED",
                    category=category,
                    reason="Evidence file hash mismatch.",
                )
                evidence_records.append(record)
                continue
            document = _loads_unique_json(evidence_bytes, label="Evidence file")
            if not isinstance(document, dict):
                raise PacketError("Evidence JSON root must be an object.")
            timestamp_value, timestamp_field = _timestamp_from_document(
                category,
                document,
            )
            timestamp = _parse_timestamp(timestamp_value)
            record["timestamp"] = (
                timestamp.isoformat(timespec="seconds")
                if timestamp is not None
                else None
            )
            record["timestampField"] = timestamp_field
            record["summary"] = _summary(category, document)

            if classification in {"HISTORICAL_REHEARSAL", "TEST_FIXTURE", "OTHER"}:
                record["reasons"].append(
                    "This classification cannot satisfy current production evidence."
                )
                record["assessment"] = "UNVERIFIED"
                _add_finding(
                    findings,
                    severity="BLOCKED",
                    category=category,
                    reason=f"{classification.lower()} evidence is not current production evidence.",
                )
            elif category in APPROVAL_CATEGORIES and classification != "OPERATOR_ATTESTATION":
                record["reasons"].append(
                    "Approval evidence must be explicitly classified as OPERATOR_ATTESTATION."
                )
                record["assessment"] = "UNVERIFIED"
                _add_finding(
                    findings,
                    severity="BLOCKED",
                    category=category,
                    reason="Approval evidence classification is not OPERATOR_ATTESTATION.",
                )
            elif category in {
                "writer_inventory",
                "process_identity",
                "open_descriptors",
                "restart_sources",
                "maintenance_hold",
            } and classification != "OPERATOR_ATTESTATION":
                record["reasons"].append(
                    "Host-operational evidence must be classified as OPERATOR_ATTESTATION."
                )
                record["assessment"] = "UNVERIFIED"
                _add_finding(
                    findings,
                    severity="BLOCKED",
                    category=category,
                    reason="Host-operational evidence classification is not OPERATOR_ATTESTATION.",
                )
            elif classification == "OPERATOR_ATTESTATION" and category not in APPROVAL_CATEGORIES | {
                "writer_inventory",
                "process_identity",
                "open_descriptors",
                "restart_sources",
                "maintenance_hold",
            }:
                record["reasons"].append(
                    "Operator attestations are not accepted as database/schema measurement evidence."
                )
                record["assessment"] = "UNVERIFIED"
                _add_finding(
                    findings,
                    severity="BLOCKED",
                    category=category,
                    reason="Operator attestation cannot substitute for measured database evidence.",
                )

            expected_classification = (
                "OPERATOR_ATTESTATION"
                if category in APPROVAL_CATEGORIES
                or category
                in {
                    "writer_inventory",
                    "process_identity",
                    "open_descriptors",
                    "restart_sources",
                    "maintenance_hold",
                }
                else "PRODUCTION_CURRENT"
            )
            if classification == expected_classification:
                record["classificationValidation"] = "PASS"
            else:
                record["classificationValidation"] = "FAIL"

            if timestamp is None:
                record["freshness"] = "UNKNOWN"
                record["reasons"].append("Evidence has no valid timezone-aware timestamp.")
                _add_finding(
                    findings,
                    severity="INCOMPLETE",
                    category=category,
                    reason="Evidence timestamp is missing or invalid.",
                )
            elif timestamp > generated_at:
                record["freshness"] = "INVALID_FUTURE"
                record["reasons"].append("Evidence timestamp is in the future.")
                _add_finding(
                    findings,
                    severity="BLOCKED",
                    category=category,
                    reason="Future-dated evidence is not accepted.",
                )
            elif generated_at - timestamp > MAX_EVIDENCE_AGE:
                record["freshness"] = "STALE"
                record["reasons"].append("Evidence is older than the 24-hour freshness window.")
                _add_finding(
                    findings,
                    severity="BLOCKED",
                    category=category,
                    reason="Evidence is stale.",
                )
            else:
                record["freshness"] = "FRESH"

            if category == "backup_verification":
                created_at_value = document.get("createdAt")
                created_at = _parse_timestamp(created_at_value)
                created_at_freshness = "FRESH"
                if created_at is None:
                    created_at_freshness = "UNKNOWN"
                    _add_finding(
                        findings,
                        severity="INCOMPLETE",
                        category=category,
                        reason="Backup creation timestamp is missing or invalid.",
                    )
                elif created_at > generated_at:
                    created_at_freshness = "INVALID_FUTURE"
                    _add_finding(
                        findings,
                        severity="BLOCKED",
                        category=category,
                        reason="Backup creation timestamp is in the future.",
                    )
                elif generated_at - created_at > MAX_EVIDENCE_AGE:
                    created_at_freshness = "STALE"
                    _add_finding(
                        findings,
                        severity="BLOCKED",
                        category=category,
                        reason="Backup creation timestamp is stale.",
                    )
                record["additionalTimestampChecks"] = {
                    "createdAt": {
                        "timestamp": _timestamp_for_report(created_at_value),
                        "freshness": created_at_freshness,
                    }
                }

            validation_issues = _validate_document(category, document)
            if classification != expected_classification:
                validation_issues.append(
                    "Evidence classification does not match the category requirements."
                )
            database_id = (
                document.get("sourceDatabaseId")
                if category == "backup_verification"
                else document.get("databaseId")
            )
            if isinstance(database_id, str):
                database_observations.append((category, database_id))
            if category == "writer_inventory":
                for writer in document.get("writers", []):
                    if isinstance(writer, dict) and isinstance(
                        writer.get("identityEvidenceRef"), str
                    ):
                        evidence_references.append(
                            (
                                category,
                                writer["identityEvidenceRef"],
                                "process_identity",
                                writer.get("writerId"),
                            )
                        )
            if category == "restart_sources":
                for source in document.get("sources", []):
                    if isinstance(source, dict) and isinstance(
                        source.get("evidenceRef"), str
                    ):
                        evidence_references.append(
                            (
                                category,
                                source["evidenceRef"],
                                "maintenance_hold",
                                None,
                            )
                        )
            if isinstance(evidence_id, str) and EVIDENCE_ID_PATTERN.fullmatch(evidence_id):
                evidence_documents[evidence_id] = document
            if category == "maintenance_hold":
                hold_start = _parse_timestamp(document.get("holdStart"))
                hold_end = _parse_timestamp(document.get("holdEnd"))
                if (
                    hold_start is not None
                    and hold_end is not None
                    and not hold_start <= generated_at <= hold_end
                ):
                    validation_issues.append(
                        "Maintenance hold does not cover packet generation time."
                    )
            record["reasons"].extend(validation_issues)
            record["structuralValidation"] = (
                "PASS" if not validation_issues else "FAIL"
            )
            for key in version_observations:
                value = document.get(key)
                if type(value) is int:
                    version_observations[key].append((category, value))
            if validation_issues:
                record["assessment"] = "UNVERIFIED"
                for issue in validation_issues:
                    _add_finding(
                        findings,
                        severity="BLOCKED",
                        category=category,
                        reason=issue,
                    )
            else:
                record["assessment"] = "STRUCTURALLY_VALIDATED"
                record["reasons"].append(
                    "Evidence content and provenance were not independently verified."
                )
                _add_finding(
                    findings,
                    severity="INCOMPLETE",
                    category=category,
                    reason="Supplied evidence has not been independently verified.",
                )
                if category in APPROVAL_CATEGORIES:
                    record["operatorApprovalStatus"] = "SUPPLIED_UNVERIFIED"
                    _add_finding(
                        findings,
                        severity="INCOMPLETE",
                        category=category,
                        reason="Approval is a supplied assertion; approval validity was not authenticated.",
                    )
            evidence_records.append(record)
        except (OSError, PacketError, json.JSONDecodeError) as error:
            reason = str(error) if isinstance(error, PacketError) else "Evidence file is unavailable or invalid JSON."
            record["reasons"].append(reason)
            record["assessment"] = "UNVERIFIED"
            _add_finding(
                findings,
                severity="INCOMPLETE",
                category=category,
                reason=reason,
            )
            evidence_records.append(record)

    for category in REQUIRED_CATEGORIES:
        count = categories_seen.get(category, 0)
        if count == 0:
            _add_finding(
                findings,
                severity="INCOMPLETE",
                category=category,
                reason="Required evidence is missing.",
            )
        elif count > 1:
            _add_finding(
                findings,
                severity="BLOCKED",
                category=category,
                reason="Multiple records for one required category are ambiguous.",
            )

    for version_key, observations in version_observations.items():
        distinct_values = sorted({value for _, value in observations})
        if len(distinct_values) > 1:
            _add_finding(
                findings,
                severity="BLOCKED",
                category="version_consistency",
                reason=(
                    f"Contradictory {version_key} values across evidence categories: "
                    + ", ".join(f"{category}={value}" for category, value in observations)
                    + "."
                ),
            )

    if len({value for _, value in database_observations}) > 1:
        _add_finding(
            findings,
            severity="BLOCKED",
            category="database_identity",
            reason="Schema, operational, and backup evidence refer to different database IDs.",
        )
    for source_category, reference, required_category, writer_id in evidence_references:
        if evidence_id_categories.get(reference) != required_category:
            _add_finding(
                findings,
                severity="BLOCKED",
                category=source_category,
                reason=f"Evidence reference must identify a {required_category} entry.",
            )
        elif source_category == "writer_inventory":
            identity_document = evidence_documents.get(reference, {})
            processes = identity_document.get("processes")
            matching_processes = [
                process
                for process in processes
                if isinstance(process, dict)
                and process.get("writerId") == writer_id
                and process.get("writerState") == "STOPPED"
            ] if isinstance(processes, list) else []
            if not isinstance(writer_id, str) or len(matching_processes) != 1:
                _add_finding(
                    findings,
                    severity="BLOCKED",
                    category=source_category,
                    reason=(
                        "Writer identity reference does not resolve to exactly one "
                        "stopped process identity for that writer."
                    ),
                )

    severity_order = {"BLOCKED": 0, "INCOMPLETE": 1}
    _add_finding(
        findings,
        severity="INCOMPLETE",
        category="independent_verification",
        reason=(
            "This tool has no trusted verifier for evidence provenance, host-state "
            "claims, backup contents, or approval identity."
        ),
    )
    findings.sort(
        key=lambda item: (
            severity_order.get(item["severity"], 2),
            item["category"],
            item["reason"],
        )
    )
    if any(item["severity"] == "BLOCKED" for item in findings):
        state = "BLOCKED"
    elif findings:
        state = "INCOMPLETE"
    else:
        state = "READY_FOR_OPERATOR_REVIEW"

    packet = {
        "format": 1,
        "generatedAt": generated_at.isoformat(timespec="seconds"),
        "state": state,
        "evidenceState": {
            "suppliedCount": len(evidence_records),
            "structurallyValidatedCount": sum(
                record["structuralValidation"] == "PASS"
                for record in evidence_records
            ),
            "independentlyVerifiedCount": 0,
        },
        "operatorApprovals": {
            category: (
                "SUPPLIED_UNVERIFIED"
                if categories_seen.get(category, 0)
                else "MISSING"
            )
            for category in sorted(APPROVAL_CATEGORIES)
        },
        "executionAuthorization": "NOT_GRANTED",
        "operatorReviewOnly": True,
        "executionPerformed": False,
        "requiredPreMigrationVersions": {
            "creSchemaVersion": 7,
            "sqliteUserVersion": 0,
            "versionFieldsAreDistinct": True,
        },
        "freshnessPolicy": {
            "maximumAgeHours": 24,
            "futureClockSkewMinutes": 0,
            "appliesTo": "all supplied evidence",
        },
        "evidence": sorted(
            evidence_records,
            key=lambda item: (item["category"], item["fileName"] or ""),
        ),
        "findings": findings,
        "missingCategories": [
            category
            for category in REQUIRED_CATEGORIES
            if categories_seen.get(category, 0) == 0
        ],
        "limitations": [
            "This packet indexes and summarizes explicitly supplied JSON only; it does not inspect a database, validate a backup file, authenticate signers, or independently establish host state.",
            "A pinned digest detects a mismatch against the supplied manifest but does not prove who created or approved that manifest.",
            "Historical rehearsal and fixture evidence are never accepted as current production evidence.",
            "A writer collector snapshot is point-in-time evidence and cannot establish continuous quiescence or a no-restart hold.",
            "READY_FOR_OPERATOR_REVIEW is not execution permission, migration approval, rollback approval, service-control approval, or cutover approval.",
        ],
    }
    encoded = (json.dumps(packet, sort_keys=True, indent=2) + "\n").encode("utf-8")
    try:
        descriptor = os.open(
            output,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
    except OSError as error:
        raise PacketError("Packet output could not be created as a new file.") from error
    return packet


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assemble a review-only CRE migration evidence packet."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        packet = build_packet(args.manifest, args.output)
    except PacketError as error:
        parser.error(str(error))
    print(
        json.dumps(
            {
                "output": args.output.name,
                "state": packet["state"],
                "findingCount": len(packet["findings"]),
            },
            sort_keys=True,
        )
    )
    return 0 if packet["state"] == "READY_FOR_OPERATOR_REVIEW" else 2


if __name__ == "__main__":
    sys.exit(main())
