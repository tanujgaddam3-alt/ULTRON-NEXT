import socket
import json
import os
import stat
from types import SimpleNamespace

from ultron.application.runtime.system_intelligence import (
    SystemIntelligence,
    _manager_handoff_status,
)


def _write_state(directory, state):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / "services.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    path.chmod(0o600)


def _successful_nvidia_smi(command, **kwargs):
    assert command[-1] == "--format=csv,noheader,nounits"
    assert kwargs["timeout"] == 2
    return SimpleNamespace(returncode=0, stdout="Test GPU, 1024, 0\n")


def _failed_nvidia_smi(command, **kwargs):
    assert command[-1] == "--format=csv,noheader,nounits"
    assert kwargs["timeout"] == 2
    return SimpleNamespace(returncode=1, stdout="")


def _low_disk_usage(root):
    assert root.is_absolute()
    return SimpleNamespace(total=1000, used=100, free=900)


def _no_available_tool(name):
    assert name in {"nvidia-smi", "ss"}
    return None


def _empty_process_snapshot(limit):
    assert limit > 0
    return []


def _no_manager_processes(root):
    assert root.is_absolute()
    return []


def _loopback_listeners():
    return [
        {"address": "127.0.0.1", "port": port, "source": "isolated test fixture"}
        for port in (8000, 8080, 8081, 8765)
    ]


def _make_host_observations_verified(monkeypatch):
    monkeypatch.setattr(
        "ultron.application.runtime.system_intelligence.shutil.which",
        lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None,
    )
    monkeypatch.setattr(
        "ultron.application.runtime.system_intelligence.subprocess.run",
        _successful_nvidia_smi,
    )
    monkeypatch.setattr(
        "ultron.application.runtime.system_intelligence._process_snapshot",
        _empty_process_snapshot,
    )


def _verified_services(manager_pid=42):
    return [
        {
            "name": name,
            "port": port,
            "expectedAddress": "127.0.0.1",
            "process": {"pid": index + 100, "parentPid": manager_pid},
            "processStatus": "VERIFIED",
            "readiness": "VERIFIED",
            "listener": True,
            "source": "isolated test fixture",
            "observedAt": "2026-10-27T00:00:00+00:00",
        }
        for index, (name, port) in enumerate((
            ("gateway", 8080),
            ("frontend", 8000),
            ("auth", 8081),
            ("voice", 8765),
        ))
    ]


def test_inventory_uses_measured_sources_and_marks_unavailable_hardware(tmp_path, monkeypatch):
    state_directory = tmp_path / "state"
    inventory = SystemIntelligence(root=tmp_path, state_directory=state_directory)
    monkeypatch.setattr(
        "ultron.application.runtime.system_intelligence.shutil.which",
        _no_available_tool,
    )
    monkeypatch.setattr(
        "ultron.application.runtime.system_intelligence._process_snapshot",
        _empty_process_snapshot,
    )
    monkeypatch.setattr(
        "ultron.application.runtime.system_intelligence.shutil.disk_usage",
        _low_disk_usage,
    )

    snapshot = inventory.collect()

    observations = {item["name"]: item for item in snapshot["observations"]}
    assert snapshot["generatedAt"]
    assert snapshot["status"] in {"DEGRADED", "UNKNOWN"}
    assert snapshot["hostStatus"] == "UNKNOWN"
    assert snapshot["serviceStatus"] == "DEGRADED"
    assert observations["os"]["source"] == "platform.platform()"
    assert observations["kernel"]["value"]
    assert observations["cpu"]["value"]["logicalProcessors"] == os.cpu_count()
    assert observations["storage"]["value"]["totalBytes"] > 0
    assert observations["processes"]["source"] == "/proc"
    assert observations["gpu"]["status"] == "UNAVAILABLE"
    assert all(item["observedAt"] and item["source"] and item["status"] for item in observations.values())
    assert snapshot["managerHandoff"]["status"] == "HANDOFF_BLOCKED"
    assert snapshot["managerHandoff"]["eligible"] is False
    assert snapshot["managerHandoff"]["managerPids"] == []
    assert all(
        service["readiness"] == ("UNKNOWN" if service["listener"] else "UNAVAILABLE")
        for service in snapshot["services"]
    )
    assert snapshot["actions"][0]["status"] == "UNAVAILABLE"
    assert stat.S_IMODE((state_directory / "system-intelligence.db").stat().st_mode) == 0o600


