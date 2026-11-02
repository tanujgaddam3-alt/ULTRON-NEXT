from __future__ import annotations

import fcntl
import json
import hashlib
import os
import platform
import re
import shutil
import socket
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


_SERVICE_PORTS = {
    "gateway": 8080,
    "frontend": 8000,
    "auth": 8081,
    "voice": 8765,
}
_SERVICE_MARKERS = {
    "gateway": ("ultron.application.runtime.service_entrypoint", "main.py"),
    "frontend": ("http.server",),
    "auth": ("ultron.application.runtime.local_auth_helper",),
    "voice": ("ultron.voice.server",),
}
_PROCESS_SNAPSHOT_LIMIT = 200
_IDENTITY = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _proc_identity(pid: int) -> dict[str, Any] | None:
    root = Path("/proc") / str(pid)
    try:
        stat_text = (root / "stat").read_text(encoding="ascii")
        tail = stat_text[stat_text.rfind(")") + 2 :].split()
        return {
            "pid": pid,
            "parentPid": int(tail[1]),
            "processGroup": int(tail[2]),
            "startTicks": int(tail[19]),
            "executable": os.path.realpath(root / "exe"),
            "workingDirectory": os.path.realpath(root / "cwd"),
            "_command": [
                value.decode("utf-8", "replace")
                for value in (root / "cmdline").read_bytes().split(b"\0")
                if value
            ],
        }
    except (OSError, ValueError, IndexError):
        return None


def _memory_bytes() -> dict[str, int] | None:
    try:
        values = {}
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            key, _, remainder = line.partition(":")
            fields = remainder.split()
            if fields and fields[0].isdecimal():
                values[key] = int(fields[0]) * 1024
        total = values["MemTotal"]
        available = values["MemAvailable"]
        return {
            "totalBytes": total,
            "availableBytes": available,
            "usedBytes": total - available,
        }
    except (OSError, KeyError, ValueError):
        return None


def _listeners() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path, family in ((Path("/proc/net/tcp"), socket.AF_INET), (Path("/proc/net/tcp6"), socket.AF_INET6)):
        try:
            lines = path.read_text(encoding="ascii").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 4 or fields[3] != "0A":
                continue
            address, _, port_hex = fields[1].partition(":")
            try:
                port = int(port_hex, 16)
                if family == socket.AF_INET:
                    packed = bytes.fromhex(address)[::-1]
                else:
                    packed = b"".join(
                        bytes.fromhex(address[index : index + 8])[::-1]
                        for index in range(0, 32, 8)
                    )
                ip = socket.inet_ntop(family, packed)
            except (ValueError, OSError):
                continue
            if port in _SERVICE_PORTS.values() or ip in {"127.0.0.1", "::1"}:
                result.append({"address": ip, "port": port, "source": str(path)})
    return result


