from __future__ import annotations

import hashlib
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from scripts.ultron_maintenance_journal import JournalError, MaintenanceJournal


def _free_ports(count: int) -> list[int]:
    sockets = [socket.socket() for _ in range(count)]
    try:
        for item in sockets:
            item.bind(("127.0.0.1", 0))
        return [item.getsockname()[1] for item in sockets]
    finally:
        for item in sockets:
            item.close()


def _manager_command(root: Path, state: Path, ports: list[int]) -> list[str]:
    script = f"""
import json, os, socket, sys
from pathlib import Path
sys.path.insert(0, {str(SCRIPTS)!r})
from ultron_service_manager import Service, ServiceManager, StartupFailure
root = Path({str(root)!r})
state = Path({str(state)!r})
names = ("gateway", "frontend", "auth", "voice")
ports = {ports!r}
services = []
for name, port in zip(names, ports):
    marker = "maintenance-fixture-" + name
    code = (
        "import socket,sys; "
        "s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); "
        "s.bind(('127.0.0.1',int(sys.argv[1]))); s.listen(); "
        "exec('while True:\\\\n c,a=s.accept()\\\\n c.close()')"
    )
    command = (sys.executable, "-u", "-c", code, str(port), marker)
    def probe(port=port, name=name):
        if os.environ.get("FAIL_MAINTENANCE_SERVICE") == name:
            return None
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.2):
                return "fixture-ready-" + name
        except OSError:
            return None
    services.append(Service(
        name, "127.0.0.1", port, command, root, probe,
        lambda args, marker=marker: marker in args,
    ))
manager = ServiceManager(
    root, state_directory=state, services=services,
    readiness_timeout=.35, poll_interval=.02, validate_project=False,
)
try:
    manager.acquire_manager_lease()
    result = manager.start_all()
    manager.start_control_channel()
    print("READY " + json.dumps({{"statuses": result, "pids": manager.owned_processes()}}), flush=True)
    manager.supervise()
except StartupFailure as exc:
    print("STARTUP_FAILED " + exc.service + " " + str(exc), flush=True)
    raise SystemExit(1)
finally:
    manager.close_control_channel()
    manager.release_manager_lease()
"""
    return [sys.executable, "-u", "-c", script]