def test_valid_host_observations_with_stale_service_identity_are_not_verified(
    tmp_path,
    monkeypatch,
):
    state_directory = tmp_path / "state"
    _write_state(state_directory, {
        "version": 1,
        "services": {
            "auth": {
                "pid": 99999999,
                "start_ticks": 1,
                "cwd": str(tmp_path),
            },
        },
        "controlOperations": {},
    })
    _make_host_observations_verified(monkeypatch)
    monkeypatch.setattr(
        "ultron.application.runtime.system_intelligence._manager_processes",
        _no_manager_processes,
    )

    snapshot = SystemIntelligence(
        root=tmp_path,
        state_directory=state_directory,
    ).collect()

    assert snapshot["hostStatus"] == "VERIFIED"
    assert snapshot["services"][2]["processStatus"] == "STALE"
    assert snapshot["status"] == "DEGRADED"


def test_missing_service_identity_is_not_verified_and_readiness_stays_unknown(
    tmp_path,
    monkeypatch,
):
    _make_host_observations_verified(monkeypatch)
    monkeypatch.setattr(
        "ultron.application.runtime.system_intelligence._manager_processes",
        _no_manager_processes,
    )
    snapshot = SystemIntelligence(
        root=tmp_path,
        state_directory=tmp_path / "state",
    ).collect()

    assert snapshot["hostStatus"] == "VERIFIED"
    assert snapshot["status"] == "DEGRADED"
    assert all(service["processStatus"] == "UNKNOWN" for service in snapshot["services"])
    assert all(
        service["readiness"] == ("UNKNOWN" if service["listener"] else "UNAVAILABLE")
        for service in snapshot["services"]
    )


def test_unknown_or_unavailable_host_observation_does_not_verify_host_status(
    tmp_path,
    monkeypatch,
):
    _make_host_observations_verified(monkeypatch)
    monkeypatch.setattr(
        "ultron.application.runtime.system_intelligence.os.cpu_count",
        lambda: None,
    )
    monkeypatch.setattr(
        "ultron.application.runtime.system_intelligence._memory_bytes",
        lambda: None,
    )
    monkeypatch.setattr(
        "ultron.application.runtime.system_intelligence._manager_processes",
        _no_manager_processes,
    )

    snapshot = SystemIntelligence(
        root=tmp_path,
        state_directory=tmp_path / "state",
    ).collect()
    observations = {item["name"]: item for item in snapshot["observations"]}

    assert observations["cpu"]["status"] == "UNKNOWN"
    assert observations["memory"]["status"] == "UNAVAILABLE"
    assert snapshot["hostStatus"] == "UNKNOWN"
    assert snapshot["status"] != "VERIFIED"


def test_known_host_observation_failure_is_degraded_not_unknown(tmp_path, monkeypatch):
    _make_host_observations_verified(monkeypatch)
    monkeypatch.setattr(
        "ultron.application.runtime.system_intelligence.subprocess.run",
        _failed_nvidia_smi,
    )
    monkeypatch.setattr(
        "ultron.application.runtime.system_intelligence._manager_processes",
        _no_manager_processes,
    )

    snapshot = SystemIntelligence(
        root=tmp_path,
        state_directory=tmp_path / "state",
    ).collect()
    observations = {item["name"]: item for item in snapshot["observations"]}

    assert observations["gpu"]["status"] == "FAILED"
    assert snapshot["hostStatus"] == "DEGRADED"
    assert snapshot["status"] == "DEGRADED"


