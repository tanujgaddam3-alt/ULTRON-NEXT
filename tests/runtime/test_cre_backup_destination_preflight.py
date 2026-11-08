from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.cre_backup_destination_preflight import (
    PreflightError,
    build_preflight_report,
)


def _fixtures(tmp_path):
    database = tmp_path / "source.sqlite"
    database.write_bytes(b"database-fixture")
    content = tmp_path / "content"
    (content / "outputs").mkdir(parents=True)
    (content / "outputs" / "artifact-1").write_bytes(b"content-fixture")
    destination = tmp_path / "destination"
    destination.mkdir(mode=0o700)
    destination.chmod(0o700)
    return destination, database, content


def _mount(_path):
    return {
        "status": "MOUNTED",
        "target": "/tmp",
        "source": "tmpfs",
        "filesystemType": "tmpfs",
        "readWrite": True,
        "options": ["rw"],
        "encryption": {
            "status": "UNKNOWN",
            "evidence": "test fixture",
            "source": "test",
        },
    }


def _report(destination, database, content, **overrides):
    options = {
        "retention_days": 1,
        "backup_frequency_minutes": 60,
        "retention_count": 24,
        "minimum_free_bytes": 1,
        "mount_inspector": _mount,
    }
    options.update(overrides)
    return build_preflight_report(destination, database, content, **options)


def test_preflight_reports_capacity_mount_encryption_and_pending_approval(
    tmp_path,
    monkeypatch,
):
    destination, database, content = _fixtures(tmp_path)
    monkeypatch.setattr(
        "scripts.cre_backup_destination_preflight.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=4096, used=1024, free=4096),
    )

    report = _report(destination, database, content)

    assert report["outcome"] == "PASS"
    assert report["readOnly"] is True
    assert report["mount"]["status"] == "MOUNTED"
    assert report["mount"]["encryption"]["status"] == "UNKNOWN"
    assert report["ownershipAndPermissions"]["ownerOnly"] is True
    assert report["retentionFeasibility"]["requiredSnapshotCountForSchedule"] == 24
    assert report["retentionFeasibility"]["capacitySufficient"] is True
    assert report["policyApproval"]["status"] == "PENDING_OPERATOR_APPROVAL"
    assert report["policyApproval"]["template"]["rpoMinutes"] is None
    assert report["policyApproval"]["template"]["recoveryOwner"] is None
    assert list(destination.iterdir()) == []


def test_preflight_fails_for_insufficient_capacity_and_retention_count(
    tmp_path,
    monkeypatch,
):
    destination, database, content = _fixtures(tmp_path)
    monkeypatch.setattr(
        "scripts.cre_backup_destination_preflight.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=1000, used=990, free=10),
    )

    report = _report(
        destination,
        database,
        content,
        retention_days=2,
        backup_frequency_minutes=60,
        retention_count=24,
    )

    assert report["outcome"] == "FAIL"
    assert report["retentionFeasibility"]["requiredSnapshotCountForSchedule"] == 48
    assert report["retentionFeasibility"]["retentionCountCoversSchedule"] is False
    assert report["retentionFeasibility"]["capacitySufficient"] is False


@pytest.mark.parametrize("mode", [0o755, 0o750])
def test_preflight_fails_when_destination_permissions_are_not_private(tmp_path, mode):
    destination, database, content = _fixtures(tmp_path)
    destination.chmod(mode)

    report = _report(destination, database, content)

    assert report["outcome"] == "FAIL"
    assert report["ownershipAndPermissions"]["ownerOnly"] is False


def test_preflight_fails_when_mount_state_is_unavailable(tmp_path, monkeypatch):
    destination, database, content = _fixtures(tmp_path)
    monkeypatch.setattr(
        "scripts.cre_backup_destination_preflight.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=4096, used=1024, free=4096),
    )

    report = _report(
        destination,
        database,
        content,
        mount_inspector=lambda _path: {"status": "NOT_INSPECTED"},
    )

    assert report["outcome"] == "FAIL"
    assert report["mount"]["status"] == "NOT_INSPECTED"


def test_preflight_rejects_destination_inside_managed_content(tmp_path):
    database = tmp_path / "source.sqlite"
    database.write_bytes(b"database-fixture")
    content = tmp_path / "content"
    content.mkdir()
    destination = content / "backup"
    destination.mkdir(mode=0o700)
    destination.chmod(0o700)

    with pytest.raises(PreflightError, match="separate"):
        _report(destination, database, content)
