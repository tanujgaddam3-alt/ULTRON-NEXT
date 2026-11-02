from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts import cre_migration_evidence_packet as packet_builder
from scripts.cre_migration_evidence_packet import (
    REQUIRED_CATEGORIES,
    build_packet,
)


NOW = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
CURRENT = NOW.isoformat()


def _document(category: str, *, timestamp: str = CURRENT) -> dict:
    document = {"capturedAt": timestamp}
    if category == "cre_schema_metadata":
        document.update(
            databaseId="production-cre-db",
            creSchemaVersion=7,
            sqliteUserVersion=0,
            assessment="VERIFIED",
        )
    elif category == "schema_baseline":
        document.update(
            databaseId="production-cre-db",
            status="PASS",
            creSchemaVersion=7,
            sqliteUserVersion=0,
            schemaObjects={"matches": True},
            tableCounts=[{"table": "fixture_table", "rows": 0}],
            relationships=[],
            checks={
                "integrityCheck": "ok",
                "foreignKeyViolationCount": 0,
            },
        )
    elif category == "writer_inventory":
        document.update(
            databaseId="production-cre-db",
            assessment="VERIFIED",
            quiescenceStatus="VERIFIED",
            inventoryComplete=True,
            databaseTarget="approved-database-reference",
            writers=[
                {
                    "writerId": "known-writer",
                    "identityEvidenceRef": "process_identity",
                    "state": "STOPPED",
                }
            ],
        )
    elif category == "process_identity":
        document.update(
            assessment="VERIFIED",
            processes=[
                {
                    "writerId": "known-writer",
                    "pid": 101,
                    "startTicks": 202,
                    "processGroup": 101,
                    "executable": "/verified/interpreter",
                    "workingDirectory": "/verified/project",
                    "identityStatus": "VERIFIED",
                    "writerState": "STOPPED",
                }
            ],
        )
    elif category == "open_descriptors":
        document.update(
            databaseId="production-cre-db",
            assessment="VERIFIED",
            checkedDatabaseTarget="approved-database-reference",
            checkedSidecars=["-wal", "-shm"],
            matches=[],
        )
    elif category == "restart_sources":
        document.update(
            assessment="VERIFIED",
            sources=[
                {
                    "sourceId": "verified-source",
                    "evidenceRef": "maintenance_hold",
                    "restartStatus": "HELD",
                }
            ],
        )
    elif category == "maintenance_hold":
        document.update(
            assessment="VERIFIED",
            holdStart=(NOW - timedelta(minutes=5)).isoformat(),
            holdEnd=(NOW + timedelta(hours=2)).isoformat(),
            ongoingObservation=True,
        )
    elif category == "backup_destination_approval":
        document.update(
            decision="APPROVED",
            scope="backup-output-destination",
            privacyRequirements="restricted operator access",
            retentionPeriod="approved retention schedule",
        )
        document["destination"] = "operator-reference"
    elif category == "backup_verification":
        document.update(
            createdAt=timestamp,
            verifiedAt=timestamp,
            verificationMethod="scripts/ultron_sqlite_backup.py verify",
            outcome="VERIFIED",
            backup={
                "file": "backup.sqlite",
                "size": 128,
                "sha256": "a" * 64,
            },
            backupMetadata={"integrity": "ok", "userVersion": 0},
            sourceMetadata={"userVersion": 0},
            sourceDatabaseId="production-cre-db",
        )
    elif category in {
        "backup_output_approval",
        "migration_v7_to_v9_approval",
        "migration_v9_to_v10_approval",
        "rollback_approval",
        "service_control_approval",
        "external_writer_control_approval",
        "cutover_approval",
    }:
        document.update(
            recordedAt=timestamp,
            decision="APPROVED",
            scope={
                "backup_output_approval": "backup-output-creation",
                "migration_v7_to_v9_approval": "cre-v7-to-v9-migration",
                "migration_v9_to_v10_approval": "cre-v9-to-v10-migration",
                "rollback_approval": "restore-to-new-recovery-candidate",
                "service_control_approval": "gateway-service-control",
                "external_writer_control_approval": "external-writer-service-control",
                "cutover_approval": "gateway-cutover",
            }[category],
        )
    return document