def test_listener_without_process_identity_is_not_readiness_or_health(
    tmp_path,
    monkeypatch,
):
    _make_host_observations_verified(monkeypatch)
    monkeypatch.setattr(
        "ultron.application.runtime.system_intelligence._listeners",
        _loopback_listeners,
    )
    monkeypatch.setattr(
        "ultron.application.runtime.system_intelligence._manager_processes",
        _no_manager_processes,
    )

    snapshot = SystemIntelligence(
        root=tmp_path,
        state_directory=tmp_path / "state",
    ).collect()
    auth = next(service for service in snapshot["services"] if service["name"] == "auth")

    assert auth["listener"] is True
    assert auth["processStatus"] == "UNKNOWN"
    assert auth["readiness"] == "UNKNOWN"
    assert snapshot["serviceStatus"] == "DEGRADED"
    assert snapshot["status"] != "VERIFIED"


def test_listener_with_stale_process_start_identity_is_not_verified(
    tmp_path,
    monkeypatch,
):
    state_directory = tmp_path / "state"
    _write_state(state_directory, {
        "version": 1,
        "services": {
            "auth": {
                "pid": 707,
                "start_ticks": 10,
                "cwd": str(tmp_path),
            },
        },
        "controlOperations": {},
    })
    _make_host_observations_verified(monkeypatch)
    monkeypatch.setattr(
        "ultron.application.runtime.system_intelligence._listeners",
        _loopback_listeners,
    )
    monkeypatch.setattr(
        "ultron.application.runtime.system_intelligence._process_snapshot",
        lambda limit: [{"pid": 707}] if limit > 0 else [],
    )
    monkeypatch.setattr(
        "ultron.application.runtime.system_intelligence._proc_identity",
        lambda pid: {
            "pid": pid,
            "parentPid": 42,
            "processGroup": pid,
            "startTicks": 11,
            "executable": os.path.realpath(tmp_path / ".venv/bin/python"),
            "workingDirectory": str(tmp_path),
            "_command": ["python", "-m", "ultron.application.runtime.local_auth_helper"],
        } if pid == 707 else None,
    )
    monkeypatch.setattr(
        "ultron.application.runtime.system_intelligence._manager_processes",
        _no_manager_processes,
    )

    snapshot = SystemIntelligence(
        root=tmp_path,
        state_directory=state_directory,
    ).collect()
    auth = next(service for service in snapshot["services"] if service["name"] == "auth")

    assert auth["listener"] is True
    assert auth["processStatus"] == "STALE"
    assert auth["readiness"] == "UNKNOWN"
    assert snapshot["status"] != "VERIFIED"


def test_verified_aggregate_requires_and_accepts_all_verified_evidence(
    tmp_path,
    monkeypatch,
):
    state_directory = tmp_path / "state"
    state_directory.mkdir(mode=0o700)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as manager_socket:
        manager_socket.bind(str(state_directory / "manager.sock"))
        (state_directory / "manager.sock").chmod(0o600)
        _make_host_observations_verified(monkeypatch)
        monkeypatch.setattr(
            "ultron.application.runtime.system_intelligence._memory_bytes",
            lambda: {"totalBytes": 1000, "availableBytes": 800, "usedBytes": 200},
        )
        monkeypatch.setattr(
            "ultron.application.runtime.system_intelligence.shutil.disk_usage",
            _low_disk_usage,
        )
        monkeypatch.setattr(
            "ultron.application.runtime.system_intelligence._manager_processes",
            lambda root: [{"pid": 42}] if root == tmp_path else [],
        )
        inventory = SystemIntelligence(
            root=tmp_path,
            state_directory=state_directory,
        )
        monkeypatch.setattr(
            inventory,
            "_service_inventory",
            lambda state: _verified_services() if isinstance(state, dict) else [],
        )
        monkeypatch.setattr(inventory, "_manager_lease_active", lambda: True)

        snapshot = inventory.collect()

        assert snapshot["hostStatus"] == "VERIFIED"
        assert snapshot["serviceStatus"] == "VERIFIED"
        assert snapshot["managerHandoff"]["ownershipStatus"] == "OWNERSHIP_VERIFIED"
        assert snapshot["managerHandoff"]["eligible"] is False
        assert snapshot["status"] == "VERIFIED"

        monkeypatch.setattr(
            "ultron.application.runtime.system_intelligence.os.cpu_count",
            lambda: None,
        )
        snapshot_with_unknown_host = inventory.collect()
        observations = {
            item["name"]: item for item in snapshot_with_unknown_host["observations"]
        }

        assert observations["cpu"]["status"] == "UNKNOWN"
        assert snapshot_with_unknown_host["hostStatus"] == "UNKNOWN"
        assert snapshot_with_unknown_host["serviceStatus"] == "VERIFIED"
        assert snapshot_with_unknown_host["status"] == "UNKNOWN"