def _read_runtime_state(state_directory: Path) -> dict[str, Any]:
    path = state_directory / "services.json"
    try:
        info = path.lstat()
        if (
            not path.is_file()
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            return {}
        state = json.loads(path.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


class SystemIntelligenceStore:
    """Persists bounded, structured diagnostic evidence in a private SQLite file."""

    def __init__(self, path: Path):
        self.path = path

    def record_diagnostics(self, records: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory = self.path.parent.lstat()
        if (
            not self.path.parent.is_dir()
            or directory.st_uid != os.getuid()
            or directory.st_mode & 0o077
        ):
            raise OSError("Diagnostics directory has unsafe ownership or permissions.")
        try:
            existing = self.path.lstat()
            if (
                not self.path.is_file()
                or existing.st_uid != os.getuid()
                or existing.st_mode & 0o077
            ):
                raise OSError("Diagnostics database has unsafe ownership or permissions.")
        except FileNotFoundError:
            descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            os.close(descriptor)
        database = sqlite3.connect(self.path, timeout=5)
        try:
            os.chmod(self.path, 0o600)
            database.execute(
                "CREATE TABLE IF NOT EXISTS system_diagnostics ("
                "id TEXT PRIMARY KEY, observed_at TEXT NOT NULL, status TEXT NOT NULL, "
                "record_json TEXT NOT NULL)"
            )
            for record in records:
                database.execute(
                    "INSERT OR REPLACE INTO system_diagnostics "
                    "(id, observed_at, status, record_json) VALUES (?, ?, ?, ?)",
                    (
                        record["id"],
                        record["observedAt"],
                        record["status"],
                        json.dumps(record, separators=(",", ":"), sort_keys=True),
                    ),
                )
            database.execute(
                "DELETE FROM system_diagnostics WHERE id NOT IN "
                "(SELECT id FROM system_diagnostics ORDER BY observed_at DESC LIMIT 500)"
            )
            database.commit()
        finally:
            database.close()

    def recent_diagnostics(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        database = sqlite3.connect(self.path, timeout=5)
        try:
            rows = database.execute(
                "SELECT record_json FROM system_diagnostics "
                "ORDER BY observed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [json.loads(row[0]) for row in rows]
        finally:
            database.close()


class SystemIntelligence:
    def __init__(
        self,
        root: Path | None = None,
        state_directory: Path | None = None,
        store: SystemIntelligenceStore | None = None,
    ):
        self.root = (root or Path(__file__).resolve().parents[3]).resolve()
        self.state_directory = state_directory or (
            Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
            / "ultron/interface-launcher"
        )
        self.store = store or SystemIntelligenceStore(
            self.state_directory / "system-intelligence.db"
        )

    @staticmethod
    def _observation(
        name: str,
        value: Any,
        source: str,
        status: str,
        detail: str | None = None,
    ) -> dict[str, Any]:
        return {
            "name": name,
            "value": value,
            "source": source,
            "observedAt": _now(),
            "status": status,
            "detail": detail,
        }

    def _service_inventory(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        recorded = state.get("services", {})
        if not isinstance(recorded, dict):
            recorded = {}
        services = []
        observed_pids = {
            item["pid"] for item in _process_snapshot(limit=500)
        }
        for name, port in _SERVICE_PORTS.items():
            record = recorded.get(name)
            identity = None
            if isinstance(record, dict):
                try:
                    identity = _proc_identity(int(record["pid"]))
                except (KeyError, TypeError, ValueError):
                    identity = None
            expected_cwd = str(self.root)
            expected_executable = str(self.root / ".venv/bin/python")
            markers = _SERVICE_MARKERS[name]
            verified = bool(
                identity
                and identity["startTicks"] == record.get("start_ticks")
                and identity["processGroup"] == identity["pid"]
                and identity["workingDirectory"] == expected_cwd
                and identity["executable"] == os.path.realpath(expected_executable)
                and any(
                    marker in argument
                    for marker in markers
                    for argument in identity["_command"]
                )
                and int(record.get("pid", -1)) in observed_pids
                and record.get("cwd") == expected_cwd
            )
            listener = any(
                item["port"] == port and item["address"] in {"127.0.0.1", "::1"}
                for item in _listeners()
            )
            process_status = "VERIFIED" if verified else (
                "STALE" if isinstance(record, dict) else "UNKNOWN"
            )
            services.append({
                "name": name,
                "port": port,
                "expectedAddress": "127.0.0.1",
                "process": _public_identity(identity) if verified else None,
                "processStatus": process_status,
                "readiness": "UNKNOWN" if listener else "UNAVAILABLE",
                "listener": listener,
                "source": str(self.state_directory / "services.json"),
                "observedAt": _now(),
            })
        return services

    def collect(self) -> dict[str, Any]:
        state = _read_runtime_state(self.state_directory)
        services = self._service_inventory(state)
        processes = _process_snapshot(limit=_PROCESS_SNAPSHOT_LIMIT)
        process_snapshot_truncated = len(processes) >= _PROCESS_SNAPSHOT_LIMIT
        memory = _memory_bytes()
        load = None
        try:
            load = list(os.getloadavg())
        except (AttributeError, OSError):
            pass

        gpu: dict[str, Any] = {"status": "UNAVAILABLE", "source": "nvidia-smi"}
        nvidia_smi = shutil.which("nvidia-smi")
        if nvidia_smi:
            try:
                result = subprocess.run(
                    [
                        nvidia_smi,
                        "--query-gpu=name,memory.total,memory.used",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=2,
                    check=False,
                )
                if result.returncode == 0:
                    gpu = {
                        "status": "VERIFIED",
                        "source": nvidia_smi,
                        "devices": [line.strip() for line in result.stdout.splitlines() if line.strip()],
                    }
                else:
                    gpu = {"status": "FAILED", "source": nvidia_smi}
            except (OSError, subprocess.TimeoutExpired):
                gpu = {"status": "FAILED", "source": nvidia_smi}

        storage = shutil.disk_usage(self.root)
        storage_value = {
            "totalBytes": storage.total,
            "usedBytes": storage.used,
            "freeBytes": storage.free,
            "usedRatio": storage.used / storage.total if storage.total else None,
        }
        storage_status = "DEGRADED" if storage_value["usedRatio"] and storage_value["usedRatio"] >= 0.9 else "VERIFIED"
        observations = [
            self._observation("os", platform.platform(), "platform.platform()", "VERIFIED"),
            self._observation("kernel", platform.release(), "platform.release()", "VERIFIED"),
            self._observation("cpu", {
                "logicalProcessors": os.cpu_count(),
                "loadAverage": load,
            }, "os.cpu_count/getloadavg", "VERIFIED" if os.cpu_count() else "UNKNOWN"),
            self._observation("memory", memory, "/proc/meminfo", "VERIFIED" if memory else "UNAVAILABLE"),
            self._observation("gpu", gpu, gpu["source"], gpu["status"]),
            self._observation("storage", storage_value, f"shutil.disk_usage({self.root})", storage_status),
            self._observation("networkInterfaces", sorted(
                path.name for path in Path("/sys/class/net").iterdir()
            ) if Path("/sys/class/net").exists() else None,
                "/sys/class/net", "VERIFIED" if Path("/sys/class/net").exists() else "UNAVAILABLE"),
            self._observation("loopbackListeners", _listeners(), "/proc/net/tcp{,6}", "VERIFIED"),
            self._observation(
                "processes",
                processes,
                "/proc",
                "UNKNOWN" if process_snapshot_truncated else "VERIFIED",
                (
                    f"Process snapshot reached its {_PROCESS_SNAPSHOT_LIMIT}-process limit; "
                    "completeness is unknown."
                    if process_snapshot_truncated
                    else None
                ),
            ),
            self._observation("runtime", {
                "python": platform.python_version(),
                "ss": shutil.which("ss"),
                "nvidiaSmi": nvidia_smi,
            }, "platform.python_version/shutil.which", "VERIFIED"),
        ]

        diagnostics = []
        for service in services:
            if service["processStatus"] != "VERIFIED" or service["readiness"] != "UNKNOWN":
                status = "DEGRADED" if service["processStatus"] == "VERIFIED" else service["processStatus"]
                diagnostics.append({
                    "id": f'{service["name"]}:{service["processStatus"]}:{service["readiness"]}',
                    "observedAt": service["observedAt"],
                    "status": status,
                    "symptom": (
                        f'{service["name"]} recorded process is '
                        f'{service["processStatus"].lower()}; listener '
                        f'{service["readiness"].lower()}.'
                    ),
                    "supportingEvidence": {
                        "service": service["name"],
                        "recordSource": service["source"],
                        "pid": service["process"]["pid"] if service["process"] else None,
                        "listener": service["listener"],
                    },
                    "likelyCause": "Manager record and observed process/listener state differ.",
                    "confidence": "LOW",
                    "alternatives": ["Service may be intentionally stopped.", "The manager state may be stale."],
                    "proposedRecovery": "Inspect the service manager record and process identity before acting.",
                    "requiredPermissions": ["Local operator session"],
                    "preconditions": ["Verify manager ownership and current process identity."],
                    "risks": ["Never signal a process using PID alone."],
                    "verification": ["Confirm recorded start identity and expected loopback listener."],
                    "rollback": "No automated recovery is performed by inventory collection.",
                    "outcome": "OBSERVED",
                })
        self.store.record_diagnostics(diagnostics)
        stored_diagnostics = self.store.recent_diagnostics()
        current_diagnostic_ids = {record["id"] for record in diagnostics}
        for record in stored_diagnostics:
            if record["id"] not in current_diagnostic_ids:
                record["lastStatus"] = record["status"]
                record["status"] = "STALE"

        transactions = state.get("controlOperations", {})
        operational_events = []
        if isinstance(transactions, dict):
            for operation_id, transaction in transactions.items():
                if not isinstance(transaction, dict):
                    continue
                for event in transaction.get("events", []):
                    if isinstance(event, dict):
                        operational_events.append({
                            "operationId": operation_id,
                            "service": transaction.get("service"),
                            **event,
                        })
        operational_events.sort(key=lambda item: item.get("at", 0), reverse=True)

        lease_active = self._manager_lease_active()
        socket_path = self.state_directory / "manager.sock"
        try:
            socket_info = socket_path.lstat()
            socket_available = (
                socket_path.is_socket()
                and socket_info.st_uid == os.getuid()
                and socket_info.st_mode & 0o077 == 0
                and lease_active
            )
        except OSError:
            socket_available = False
        auth = next(service for service in services if service["name"] == "auth")
        observed_at = _now()
        manager_handoff = _manager_handoff_status(
            _manager_processes(self.root),
            services,
            lease_active=lease_active,
            socket_available=socket_available,
            observed_at=observed_at,
        )
        observation_statuses = {
            observation.get("status")
            for observation in observations
        }
        if observation_statuses & {"DEGRADED", "FAILED"}:
            host_status = "DEGRADED"
        elif observation_statuses != {"VERIFIED"}:
            host_status = "UNKNOWN"
        else:
            host_status = "VERIFIED"
        service_identity_status = (
            "VERIFIED"
            if all(service["processStatus"] == "VERIFIED" for service in services)
            else "DEGRADED"
        )
        service_status = (
            service_identity_status
            if service_identity_status != "VERIFIED"
            else (
                "VERIFIED"
                if all(service["readiness"] == "VERIFIED" for service in services)
                else "UNKNOWN"
            )
        )
        if (
            host_status == "DEGRADED"
            or service_identity_status != "VERIFIED"
            or manager_handoff["ownershipStatus"] != "OWNERSHIP_VERIFIED"
        ):
            inventory_status = "DEGRADED"
        elif host_status != "VERIFIED" or service_status != "VERIFIED":
            inventory_status = "UNKNOWN"
        else:
            inventory_status = "VERIFIED"
        return {
            "status": inventory_status,
            "hostStatus": host_status,
            "serviceStatus": service_status,
            "generatedAt": observed_at,
            "observations": observations,
            "services": services,
            "dependencies": [
                {
                    "from": "frontend",
                    "to": "auth",
                    "status": "VERIFIED_FROM_SOURCE",
                    "evidence": "interface/services/providers/websocketProvider.js default endpoint",
                    "relationship": "The dashboard's local mode connects to the auth helper.",
                },
                {
                    "from": "auth",
                    "to": "gateway",
                    "status": "VERIFIED_FROM_SOURCE",
                    "evidence": "ultron/application/runtime/local_auth_helper.py upstream gateway endpoint",
                    "relationship": "The helper authenticates and proxies to the gateway.",
                },
            ],
            "operationalEvents": operational_events[:100],
            "diagnostics": stored_diagnostics,
            "managerHandoff": manager_handoff,
            "actions": [{
                "id": "restart-auth",
                "status": (
                    "AVAILABLE"
                    if socket_available
                    and auth["processStatus"] == "VERIFIED"
                    and manager_handoff["ownershipStatus"] == "OWNERSHIP_VERIFIED"
                    else "UNAVAILABLE"
                ),
                "authorization": "Authenticated configured operator session required.",
                "description": "Restart only the local authentication helper through its owning manager.",
            }],
            "manager": {
                "lease": "ACTIVE" if lease_active else "INACTIVE",
                "controlSocket": "AVAILABLE" if socket_available else "UNAVAILABLE",
                "source": str(self.state_directory / "manager.lease"),
            },
            "runtimeState": str(self.state_directory / "services.json"),
        }

    def _manager_lease_active(self) -> bool:
        path = self.state_directory / "manager.lease"
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            return False
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            else:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                return False
        finally:
            os.close(descriptor)


def _process_snapshot(limit: int) -> list[dict[str, Any]]:
    output = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        identity = _proc_identity(int(entry.name))
        if identity:
            output.append(_public_identity(identity))
        if len(output) >= limit:
            break
    return output


def _public_identity(identity: dict[str, Any]) -> dict[str, Any]:
    command = identity.get("_command", [])
    safe = {key: value for key, value in identity.items() if not key.startswith("_")}
    safe["commandFingerprint"] = hashlib.sha256(
        "\0".join(command).encode("utf-8", "replace")
    ).hexdigest()
    return safe


def _manager_processes(root: Path) -> list[dict[str, int]]:
    script_path = str((root / "scripts/ultron_service_manager.py").resolve())
    managers = []
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return managers
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        identity = _proc_identity(int(entry.name))
        if (
            identity
            and identity["workingDirectory"] == str(root)
            and script_path in identity["_command"]
            and "start" in identity["_command"]
        ):
            managers.append({"pid": identity["pid"]})
    return sorted(managers, key=lambda manager: manager["pid"])


def _manager_handoff_status(
    managers: list[dict[str, Any]],
    services: list[dict[str, Any]],
    *,
    lease_active: bool,
    socket_available: bool,
    observed_at: str,
) -> dict[str, Any]:
    manager_pids = sorted({
        manager["pid"]
        for manager in managers
        if isinstance(manager, dict)
        and isinstance(manager.get("pid"), int)
        and not isinstance(manager.get("pid"), bool)
    })
    result = {
        "status": "HANDOFF_BLOCKED",
        "phase": "HANDOFF_BLOCKED",
        "detail": "No supported manager-to-manager handoff protocol is implemented.",
        "observedAt": observed_at,
        "managerPids": manager_pids,
        "eligible": False,
        "ownershipStatus": "OWNERSHIP_UNAVAILABLE",
        "leaseActive": lease_active,
        "controlSocketAvailable": socket_available,
    }
    if len(manager_pids) > 1:
        result.update({
            "status": "OWNERSHIP_UNCERTAIN",
            "phase": "OWNERSHIP_UNCERTAIN",
            "detail": "Multiple service managers were observed; ownership is ambiguous.",
            "ownershipStatus": "OWNERSHIP_UNCERTAIN",
        })
        return result
    if not manager_pids:
        if lease_active or socket_available:
            result.update({
                "status": "OWNERSHIP_UNCERTAIN",
                "phase": "OWNERSHIP_UNCERTAIN",
                "detail": (
                    "Manager lease or control socket exists without one uniquely "
                    "identified manager process."
                ),
                "ownershipStatus": "OWNERSHIP_UNCERTAIN",
            })
        else:
            result["detail"] = (
                "No manager process or supported ownership channel was identified."
            )
        return result

    manager_pid = manager_pids[0]
    if not services or any(
        service.get("processStatus") != "VERIFIED"
        or not isinstance(service.get("process"), dict)
        or service["process"].get("parentPid") != manager_pid
        for service in services
    ):
        result.update({
            "status": "OWNERSHIP_UNCERTAIN",
            "phase": "RUNNING_OLD",
            "detail": (
                "A service identity is stale or ambiguous (missing or has an "
                "unexpected parent); "
                "manager ownership cannot be established."
            ),
            "ownershipStatus": "OWNERSHIP_UNCERTAIN",
        })
        return result
    if not lease_active or not socket_available:
        result.update({
            "phase": "RUNNING_OLD",
            "detail": (
                "legacy managers cannot participate without the active manager "
                "lease and authenticated control socket."
            ),
            "ownershipStatus": "OWNERSHIP_UNCERTAIN",
        })
        return result

    result.update({
        "phase": "RUNNING_OLD",
        "detail": "There is no manager-to-manager handoff protocol.",
        "ownershipStatus": "OWNERSHIP_VERIFIED",
    })
    return result


def request_helper_restart(
    state_directory: Path,
    operation_id: str,
    *,
    timeout: float = 60.0,
) -> dict[str, Any]:
    if not isinstance(operation_id, str) or not _IDENTITY.fullmatch(operation_id):
        raise ValueError("A valid operation ID is required.")
    socket_path = state_directory / "manager.sock"
    info = socket_path.lstat()
    if (
        not socket_path.is_socket()
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise PermissionError("Manager control socket is unavailable or unsafe.")
    request = json.dumps({
        "operation": "restart-auth",
        "operationId": operation_id,
    }, separators=(",", ":")).encode("utf-8") + b"\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(socket_path))
        connection.sendall(request)
        response = bytearray()
        while len(response) <= 4096:
            chunk = connection.recv(4097 - len(response))
            if not chunk:
                break
            response.extend(chunk)
            if response.endswith(b"\n"):
                break
    if len(response) > 4096 or not response.endswith(b"\n"):
        raise OSError("Manager returned an invalid response.")
    payload = json.loads(response)
    if not isinstance(payload, dict) or not isinstance(payload.get("success"), bool):
        raise OSError("Manager returned an invalid response.")
    return payload
