from __future__ import annotations

import json
import sqlite3
import threading
import time

import pytest

from scripts.ultron_service_manager import ServiceManager, StartupFailure
from ultron.application.content.maintenance import (
    CreMaintenanceControl,
    MaintenanceControlError,
    MaintenanceHoldActive,
)
from ultron.application.content.store import ContentStore


def _control(tmp_path, *, name="cre.sqlite"):
    return CreMaintenanceControl(tmp_path / name)


def test_request_blocks_new_writers_until_explicit_release(tmp_path):
    control = _control(tmp_path)
    requested = control.request_hold(operation_id="hold-1", actor="operator")

    assert requested["status"] == "REQUESTED"
    with pytest.raises(MaintenanceHoldActive, match="REQUESTED"):
        with control.writer_lease():
            pytest.fail("writer entered during a requested hold")

    active = control.activate_hold(
        operation_id="hold-1",
        actor="operator",
        timeout_seconds=0.1,
    )
    assert active["status"] == "ACTIVE"
    with pytest.raises(MaintenanceHoldActive, match="ACTIVE"):
        with control.writer_lease():
            pytest.fail("writer entered during an active hold")

    control.release_hold(operation_id="hold-1", actor="operator")
    assert control.status()["status"] == "OPEN"
    with control.writer_lease():
        pass


def test_hold_activation_waits_for_all_concurrent_writer_leases(tmp_path):
    control = _control(tmp_path)
    first_lease = control.writer_lease()
    second_lease = control.writer_lease()
    first_lease.__enter__()
    second_lease.__enter__()
    control.request_hold(operation_id="drain-1", actor="operator")
    completed = threading.Event()
    result = []

    def activate():
        result.append(
            control.activate_hold(
                operation_id="drain-1",
                actor="operator",
                timeout_seconds=2,
            )
        )
        completed.set()

    thread = threading.Thread(target=activate)
    thread.start()
    time.sleep(0.1)
    assert not completed.is_set()
    with pytest.raises(MaintenanceHoldActive):
        with control.writer_lease():
            pass

    first_lease.__exit__(None, None, None)
    time.sleep(0.1)
    assert not completed.is_set()
    second_lease.__exit__(None, None, None)
    thread.join(timeout=3)

    assert completed.is_set()
    assert result[0]["status"] == "ACTIVE"
    assert json.loads(control.audit_path.read_text().splitlines()[-1])["action"] == "HOLD_ACTIVATED"


def test_failed_drain_keeps_request_fail_closed_then_recovers(tmp_path):
    control = _control(tmp_path)
    lease = control.writer_lease()
    lease.__enter__()
    control.request_hold(operation_id="timeout-1", actor="operator")

    with pytest.raises(MaintenanceControlError, match="remains REQUESTED"):
        control.activate_hold(
            operation_id="timeout-1",
            actor="operator",
            timeout_seconds=0.05,
        )
    assert control.status()["status"] == "REQUESTED"
    with pytest.raises(MaintenanceHoldActive):
        with control.writer_lease():
            pass

    lease.__exit__(None, None, None)
    assert control.activate_hold(
        operation_id="timeout-1",
        actor="operator",
        timeout_seconds=0.2,
    )["status"] == "ACTIVE"


def test_content_store_lease_drains_on_close(tmp_path):
    database = tmp_path / "cre.sqlite"
    store = ContentStore(database, recover_interrupted_jobs=False)
    control = CreMaintenanceControl(database)
    control.request_hold(operation_id="store-1", actor="operator")

    with pytest.raises(MaintenanceControlError, match="remains REQUESTED"):
        control.activate_hold(
            operation_id="store-1",
            actor="operator",
            timeout_seconds=0.05,
        )
    store.close()
    assert control.activate_hold(
        operation_id="store-1",
        actor="operator",
        timeout_seconds=0.2,
    )["status"] == "ACTIVE"


def test_maintenance_hold_prevents_content_store_database_creation(tmp_path):
    database = tmp_path / "new" / "cre.sqlite"
    control = CreMaintenanceControl(database)
    control.request_hold(operation_id="no-create-1", actor="operator")
    control.activate_hold(
        operation_id="no-create-1",
        actor="operator",
        timeout_seconds=0.1,
    )

    with pytest.raises(MaintenanceHoldActive, match="ACTIVE"):
        ContentStore(database, recover_interrupted_jobs=False)

    assert not database.exists()


def test_failed_content_store_initialization_releases_writer_lease(tmp_path):
    database = tmp_path / "malformed.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")

    with pytest.raises(RuntimeError, match="metadata is missing"):
        ContentStore(database, recover_interrupted_jobs=False)

    control = CreMaintenanceControl(database)
    control.request_hold(operation_id="failed-init-1", actor="operator")
    assert control.activate_hold(
        operation_id="failed-init-1",
        actor="operator",
        timeout_seconds=0.2,
    )["status"] == "ACTIVE"