def test_stale_manager_identity_is_diagnostic_and_later_becomes_stale(tmp_path):
    state_directory = tmp_path / "state"
    _write_state(state_directory, {
        "version": 1,
        "services": {
            "auth": {
                "pid": 99999999,
                "start_ticks": 1,
                "cwd": str(tmp_path),
            },
        },
        "controlOperations": {},
    })
    inventory = SystemIntelligence(root=tmp_path, state_directory=state_directory)

    first = inventory.collect()
    auth = next(item for item in first["services"] if item["name"] == "auth")
    assert auth["processStatus"] == "STALE"
    diagnostic = next(item for item in first["diagnostics"] if item["supportingEvidence"]["service"] == "auth")
    assert diagnostic["status"] == "STALE"
    assert diagnostic["confidence"] == "LOW"
    assert diagnostic["outcome"] == "OBSERVED"

    _write_state(state_directory, {"version": 1, "services": {}, "controlOperations": {}})
    second = inventory.collect()
    auth_diagnostic = next(
        item for item in second["diagnostics"]
        if item["id"].startswith("auth:STALE:")
    )
    assert auth_diagnostic["status"] == "STALE"
    assert auth_diagnostic["lastStatus"] == "STALE"


def test_process_observations_do_not_publish_raw_command_arguments(tmp_path):
    snapshot = SystemIntelligence(
        root=tmp_path,
        state_directory=tmp_path / "state",
    ).collect()
    processes = next(
        item["value"] for item in snapshot["observations"]
        if item["name"] == "processes"
    )
    assert processes
    assert all("command" not in process for process in processes)
    assert all(len(process["commandFingerprint"]) == 64 for process in processes)


def test_process_snapshot_at_limit_is_unknown_not_degraded(tmp_path, monkeypatch):
    _make_host_observations_verified(monkeypatch)
    monkeypatch.setattr(
        "ultron.application.runtime.system_intelligence._process_snapshot",
        lambda limit: [{"pid": pid} for pid in range(limit)],
    )
    monkeypatch.setattr(
        "ultron.application.runtime.system_intelligence._manager_processes",
        _no_manager_processes,
    )

    snapshot = SystemIntelligence(
        root=tmp_path,
        state_directory=tmp_path / "state",
    ).collect()
    processes = next(
        item for item in snapshot["observations"] if item["name"] == "processes"
    )

    assert len(processes["value"]) == 200
    assert processes["status"] == "UNKNOWN"
    assert "completeness is unknown" in processes["detail"]
    assert snapshot["hostStatus"] == "UNKNOWN"
    assert snapshot["status"] != "VERIFIED"


def test_handoff_preflight_blocks_legacy_manager_without_control_ownership():
    managers = [{"pid": 42}]
    services = [{
        "name": "gateway",
        "processStatus": "VERIFIED",
        "process": {"parentPid": 42},
    }]

    result = _manager_handoff_status(
        managers,
        services,
        lease_active=False,
        socket_available=False,
        observed_at="2026-10-27T00:00:00+00:00",
    )

    assert result["status"] == "HANDOFF_BLOCKED"
    assert result["phase"] == "RUNNING_OLD"
    assert result["eligible"] is False
    assert "legacy managers cannot participate" in result["detail"]