def _write_evidence_set(
    directory: Path,
    *,
    omit: set[str] | None = None,
    classifications: dict[str, str] | None = None,
    documents: dict[str, dict] | None = None,
) -> Path:
    omitted = omit or set()
    classifications = classifications or {}
    documents = documents or {}
    evidence = []
    for category in REQUIRED_CATEGORIES:
        if category in omitted:
            continue
        document = documents.get(category, _document(category))
        file_name = f"{category}.json"
        content = json.dumps(document, sort_keys=True).encode()
        (directory / file_name).write_bytes(content)
        evidence.append(
            {
                "id": category,
                "category": category,
                "file": file_name,
                "classification": classifications.get(
                    category,
                    "OPERATOR_ATTESTATION"
                    if category in {
                        "backup_output_approval",
                        "backup_destination_approval",
                        "migration_v7_to_v9_approval",
                        "migration_v9_to_v10_approval",
                        "rollback_approval",
                        "service_control_approval",
                        "external_writer_control_approval",
                        "cutover_approval",
                        "writer_inventory",
                        "process_identity",
                        "open_descriptors",
                        "restart_sources",
                        "maintenance_hold",
                    }
                    else "PRODUCTION_CURRENT",
                ),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    manifest = directory / "evidence-index.json"
    manifest.write_text(
        json.dumps({"format": 1, "evidence": evidence}),
        encoding="utf-8",
    )
    return manifest


def _assemble(tmp_path: Path, manifest: Path | None = None):
    output = tmp_path / "packet.json"
    packet = build_packet(manifest or _write_evidence_set(tmp_path), output, now=NOW)
    return packet, output


def test_missing_evidence_and_approvals_are_incomplete(tmp_path):
    manifest = _write_evidence_set(
        tmp_path,
        omit={
            "migration_v7_to_v9_approval",
            "migration_v9_to_v10_approval",
            "cutover_approval",
            "schema_baseline",
        },
    )

    packet, output = _assemble(tmp_path, manifest)

    assert packet["state"] == "INCOMPLETE"
    assert packet["missingCategories"] == [
        "schema_baseline",
        "migration_v7_to_v9_approval",
        "migration_v9_to_v10_approval",
        "cutover_approval",
    ]
    assert output.is_file()
    assert packet["executionPerformed"] is False
    assert packet["operatorReviewOnly"] is True


def test_absent_separate_approvals_are_not_inferred(tmp_path):
    approval_categories = {
        "backup_output_approval",
        "backup_destination_approval",
        "migration_v7_to_v9_approval",
        "migration_v9_to_v10_approval",
        "rollback_approval",
        "service_control_approval",
        "external_writer_control_approval",
        "cutover_approval",
    }
    manifest = _write_evidence_set(tmp_path, omit=approval_categories)

    packet, _ = _assemble(tmp_path, manifest)

    assert packet["state"] == "INCOMPLETE"
    assert set(packet["missingCategories"]) == approval_categories


@pytest.mark.parametrize(
    ("category", "wrong_scope"),
    [
        ("backup_output_approval", "backup-output-destination"),
        ("backup_destination_approval", "backup-output-creation"),
        ("migration_v7_to_v9_approval", "cre-v9-to-v10-migration"),
        ("migration_v9_to_v10_approval", "cre-v7-to-v9-migration"),
        ("rollback_approval", "gateway-cutover"),
        ("service_control_approval", "external-writer-service-control"),
        ("external_writer_control_approval", "gateway-service-control"),
        ("cutover_approval", "gateway-service-control"),
    ],
)
def test_approval_scope_must_match_exact_action(tmp_path, category, wrong_scope):
    document = _document(category)
    document["scope"] = wrong_scope
    manifest = _write_evidence_set(tmp_path, documents={category: document})

    packet, _ = _assemble(tmp_path, manifest)

    assert packet["state"] == "BLOCKED"
    evidence = next(item for item in packet["evidence"] if item["category"] == category)
    assert evidence["structuralValidation"] == "FAIL"
    assert evidence["operatorApprovalStatus"] == "SUPPLIED_UNVERIFIED"
    assert packet["executionAuthorization"] == "NOT_GRANTED"


def test_legacy_broad_migration_approval_cannot_satisfy_either_stage(tmp_path):
    manifest = _write_evidence_set(
        tmp_path,
        omit={"migration_v7_to_v9_approval", "migration_v9_to_v10_approval"},
    )
    index = json.loads(manifest.read_text(encoding="utf-8"))
    legacy = _document("migration_v7_to_v9_approval")
    legacy["scope"] = "cre-v7-to-v10-migration"
    legacy_path = tmp_path / "legacy-migration-approval.json"
    legacy_bytes = json.dumps(legacy, sort_keys=True).encode()
    legacy_path.write_bytes(legacy_bytes)
    index["evidence"].append(
        {
            "id": "legacy-migration-approval",
            "category": "migration_approval",
            "file": legacy_path.name,
            "classification": "OPERATOR_ATTESTATION",
            "sha256": hashlib.sha256(legacy_bytes).hexdigest(),
        }
    )
    manifest.write_text(json.dumps(index), encoding="utf-8")

    packet, _ = _assemble(tmp_path, manifest)

    assert packet["state"] == "BLOCKED"
    assert set(packet["missingCategories"]) >= {
        "migration_v7_to_v9_approval",
        "migration_v9_to_v10_approval",
    }
    assert any(
        finding["category"] == "manifest"
        and finding["reason"] == "Unknown or unsupported evidence category."
        and finding["severity"] == "BLOCKED"
        for finding in packet["findings"]
    )


def test_stale_evidence_blocks_packet(tmp_path):
    stale = (NOW - timedelta(hours=25)).isoformat()
    manifest = _write_evidence_set(
        tmp_path,
        documents={"cre_schema_metadata": _document("cre_schema_metadata", timestamp=stale)},
    )

    packet, _ = _assemble(tmp_path, manifest)

    stale_record = next(
        item for item in packet["evidence"]
        if item["category"] == "cre_schema_metadata"
    )
    assert packet["state"] == "BLOCKED"
    assert stale_record["freshness"] == "STALE"


def test_any_future_timestamp_blocks_packet(tmp_path):
    future = (NOW + timedelta(seconds=1)).isoformat()
    manifest = _write_evidence_set(
        tmp_path,
        documents={"cre_schema_metadata": _document("cre_schema_metadata", timestamp=future)},
    )

    packet, _ = _assemble(tmp_path, manifest)

    future_record = next(
        item for item in packet["evidence"]
        if item["category"] == "cre_schema_metadata"
    )
    assert packet["state"] == "BLOCKED"
    assert future_record["freshness"] == "INVALID_FUTURE"


def test_stale_backup_creation_time_blocks_even_when_verified_recently(tmp_path):
    backup = _document("backup_verification")
    backup["createdAt"] = (NOW - timedelta(hours=25)).isoformat()
    backup["verifiedAt"] = CURRENT
    manifest = _write_evidence_set(
        tmp_path,
        documents={"backup_verification": backup},
    )

    packet, _ = _assemble(tmp_path, manifest)

    backup_record = next(
        item for item in packet["evidence"]
        if item["category"] == "backup_verification"
    )
    assert packet["state"] == "BLOCKED"
    assert backup_record["freshness"] == "FRESH"
    assert backup_record["additionalTimestampChecks"]["createdAt"]["freshness"] == "STALE"


def test_timezone_naive_timestamp_is_unverified(tmp_path):
    manifest = _write_evidence_set(
        tmp_path,
        documents={
            "cre_schema_metadata": _document(
                "cre_schema_metadata",
                timestamp="2026-11-01T10:00:00",
            )
        },
    )

    packet, _ = _assemble(tmp_path, manifest)

    metadata = next(
        item for item in packet["evidence"]
        if item["category"] == "cre_schema_metadata"
    )
    assert packet["state"] == "INCOMPLETE"
    assert metadata["freshness"] == "UNKNOWN"


def test_contradictory_cre_and_sqlite_versions_are_reported_separately(tmp_path):
    baseline = _document("schema_baseline")
    baseline["creSchemaVersion"] = 9
    baseline["sqliteUserVersion"] = 2
    manifest = _write_evidence_set(
        tmp_path,
        documents={"schema_baseline": baseline},
    )

    packet, _ = _assemble(tmp_path, manifest)

    assert packet["state"] == "BLOCKED"
    assert packet["requiredPreMigrationVersions"] == {
        "creSchemaVersion": 7,
        "sqliteUserVersion": 0,
        "versionFieldsAreDistinct": True,
    }
    assert any(item["category"] == "version_consistency" for item in packet["findings"])


def test_contradictory_database_identity_is_blocked(tmp_path):
    baseline = _document("schema_baseline")
    baseline["databaseId"] = "different-db"
    manifest = _write_evidence_set(
        tmp_path,
        documents={"schema_baseline": baseline},
    )

    packet, _ = _assemble(tmp_path, manifest)

    assert packet["state"] == "BLOCKED"
    assert any(item["category"] == "database_identity" for item in packet["findings"])


def test_writer_identity_reference_must_match_the_writer(tmp_path):
    process_identity = _document("process_identity")
    process_identity["processes"][0]["writerId"] = "different-writer"
    manifest = _write_evidence_set(
        tmp_path,
        documents={"process_identity": process_identity},
    )

    packet, _ = _assemble(tmp_path, manifest)

    assert packet["state"] == "BLOCKED"
    assert any(
        "exactly one stopped process identity" in item["reason"]
        for item in packet["findings"]
    )


def test_fixture_and_historical_evidence_never_satisfy_current_evidence(tmp_path):
    manifest = _write_evidence_set(
        tmp_path,
        classifications={
            "schema_baseline": "TEST_FIXTURE",
            "writer_inventory": "HISTORICAL_REHEARSAL",
        },
        documents={
            "writer_inventory": {
                "generatedAt": CURRENT,
                "overallStatus": "NOT VERIFIED",
                "quiescenceStatus": "NOT VERIFIED",
                "processSnapshot": {"snapshotOnly": True},
            }
        },
    )

    packet, _ = _assemble(tmp_path, manifest)

    assert packet["state"] == "BLOCKED"
    assert any(
        "fixture" in finding["reason"] or "historical" in finding["reason"]
        for finding in packet["findings"]
    )
    assert any(
        "point-in-time" in finding["reason"]
        for finding in packet["findings"]
    )


def test_tampered_evidence_hash_is_detected(tmp_path):
    manifest = _write_evidence_set(tmp_path)
    entry = json.loads(manifest.read_text())
    target = next(item for item in entry["evidence"] if item["category"] == "cre_schema_metadata")
    target_path = tmp_path / target["file"]
    target_path.write_text(
        json.dumps(_document("cre_schema_metadata") | {"creSchemaVersion": 10}),
        encoding="utf-8",
    )
    manifest.write_text(json.dumps(entry), encoding="utf-8")

    packet, _ = _assemble(tmp_path, manifest)

    assert packet["state"] == "BLOCKED"
    record = next(
        item for item in packet["evidence"]
        if item["category"] == "cre_schema_metadata"
    )
    assert record["assessment"] == "TAMPERED"
    assert any("hash mismatch" in item["reason"] for item in packet["findings"])


def test_duplicate_evidence_ids_are_blocked(tmp_path):
    manifest = _write_evidence_set(tmp_path)
    index = json.loads(manifest.read_text())
    index["evidence"][1]["id"] = index["evidence"][0]["id"]
    manifest.write_text(json.dumps(index), encoding="utf-8")

    packet, _ = _assemble(tmp_path, manifest)

    assert packet["state"] == "BLOCKED"
    assert any("Duplicate evidence ID" in item["reason"] for item in packet["findings"])


def test_duplicate_json_keys_are_rejected(tmp_path):
    manifest = _write_evidence_set(tmp_path)
    index = json.loads(manifest.read_text())
    metadata_entry = next(
        item for item in index["evidence"]
        if item["category"] == "cre_schema_metadata"
    )
    (tmp_path / metadata_entry["file"]).write_text(
        '{"capturedAt":"2026-11-01T10:00:00+00:00",'
        '"creSchemaVersion":7,"creSchemaVersion":10,'
        '"sqliteUserVersion":0,"assessment":"VERIFIED"}',
        encoding="utf-8",
    )
    metadata_entry["sha256"] = hashlib.sha256(
        (tmp_path / metadata_entry["file"]).read_bytes()
    ).hexdigest()
    manifest.write_text(json.dumps(index), encoding="utf-8")

    packet, _ = _assemble(tmp_path, manifest)

    assert packet["state"] == "INCOMPLETE"
    assert any(
        "Duplicate JSON object key" in item["reason"]
        for item in packet["findings"]
    )


def test_evidence_paths_must_be_safe_basenames_and_symlinks_are_rejected(tmp_path):
    manifest = _write_evidence_set(tmp_path)
    index = json.loads(manifest.read_text())
    metadata_entry = next(
        item for item in index["evidence"]
        if item["category"] == "cre_schema_metadata"
    )
    metadata_entry["file"] = "../outside.json"
    manifest.write_text(json.dumps(index), encoding="utf-8")

    packet, _ = _assemble(tmp_path, manifest)

    assert packet["state"] == "INCOMPLETE"
    assert any(
        "simple basename" in item["reason"]
        for item in packet["findings"]
    )


def test_symlink_evidence_is_not_opened(tmp_path):
    manifest = _write_evidence_set(tmp_path)
    index = json.loads(manifest.read_text())
    metadata_entry = next(
        item for item in index["evidence"]
        if item["category"] == "cre_schema_metadata"
    )
    target = tmp_path / "real-metadata.json"
    (tmp_path / metadata_entry["file"]).rename(target)
    (tmp_path / metadata_entry["file"]).symlink_to(target)
    metadata_entry["file"] = "cre_schema_metadata.json"
    manifest.write_text(json.dumps(index), encoding="utf-8")

    packet, _ = _assemble(tmp_path, manifest)

    assert packet["state"] == "INCOMPLETE"
    assert any(
        item["category"] == "cre_schema_metadata"
        and "cannot be opened safely" in item["reason"]
        for item in packet["findings"]
    )


def test_packet_summarizes_exact_bytes_opened_under_path_replacement(tmp_path, monkeypatch):
    manifest = _write_evidence_set(tmp_path)
    index = json.loads(manifest.read_text())
    metadata_entry = next(
        item for item in index["evidence"]
        if item["category"] == "cre_schema_metadata"
    )
    evidence_path = tmp_path / metadata_entry["file"]
    original_open = os.open
    replaced = False

    def replace_after_open(path, flags, *args, **kwargs):
        nonlocal replaced
        descriptor = original_open(path, flags, *args, **kwargs)
        if Path(path) == evidence_path and not replaced:
            replacement = tmp_path / "replacement.json"
            replacement.write_text(
                json.dumps(_document("cre_schema_metadata") | {"creSchemaVersion": 10}),
                encoding="utf-8",
            )
            os.replace(replacement, evidence_path)
            replaced = True
        return descriptor

    monkeypatch.setattr(packet_builder.os, "open", replace_after_open)
    output = tmp_path / "packet.json"
    packet = build_packet(manifest, output, now=NOW)

    record = next(
        item for item in packet["evidence"]
        if item["category"] == "cre_schema_metadata"
    )
    assert replaced is True
    assert record["sha256"] == metadata_entry["sha256"]
    assert record["summary"]["creSchemaVersion"] == 7
    assert record["assessment"] == "STRUCTURALLY_VALIDATED"


def test_oversized_manifest_is_rejected_before_parse(tmp_path):
    manifest = tmp_path / "oversized.json"
    manifest.write_bytes(b" " * (packet_builder.MAX_EVIDENCE_BYTES + 1))

    with pytest.raises(ValueError, match="size limit"):
        build_packet(manifest, tmp_path / "packet.json", now=NOW)


def test_complete_structural_packet_remains_incomplete_without_verifier(tmp_path):
    document = _document("cre_schema_metadata") | {
        "privateNote": "sensitive-private-value",
    }
    manifest = _write_evidence_set(
        tmp_path,
        documents={
            "cre_schema_metadata": document,
            "process_identity": _document("process_identity")
            | {
                "processes": [
                    _document("process_identity")["processes"][0]
                    | {"writerId": "sensitive-person-name"}
                ]
            },
            "writer_inventory": _document("writer_inventory")
            | {
                "databaseTarget": "/home/person/private/credential-path",
                "writers": [
                    {
                        "writerId": "sensitive-person-name",
                        "identityEvidenceRef": "process_identity",
                        "state": "STOPPED",
                    }
                ],
            },
        },
    )
    packet, output = _assemble(tmp_path, manifest)

    assert packet["state"] == "INCOMPLETE"
    assert packet["operatorReviewOnly"] is True
    assert packet["executionPerformed"] is False
    assert packet["executionAuthorization"] == "NOT_GRANTED"
    assert packet["freshnessPolicy"]["futureClockSkewMinutes"] == 0
    assert packet["evidenceState"]["independentlyVerifiedCount"] == 0
    assert all(
        status == "SUPPLIED_UNVERIFIED"
        for status in packet["operatorApprovals"].values()
    )
    assert all(
        item["structuralValidation"] == "PASS"
        for item in packet["evidence"]
    )
    assert packet["requiredPreMigrationVersions"]["versionFieldsAreDistinct"] is True
    assert len(packet["evidence"]) == len(REQUIRED_CATEGORIES)
    assert all(item["sha256"] == item["expectedSha256"] for item in packet["evidence"])
    assert output.is_file()
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    packet_text = output.read_text(encoding="utf-8")
    assert "sensitive-private-value" not in packet_text
    assert "credential-path" not in packet_text
    assert "sensitive-person-name" not in packet_text
    approval_record = next(
        item for item in packet["evidence"]
        if item["category"] == "migration_v7_to_v9_approval"
    )
    assert approval_record["operatorApprovalStatus"] == "SUPPLIED_UNVERIFIED"
    assert approval_record["summary"]["decisionClaim"] == "APPROVED"
    assert (
        packet["operatorApprovals"]["migration_v7_to_v9_approval"]
        == "SUPPLIED_UNVERIFIED"
    )
    assert "executionAuthorization" in output.read_text(encoding="utf-8")


def test_packet_output_must_be_new_and_absolute(tmp_path):
    manifest = _write_evidence_set(tmp_path)

    with pytest.raises(ValueError, match="absolute"):
        build_packet(manifest, "relative-packet.json", now=NOW)

    output = tmp_path / "already-there.json"
    output.write_text("untouched", encoding="utf-8")
    with pytest.raises(ValueError, match="new"):
        build_packet(manifest, output, now=NOW)
    assert output.read_text(encoding="utf-8") == "untouched"