def test_service_manager_start_and_restart_are_blocked_by_durable_hold(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    database = root / "data" / "ultron_cre.db"
    control = CreMaintenanceControl(database)
    control.request_hold(operation_id="manager-1", actor="operator")
    control.activate_hold(
        operation_id="manager-1",
        actor="operator",
        timeout_seconds=0.1,
    )
    manager = ServiceManager(
        root,
        state_directory=tmp_path / "manager-state",
        validate_project=False,
    )
    gateway = next(service for service in manager.services if service.name == "gateway")

    with pytest.raises(StartupFailure, match="maintenance hold"):
        manager._ensure_cre_writer_admission(gateway)
    with pytest.raises(StartupFailure, match="maintenance hold"):
        manager.start_all()
    with pytest.raises(StartupFailure, match="maintenance hold"):
        manager._start_service(gateway, {"services": {}})
    assert manager._processes == {}


def test_service_manager_restart_is_refused_before_state_change_during_hold(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "project"
    root.mkdir()
    database = root / "data" / "ultron_cre.db"
    control = CreMaintenanceControl(database)
    control.request_hold(operation_id="restart-hold-1", actor="operator")
    control.activate_hold(
        operation_id="restart-hold-1",
        actor="operator",
        timeout_seconds=0.1,
    )
    manager = ServiceManager(
        root,
        state_directory=tmp_path / "manager-state",
        validate_project=False,
    )
    monkeypatch.setattr(
        manager,
        "_lock_state",
        lambda: pytest.fail("restart must check CRE admission before changing manager state"),
    )

    result = manager._restart_gateway("restart-during-hold")

    assert result["success"] is False
    assert result["status"] == "CRE_ADMISSION_BLOCKED"
    assert result["gatewayStatus"] == "NOT_CHANGED"
    assert "ACTIVE" in result["detail"]
    assert manager._processes == {}


def test_requested_hold_survives_manager_recreation_and_recovery(tmp_path):
    database = tmp_path / "cre.sqlite"
    requester = CreMaintenanceControl(database)
    requester.request_hold(operation_id="interrupt-1", actor="operator")

    recovered = CreMaintenanceControl(database)
    assert recovered.status()["status"] == "REQUESTED"
    assert recovered.activate_hold(
        operation_id="interrupt-1",
        actor="recovery-operator",
        timeout_seconds=0.1,
    )["status"] == "ACTIVE"
    recovered.release_hold(
        operation_id="interrupt-1",
        actor="recovery-operator",
    )
    assert CreMaintenanceControl(database).status()["status"] == "OPEN"


def test_active_hold_survives_controller_recreation_until_explicit_release(tmp_path):
    database = tmp_path / "cre.sqlite"
    control = CreMaintenanceControl(database)
    control.request_hold(operation_id="active-crash-1", actor="operator")
    control.activate_hold(
        operation_id="active-crash-1",
        actor="operator",
        timeout_seconds=0.1,
    )

    recovered = CreMaintenanceControl(database)
    assert recovered.status()["status"] == "ACTIVE"
    with pytest.raises(MaintenanceHoldActive, match="ACTIVE"):
        with recovered.writer_lease():
            pytest.fail("writer entered after active hold recovery")

    recovered.release_hold(operation_id="active-crash-1", actor="recovery-operator")
    assert CreMaintenanceControl(database).status()["status"] == "OPEN"


def test_hold_release_requires_matching_operation_and_active_state(tmp_path):
    control = _control(tmp_path)
    control.request_hold(operation_id="release-1", actor="operator")

    with pytest.raises(MaintenanceControlError, match="Only an ACTIVE"):
        control.release_hold(operation_id="release-1", actor="operator")
    with pytest.raises(MaintenanceControlError, match="does not match"):
        control.activate_hold(
            operation_id="wrong-id",
            actor="operator",
            timeout_seconds=0.1,
        )

    control.activate_hold(
        operation_id="release-1",
        actor="operator",
        timeout_seconds=0.1,
    )
    with pytest.raises(MaintenanceControlError, match="does not match"):
        control.release_hold(operation_id="wrong-id", actor="operator")
    control.release_hold(operation_id="release-1", actor="operator")
    actions = [json.loads(line)["action"] for line in control.audit_path.read_text().splitlines()]
    assert actions == [
        "HOLD_REQUESTED",
        "HOLD_ACTIVATED",
        "HOLD_RELEASE_REQUESTED",
        "HOLD_RELEASED",
    ]