def test_handoff_preflight_marks_multiple_owners_uncertain():
    result = _manager_handoff_status(
        [{"pid": 42}, {"pid": 43}],
        [],
        lease_active=True,
        socket_available=True,
        observed_at="2026-10-27T00:00:00+00:00",
    )

    assert result["status"] == "OWNERSHIP_UNCERTAIN"
    assert result["phase"] == "OWNERSHIP_UNCERTAIN"
    assert result["managerPids"] == [42, 43]
    assert result["eligible"] is False
    assert result["ownershipStatus"] == "OWNERSHIP_UNCERTAIN"


def test_handoff_preflight_rejects_service_with_different_parent():
    result = _manager_handoff_status(
        [{"pid": 42}],
        [{
            "name": "auth",
            "processStatus": "VERIFIED",
            "process": {"parentPid": 99},
        }],
        lease_active=True,
        socket_available=True,
        observed_at="2026-10-27T00:00:00+00:00",
    )

    assert result["status"] == "OWNERSHIP_UNCERTAIN"
    assert result["phase"] == "RUNNING_OLD"
    assert result["eligible"] is False
    assert "stale or ambiguous" in result["detail"]


def test_handoff_preflight_does_not_claim_transfer_when_endpoint_exists():
    result = _manager_handoff_status(
        [{"pid": 42}],
        [{
            "name": "auth",
            "processStatus": "VERIFIED",
            "process": {"parentPid": 42},
        }],
        lease_active=True,
        socket_available=True,
        observed_at="2026-10-27T00:00:00+00:00",
    )

    assert result["status"] == "HANDOFF_BLOCKED"
    assert result["phase"] == "RUNNING_OLD"
    assert result["eligible"] is False
    assert "no manager-to-manager handoff protocol" in result["detail"]


def test_handoff_preflight_without_manager_is_blocked():
    result = _manager_handoff_status(
        [],
        [],
        lease_active=False,
        socket_available=False,
        observed_at="2026-10-27T00:00:00+00:00",
    )

    assert result["status"] == "HANDOFF_BLOCKED"
    assert result["phase"] == "HANDOFF_BLOCKED"
    assert result["eligible"] is False
    assert result["ownershipStatus"] == "OWNERSHIP_UNAVAILABLE"


def test_handoff_preflight_without_identified_manager_but_with_control_artifacts_is_uncertain():
    result = _manager_handoff_status(
        [],
        [],
        lease_active=True,
        socket_available=True,
        observed_at="2026-10-27T00:00:00+00:00",
    )

    assert result["status"] == "OWNERSHIP_UNCERTAIN"
    assert result["phase"] == "OWNERSHIP_UNCERTAIN"
    assert result["eligible"] is False
    assert result["ownershipStatus"] == "OWNERSHIP_UNCERTAIN"
    assert "without one uniquely identified manager" in result["detail"]


def test_inventory_handoff_preflight_does_not_rewrite_manager_records(tmp_path):
    state_directory = tmp_path / "state"
    original = {
        "version": 1,
        "services": {"auth": {"pid": 99999999, "start_ticks": 1}},
    }
    _write_state(state_directory, original)
    state_path = state_directory / "services.json"
    original_bytes = state_path.read_bytes()
    lease_path = state_directory / "manager.lease"
    lease_path.write_text("stale lease metadata\n", encoding="utf-8")
    lease_path.chmod(0o600)
    lease_bytes = lease_path.read_bytes()

    snapshot = SystemIntelligence(
        root=tmp_path,
        state_directory=state_directory,
    ).collect()

    assert snapshot["managerHandoff"]["eligible"] is False
    assert snapshot["managerHandoff"]["status"] == "HANDOFF_BLOCKED"
    assert state_path.read_bytes() == original_bytes
    assert lease_path.read_bytes() == lease_bytes