def _read_ready(process: subprocess.Popen, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if line.startswith("READY "):
            return json.loads(line[6:])
        if line.startswith("STARTUP_FAILED "):
            raise AssertionError(line)
        if process.poll() is not None:
            raise AssertionError(f"manager exited early with {process.returncode}")
    raise AssertionError("manager did not report readiness before timeout")


def _stop_isolated_manager(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=12)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def test_maintenance_journal_requires_ordered_evidence_and_retains_interruption(tmp_path):
    journal = MaintenanceJournal(tmp_path / "state")
    transaction = journal.begin("window-1", tmp_path, "Read-only preflight recorded.")
    assert transaction["status"] == "PREPARED"
    assert journal.begin("window-1", tmp_path, "Read-only preflight recorded.") == transaction

    with pytest.raises(JournalError, match="expected STOP_REQUESTED"):
        journal.advance("window-1", "OLD_OWNER_RELEASED", "Not yet stopped.")
    for status in (
        "STOP_REQUESTED",
        "OLD_OWNER_RELEASED",
        "START_REQUESTED",
    ):
        transaction = journal.advance("window-1", status, f"Observed {status}.")
    assert journal.advance(
        "window-1", "START_REQUESTED", "Observed START_REQUESTED."
    ) == transaction

    reloaded = MaintenanceJournal(tmp_path / "state").status("window-1")
    assert reloaded["status"] == "START_REQUESTED"
    assert reloaded["events"][-1]["evidence"] == "Observed START_REQUESTED."
    assert transaction["status"] != "COMMITTED"
    assert (tmp_path / "state/maintenance.json").stat().st_mode & 0o777 == 0o600


def test_maintenance_journal_blocks_commit_after_failure_and_pre_stop_abort(tmp_path):
    journal = MaintenanceJournal(tmp_path / "state")
    journal.begin("window-failed", tmp_path, "Preflight passed.")
    journal.abort("window-failed", "Aborted before shutdown.")
    with pytest.raises(JournalError, match="terminal"):
        journal.advance("window-failed", "STOP_REQUESTED", "Must remain aborted.")

    journal.begin("window-recovery", tmp_path, "Preflight passed.")
    journal.advance("window-recovery", "STOP_REQUESTED", "Shutdown requested.")
    journal.fail("window-recovery", "Old manager did not relinquish ownership.")
    with pytest.raises(JournalError, match="terminal"):
        journal.advance("window-recovery", "OLD_OWNER_RELEASED", "Must not commit.")
    assert journal.status("window-recovery")["status"] == "OPERATOR_INTERVENTION_REQUIRED"


def test_isolated_maintenance_stops_then_starts_exactly_one_manager_and_preserves_cre_data(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    state = tmp_path / "runtime-state"
    ports = _free_ports(4)
    data_directory = root / "data"
    data_directory.mkdir()
    database_path = data_directory / "ultron_cre.db"
    with sqlite3.connect(database_path) as database:
        database.execute("CREATE TABLE persisted_preview (id TEXT PRIMARY KEY, evidence TEXT)")
        database.execute("INSERT INTO persisted_preview VALUES ('synthetic', 'verified')")
    database_hash = hashlib.sha256(database_path.read_bytes()).hexdigest()

    old = subprocess.Popen(
        _manager_command(root, state, ports),
        cwd=root,
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        old_ready = _read_ready(old)
        original_pids = old_ready["pids"]
        competing = subprocess.run(
            _manager_command(root, state, ports),
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert competing.returncode == 1
        assert "Another service manager holds the manager lease" in competing.stdout
        assert all(Path(f"/proc/{pid}").exists() for pid in original_pids.values())
        journal = MaintenanceJournal(state)
        journal.begin("isolated-window", root, "Temporary manager identities and CRE hash captured.")
        journal.advance("isolated-window", "STOP_REQUESTED", "SIGINT requested through fixture manager process.")
        _stop_isolated_manager(old)
        assert old.returncode == 0
        assert all(not Path(f"/proc/{pid}").exists() for pid in original_pids.values())
        assert json.loads((state / "services.json").read_text())["services"] == {}
        journal.advance("isolated-window", "OLD_OWNER_RELEASED", "Fixture exited; service records empty.")

        journal.advance("isolated-window", "START_REQUESTED", "Starting updated fixture manager once.")
        new = subprocess.Popen(
            _manager_command(root, state, ports),
            cwd=root,
            env=os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            new_ready = _read_ready(new)
            assert set(new_ready["statuses"]) == {"gateway", "frontend", "auth", "voice"}
            assert all("started and ready" in value for value in new_ready["statuses"].values())
            assert new_ready["pids"].keys() == original_pids.keys()
            assert all(
                new_ready["pids"][name] != original_pids[name]
                for name in original_pids
            )
            journal.advance("isolated-window", "NEW_MANAGER_READY", "Fixture manager owns lease and control socket.")
            journal.advance("isolated-window", "SERVICES_READY", "All four service probes passed.")
            journal.advance("isolated-window", "DASHBOARD_VERIFIED", "Dashboard check represented by isolated probe fixture.")
            journal.advance("isolated-window", "CRE_VERIFIED", "Temporary CRE database integrity verified.")
            journal.advance("isolated-window", "COMMITTED", "All isolated maintenance checks completed.")
        finally:
            _stop_isolated_manager(new)

        assert journal.status("isolated-window")["status"] == "COMMITTED"
        with sqlite3.connect(database_path) as database:
            assert database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert database.execute(
                "SELECT evidence FROM persisted_preview WHERE id='synthetic'"
            ).fetchone()[0] == "verified"
        assert hashlib.sha256(database_path.read_bytes()).hexdigest() == database_hash
    finally:
        _stop_isolated_manager(old)


@pytest.mark.parametrize("failed_service", ["gateway", "frontend", "auth", "voice"])
def test_failed_new_manager_readiness_rolls_back_only_new_fixture_services(
    tmp_path, failed_service
):
    root = tmp_path / "project"
    root.mkdir()
    state = tmp_path / "runtime-state"
    ports = _free_ports(4)
    old = subprocess.Popen(
        _manager_command(root, state, ports),
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        original = _read_ready(old)["pids"]
        _stop_isolated_manager(old)
        assert old.returncode == 0
        assert all(not Path(f"/proc/{pid}").exists() for pid in original.values())

        environment = os.environ.copy()
        environment["FAIL_MAINTENANCE_SERVICE"] = failed_service
        failed = subprocess.run(
            _manager_command(root, state, ports),
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert failed.returncode == 1
        assert f"STARTUP_FAILED {failed_service}" in failed.stdout
        assert json.loads((state / "services.json").read_text())["services"] == {}
        for port in ports:
            with socket.socket() as probe:
                probe.settimeout(.1)
                assert probe.connect_ex(("127.0.0.1", port)) != 0
    finally:
        _stop_isolated_manager(old)
