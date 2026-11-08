#!/usr/bin/env python3
"""Safe local ULTRON service startup and shutdown."""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import queue
import re
import secrets
import select
import signal
import socket
import socketserver
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Sequence


FRONTEND_PORT = 8000
GATEWAY_PORT = 8080
AUTH_PORT = 8081
VOICE_PORT = 8765
HELPER_SHUTDOWN_STEPS = (
    (signal.SIGINT, 8.0),
    (signal.SIGTERM, 3.0),
    (signal.SIGKILL, 2.0),
)
SERVICE_SHUTDOWN_STEPS = (
    (signal.SIGINT, 8.0),
    (signal.SIGTERM, 3.0),
)
SERVICE_KILL_WAIT = 2.0
GATEWAY_SHUTDOWN_STEPS = (
    (signal.SIGINT, 14.0),
    (signal.SIGTERM, 3.0),
)
GATEWAY_KILL_WAIT = 2.0
GATEWAY_LIFECYCLE_STATES = frozenset({
    "STOPPED",
    "STARTING",
    "RUNNING",
    "QUIESCING",
    "QUIESCED",
    "RECOVERING",
    "FAILED",
})
GATEWAY_LIFECYCLE_TRANSITIONS = {
    "STOPPED": {"STARTING"},
    "STARTING": {"RUNNING", "FAILED", "STOPPED"},
    "RUNNING": {"QUIESCING", "FAILED"},
    "QUIESCING": {"QUIESCED", "RECOVERING", "FAILED", "RUNNING", "STOPPED"},
    "QUIESCED": {"RECOVERING"},
    "RECOVERING": {"RUNNING", "FAILED", "QUIESCED"},
    "FAILED": {"STARTING", "RECOVERING", "STOPPED", "QUIESCING"},
}
GATEWAY_LIFECYCLE_HISTORY_LIMIT = 32
WEBSOCKET_PATH = "/ultron/stream"
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43,}$")
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(token|password|credential|secret)(\s*[:=]\s*)"
    r"([^\s,;\"'}]+)"
)
SENSITIVE_ENV_NAME = re.compile(
    r"(?i)(TOKEN|SECRET|PASSWORD|CREDENTIAL|API.?KEY|PRIVATE.?KEY)"
)
CRE_GATEWAY_ENV_NAMES = (
    "ULTRON_CRE_DATABASE",
    "ULTRON_CRE_FASTER_WHISPER_MODEL",
    "ULTRON_INTERFACE_CRE_REVIEWER_PRINCIPAL",
    "ULTRON_INTERFACE_CRE_REVIEWER_TOKEN",
)


class StartupFailure(RuntimeError):
    def __init__(self, service: str, reason: str, statuses: dict[str, str]):
        super().__init__(reason)
        self.service = service
        self.statuses = statuses


@dataclass(frozen=True)
class Service:
    name: str
    host: str
    port: int
    command: tuple[str, ...]
    cwd: Path
    probe: Callable[[], str | None]
    identity: Callable[[Sequence[str]], bool]


_OPERATOR_ATTESTATION_FIELDS = {
    "quiesceOperationId",
    "operator",
    "attestedAt",
    "writerInventory",
    "restartSourceInventory",
    "evidenceReferences",
    "allWritersStopped",
    "allRestartSourcesHeld",
    "inventoryCompletenessChecked",
}


def _nonempty_string(value: object, *, maximum: int = 256) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= maximum
        and value == value.strip()
    )


def _validate_operator_attestation(attestation: object, now: float | None = None) -> str | None:
    if not isinstance(attestation, dict) or set(attestation) != _OPERATOR_ATTESTATION_FIELDS:
        return "INVALID_ATTESTATION_SCHEMA"
    if not _nonempty_string(attestation["quiesceOperationId"] , maximum=128):
        return "INVALID_ATTESTATION_SCHEMA"
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", attestation["quiesceOperationId"]):
        return "INVALID_ATTESTATION_SCHEMA"
    if not _nonempty_string(attestation["operator"]):
        return "INVALID_ATTESTATION_SCHEMA"
    timestamp = attestation["attestedAt"]
    if not isinstance(timestamp, str):
        return "INVALID_ATTESTATION_SCHEMA"
    try:
        attested = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if attested.tzinfo is None:
            return "INVALID_ATTESTATION_SCHEMA"
        attested = attested.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return "INVALID_ATTESTATION_SCHEMA"
    current = datetime.fromtimestamp(now if now is not None else time.time(), timezone.utc)
    if attested > current + timedelta(minutes=5) or attested < current - timedelta(hours=24):
        return "STALE_ATTESTATION"
    for field in ("writerInventory", "restartSourceInventory", "evidenceReferences"):
        inventory = attestation[field]
        if not isinstance(inventory, list) or not inventory:
            return "INVALID_ATTESTATION_SCHEMA"
        if any(not _nonempty_string(item, maximum=512) for item in inventory):
            return "INVALID_ATTESTATION_SCHEMA"
        if len(set(inventory)) != len(inventory):
            return "INVALID_ATTESTATION_SCHEMA"
    for field in (
        "allWritersStopped",
        "allRestartSourcesHeld",
        "inventoryCompletenessChecked",
    ):
        if attestation[field] is not True:
            return "INVALID_ATTESTATION_CONFIRMATION"
    return None


def _request_shutdown_on_sigterm(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def _configure_manager_signal_policy() -> None:
    """Keep the supervisor alive on terminal hangup; explicit signals stop it."""
    if threading.current_thread() is not threading.main_thread():
        return
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, _request_shutdown_on_sigterm)


def _read_proc(pid: int) -> tuple[Path, Path, list[str], int, int] | None:
    try:
        process_root = Path("/proc") / str(pid)
        executable = Path(os.path.realpath(process_root / "exe"))
        working_directory = Path(os.path.realpath(process_root / "cwd"))
        arguments = (process_root / "cmdline").read_bytes().split(b"\0")
        command_line = [part.decode("utf-8", "replace") for part in arguments if part]
        stat_text = (process_root / "stat").read_text(encoding="ascii")
        stat_tail = stat_text[stat_text.rfind(")") + 2 :].split()
        process_group = int(stat_tail[2])
        start_ticks = int(stat_tail[19])
        return executable, working_directory, command_line, process_group, start_ticks
    except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError, IndexError):
        return None


def _read_exact(reader, length: int) -> bytes:
    result = bytearray()
    while len(result) < length:
        chunk = reader.read(length - len(result))
        if not chunk:
            raise OSError("WebSocket closed before readiness response.")
        result.extend(chunk)
    return bytes(result)


def _websocket_greeting(port: int, *, origin: str | None = None) -> dict | None:
    key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    request_headers = [
        f"GET {WEBSOCKET_PATH} HTTP/1.1",
        f"Host: 127.0.0.1:{port}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
    ]
    if origin:
        request_headers.append(f"Origin: {origin}")

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as connection:
            connection.settimeout(2.0)
            reader = connection.makefile("rb")
            connection.sendall(("\r\n".join(request_headers) + "\r\n\r\n").encode("ascii"))
            status_line = reader.readline(4096).decode("ascii", "replace").strip()
            headers: dict[str, str] = {}
            while True:
                line = reader.readline(4096)
                if not line or line in (b"\r\n", b"\n"):
                    break
                name, separator, value = line.decode("ascii", "replace").partition(":")
                if separator:
                    headers[name.strip().lower()] = value.strip()
            expected_accept = base64.b64encode(
                hashlib.sha1(
                    (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
                ).digest()
            ).decode("ascii")
            if (
                " 101 " not in f" {status_line} "
                or headers.get("upgrade", "").lower() != "websocket"
                or headers.get("sec-websocket-accept") != expected_accept
            ):
                return None

            while True:
                first, second = _read_exact(reader, 2)
                opcode = first & 0x0F
                length = second & 0x7F
                if length == 126:
                    length = int.from_bytes(_read_exact(reader, 2), "big")
                elif length == 127:
                    length = int.from_bytes(_read_exact(reader, 8), "big")
                if length > 64 * 1024:
                    return None
                mask = _read_exact(reader, 4) if second & 0x80 else b""
                payload = _read_exact(reader, length)
                if mask:
                    payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
                if opcode == 0x9:
                    mask = secrets.token_bytes(4)
                    masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
                    connection.sendall(
                        bytes((0x8A, 0x80 | len(payload))) + mask + masked
                    )
                    continue
                if opcode != 0x1:
                    if opcode == 0x8:
                        return None
                    continue
                value = json.loads(payload.decode("utf-8"))
                return value if isinstance(value, dict) else None
    except (OSError, TimeoutError, ValueError, UnicodeError, json.JSONDecodeError):
        return None


def _probe_frontend(port: int = FRONTEND_PORT) -> str | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as response:
            body = response.read(64 * 1024)
            if response.status == 200 and b"<title>ULTRON | Operator Console</title>" in body:
                return "reachable"
    except (OSError, urllib.error.URLError, TimeoutError):
        pass
    return None


def _probe_gateway(port: int = GATEWAY_PORT) -> str | None:
    greeting = _websocket_greeting(port)
    subsystems = greeting.get("subsystems") if isinstance(greeting, dict) else None
    core = subsystems.get("core") if isinstance(subsystems, dict) else None
    if (
        greeting
        and greeting.get("type") == "connected"
        and greeting.get("channel") == "runtimeEvents"
        and greeting.get("authenticationRequired") is True
        and isinstance(core, dict)
        and core.get("status") == "AVAILABLE"
    ):
        cre = subsystems.get("cre")
        cre_status = cre.get("status") if isinstance(cre, dict) else "UNKNOWN"
        return (
            "gateway core ready (authentication required; "
            f"CRE {cre_status.lower()})"
        )
    return None


def _probe_auth(port: int = AUTH_PORT) -> str | None:
    greeting = _websocket_greeting(port, origin="http://127.0.0.1:8000")
    if (
        greeting
        and greeting.get("type") == "connected"
        and greeting.get("authenticationRequired") is True
        and greeting.get("authMethod") == "local-unlock"
    ):
        return "local-unlock helper ready"
    return None


def _probe_voice(port: int = VOICE_PORT) -> str | None:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/v1/health", timeout=3
        ) as response:
            payload = json.loads(response.read(64 * 1024))
    except (OSError, urllib.error.URLError, TimeoutError, ValueError):
        return None
    if response.status != 200 or payload.get("ready") is not True:
        return None
    engine = payload.get("engine")
    if not isinstance(engine, str) or not engine.strip():
        return None
    clone = payload.get("is_clone")
    clone_status = str(clone).lower() if isinstance(clone, bool) else "unknown"
    return f"engine={engine.strip()} is_clone={clone_status}"


def _module_identity(module: str, trailing: tuple[str, ...] = ()) -> Callable[[Sequence[str]], bool]:
    def matches(arguments: Sequence[str]) -> bool:
        return (
            any(
                arguments[index] == "-m" and index + 1 < len(arguments)
                and arguments[index + 1] == module
                for index in range(len(arguments))
            )
            and all(marker in arguments for marker in trailing)
        )

    return matches


def _frontend_identity(arguments: Sequence[str]) -> bool:
    return (
        any(
            arguments[index] == "-m" and index + 1 < len(arguments)
            and arguments[index + 1] == "http.server"
            for index in range(len(arguments))
        )
        and str(FRONTEND_PORT) in arguments
        and "--directory" in arguments
        and "interface" in arguments
        and "--bind" in arguments
        and "127.0.0.1" in arguments
    )


def _gateway_identity(root: Path, arguments: Sequence[str]) -> bool:
    script_identity = any(
        argument in {"main.py", str(root / "main.py")} for argument in arguments
    )
    service_module_identity = _module_identity(
        "ultron.application.runtime.service_entrypoint"
    )(arguments)
    return script_identity or service_module_identity


def request_gateway_control_operation(
    state_directory: Path,
    operation: str,
    operation_id: str | None = None,
    *,
    attestation: dict | None = None,
    timeout: float = 60.0,
) -> dict:
    if operation not in {
        "stop-stack",
        "restart-gateway",
        "quiesce-gateway",
        "resume-gateway",
        "attest-gateway-writers",
    }:
        raise ValueError("Unsupported gateway control operation.")
    operation_id = operation_id or secrets.token_hex(16)
    if not isinstance(operation_id, str) or not re.fullmatch(
        r"[A-Za-z0-9._:-]{1,128}", operation_id
    ):
        raise ValueError("A valid operation ID is required.")
    socket_path = state_directory / "manager.sock"
    info = socket_path.lstat()
    if (
        not socket_path.is_socket()
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise PermissionError("Manager control socket is unavailable or unsafe.")
    request_payload = {
        "operation": operation,
        "operationId": operation_id,
    }
    if operation == "attest-gateway-writers":
        if not isinstance(attestation, dict):
            raise ValueError("A writer attestation is required.")
        request_payload["attestation"] = attestation
    elif attestation is not None:
        raise ValueError("Attestation is only valid for the writer-attestation operation.")
    request = json.dumps(request_payload, separators=(",", ":")).encode("utf-8") + b"\n"
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


def request_gateway_restart(
    state_directory: Path,
    operation_id: str | None = None,
    *,
    timeout: float = 60.0,
) -> dict:
    return request_gateway_control_operation(
        state_directory,
        "restart-gateway",
        operation_id,
        timeout=timeout,
    )


class ServiceManager:
    def __init__(
        self,
        root: Path,
        *,
        state_directory: Path | None = None,
        services: Sequence[Service] | None = None,
        readiness_timeout: float = 30.0,
        poll_interval: float = 0.25,
        extra_secrets: Sequence[str] = (),
        validate_project: bool = True,
    ):
        self.root = root.resolve()
        self.state_directory = (
            state_directory
            or Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
            / "ultron/interface-launcher"
        )
        self.state_path = self.state_directory / "services.json"
        self.lock_path = self.state_directory / "manager.lock"
        self.manager_lease_path = self.state_directory / "manager.lease"
        self.control_socket_path = self.state_directory / "manager.sock"
        self.logs_directory = self.state_directory / "logs"
        self.readiness_timeout = readiness_timeout
        self.poll_interval = poll_interval
        self.validate_project = validate_project
        self.services = tuple(services or self._production_services())
        self._processes: dict[str, subprocess.Popen] = {}
        self._reader_threads: list[threading.Thread] = []
        self._secrets = [value.encode("utf-8") for value in extra_secrets if value]
        self.statuses: dict[str, str] = {}
        self._manager_lease_descriptor: int | None = None
        self._control_server: socketserver.ThreadingUnixStreamServer | None = None
        self._control_thread: threading.Thread | None = None
        self._control_requests: queue.Queue = queue.Queue()
        self._startup_recovery_requires_intervention = False
        self._shutdown_requested = False

    def acquire_manager_lease(self) -> None:
        """Fence modern manager instances and refuse handoff from legacy ones."""
        descriptor = self._state_lock_file(self.manager_lease_path)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            raise StartupFailure(
                "manager",
                "Another service manager holds the manager lease.",
                self.statuses,
            ) from None

        script_path = str((self.root / "scripts/ultron_service_manager.py").resolve())
        for entry in Path("/proc").iterdir():
            if not entry.name.isdecimal() or int(entry.name) == os.getpid():
                continue
            proc = _read_proc(int(entry.name))
            if proc is None or proc[1] != self.root:
                continue
            arguments = proc[2]
            if script_path in arguments and "start" in arguments:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
                raise StartupFailure(
                    "manager",
                    "A legacy service manager is already supervising this project; "
                    "live manager adoption is not supported.",
                    self.statuses,
                )
        try:
            recovery = self._read_state().get("startupRecovery")
        except StartupFailure:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            raise
        if (
            isinstance(recovery, dict)
            and recovery.get("status") == "OPERATOR_INTERVENTION_REQUIRED"
        ):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            raise StartupFailure(
                "manager",
                "A previous startup rollback requires operator intervention; "
                "service adoption is blocked.",
                self.statuses,
            )
        self._manager_lease_descriptor = descriptor

    @staticmethod
    def _state_lock_file(path: Path) -> int:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory_info = path.parent.lstat()
        if (
            not stat.S_ISDIR(directory_info.st_mode)
            or directory_info.st_uid != os.getuid()
            or stat.S_IMODE(directory_info.st_mode) & 0o077
        ):
            raise StartupFailure(
                "state",
                "Manager state directory has unsafe ownership or permissions.",
                {},
            )
        descriptor = os.open(
            path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            os.close(descriptor)
            raise StartupFailure(
                "state",
                "Manager lease file has unsafe ownership or permissions.",
                {},
            )
        os.fchmod(descriptor, 0o600)
        return descriptor

    def release_manager_lease(self) -> None:
        if self._manager_lease_descriptor is None:
            return
        fcntl.flock(self._manager_lease_descriptor, fcntl.LOCK_UN)
        os.close(self._manager_lease_descriptor)
        self._manager_lease_descriptor = None

    def _project_manager_process(self, pid: int) -> bool | None:
        proc = _read_proc(pid)
        if proc is None:
            return None
        script_path = str((self.root / "scripts/ultron_service_manager.py").resolve())
        return (
            proc[1] == self.root
            and script_path in proc[2]
            and "start" in proc[2]
        )

    def _manager_ownership_status(self) -> str:
        try:
            lease_info = self.manager_lease_path.lstat()
            if (
                not stat.S_ISREG(lease_info.st_mode)
                or lease_info.st_uid != os.getuid()
                or stat.S_IMODE(lease_info.st_mode) & 0o077
            ):
                return "NOT_VERIFIED"
            this_manager_lease = False
            if self._manager_lease_descriptor is not None:
                owned_info = os.fstat(self._manager_lease_descriptor)
                this_manager_lease = (
                    owned_info.st_dev == lease_info.st_dev
                    and owned_info.st_ino == lease_info.st_ino
                )

            lock_rows = []
            lock_text = Path("/proc/locks").read_text(encoding="ascii")
            for line in lock_text.splitlines():
                fields = line.split()
                if len(fields) < 6 or fields[1] != "FLOCK":
                    continue
                device, inode = fields[5].rsplit(":", 1)
                major, minor = device.split(":", 1)
                if (
                    int(major, 16) == os.major(lease_info.st_dev)
                    and int(minor, 16) == os.minor(lease_info.st_dev)
                    and int(inode) == lease_info.st_ino
                ):
                    lock_rows.append(int(fields[4]))
        except (OSError, ValueError, IndexError):
            return "NOT_VERIFIED"

        if this_manager_lease and os.getpid() in lock_rows:
            return "HELD_BY_THIS_MANAGER"
        if len(lock_rows) > 1:
            return "AMBIGUOUS"
        if lock_rows:
            manager_match = self._project_manager_process(lock_rows[0])
            if manager_match is None:
                return "OWNER_UNVERIFIED"
            return "HELD_BY_PROJECT_MANAGER" if manager_match else "HELD_BY_UNKNOWN_PROCESS"

        try:
            for entry in Path("/proc").iterdir():
                if not entry.name.isdecimal():
                    continue
                manager_match = self._project_manager_process(int(entry.name))
                if manager_match:
                    return "LEGACY_MANAGER_PRESENT"
                if manager_match is None and entry.name == str(os.getpid()):
                    return "NOT_VERIFIED"
        except OSError:
            return "NOT_VERIFIED"
        return "NOT_HELD"

    @staticmethod
    def _quiesce_process_status(quiesce: object) -> str:
        if not isinstance(quiesce, dict):
            return "NOT_VERIFIED"
        process = quiesce.get("process")
        if not isinstance(process, dict):
            return "IDENTITY_UNAVAILABLE"
        pid = process.get("pid")
        start_ticks = process.get("start_ticks")
        if type(pid) is not int or pid <= 0 or type(start_ticks) is not int or start_ticks < 0:
            return "IDENTITY_UNAVAILABLE"
        try:
            (Path("/proc") / str(pid)).stat()
        except FileNotFoundError:
            return "ORIGINAL_PROCESS_EXITED"
        except OSError:
            return "IDENTITY_UNAVAILABLE"
        current = _read_proc(pid)
        if current is None:
            return "IDENTITY_UNAVAILABLE"
        if current[4] != start_ticks:
            return "ORIGINAL_PROCESS_EXITED_PID_REUSED"
        return "ORIGINAL_PROCESS_ACTIVE"

    def _verify_quiesced_gateway(self, state: dict, quiesce: dict) -> dict:
        ownership_status = self._manager_ownership_status()
        if ownership_status != "HELD_BY_THIS_MANAGER":
            return {
                "success": False,
                "status": "NOT_MANAGER_OWNED",
                "managerOwnershipStatus": ownership_status,
                "detail": "This manager must hold the verified manager lease for gateway recovery.",
            }
        if (
            quiesce.get("status") != "QUIESCED"
            or "gateway" in state.get("services", {})
            or "gateway" in self._processes
        ):
            return {
                "success": False,
                "status": "REFUSED_STATE",
                "detail": "Gateway quiescence or managed-process state is inconsistent.",
            }
        gateway = next(
            (service for service in self.services if service.name == "gateway"),
            None,
        )
        if gateway is None:
            return {
                "success": False,
                "status": "REFUSED_CONFIGURATION",
                "detail": "No gateway service is configured.",
            }
        process_status = self._quiesce_process_status(quiesce)
        if process_status == "ORIGINAL_PROCESS_ACTIVE":
            return {
                "success": False,
                "status": "GATEWAY_PROCESS_ACTIVE",
                "detail": "The original gateway process identity is still active.",
            }
        if process_status not in {
            "ORIGINAL_PROCESS_EXITED",
            "ORIGINAL_PROCESS_EXITED_PID_REUSED",
        }:
            return {
                "success": False,
                "status": "GATEWAY_IDENTITY_UNVERIFIED",
                "detail": "The original gateway process identity cannot be verified as exited.",
            }
        try:
            listeners = self._listener_details(gateway.port)
        except (StartupFailure, OSError, ValueError):
            listeners = None
        if listeners is None:
            return {
                "success": False,
                "status": "LISTENER_UNVERIFIED",
                "detail": "The gateway listener state cannot be safely inspected.",
            }
        if listeners:
            return {
                "success": False,
                "status": "LISTENER_CONFLICT",
                "detail": "A process is listening on the gateway port; recovery is refused.",
            }
        return {
            "success": True,
            "status": "VERIFIED",
            "managerOwnershipStatus": ownership_status,
            "gatewayProcessStatus": process_status,
            "listenerStatus": "NOT_LISTENING",
            "verifiedAt": time.time(),
        }

    def start_control_channel(self) -> None:
        if self._manager_lease_descriptor is None:
            raise StartupFailure(
                "manager",
                "The manager must hold its lifetime lease before opening control IPC.",
                self.statuses,
            )
        if len(os.fsencode(self.control_socket_path)) >= 104:
            raise StartupFailure("manager", "Control socket path is too long.", self.statuses)
        if self.control_socket_path.exists():
            info = self.control_socket_path.lstat()
            if (
                not stat.S_ISSOCK(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077
            ):
                raise StartupFailure(
                    "manager",
                    "Existing control socket is ambiguous; it was left untouched.",
                    self.statuses,
                )
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(0.2)
                    probe.connect(str(self.control_socket_path))
            except OSError:
                self.control_socket_path.unlink()
            else:
                raise StartupFailure(
                    "manager",
                    "A control socket is already active.",
                    self.statuses,
                )

        manager = self

        class ControlServer(socketserver.ThreadingUnixStreamServer):
            daemon_threads = True
            allow_reuse_address = False

            def server_bind(self):
                super().server_bind()
                os.chmod(self.server_address, 0o600)

        class RequestHandler(socketserver.StreamRequestHandler):
            def handle(self):
                try:
                    credentials = self.request.getsockopt(
                        socket.SOL_SOCKET,
                        socket.SO_PEERCRED,
                        struct.calcsize("3i"),
                    )
                    _peer_pid, peer_uid, _peer_gid = struct.unpack("3i", credentials)
                    if peer_uid != os.getuid():
                        self._respond({
                            "success": False,
                            "status": "UNAUTHORIZED_PEER",
                        })
                        return
                    raw = self.rfile.readline(4097)
                    if len(raw) > 4096 or not raw.endswith(b"\n"):
                        self._respond({
                            "success": False,
                            "status": "INVALID_REQUEST",
                        })
                        return
                    request = json.loads(raw)
                    operation = request.get("operation") if isinstance(request, dict) else None
                    attestation_operation = operation == "attest-gateway-writers"
                    expected_fields = (
                        {"operation", "operationId", "attestation"}
                        if attestation_operation
                        else {"operation", "operationId"}
                    )
                    if (
                        not isinstance(request, dict)
                        or set(request) != expected_fields
                        or operation not in {
                            "stop-stack",
                            "restart-auth",
                            "restart-gateway",
                            "quiesce-gateway",
                            "resume-gateway",
                            "attest-gateway-writers",
                        }
                        or not isinstance(request.get("operationId"), str)
                        or not re.fullmatch(
                            r"[A-Za-z0-9._:-]{1,128}",
                            request["operationId"],
                        )
                        or (attestation_operation and not isinstance(request.get("attestation"), dict))
                    ):
                        self._respond({
                            "success": False,
                            "status": "INVALID_REQUEST",
                        })
                        return
                    completion = threading.Event()
                    result: dict = {}
                    manager._control_requests.put((
                        operation,
                        request["operationId"],
                        completion,
                        result,
                        request.get("attestation"),
                    ))
                    if not completion.wait(timeout=manager.readiness_timeout + 20):
                        self._respond({
                            "success": False,
                            "status": "REQUEST_TIMEOUT",
                        })
                        return
                    self._respond(result)
                except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
                    self._respond({
                        "success": False,
                        "status": "INVALID_REQUEST",
                    })

            def _respond(self, result: dict) -> None:
                self.wfile.write(
                    json.dumps(result, separators=(",", ":")).encode("utf-8")
                    + b"\n"
                )

        try:
            server = ControlServer(str(self.control_socket_path), RequestHandler)
            os.chmod(self.control_socket_path, 0o600)
        except OSError:
            raise StartupFailure(
                "manager",
                "Could not safely create the manager control socket.",
                self.statuses,
            ) from None
        self._control_server = server
        self._control_thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.1},
            name="ultron-manager-control",
            daemon=True,
        )
        self._control_thread.start()

    def close_control_channel(self) -> None:
        server = self._control_server
        self._control_server = None
        if server is not None:
            server.shutdown()
            server.server_close()
            try:
                self.control_socket_path.unlink()
            except FileNotFoundError:
                pass
        if self._control_thread is not None:
            self._control_thread.join(timeout=1.0)
            self._control_thread = None
        self.release_manager_lease()

    def _production_services(self) -> tuple[Service, ...]:
        python = self.root / ".venv/bin/python"
        return (
            Service(
                "gateway",
                "127.0.0.1",
                GATEWAY_PORT,
                (str(python), "-m", "ultron.application.runtime.service_entrypoint"),
                self.root,
                _probe_gateway,
                lambda args: _gateway_identity(self.root, args),
            ),
            Service(
                "frontend",
                "127.0.0.1",
                FRONTEND_PORT,
                (
                    str(python), "-m", "http.server", str(FRONTEND_PORT),
                    "--bind", "127.0.0.1", "--directory", "interface",
                ),
                self.root,
                _probe_frontend,
                _frontend_identity,
            ),
            Service(
                "auth",
                "127.0.0.1",
                AUTH_PORT,
                (
                    str(python), "-m", "ultron.application.runtime.local_auth_helper",
                    "serve",
                ),
                self.root,
                _probe_auth,
                _module_identity(
                    "ultron.application.runtime.local_auth_helper", ("serve",)
                ),
            ),
            Service(
                "voice",
                "127.0.0.1",
                VOICE_PORT,
                (
                    str(python), "-m", "ultron.voice.server", "--port",
                    str(VOICE_PORT), "--engine", "edge",
                ),
                self.root,
                _probe_voice,
                _module_identity(
                    "ultron.voice.server",
                    ("--port", str(VOICE_PORT)),
                ),
            ),
        )

    def _validate_project(self) -> None:
        required = (
            self.root / "main.py",
            self.root / "interface/index.html",
            self.root / "ultron/application/runtime/interface_gateway.py",
            self.root / "ultron/application/runtime/local_auth_helper.py",
            self.root / "ultron/voice/server.py",
            self.root / "ultron/config/ultron_voice.json",
            self.root / ".venv/bin/python",
        )
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise StartupFailure(
                "preflight",
                "Required project files are missing: " + ", ".join(missing),
                self.statuses,
            )
        if not os.access(self.root / ".venv/bin/python", os.X_OK):
            raise StartupFailure("preflight", "Project interpreter is not executable.", self.statuses)

    def _state_lock(self):
        try:
            self.state_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory_info = self.state_directory.lstat()
        except OSError:
            raise StartupFailure("state", "Launcher state directory is unavailable.", self.statuses) from None
        if (
            not stat.S_ISDIR(directory_info.st_mode)
            or directory_info.st_uid != os.getuid()
            or stat.S_IMODE(directory_info.st_mode) & 0o077
        ):
            raise StartupFailure("state", "Launcher state directory has unsafe ownership or permissions.", self.statuses)
        descriptor = os.open(
            self.lock_path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        lock_info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(lock_info.st_mode)
            or lock_info.st_uid != os.getuid()
            or stat.S_IMODE(lock_info.st_mode) & 0o077
        ):
            os.close(descriptor)
            raise StartupFailure("state", "Launcher lock file has unsafe ownership or permissions.", self.statuses)
        os.fchmod(descriptor, 0o600)
        return descriptor

    def _read_state(self) -> dict:
        try:
            info = self.state_path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077
            ):
                raise StartupFailure("state", "Launcher state file has unsafe ownership or permissions.", self.statuses)
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if state.get("version") != 1 or not isinstance(state.get("services"), dict):
                raise ValueError("invalid state shape")
            return state
        except FileNotFoundError:
            return {"version": 1, "services": {}}
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise StartupFailure("state", f"Cannot safely read launcher state ({type(exc).__name__}).", self.statuses) from None

    def _write_state(self, state: dict) -> None:
        self.state_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".services-", suffix=".tmp", dir=self.state_directory
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(state, output, separators=(",", ":"))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_name, self.state_path)
            os.chmod(self.state_path, 0o600)
            directory_fd = os.open(self.state_directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

    @staticmethod
    def _inferred_gateway_lifecycle_state(state: dict) -> str:
        lifecycle = state.get("gatewayLifecycle")
        if isinstance(lifecycle, dict):
            current = lifecycle.get("state")
            return (
                current
                if isinstance(current, str)
                and current in GATEWAY_LIFECYCLE_STATES
                else "UNKNOWN"
            )
        quiesce = state.get("gatewayQuiesce")
        if isinstance(quiesce, dict):
            return {
                "QUIESCED": "QUIESCED",
                "QUIESCE_REQUESTED": "QUIESCING",
                "RESUME_REQUESTED": "RECOVERING",
                "NEEDS_OPERATOR": "FAILED",
            }.get(quiesce.get("status"), "FAILED")
        if "gateway" in state.get("services", {}):
            return "RUNNING"
        return "STOPPED"

    def _transition_gateway_lifecycle(
        self,
        state: dict,
        target: str,
        reason: str,
        *,
        operation_id: str | None = None,
    ) -> None:
        if target not in GATEWAY_LIFECYCLE_STATES:
            raise ValueError(f"Unknown gateway lifecycle state: {target}")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Gateway lifecycle transitions require a reason.")
        lifecycle = state.get("gatewayLifecycle")
        if lifecycle is None:
            current = self._inferred_gateway_lifecycle_state(state)
            lifecycle = {
                "state": current,
                "updatedAt": time.time(),
                "reason": "Lifecycle state inferred from persisted gateway state.",
                "operationId": None,
                "history": [],
            }
            state["gatewayLifecycle"] = lifecycle
        elif (
            not isinstance(lifecycle, dict)
            or lifecycle.get("state") not in GATEWAY_LIFECYCLE_STATES
            or not isinstance(lifecycle.get("history", []), list)
        ):
            raise StartupFailure(
                "gateway",
                "Persisted gateway lifecycle state is malformed; refusing transition.",
                self.statuses,
            )

        current = lifecycle["state"]
        if current == target:
            return
        if target not in GATEWAY_LIFECYCLE_TRANSITIONS[current]:
            raise StartupFailure(
                "gateway",
                f"Illegal gateway lifecycle transition: {current} -> {target}.",
                self.statuses,
            )
        changed_at = time.time()
        lifecycle["state"] = target
        lifecycle["updatedAt"] = changed_at
        lifecycle["reason"] = reason.strip()
        lifecycle["operationId"] = operation_id
        history = lifecycle.setdefault("history", [])
        history.append({
            "state": target,
            "at": changed_at,
            "operationId": operation_id,
            "reason": reason.strip(),
        })
        del history[:-GATEWAY_LIFECYCLE_HISTORY_LIMIT]

    def _process_matches(self, service: Service, pid: int, start_ticks: int | None = None, *, managed: bool = False) -> bool:
        proc = _read_proc(pid)
        if proc is None:
            return False
        executable, cwd, arguments, process_group, actual_start_ticks = proc
        expected_executable = Path(os.path.realpath(service.command[0]))
        return (
            executable == expected_executable
            and cwd == service.cwd.resolve()
            and service.identity(arguments)
            and (start_ticks is None or actual_start_ticks == start_ticks)
            and (not managed or process_group == pid)
        )

    def _listener_pids(self, port: int) -> list[int] | None:
        details = self._listener_details(port)
        if details is None:
            return None
        pids = {pid for _address, listener_pids in details for pid in listener_pids}
        if pids:
            return sorted(pids)
        return [] if not details else None

    def _listener_details(self, port: int) -> list[tuple[str, list[int]]] | None:
        ss_path = shutil.which("ss")
        if not ss_path:
            raise StartupFailure("preflight", "Cannot safely inspect listeners: ss is unavailable.", self.statuses)
        try:
            result = subprocess.run(
                [ss_path, "-ltnpH", "sport", "=", f":{port}"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise StartupFailure("preflight", "Cannot safely inspect current port listeners.", self.statuses) from None
        if result.returncode != 0:
            raise StartupFailure("preflight", "Cannot safely inspect current port listeners.", self.statuses)
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            return []
        details: list[tuple[str, list[int]]] = []
        for line in lines:
            fields = line.split()
            if len(fields) < 5:
                return None
            address = fields[3].rsplit(":", 1)[0].strip("[]")
            pids = sorted({int(value) for value in re.findall(r"\bpid=(\d+)", line)})
            details.append((address, pids))
        return details

    def _probe(self, service: Service) -> str | None:
        try:
            return service.probe()
        except Exception:
            return None

    def _secure_file(self, path: Path, *, expected_mode: int = 0o600) -> bool:
        try:
            info = path.lstat()
        except OSError:
            return False
        return (
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == expected_mode
        )

    def _load_gateway_token(self) -> str:
        config_directory = Path.home() / ".config/ultron"
        token_path = config_directory / "interface-token"
        try:
            directory_info = config_directory.lstat()
        except OSError:
            raise StartupFailure("gateway", "Protected ULTRON authentication files are unavailable.", self.statuses) from None
        if (
            not stat.S_ISDIR(directory_info.st_mode)
            or directory_info.st_uid != os.getuid()
            or stat.S_IMODE(directory_info.st_mode) & 0o077
            or not self._secure_file(token_path)
        ):
            raise StartupFailure("gateway", "Protected ULTRON authentication files have unsafe permissions or are missing.", self.statuses)
        try:
            token = token_path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            raise StartupFailure("gateway", "Protected gateway credential could not be read.", self.statuses) from None
        if not TOKEN_PATTERN.fullmatch(token):
            raise StartupFailure("gateway", "Protected gateway credential has an invalid format.", self.statuses)
        self._secrets.append(token.encode("ascii"))
        return token

    def _check_auth_prerequisites(self) -> None:
        config_directory = Path.home() / ".config/ultron"
        verifier_path = config_directory / "interface-unlock.json"
        try:
            directory_info = config_directory.lstat()
        except OSError:
            raise StartupFailure("auth", "Local authentication is not provisioned; see docs/RUNTIME_OPERATIONS.md.", self.statuses) from None
        if (
            not stat.S_ISDIR(directory_info.st_mode)
            or directory_info.st_uid != os.getuid()
            or stat.S_IMODE(directory_info.st_mode) & 0o077
            or not self._secure_file(verifier_path)
            or not self._secure_file(config_directory / "interface-token")
        ):
            raise StartupFailure("auth", "Local authentication files are missing or do not have owner-only permissions.", self.statuses)

    def _environment_for(self, service: Service) -> dict[str, str]:
        if service.name == "gateway":
            environment = os.environ.copy()
            for name, value in environment.items():
                if value and SENSITIVE_ENV_NAME.search(name):
                    self._secrets.append(value.encode("utf-8"))
            cre_config = self._load_cre_runtime_config()
            for name, value in cre_config.items():
                environment.setdefault(name, value)
            reviewer_token = cre_config.get("ULTRON_INTERFACE_CRE_REVIEWER_TOKEN")
            if reviewer_token:
                self._secrets.append(reviewer_token.encode("utf-8"))
        else:
            environment = {
                name: value for name, value in os.environ.items()
                if not SENSITIVE_ENV_NAME.search(name)
            }
            for name in CRE_GATEWAY_ENV_NAMES:
                environment.pop(name, None)
        environment.pop("ULTRON_INTERFACE_AUTH_TOKEN", None)
        environment["PYTHONPATH"] = str(self.root) + (
            os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else ""
        )
        if service.name == "gateway":
            environment.setdefault("ULTRON_EXECUTION_BACKEND", "simulation")
            environment["ULTRON_INTERFACE_AUTH_TOKEN"] = self._load_gateway_token()
        elif service.name == "auth":
            self._check_auth_prerequisites()
        else:
            environment.pop("ULTRON_INTERFACE_AUTH_PRINCIPAL", None)
        return environment

    def _load_cre_runtime_config(self) -> dict[str, str]:
        """Read optional CRE gateway settings from a protected local JSON file."""
        config_directory = Path.home() / ".config/ultron"
        config_path = config_directory / "cre-runtime.json"
        try:
            directory_info = config_directory.lstat()
        except FileNotFoundError:
            return {}
        except OSError:
            raise StartupFailure(
                "gateway",
                "CRE runtime configuration directory is unavailable.",
                self.statuses,
            ) from None
        if (
            not stat.S_ISDIR(directory_info.st_mode)
            or directory_info.st_uid != os.getuid()
            or stat.S_IMODE(directory_info.st_mode) & 0o077
        ):
            raise StartupFailure(
                "gateway",
                "CRE runtime configuration directory has unsafe ownership or permissions.",
                self.statuses,
            )

        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(config_path, flags)
        except FileNotFoundError:
            return {}
        except OSError:
            raise StartupFailure(
                "gateway",
                "CRE runtime configuration file is unavailable or unsafe.",
                self.statuses,
            ) from None
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size > 8192
            ):
                raise StartupFailure(
                    "gateway",
                    "CRE runtime configuration file has unsafe ownership, permissions, or size.",
                    self.statuses,
                )
            chunks = bytearray()
            while len(chunks) <= 8192:
                chunk = os.read(descriptor, min(4096, 8193 - len(chunks)))
                if not chunk:
                    break
                chunks.extend(chunk)
            if len(chunks) > 8192:
                raise StartupFailure(
                    "gateway",
                    "CRE runtime configuration file exceeds its size limit.",
                    self.statuses,
                )
        finally:
            os.close(descriptor)

        try:
            payload = json.loads(chunks.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise StartupFailure(
                "gateway",
                "CRE runtime configuration file is not valid UTF-8 JSON.",
                self.statuses,
            ) from None
        allowed = {
            "ULTRON_CRE_FASTER_WHISPER_MODEL",
            "ULTRON_INTERFACE_CRE_REVIEWER_PRINCIPAL",
            "ULTRON_INTERFACE_CRE_REVIEWER_TOKEN",
        }
        if (
            not isinstance(payload, dict)
            or set(payload) - allowed
            or any(
                not isinstance(name, str)
                or not isinstance(value, str)
                or not value
                or "\x00" in value
                for name, value in payload.items()
            )
        ):
            raise StartupFailure(
                "gateway",
                "CRE runtime configuration contains invalid or unsupported settings.",
                self.statuses,
            )
        reviewer_names = {
            "ULTRON_INTERFACE_CRE_REVIEWER_PRINCIPAL",
            "ULTRON_INTERFACE_CRE_REVIEWER_TOKEN",
        }
        if len(reviewer_names & payload.keys()) == 1:
            raise StartupFailure(
                "gateway",
                "CRE reviewer principal and credential must be configured together.",
                self.statuses,
            )
        return payload

    def _sanitize_log(self, line: bytes) -> bytes:
        result = line
        for secret in self._secrets:
            result = result.replace(secret, b"[redacted]")
        decoded = result.decode("utf-8", "replace")
        decoded = SECRET_ASSIGNMENT.sub(r"\1\2[redacted]", decoded)
        return decoded.encode("utf-8", "replace")

    def _pump_log(self, stream, log_path: Path) -> None:
        try:
            with log_path.open("ab", buffering=0) as output:
                os.chmod(log_path, 0o600)
                for line in iter(stream.readline, b""):
                    output.write(self._sanitize_log(line))
        finally:
            stream.close()

    def _record_for(self, service: Service, pid: int) -> dict:
        proc = _read_proc(pid)
        if proc is None:
            raise StartupFailure(service.name, "Service exited before its process identity could be recorded.", self.statuses)
        return {
            "pid": pid,
            "start_ticks": proc[4],
            "cwd": str(service.cwd.resolve()),
            "log": str(self.logs_directory / f"{service.name}.log"),
        }

    def _start_service(
        self,
        service: Service,
        state: dict,
        *,
        readiness_callback: Callable[[], None] | None = None,
        persist_start_record: bool = True,
        started_callback: Callable[[dict], None] | None = None,
    ) -> str:
        log_path = self.logs_directory / f"{service.name}.log"
        self.logs_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        log_directory_info = self.logs_directory.lstat()
        if (
            not stat.S_ISDIR(log_directory_info.st_mode)
            or log_directory_info.st_uid != os.getuid()
            or stat.S_IMODE(log_directory_info.st_mode) & 0o077
        ):
            raise StartupFailure(service.name, "Service log directory has unsafe ownership or permissions.", self.statuses)
        descriptor = os.open(
            log_path,
            os.O_CREAT | os.O_APPEND | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        try:
            environment = self._environment_for(service)
        except StartupFailure:
            raise
        try:
            process = subprocess.Popen(
                service.command,
                cwd=service.cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        except (OSError, StartupFailure) as exc:
            raise StartupFailure(service.name, "Service process could not be started.", self.statuses) from None
        self._processes[service.name] = process
        reader = threading.Thread(
            target=self._pump_log,
            args=(process.stdout, log_path),
            name=f"ultron-log-{service.name}",
            daemon=True,
        )
        reader.start()
        self._reader_threads.append(reader)
        record = self._record_for(service, process.pid)
        state["services"][service.name] = record
        if persist_start_record:
            self._write_state(state)
        elif started_callback is not None:
            started_callback(record)

        try:
            deadline = time.monotonic() + self.readiness_timeout
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    state["services"].pop(service.name, None)
                    self._processes.pop(service.name, None)
                    self._write_state(state)
                    raise StartupFailure(
                        service.name,
                        f"Process exited with status {process.returncode}; inspect {log_path}.",
                        self.statuses,
                    )
                if not self._process_matches(
                    service, process.pid, record["start_ticks"], managed=True
                ):
                    time.sleep(self.poll_interval)
                    if process.poll() is not None:
                        state["services"].pop(service.name, None)
                        self._processes.pop(service.name, None)
                        self._write_state(state)
                        raise StartupFailure(
                            service.name,
                            f"Process exited with status {process.returncode}; inspect {log_path}.",
                            self.statuses,
                        )
                    stopped = self._stop_record(service, record)
                    if stopped:
                        state["services"].pop(service.name, None)
                        self._processes.pop(service.name, None)
                        self._write_state(state)
                    raise StartupFailure(
                        service.name,
                        "Started process identity could not be verified; "
                        + ("the process was stopped." if stopped else "it was not signalled and its state record was retained."),
                        self.statuses,
                    )
                health = self._probe(service)
                if health:
                    self.statuses[service.name] = f"started and ready ({health})"
                    return health
                if readiness_callback is not None:
                    readiness_callback()
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            stopped = self._stop_record(service, record)
            if stopped:
                state["services"].pop(service.name, None)
                self._processes.pop(service.name, None)
            self._write_state(state)
            raise

        stopped = self._stop_record(service, record)
        if stopped:
            state["services"].pop(service.name, None)
            self._processes.pop(service.name, None)
            self._write_state(state)
        raise StartupFailure(
            service.name,
            f"Readiness timed out after {self.readiness_timeout:g}s"
            + ("; process stopped. " if stopped else "; process remains managed because shutdown did not complete. ")
            + f"Inspect {log_path}.",
            self.statuses,
        )

    def _stop_record(self, service: Service, record: dict) -> bool:
        try:
            pid = int(record["pid"])
            start_ticks = int(record["start_ticks"])
        except (KeyError, TypeError, ValueError):
            return False
        managed_process = self._processes.get(service.name)
        if managed_process is not None and managed_process.pid != pid:
            managed_process = None
        if _read_proc(pid) is None:
            if managed_process is not None:
                managed_process.poll()
            return not (Path("/proc") / str(pid)).exists()
        if not self._process_matches(service, pid, start_ticks, managed=True):
            return False

        def wait_for_exit(timeout: float) -> bool:
            if managed_process is not None:
                try:
                    managed_process.wait(timeout=timeout)
                    return True
                except subprocess.TimeoutExpired:
                    return False
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if not self._process_matches(
                    service,
                    pid,
                    start_ticks,
                    managed=True,
                ):
                    return True
                time.sleep(0.1)
            return not self._process_matches(
                service,
                pid,
                start_ticks,
                managed=True,
            )

        for requested_signal, timeout in SERVICE_SHUTDOWN_STEPS:
            if not self._process_matches(service, pid, start_ticks, managed=True):
                if managed_process is not None:
                    managed_process.poll()
                return True
            try:
                os.kill(pid, requested_signal)
            except ProcessLookupError:
                if managed_process is not None:
                    managed_process.wait()
                return True
            if wait_for_exit(timeout):
                return True
        if self._process_matches(service, pid, start_ticks, managed=True):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if managed_process is not None:
            try:
                managed_process.wait(timeout=SERVICE_KILL_WAIT)
                return True
            except subprocess.TimeoutExpired:
                return False
        return wait_for_exit(2.0)

    def _stop_verified_candidate(self, service: Service, record: dict) -> bool:
        try:
            pid = int(record["pid"])
            start_ticks = int(record["start_ticks"])
        except (KeyError, TypeError, ValueError):
            return False
        if not (Path("/proc") / str(pid)).exists():
            return True
        if not self._process_matches(service, pid, start_ticks, managed=True):
            return False
        if not callable(getattr(os, "pidfd_open", None)) or not callable(
            getattr(signal, "pidfd_send_signal", None)
        ):
            return False
        try:
            pidfd = os.pidfd_open(pid, 0)
        except OSError:
            return False
        try:
            if not self._process_matches(service, pid, start_ticks, managed=True):
                return False
            poller = select.poll()
            poller.register(pidfd, select.POLLIN)
            if poller.poll(0):
                return True
            for requested_signal, timeout in HELPER_SHUTDOWN_STEPS:
                try:
                    signal.pidfd_send_signal(pidfd, requested_signal)
                except OSError:
                    return bool(poller.poll(0))
                if poller.poll(int(timeout * 1000)):
                    return True
            return bool(poller.poll(0))
        finally:
            os.close(pidfd)

    def _recover_interrupted_control(self, state: dict) -> None:
        active_id = state.get("activeControlOperation")
        operations = state.get("controlOperations", {})
        if active_id is None:
            if not isinstance(operations, dict):
                return
            if any(
                isinstance(transaction, dict)
                and transaction.get("status") in {
                    "REQUESTED", "AUTHORIZED", "QUIESCING", "OLD_EXITED",
                    "STARTING", "VERIFYING",
                }
                for transaction in operations.values()
            ):
                raise StartupFailure(
                    "manager",
                    "Interrupted service-control state is ambiguous; no process was adopted or signalled.",
                    self.statuses,
                )
            return
        if (
            not isinstance(active_id, str)
            or not isinstance(operations, dict)
            or not isinstance(operations.get(active_id), dict)
            or not isinstance(state.get("services"), dict)
        ):
            raise StartupFailure(
                "manager",
                "Interrupted service-control state is malformed; no process was adopted or signalled.",
                self.statuses,
            )

        transaction = operations[active_id]
        if transaction.get("operation") == "quiesce-gateway":
            quiesce = state.get("gatewayQuiesce")
            old_record = (
                quiesce.get("process")
                if isinstance(quiesce, dict)
                else transaction.get("oldProcess")
            )
            gateway = next(
                (service for service in self.services if service.name == "gateway"),
                None,
            )
            if (
                gateway is None
                or not isinstance(old_record, dict)
                or transaction.get("service") != "gateway"
            ):
                raise StartupFailure(
                    "gateway",
                    "Interrupted gateway quiesce state is malformed; operator intervention is required.",
                    self.statuses,
                )
            try:
                pid = int(old_record["pid"])
                start_ticks = int(old_record["start_ticks"])
            except (KeyError, TypeError, ValueError):
                raise StartupFailure(
                    "gateway",
                    "Interrupted gateway identity is ambiguous; no process was signalled.",
                    self.statuses,
                ) from None
            if self._process_matches(gateway, pid, start_ticks, managed=True):
                if isinstance(quiesce, dict):
                    quiesce["status"] = "NEEDS_OPERATOR"
                self._transition_gateway_lifecycle(
                    state,
                    "FAILED",
                    "Interrupted quiesce left the original gateway process running; operator review is required.",
                    operation_id=active_id,
                )
                self._control_transition(
                    state,
                    active_id,
                    "INTERRUPTED_NEEDS_OPERATOR",
                    "Gateway identity remains live after interrupted quiesce; it was not signalled again.",
                )
                raise StartupFailure(
                    "gateway",
                    "Interrupted gateway quiesce left the verified gateway running; explicit operator review is required.",
                    self.statuses,
                )
            if (Path("/proc") / str(pid)).exists():
                if isinstance(quiesce, dict):
                    quiesce["status"] = "NEEDS_OPERATOR"
                self._transition_gateway_lifecycle(
                    state,
                    "FAILED",
                    "Interrupted quiesce found an ambiguous process using the recorded gateway PID.",
                    operation_id=active_id,
                )
                self._control_transition(
                    state,
                    active_id,
                    "INTERRUPTED_NEEDS_OPERATOR",
                    "Gateway PID is occupied by an ambiguous process after interrupted quiesce.",
                )
                raise StartupFailure(
                    "gateway",
                    "Interrupted gateway quiesce has an ambiguous process identity; no process was signalled.",
                    self.statuses,
                )
            listeners = self._listener_details(gateway.port)
            if listeners is None or listeners:
                if isinstance(quiesce, dict):
                    quiesce["status"] = "NEEDS_OPERATOR"
                    quiesce["gatewayStatus"] = "STOPPED_LISTENER_AMBIGUOUS"
                self._transition_gateway_lifecycle(
                    state,
                    "FAILED",
                    "Interrupted quiesce could not verify the gateway listener is absent.",
                    operation_id=active_id,
                )
                self._control_transition(
                    state,
                    active_id,
                    "INTERRUPTED_NEEDS_OPERATOR",
                    "Original gateway PID is absent, but its listener is occupied or cannot be inspected.",
                )
                raise StartupFailure(
                    "gateway",
                    "Interrupted quiesce cannot verify the gateway listener is stopped.",
                    self.statuses,
                )
            if not isinstance(quiesce, dict):
                quiesce = {"process": dict(old_record)}
                state["gatewayQuiesce"] = quiesce
            quiesce["status"] = "QUIESCED"
            quiesce["writersStatus"] = "NOT_VERIFIED"
            state["services"].pop("gateway", None)
            self._processes.pop("gateway", None)
            self._transition_gateway_lifecycle(
                state,
                "QUIESCED",
                "Interrupted quiesce verified the original process and listener are absent; external writers remain unverified.",
                operation_id=active_id,
            )
            self._control_transition(
                state,
                active_id,
                "SUCCEEDED",
                "Interrupted quiesce confirmed the original gateway process is gone; no replacement was started.",
            )
            return

        operation_services = {
            "restart-auth": "auth",
            "restart-gateway": "gateway",
            "resume-gateway": "gateway",
        }
        operation = transaction.get("operation")
        service_name = operation_services.get(operation)
        if (
            service_name is None
            or transaction.get("service") != service_name
            or transaction.get("status") not in {
                "REQUESTED", "AUTHORIZED", "QUIESCING", "OLD_EXITED",
                "STARTING", "VERIFYING",
            }
        ):
            raise StartupFailure(
                "manager",
                "Interrupted service-control operation is unsupported or ambiguous; "
                "no process was adopted or signalled.",
                self.statuses,
            )
        target = next((service for service in self.services if service.name == service_name), None)
        if target is None:
            raise StartupFailure(
                "manager",
                f"Interrupted {service_name} transaction has no configured service.",
                self.statuses,
            )

        candidate = transaction.get("candidateProcess")
        if candidate is not None:
            if not isinstance(candidate, dict) or not self._stop_verified_candidate(target, candidate):
                raise StartupFailure(
                    service_name,
                    "Interrupted replacement candidate is ambiguous or could not be safely stopped; "
                    "no process was adopted or signalled.",
                    self.statuses,
                )
            state["services"].pop(service_name, None)
            transaction.pop("candidateProcess", None)
            if operation == "resume-gateway":
                quiesce = state.get("gatewayQuiesce")
                if isinstance(quiesce, dict):
                    quiesce["status"] = "QUIESCED"
                    quiesce["gatewayStatus"] = "STOPPED"
                    self._transition_gateway_lifecycle(
                        state,
                        "QUIESCED",
                        "Interrupted gateway recovery candidate was stopped after identity verification.",
                        operation_id=active_id,
                    )
            elif operation == "restart-gateway":
                self._transition_gateway_lifecycle(
                    state,
                    "FAILED",
                    "Interrupted gateway restart candidate was safely stopped; operator recovery is required.",
                    operation_id=active_id,
                )
            self._control_transition(
                state,
                active_id,
                "FAILED",
                f"Interrupted {service_name} replacement was identity-verified and stopped; operator may retry.",
            )
            return

        old_record = transaction.get("oldProcess")
        if not isinstance(old_record, dict):
            old_record = state["services"].get(service_name)
        if isinstance(old_record, dict):
            try:
                pid = int(old_record["pid"])
                start_ticks = int(old_record["start_ticks"])
            except (KeyError, TypeError, ValueError):
                raise StartupFailure(
                    service_name,
                    f"Interrupted {service_name} identity is ambiguous; no process was signalled.",
                    self.statuses,
                ) from None
            if self._process_matches(target, pid, start_ticks, managed=True):
                listeners = self._listener_details(target.port)
                if (
                    listeners
                    and len(listeners) == 1
                    and listeners[0][0] == target.host
                    and listeners[0][1] == [pid]
                    and self._probe(target)
                ):
                    if operation == "restart-gateway":
                        self._transition_gateway_lifecycle(
                            state,
                            "RUNNING",
                            "Interrupted restart left the original gateway identity and readiness verified.",
                            operation_id=active_id,
                        )
                    self._control_transition(
                        state,
                        active_id,
                        "FAILED",
                        f"Interrupted restart left the original {service_name} verified and healthy; it was not signalled again.",
                    )
                    return
                if operation == "restart-gateway":
                    self._transition_gateway_lifecycle(
                        state,
                        "FAILED",
                        "Interrupted gateway restart left readiness ambiguous; no process was signalled.",
                        operation_id=active_id,
                    )
                raise StartupFailure(
                    service_name,
                    f"Original {service_name} identity remains but readiness is ambiguous; no process was signalled.",
                    self.statuses,
                )
            if (Path("/proc") / str(pid)).exists():
                raise StartupFailure(
                    service_name,
                    f"Original {service_name} PID now identifies an ambiguous process; no process was signalled.",
                    self.statuses,
                )
            state["services"].pop(service_name, None)
        if operation == "restart-gateway":
            self._transition_gateway_lifecycle(
                state,
                "FAILED",
                "Interrupted gateway restart has no verified live gateway process.",
                operation_id=active_id,
            )
        if operation == "resume-gateway":
            quiesce = state.get("gatewayQuiesce")
            if isinstance(quiesce, dict) and "gateway" not in state["services"]:
                quiesce["status"] = "QUIESCED"
                quiesce["gatewayStatus"] = "STOPPED"
                self._transition_gateway_lifecycle(
                    state,
                    "QUIESCED",
                    "Interrupted gateway recovery has no verified candidate; quiescence is preserved.",
                    operation_id=active_id,
                )
        self._control_transition(
            state,
            active_id,
            "FAILED",
            f"Interrupted restart has no verified live {service_name} record; no process was adopted or signalled.",
        )

    def _lock_state(self) -> int:
        descriptor = self._state_lock()
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor

    def _control_transition(
        self,
        state: dict,
        operation_id: str,
        status: str,
        detail: str,
    ) -> dict:
        operations = state.setdefault("controlOperations", {})
        transaction = operations[operation_id]
        now = time.time()
        transaction["status"] = status
        transaction["updatedAt"] = now
        transaction.setdefault("events", []).append({
            "status": status,
            "at": now,
            "detail": detail[:240],
        })
        del transaction["events"][:-32]
        state["activeControlOperation"] = (
            operation_id
            if status not in {
                "SUCCEEDED",
                "OPERATOR_ATTESTED",
                "FAILED",
                "REJECTED_STALE",
                "INTERRUPTED_NEEDS_OPERATOR",
            }
            else None
        )
        for old_id in tuple(operations)[:-100]:
            if old_id != operation_id and old_id != state["activeControlOperation"]:
                operations.pop(old_id, None)
        self._write_state(state)
        return transaction

    def _record_restart_candidate(
        self,
        state: dict,
        operation_id: str,
        candidate: dict,
    ) -> None:
        transaction = state.setdefault("controlOperations", {}).get(operation_id)
        if not isinstance(transaction, dict):
            raise StartupFailure(
                "manager",
                "Restart transaction disappeared before candidate startup.",
                self.statuses,
            )
        transaction["candidateProcess"] = dict(candidate)
        self._control_transition(
            state,
            operation_id,
            "VERIFYING",
            "Replacement candidate identity recorded; readiness is not yet asserted.",
        )

    def _restart_auth(
        self,
        operation_id: str,
    ) -> dict:
        descriptor = self._lock_state()
        try:
            state = self._read_state()
            transactions = state.setdefault("controlOperations", {})
            previous = transactions.get(operation_id)
            if previous is not None:
                return {
                    "success": previous.get("status") == "SUCCEEDED",
                    "operationId": operation_id,
                    "status": previous.get("status", "UNKNOWN"),
                    "detail": previous.get("events", [{}])[-1].get("detail", ""),
                }
            if state.get("activeControlOperation") is not None:
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "BUSY",
                    "detail": "Another control operation is active.",
                }

            service_by_name = {service.name: service for service in self.services}
            auth = service_by_name.get("auth")
            if auth is None or set(self._processes) != set(service_by_name):
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "NOT_MANAGER_OWNED",
                    "detail": "The manager does not own every configured service process.",
                }
            record = state.get("services", {}).get("auth")
            process = self._processes.get("auth")
            try:
                pid = int(record["pid"])
                start_ticks = int(record["start_ticks"])
            except (TypeError, KeyError, ValueError):
                pid = -1
                start_ticks = -1
            if (
                process is None
                or process.pid != pid
                or process.poll() is not None
                or not self._process_matches(auth, pid, start_ticks, managed=True)
            ):
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "REJECTED_STALE",
                    "detail": "Authentication helper process identity is stale or ambiguous.",
                }
            for other in self.services:
                if other.name == "auth":
                    continue
                other_record = state["services"].get(other.name)
                other_process = self._processes.get(other.name)
                try:
                    matches = (
                        other_process is not None
                        and other_process.poll() is None
                        and self._process_matches(
                            other,
                            int(other_record["pid"]),
                            int(other_record["start_ticks"]),
                            managed=True,
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    matches = False
                if not matches:
                    return {
                        "success": False,
                        "operationId": operation_id,
                        "status": "REJECTED_STALE",
                        "detail": f"Service identity for {other.name} is stale or ambiguous.",
                    }

            listeners = self._listener_details(auth.port)
            if (
                listeners is None
                or len(listeners) != 1
                or listeners[0][0] != auth.host
                or listeners[0][1] != [pid]
            ):
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "LISTENER_CONFLICT",
                    "detail": "The helper listener is not uniquely verified on its expected address.",
                }

            transactions[operation_id] = {
                "operation": "restart-auth",
                "service": "auth",
                "status": "REQUESTED",
                "createdAt": time.time(),
                "updatedAt": time.time(),
                "oldProcess": {"pid": pid, "start_ticks": start_ticks},
                "events": [],
            }
            self._control_transition(
                state,
                operation_id,
                "AUTHORIZED",
                "Authenticated local gateway request accepted for the auth service.",
            )
            state.setdefault("controlOperations", {}).get(
                operation_id, {}
            ).pop("candidateProcess", None)
            self._control_transition(
                state,
                operation_id,
                "QUIESCING",
                "Intentional helper generation recorded before graceful shutdown.",
            )

            old_pidfd = None
            try:
                if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
                    raise OSError("pidfd signaling is unavailable")
                old_pidfd = os.pidfd_open(pid, 0)
                if not self._process_matches(auth, pid, start_ticks, managed=True):
                    raise ProcessLookupError("helper identity changed before signal")
                for requested_signal, timeout in HELPER_SHUTDOWN_STEPS:
                    signal.pidfd_send_signal(old_pidfd, requested_signal)
                    try:
                        process.wait(timeout=timeout)
                        break
                    except subprocess.TimeoutExpired:
                        if requested_signal == signal.SIGKILL:
                            raise
                        self._check_unrelated_processes()
                if process.poll() is None:
                    raise subprocess.TimeoutExpired(process.args, 2.0)
            except (OSError, ProcessLookupError, subprocess.TimeoutExpired) as exc:
                exited = process.poll() is not None
                if exited:
                    self._processes.pop("auth", None)
                    state["services"].pop("auth", None)
                self._control_transition(
                    state,
                    operation_id,
                    "FAILED",
                    f"Helper shutdown failed ({type(exc).__name__}); "
                    + ("the old process exited." if exited else "the old process remains running."),
                )
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "FAILED",
                    "detail": (
                        "Old helper exited; no replacement was started."
                        if exited
                        else "Old helper remains running; no replacement was started."
                    ),
                }
            finally:
                if old_pidfd is not None:
                    os.close(old_pidfd)

            self._processes.pop("auth", None)
            state["services"].pop("auth", None)
            self._control_transition(
                state,
                operation_id,
                "OLD_EXITED",
                "Original helper process exited and was reaped.",
            )
            self._control_transition(
                state,
                operation_id,
                "STARTING",
                "Starting one replacement authentication helper.",
            )
            try:
                health = self._start_service(
                    auth,
                    state,
                    readiness_callback=self._check_unrelated_processes,
                    persist_start_record=False,
                    started_callback=lambda candidate: self._record_restart_candidate(
                        state, operation_id, candidate
                    ),
                )
                replacement = state["services"].get("auth")
                replacement_pid = int(replacement["pid"])
                replacement_ticks = int(replacement["start_ticks"])
                if not self._process_matches(
                    auth,
                    replacement_pid,
                    replacement_ticks,
                    managed=True,
                ):
                    raise StartupFailure(
                        "auth",
                        "Replacement helper identity could not be verified.",
                        self.statuses,
                    )
                listeners = self._listener_details(auth.port)
                if (
                    listeners is None
                    or len(listeners) != 1
                    or listeners[0][0] != auth.host
                    or listeners[0][1] != [replacement_pid]
                    or not health
                ):
                    raise StartupFailure(
                        "auth",
                        "Replacement helper listener or readiness is not verified.",
                        self.statuses,
                    )
                state.setdefault("controlOperations", {}).get(
                    operation_id, {}
                ).pop("candidateProcess", None)
            except (StartupFailure, OSError, ValueError, TypeError) as exc:
                candidate = state["services"].get("auth")
                stopped = (
                    self._stop_record(auth, candidate)
                    if isinstance(candidate, dict)
                    else True
                )
                if stopped:
                    state["services"].pop("auth", None)
                    self._processes.pop("auth", None)
                else:
                    self.statuses["auth"] = "replacement remains managed but unhealthy"
                actual_state = (
                    "no verified helper process"
                    if stopped
                    else "candidate helper remains running and recorded"
                )
                self._control_transition(
                    state,
                    operation_id,
                    "FAILED",
                    f"Replacement readiness failed ({type(exc).__name__}); {actual_state}.",
                )
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "FAILED",
                    "detail": actual_state,
                    "serviceStatus": self.statuses.get("auth", "stopped"),
                }

            self._control_transition(
                state,
                operation_id,
                "SUCCEEDED",
                "Replacement identity, loopback listener, and helper readiness verified.",
            )
            self.statuses["auth"] = f"restarted and ready ({health})"
            return {
                "success": True,
                "operationId": operation_id,
                "status": "SUCCEEDED",
                "detail": "Authentication helper restarted and verified.",
                "serviceStatus": self.statuses["auth"],
            }
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def start_with_control_channel(self) -> dict[str, str]:
        statuses = self.start_all()
        if not self.owned_processes():
            return statuses
        try:
            self.start_control_channel()
        except (StartupFailure, OSError) as failure:
            try:
                recovery = self._rollback_after_control_failure(failure)
            except (StartupFailure, OSError, ValueError, TypeError) as rollback_failure:
                self._startup_recovery_requires_intervention = True
                recovery = {
                    "status": "OPERATOR_INTERVENTION_REQUIRED",
                    "rollbackError": type(rollback_failure).__name__,
                }
            detail = (
                f"Control-channel initialization failed: {type(failure).__name__}: "
                f"{failure}. Rollback status={recovery['status']}."
            )
            if recovery["status"] == "OPERATOR_INTERVENTION_REQUIRED":
                self._startup_recovery_requires_intervention = True
                detail += (
                    " Residual or uncertain manager-started processes or state remain; "
                    "operator intervention is required and supervision remains active."
                )
            else:
                detail += " All manager-started services were stopped and their records cleared."
            raise StartupFailure("manager", detail, dict(self.statuses)) from failure
        return statuses

    def _rollback_after_control_failure(self, failure: Exception) -> dict:
        descriptor = self._lock_state()
        outcomes: dict[str, str] = {}
        persistence_error: str | None = None
        state: dict = {"version": 1, "services": {}}
        try:
            state = self._read_state()
            services = {service.name: service for service in self.services}
            for name in reversed(tuple(self._processes)):
                process = self._processes[name]
                record = state.get("services", {}).get(name)
                if (
                    not isinstance(record, dict)
                    or record.get("pid") != process.pid
                ):
                    outcomes[name] = "not signalled: service record is missing or mismatched"
                    self.statuses[name] = "operator intervention required: identity record is ambiguous"
                    continue
                service = services.get(name)
                if service is None:
                    outcomes[name] = "not signalled: service definition is unavailable"
                    self.statuses[name] = "operator intervention required: service definition is unavailable"
                    continue
                try:
                    stopped = self._stop_record(service, record)
                except (OSError, StartupFailure, ValueError, TypeError) as stop_failure:
                    stopped = False
                    outcomes[name] = (
                        f"not stopped: shutdown failed ({type(stop_failure).__name__})"
                    )
                else:
                    outcomes[name] = (
                        "stopped" if stopped else
                        "not stopped: process identity changed or bounded shutdown failed"
                    )
                if stopped:
                    prior_record = record
                    state["services"].pop(name, None)
                    try:
                        self._write_state(state)
                    except OSError as write_failure:
                        persistence_error = type(write_failure).__name__
                        state["services"][name] = prior_record
                        outcomes[name] = (
                            "process stopped but service-record cleanup failed "
                            f"({persistence_error})"
                        )
                        self.statuses[name] = "operator intervention required: service-record cleanup failed"
                    else:
                        self._processes.pop(name, None)
                        self.statuses[name] = "stopped after control-channel startup failure"
                else:
                    self.statuses[name] = "operator intervention required: process remains or identity is uncertain"

            residual_names = list(self._processes)
            status = (
                "OPERATOR_INTERVENTION_REQUIRED"
                if residual_names or persistence_error
                else "ROLLED_BACK"
            )
            recovery = {
                "status": status,
                "failureService": getattr(failure, "service", "manager"),
                "failureType": type(failure).__name__,
                "failureReason": str(failure),
                "outcomes": outcomes,
            }
            if persistence_error:
                recovery["statePersistenceError"] = persistence_error
            state["startupRecovery"] = recovery
            try:
                self._write_state(state)
            except OSError as write_failure:
                self._startup_recovery_requires_intervention = True
                recovery["status"] = "OPERATOR_INTERVENTION_REQUIRED"
                recovery["statePersistenceError"] = type(write_failure).__name__
                status = recovery["status"]
        except (OSError, StartupFailure, ValueError, TypeError) as state_failure:
            self._startup_recovery_requires_intervention = True
            status = "OPERATOR_INTERVENTION_REQUIRED"
            recovery = {
                "status": status,
                "failureService": getattr(failure, "service", "manager"),
                "failureType": type(failure).__name__,
                "failureReason": str(failure),
                "rollbackError": type(state_failure).__name__,
                "outcomes": outcomes,
            }
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
        if status == "OPERATOR_INTERVENTION_REQUIRED":
            self._startup_recovery_requires_intervention = True
        return recovery

    def _check_unrelated_processes(self, exclude_name: str = "auth") -> None:
        failed = [
            name for name, process in self._processes.items()
            if name != exclude_name and process.poll() is not None
        ]
        if not failed:
            return
        raise StartupFailure(
            failed[0],
            f"An unrelated managed service exited during the {exclude_name} restart.",
            self.statuses,
        )

    def start_all(self) -> dict[str, str]:
        if self.validate_project:
            self._validate_project()
        descriptor = self._lock_state()
        newly_started: list[str] = []
        try:
            state = self._read_state()
            recovery = state.get("startupRecovery")
            if (
                isinstance(recovery, dict)
                and recovery.get("status") == "OPERATOR_INTERVENTION_REQUIRED"
            ):
                raise StartupFailure(
                    "manager",
                    "A previous startup rollback requires operator intervention; "
                    "service adoption is blocked.",
                    self.statuses,
                )
            self._recover_interrupted_control(state)
            quiesce = state.get("gatewayQuiesce")
            if "gatewayQuiesce" in state:
                if not isinstance(quiesce, dict) or quiesce.get("status") != "QUIESCED":
                    raise StartupFailure(
                        "gateway",
                        "Gateway quiesce state is incomplete or ambiguous; explicit operator review is required.",
                        self.statuses,
                    )
                if "gateway" in state["services"]:
                    raise StartupFailure(
                        "gateway",
                        "Gateway is recorded as both quiesced and managed; refusing startup.",
                        self.statuses,
                    )
            lifecycle_state = self._inferred_gateway_lifecycle_state(state)
            if lifecycle_state == "UNKNOWN":
                raise StartupFailure(
                    "gateway",
                    "Persisted gateway lifecycle state is ambiguous; explicit operator review is required.",
                    self.statuses,
                )
            if lifecycle_state == "FAILED" and quiesce is None:
                raise StartupFailure(
                    "gateway",
                    "Gateway lifecycle is FAILED; a manager-controlled recovery transition is required.",
                    self.statuses,
                )
            if (
                lifecycle_state == "RUNNING"
                and quiesce is None
                and "gateway" not in state["services"]
            ):
                raise StartupFailure(
                    "gateway",
                    "Gateway lifecycle says RUNNING but its process record is missing.",
                    self.statuses,
                )
            if (
                isinstance(quiesce, dict)
                and quiesce.get("status") == "QUIESCED"
                and "gatewayLifecycle" not in state
            ):
                self._transition_gateway_lifecycle(
                    state,
                    "QUIESCED",
                    str(quiesce.get("reason") or "Legacy persisted gateway quiesce state."),
                    operation_id=quiesce.get("operationId"),
                )
            by_name = {service.name: service for service in self.services}
            for name in list(state["services"]):
                record = state["services"][name]
                service = by_name.get(name)
                if service is None or not self._process_matches(
                    service, int(record.get("pid", -1)),
                    int(record.get("start_ticks", -1)), managed=True,
                ):
                    state["services"].pop(name, None)
                    if name == "gateway":
                        self._transition_gateway_lifecycle(
                            state,
                            "FAILED",
                            "Recorded gateway process identity was stale or could not be verified.",
                        )
                    continue
                owned_process = self._processes.get(name)
                if owned_process is None or owned_process.pid != int(record["pid"]):
                    raise StartupFailure(
                        "manager",
                        "A live service record belongs to a process this manager "
                        "did not start; adoption requires a validated ownership transfer.",
                        self.statuses,
                    )
            self._write_state(state)

            for service in self.services:
                if service.name == "gateway" and state.get("gatewayQuiesce") is not None:
                    quiesce = state["gatewayQuiesce"]
                    operation_id = quiesce.get("operationId", "UNKNOWN")
                    writers_status = quiesce.get("writersStatus", "NOT_VERIFIED")
                    gateway_status = quiesce.get("gatewayStatus", "STOPPED")
                    self.statuses["gateway"] = (
                        "quiesced (QUIESCED); gateway="
                        f"{gateway_status}; writers={writers_status}; "
                        f"operationId={operation_id}; gateway startup refused"
                    )
                    continue
                record = state["services"].get(service.name)
                if record:
                    health = self._probe(service)
                    if health:
                        if service.name == "gateway":
                            self._transition_gateway_lifecycle(
                                state,
                                "RUNNING",
                                "Manager verified the recorded gateway process and readiness probe.",
                            )
                            self._write_state(state)
                        self.statuses[service.name] = f"managed and healthy ({health})"
                        continue
                    raise StartupFailure(
                        service.name,
                        f"Launcher-managed process is running but failed readiness; inspect {record['log']}.",
                        self.statuses,
                    )

                listener_pids = self._listener_pids(service.port)
                if listener_pids is None:
                    raise StartupFailure(
                        service.name,
                        f"Port {service.port} is occupied but its owner cannot be identified safely.",
                        self.statuses,
                    )
                if listener_pids:
                    if len(listener_pids) == 1 and self._process_matches(service, listener_pids[0]):
                        health = self._probe(service)
                        if health:
                            if service.name == "gateway":
                                self._transition_gateway_lifecycle(
                                    state,
                                    "RUNNING",
                                    "A healthy loopback gateway listener passed identity and readiness checks.",
                                )
                                self._write_state(state)
                            self.statuses[service.name] = f"reused healthy pre-existing service ({health})"
                            continue
                    raise StartupFailure(
                        service.name,
                        f"Port {service.port} is occupied by a service that cannot be verified as healthy ULTRON.",
                        self.statuses,
                    )

                try:
                    if service.name == "gateway":
                        self._transition_gateway_lifecycle(
                            state,
                            "STARTING",
                            "Manager began gateway startup after port and lifecycle checks.",
                        )
                        self._write_state(state)
                    health = self._start_service(service, state)
                except (StartupFailure, OSError):
                    if service.name == "gateway":
                        self._transition_gateway_lifecycle(
                            state,
                            "FAILED",
                            "Gateway startup failed before readiness was verified.",
                        )
                        self._write_state(state)
                    raise
                newly_started.append(service.name)
                if service.name == "gateway":
                    self._transition_gateway_lifecycle(
                        state,
                        "RUNNING",
                        "Gateway process identity, loopback listener, and readiness probe verified.",
                    )
                self._write_state(state)

            return dict(self.statuses)
        except KeyboardInterrupt:
            self._rollback(state if "state" in locals() else {"services": {}}, newly_started)
            raise
        except StartupFailure as failure:
            self.statuses = failure.statuses or self.statuses
            self._rollback(state if "state" in locals() else {"services": {}}, newly_started)
            failure.statuses = dict(self.statuses)
            raise
        except OSError as exc:
            self._rollback(state if "state" in locals() else {"services": {}}, newly_started)
            raise StartupFailure(
                "state",
                f"Filesystem error during startup ({type(exc).__name__}); launcher-owned services were rolled back where possible.",
                dict(self.statuses),
            ) from None
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _rollback(self, state: dict, names: Sequence[str]) -> None:
        services = {service.name: service for service in self.services}
        for name in reversed(names):
            record = state.get("services", {}).get(name)
            service = services.get(name)
            if record is not None and service is not None:
                stopped = self._stop_record(service, record)
                self.statuses[name] = "stopped after partial startup failure" if stopped else "not stopped: process identity changed"
                if stopped:
                    state["services"].pop(name, None)
                    self._processes.pop(name, None)
                    self._write_state(state)

    def owned_processes(self) -> dict[str, int]:
        return {name: process.pid for name, process in self._processes.items()}

    def stop_managed(self) -> dict[str, str]:
        descriptor = self._lock_state()
        result: dict[str, str] = {}
        try:
            state = self._read_state()
            services = {service.name: service for service in self.services}
            for name in reversed(tuple(state["services"])):
                record = state["services"][name]
                service = services.get(name)
                if service is None:
                    result[name] = "not stopped: unknown service record"
                    continue
                if not self._process_matches(
                    service, int(record.get("pid", -1)),
                    int(record.get("start_ticks", -1)), managed=True,
                ):
                    result[name] = "stale record removed; process left untouched"
                    state["services"].pop(name, None)
                    if name == "gateway":
                        self._transition_gateway_lifecycle(
                            state,
                            "FAILED",
                            "Gateway stop found a stale or ambiguous process identity; no signal was sent.",
                        )
                    self._write_state(state)
                    continue
                if name == "gateway":
                    self._transition_gateway_lifecycle(
                        state,
                        "QUIESCING",
                        "Manager is stopping the gateway as part of the explicit stack stop.",
                    )
                    self._write_state(state)
                if self._stop_record(service, record):
                    result[name] = "stopped launcher-managed process"
                    state["services"].pop(name, None)
                    if name == "gateway":
                        self._transition_gateway_lifecycle(
                            state,
                            "STOPPED",
                            "Gateway process stopped through the manager's explicit stack-stop action.",
                        )
                    self._write_state(state)
                else:
                    result[name] = "not stopped: process identity changed or graceful shutdown failed"
                    if name == "gateway":
                        self._transition_gateway_lifecycle(
                            state,
                            "FAILED",
                            "Gateway could not be safely stopped by the manager.",
                        )
                        self._write_state(state)
            return result
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def read_only_status(self) -> dict:
        """Observe manager records and verified listeners without changing state."""
        state_status = "AVAILABLE"
        try:
            directory_info = self.state_directory.lstat()
            if (
                not stat.S_ISDIR(directory_info.st_mode)
                or directory_info.st_uid != os.getuid()
                or stat.S_IMODE(directory_info.st_mode) & 0o077
            ):
                raise StartupFailure(
                    "state",
                    "Launcher state directory has unsafe ownership or permissions.",
                    self.statuses,
                )
            state_info = self.state_path.lstat()
            if (
                not stat.S_ISREG(state_info.st_mode)
                or state_info.st_uid != os.getuid()
                or stat.S_IMODE(state_info.st_mode) & 0o077
            ):
                raise StartupFailure(
                    "state",
                    "Launcher state file has unsafe ownership or permissions.",
                    self.statuses,
                )
            state = self._read_state()
        except FileNotFoundError:
            state_status = "MISSING"
            state = {"version": 1, "services": {}}
        except (StartupFailure, OSError, ValueError, TypeError, AttributeError):
            return {
                "stateStatus": "UNAVAILABLE",
                "managerOwnershipStatus": "NOT_VERIFIED",
                "services": {},
                "gatewayQuiesce": {
                    "status": "UNKNOWN",
                    "writersStatus": "NOT_VERIFIED",
                },
                "writersStatus": "NOT_VERIFIED",
            }

        records = state["services"]
        manager_ownership_status = self._manager_ownership_status()
        service_statuses: dict[str, dict[str, str]] = {}
        service_by_name = {service.name: service for service in self.services}
        for service in self.services:
            has_record = service.name in records
            record = records.get(service.name)
            pid = None
            if not has_record:
                identity_status = "NOT_RECORDED"
            elif not isinstance(record, dict):
                identity_status = "AMBIGUOUS"
            else:
                record_pid = record.get("pid")
                record_start_ticks = record.get("start_ticks")
                if (
                    type(record_pid) is not int
                    or type(record_start_ticks) is not int
                    or record_pid <= 0
                    or record_start_ticks < 0
                ):
                    identity_status = "AMBIGUOUS"
                else:
                    pid = record_pid
                    process_path = Path("/proc") / str(pid)
                    try:
                        process_path.stat()
                    except FileNotFoundError:
                        identity_status = "STALE"
                    except OSError:
                        identity_status = "UNAVAILABLE"
                    else:
                        if _read_proc(pid) is None:
                            identity_status = "UNAVAILABLE"
                        elif self._process_matches(
                            service, pid, record_start_ticks, managed=True
                        ):
                            identity_status = "MATCH"
                        else:
                            identity_status = "MISMATCH"

            try:
                listeners = self._listener_details(service.port)
            except (StartupFailure, OSError, ValueError):
                listeners = None
            if listeners is None:
                listener_status = "UNAVAILABLE"
            elif not listeners:
                listener_status = "NOT_LISTENING"
            elif any(not listener_pids for _, listener_pids in listeners):
                listener_status = "OWNER_UNAVAILABLE"
            elif identity_status != "MATCH":
                listener_status = "UNVERIFIED_OWNER"
            elif (
                len(listeners) == 1
                and listeners[0][0] == service.host
                and listeners[0][1] == [pid]
            ):
                listener_status = "OWNED_BY_RECORDED_PROCESS"
            else:
                listener_status = "CONFLICT"

            if identity_status != "MATCH":
                health_status = "SKIPPED_IDENTITY_UNVERIFIED"
            elif listener_status != "OWNED_BY_RECORDED_PROCESS":
                health_status = "SKIPPED_LISTENER_UNVERIFIED"
            else:
                health_status = (
                    "HEALTHY" if self._probe(service) is not None else "UNHEALTHY"
                )
            service_statuses[service.name] = {
                "identityStatus": identity_status,
                "listenerStatus": listener_status,
                "healthProbe": health_status,
            }

        unknown_record_count = sum(
            name not in service_by_name for name in records
        )
        quiesce = state.get("gatewayQuiesce")
        if "gatewayQuiesce" not in state:
            quiesce_status = "NOT_QUIESCED"
        elif not isinstance(quiesce, dict) or quiesce.get("status") not in {
            "QUIESCE_REQUESTED",
            "QUIESCED",
            "NEEDS_OPERATOR",
        }:
            quiesce_status = "AMBIGUOUS"
        else:
            quiesce_status = quiesce["status"]
            if quiesce_status == "QUIESCED":
                gateway_status = service_statuses.get("gateway", {})
                if "gateway" in records:
                    quiesce_status = "INCONSISTENT"
                elif gateway_status.get("listenerStatus") != "NOT_LISTENING":
                    quiesce_status = "AMBIGUOUS"

        gateway_recovery_evidence = None
        if isinstance(quiesce, dict) and quiesce.get("status") == "QUIESCED":
            gateway = service_by_name.get("gateway")
            try:
                listeners = (
                    self._listener_details(gateway.port)
                    if gateway is not None
                    else None
                )
            except (StartupFailure, OSError, ValueError):
                listeners = None
            listener_status = (
                "UNVERIFIED"
                if listeners is None
                else "NOT_LISTENING"
                if not listeners
                else "CONFLICT"
            )
            gateway_recovery_evidence = {
                "managerOwnershipStatus": manager_ownership_status,
                "gatewayProcessStatus": self._quiesce_process_status(quiesce),
                "listenerStatus": listener_status,
            }

        writers_status = "NOT_VERIFIED"
        attestation_source = None
        if isinstance(quiesce, dict) and quiesce.get("status") == "QUIESCED":
            attestation = quiesce.get("writerAttestation")
            if (
                quiesce.get("writersStatus") == "OPERATOR_ATTESTED"
                and quiesce.get("writerAttestationSource") == "OPERATOR"
                and _validate_operator_attestation(attestation) is None
                and isinstance(attestation, dict)
                and attestation.get("quiesceOperationId") == quiesce.get("operationId")
            ):
                writers_status = "OPERATOR_ATTESTED"
                attestation_source = "OPERATOR"
        result = {
            "stateStatus": (
                "AMBIGUOUS" if unknown_record_count else state_status
            ),
            "managerOwnershipStatus": manager_ownership_status,
            "services": service_statuses,
            "gatewayQuiesce": {
                "status": quiesce_status,
                "writersStatus": writers_status,
            },
            "gatewayLifecycle": {
                "state": self._inferred_gateway_lifecycle_state(state),
                "updatedAt": (
                    state.get("gatewayLifecycle", {}).get("updatedAt")
                    if isinstance(state.get("gatewayLifecycle"), dict)
                    else None
                ),
                "reason": (
                    state.get("gatewayLifecycle", {}).get("reason")
                    if isinstance(state.get("gatewayLifecycle"), dict)
                    else None
                ),
            },
            "writersStatus": writers_status,
        }
        if isinstance(quiesce, dict) and isinstance(quiesce.get("reason"), str):
            result["gatewayQuiesce"]["reason"] = quiesce["reason"]
        if gateway_recovery_evidence is not None:
            result["gatewayQuiesce"].update(gateway_recovery_evidence)
        if attestation_source is not None:
            result["writerAttestationSource"] = attestation_source
        if unknown_record_count:
            result["unknownServiceRecordCount"] = unknown_record_count
        return result

    def _quiesce_gateway(self, operation_id: str) -> dict:
        descriptor = self._lock_state()
        try:
            state = self._read_state()
            transactions = state.setdefault("controlOperations", {})
            previous = transactions.get(operation_id)
            if previous is not None:
                if previous.get("operation") != "quiesce-gateway":
                    return {
                        "success": False,
                        "operationId": operation_id,
                        "status": "OPERATION_ID_CONFLICT",
                        "gatewayStatus": "NOT_CHANGED",
                        "writersStatus": "NOT_VERIFIED",
                        "detail": "Operation ID is already assigned to another control operation.",
                    }
                marker = state.get("gatewayQuiesce")
                stopped = (
                    isinstance(marker, dict)
                    and marker.get("status") == "QUIESCED"
                )
                return {
                    "success": stopped,
                    "operationId": operation_id,
                    "status": "GATEWAY_STOPPED" if stopped else previous.get("status", "UNKNOWN"),
                    "gatewayStatus": "STOPPED" if stopped else "NOT_VERIFIED",
                    "writersStatus": "NOT_VERIFIED",
                    "detail": previous.get("events", [{}])[-1].get("detail", ""),
                }
            if state.get("activeControlOperation") is not None:
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "BUSY",
                    "gatewayStatus": "NOT_CHANGED",
                    "writersStatus": "NOT_VERIFIED",
                    "detail": "Another control operation is active.",
                }

            existing_quiesce = state.get("gatewayQuiesce")
            if "gatewayQuiesce" in state:
                if (
                    isinstance(existing_quiesce, dict)
                    and existing_quiesce.get("status") == "QUIESCED"
                    and "gateway" not in state["services"]
                ):
                    return {
                        "success": True,
                        "operationId": operation_id,
                        "status": "GATEWAY_STOPPED",
                        "gatewayStatus": "STOPPED",
                        "writersStatus": "NOT_VERIFIED",
                        "detail": "Gateway is already quiesced; external writers were not verified.",
                    }
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "NEEDS_OPERATOR",
                    "gatewayStatus": "AMBIGUOUS",
                    "writersStatus": "NOT_VERIFIED",
                    "detail": "Existing gateway quiesce state is incomplete or inconsistent.",
                }

            gateway = next(
                (service for service in self.services if service.name == "gateway"),
                None,
            )
            process = self._processes.get("gateway")
            record = state.get("services", {}).get("gateway")
            try:
                pid = int(record["pid"])
                start_ticks = int(record["start_ticks"])
            except (TypeError, KeyError, ValueError):
                pid = -1
                start_ticks = -1
            if (
                gateway is None
                or process is None
                or process.pid != pid
                or process.poll() is not None
                or not self._process_matches(gateway, pid, start_ticks, managed=True)
            ):
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "REJECTED_STALE",
                    "gatewayStatus": "NOT_CHANGED",
                    "writersStatus": "NOT_VERIFIED",
                    "detail": "Gateway process identity is stale or ambiguous.",
                }

            listeners = self._listener_details(gateway.port)
            if (
                listeners is None
                or len(listeners) != 1
                or listeners[0][0] != gateway.host
                or listeners[0][1] != [pid]
            ):
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "LISTENER_CONFLICT",
                    "gatewayStatus": "NOT_CHANGED",
                    "writersStatus": "NOT_VERIFIED",
                    "detail": "Gateway listener is not uniquely verified on its expected address.",
                }

            transactions[operation_id] = {
                "operation": "quiesce-gateway",
                "service": "gateway",
                "status": "REQUESTED",
                "createdAt": time.time(),
                "updatedAt": time.time(),
                "oldProcess": {"pid": pid, "start_ticks": start_ticks},
                "events": [],
            }
            self._transition_gateway_lifecycle(
                state,
                "QUIESCING",
                "Operator requested gateway-only quiescence; stopping new protocol work.",
                operation_id=operation_id,
            )
            state["gatewayQuiesce"] = {
                "status": "QUIESCE_REQUESTED",
                "operationId": operation_id,
                "process": {"pid": pid, "start_ticks": start_ticks},
                "requestedAt": time.time(),
                "reason": "Operator requested an explicit gateway-only quiesce.",
                "writersStatus": "NOT_VERIFIED",
            }
            self._control_transition(
                state,
                operation_id,
                "QUIESCING",
                "Persistent gateway quiesce intent recorded before graceful shutdown.",
            )

            pidfd = None
            shutdown_failure = None
            try:
                if not callable(getattr(os, "pidfd_open", None)) or not callable(
                    getattr(signal, "pidfd_send_signal", None)
                ):
                    raise OSError("pidfd signaling is unavailable")
                pidfd = os.pidfd_open(pid, 0)
                if not self._process_matches(gateway, pid, start_ticks, managed=True):
                    raise ProcessLookupError("gateway identity changed before signal")
                for requested_signal, timeout in GATEWAY_SHUTDOWN_STEPS:
                    signal.pidfd_send_signal(pidfd, requested_signal)
                    try:
                        process.wait(timeout=timeout)
                        break
                    except subprocess.TimeoutExpired:
                        if requested_signal == signal.SIGTERM:
                            if not self._process_matches(
                                gateway, pid, start_ticks, managed=True
                            ):
                                raise ProcessLookupError(
                                    "gateway identity changed before fallback"
                                )
                            signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                            process.wait(timeout=GATEWAY_KILL_WAIT)
                            break
                if process.poll() is None:
                    raise subprocess.TimeoutExpired(process.args, GATEWAY_KILL_WAIT)
            except (OSError, ProcessLookupError, subprocess.TimeoutExpired) as exc:
                shutdown_failure = exc
            finally:
                if pidfd is not None:
                    os.close(pidfd)

            if shutdown_failure is not None:
                exited = process.poll() is not None
                if exited:
                    self._processes.pop("gateway", None)
                    state["services"].pop("gateway", None)
                quiesce = state["gatewayQuiesce"]
                quiesce["status"] = "NEEDS_OPERATOR"
                quiesce["gatewayStatus"] = (
                    "STOPPED_IDENTITY_EXITED" if exited else "RUNNING_IDENTITY_VERIFIED"
                )
                self._transition_gateway_lifecycle(
                    state,
                    "FAILED",
                    "Gateway shutdown did not complete within the bounded graceful-stop policy.",
                    operation_id=operation_id,
                )
                self._control_transition(
                    state,
                    operation_id,
                    "INTERRUPTED_NEEDS_OPERATOR",
                    f"Gateway quiesce failed ({type(shutdown_failure).__name__}); "
                    + ("the process exited." if exited else "the process remains managed."),
                )
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "NEEDS_OPERATOR",
                    "gatewayStatus": quiesce["gatewayStatus"],
                    "writersStatus": "NOT_VERIFIED",
                    "detail": "Quiesce did not complete cleanly; startup is blocked until operator review.",
                }

            self._processes.pop("gateway", None)
            state["services"].pop("gateway", None)
            try:
                listeners = self._listener_details(gateway.port)
            except StartupFailure:
                listeners = None
            if listeners is None or listeners:
                state["gatewayQuiesce"]["status"] = "NEEDS_OPERATOR"
                state["gatewayQuiesce"]["gatewayStatus"] = "STOPPED_LISTENER_AMBIGUOUS"
                self._transition_gateway_lifecycle(
                    state,
                    "FAILED",
                    "Gateway exited, but its listener could not be verified absent.",
                    operation_id=operation_id,
                )
                self._control_transition(
                    state,
                    operation_id,
                    "INTERRUPTED_NEEDS_OPERATOR",
                    "Original gateway exited, but the expected port is occupied or cannot be inspected.",
                )
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "NEEDS_OPERATOR",
                    "gatewayStatus": "STOPPED_LISTENER_AMBIGUOUS",
                    "writersStatus": "NOT_VERIFIED",
                    "detail": "Original gateway exited, but no-listener verification failed.",
                }

            state["gatewayQuiesce"]["status"] = "QUIESCED"
            state["gatewayQuiesce"]["gatewayStatus"] = "STOPPED"
            state["gatewayQuiesce"]["stoppedAt"] = time.time()
            self._transition_gateway_lifecycle(
                state,
                "QUIESCED",
                "Gateway process stopped and its expected listener is absent; external writers remain unverified.",
                operation_id=operation_id,
            )
            self._control_transition(
                state,
                operation_id,
                "SUCCEEDED",
                "Verified gateway process stopped; external database writers were not verified.",
            )
            return {
                "success": True,
                "operationId": operation_id,
                "status": "GATEWAY_STOPPED",
                "gatewayStatus": "STOPPED",
                "writersStatus": "NOT_VERIFIED",
                "detail": "Gateway stopped; this operation does not verify external CRE writers.",
            }
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _attest_gateway_writers(self, operation_id: str, attestation: object) -> dict:
        descriptor = self._lock_state()
        try:
            state = self._read_state()
            transactions = state.setdefault("controlOperations", {})
            previous = transactions.get(operation_id)
            if previous is not None:
                if previous.get("operation") != "attest-gateway-writers":
                    return {
                        "success": False,
                        "operationId": operation_id,
                        "status": "OPERATION_ID_CONFLICT",
                        "writersStatus": "NOT_VERIFIED",
                        "detail": "Operation ID is already assigned to another control operation.",
                    }
                quiesce = state.get("gatewayQuiesce")
                persisted_attestation = quiesce.get("writerAttestation") if isinstance(quiesce, dict) else None
                replay_valid = (
                    previous.get("status") == "OPERATOR_ATTESTED"
                    and isinstance(quiesce, dict)
                    and quiesce.get("status") == "QUIESCED"
                    and quiesce.get("writersStatus") == "OPERATOR_ATTESTED"
                    and quiesce.get("writerAttestationSource") == "OPERATOR"
                    and persisted_attestation == attestation
                    and isinstance(persisted_attestation, dict)
                    and persisted_attestation.get("quiesceOperationId") == quiesce.get("operationId")
                    and _validate_operator_attestation(persisted_attestation) is None
                    and previous.get("writerAttestationFingerprint")
                    == hashlib.sha256(
                        json.dumps(
                            persisted_attestation,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                    and isinstance(quiesce.get("gatewayRecoveryEvidence"), dict)
                    and quiesce["gatewayRecoveryEvidence"].get("quiesceOperationId")
                    == quiesce.get("operationId")
                    and quiesce["gatewayRecoveryEvidence"].get("attestationOperationId")
                    == operation_id
                )
                if replay_valid:
                    recovery_check = self._verify_quiesced_gateway(state, quiesce)
                    replay_valid = recovery_check["success"]
                return {
                    "success": replay_valid,
                    "operationId": operation_id,
                    "status": "OPERATOR_ATTESTED" if replay_valid else "STALE_ATTESTATION",
                    "writersStatus": "OPERATOR_ATTESTED" if replay_valid else "NOT_VERIFIED",
                    "detail": "Current operator attestation replay accepted." if replay_valid else "The attestation operation no longer matches the active quiesce state.",
                }
            validation_error = _validate_operator_attestation(attestation)
            if validation_error is not None:
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": validation_error,
                    "writersStatus": "NOT_VERIFIED",
                    "detail": "Operator writer attestation failed strict validation.",
                }
            quiesce = state.get("gatewayQuiesce")
            current_quiesce_id = quiesce.get("operationId") if isinstance(quiesce, dict) else None
            if (
                not isinstance(quiesce, dict)
                or quiesce.get("status") != "QUIESCED"
                or current_quiesce_id != attestation["quiesceOperationId"]
            ):
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "STALE_ATTESTATION",
                    "writersStatus": "NOT_VERIFIED",
                    "detail": "Attestation is not bound to the active gateway quiesce operation.",
                }
            recovery_evidence = self._verify_quiesced_gateway(state, quiesce)
            if not recovery_evidence["success"]:
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": recovery_evidence["status"],
                    "writersStatus": "NOT_VERIFIED",
                    "managerOwnershipStatus": recovery_evidence.get(
                        "managerOwnershipStatus", self._manager_ownership_status()
                    ),
                    "detail": recovery_evidence["detail"],
                }
            transactions[operation_id] = {
                "operation": "attest-gateway-writers",
                "service": "gateway",
                "status": "REQUESTED",
                "createdAt": time.time(),
                "updatedAt": time.time(),
                "writerAttestationFingerprint": hashlib.sha256(
                    json.dumps(
                        attestation,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
                "events": [],
            }
            quiesce["writersStatus"] = "OPERATOR_ATTESTED"
            quiesce["writerAttestation"] = dict(attestation)
            quiesce["writerAttestationSource"] = "OPERATOR"
            quiesce["gatewayRecoveryEvidence"] = {
                "quiesceOperationId": quiesce.get("operationId"),
                "attestationOperationId": operation_id,
                **recovery_evidence,
            }
            self._control_transition(
                state,
                operation_id,
                "OPERATOR_ATTESTED",
                "Operator-attested writer inventory and restart holds recorded; "
                "the manager verified only its lease, the old gateway process identity, "
                "and the expected listener. External writer absence was not independently verified.",
            )
            return {
                "success": True,
                "operationId": operation_id,
                "status": "OPERATOR_ATTESTED",
                "writersStatus": "OPERATOR_ATTESTED",
                "writerAttestationSource": "OPERATOR",
                "detail": "Operator attestation recorded for the active gateway quiesce operation.",
            }
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _resume_gateway(self, operation_id: str) -> dict:
        descriptor = self._lock_state()
        try:
            state = self._read_state()
            transactions = state.setdefault("controlOperations", {})
            previous = transactions.get(operation_id)
            if previous is not None:
                if previous.get("operation") != "resume-gateway":
                    return {
                        "success": False,
                        "operationId": operation_id,
                        "status": "OPERATION_ID_CONFLICT",
                        "gatewayStatus": "NOT_CHANGED",
                        "writersStatus": "NOT_VERIFIED",
                        "detail": "Operation ID is already assigned to another control operation.",
                    }
                succeeded = previous.get("status") == "SUCCEEDED"
                attested = previous.get("writersStatus") == "OPERATOR_ATTESTED" and (
                    previous.get("writerAttestationSource") == "OPERATOR"
                )
                current_quiesce = state.get("gatewayQuiesce")
                replay_quiesce_id = previous.get("quiesceOperationId")
                replay_fingerprint = previous.get("writerAttestationFingerprint")
                if succeeded and (
                    not attested
                    or not isinstance(replay_quiesce_id, str)
                    or not replay_quiesce_id
                    or not isinstance(replay_fingerprint, str)
                    or len(replay_fingerprint) != 64
                ):
                    return {
                        "success": False,
                        "operationId": operation_id,
                        "status": "STALE_CONTROL_PROVENANCE",
                        "gatewayStatus": "NOT_CHANGED",
                        "writersStatus": "NOT_VERIFIED",
                        "detail": "Recorded resume success lacks matching attested-quiesce provenance.",
                    }
                if isinstance(current_quiesce, dict):
                    current_attestation = current_quiesce.get("writerAttestation")
                    current_fingerprint = hashlib.sha256(
                        json.dumps(current_attestation, sort_keys=True, separators=(",", ":")).encode("utf-8")
                    ).hexdigest() if isinstance(current_attestation, dict) else None
                    if (
                        current_quiesce.get("operationId") != replay_quiesce_id
                        or current_quiesce.get("resumeOperationId") != operation_id
                        or current_fingerprint != replay_fingerprint
                    ):
                        return {
                            "success": False,
                            "operationId": operation_id,
                            "status": "STALE_CONTROL_PROVENANCE",
                            "gatewayStatus": "NOT_CHANGED",
                            "writersStatus": "OPERATOR_ATTESTED",
                            "writerAttestationSource": "OPERATOR",
                            "detail": "Active quiesce or writer attestation changed after the recorded resume.",
                        }
                return {
                    "success": succeeded,
                    "operationId": operation_id,
                    "status": previous.get("status", "UNKNOWN"),
                    "gatewayStatus": (
                        "READY" if succeeded else "NOT_VERIFIED"
                    ),
                    "writersStatus": "OPERATOR_ATTESTED" if attested else "NOT_VERIFIED",
                    "writerAttestationSource": "OPERATOR" if attested else "NONE",
                    "detail": previous.get("events", [{}])[-1].get("detail", ""),
                }
            if state.get("activeControlOperation") is not None:
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "BUSY",
                    "gatewayStatus": "NOT_CHANGED",
                    "writersStatus": "NOT_VERIFIED",
                    "detail": "Another control operation is active.",
                }
            quiesce = state.get("gatewayQuiesce")
            if (
                not isinstance(quiesce, dict)
                or quiesce.get("status") != "QUIESCED"
                or "gateway" in state["services"]
                or "gateway" in self._processes
            ):
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "REFUSED_STATE",
                    "gatewayStatus": "NOT_CHANGED",
                    "writersStatus": "NOT_VERIFIED",
                    "detail": "Gateway is not in a consistent, explicitly quiesced state.",
                }
            attestation = quiesce.get("writerAttestation")
            validation_error = _validate_operator_attestation(attestation)
            if (
                quiesce.get("writersStatus") != "OPERATOR_ATTESTED"
                or quiesce.get("writerAttestationSource") != "OPERATOR"
                or validation_error is not None
                or not isinstance(attestation, dict)
                or attestation.get("quiesceOperationId") != quiesce.get("operationId")
            ):
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "REFUSED_WRITER_SAFETY",
                    "gatewayStatus": "NOT_CHANGED",
                    "writersStatus": quiesce.get("writersStatus", "NOT_VERIFIED"),
                    "detail": "A matching, current operator writer attestation is required; gateway resume refused.",
                }
            recovery_evidence = quiesce.get("gatewayRecoveryEvidence")
            if (
                not isinstance(recovery_evidence, dict)
                or recovery_evidence.get("quiesceOperationId") != quiesce.get("operationId")
                or recovery_evidence.get("status") != "VERIFIED"
                or recovery_evidence.get("managerOwnershipStatus") != "HELD_BY_THIS_MANAGER"
                or recovery_evidence.get("gatewayProcessStatus") not in {
                    "ORIGINAL_PROCESS_EXITED",
                    "ORIGINAL_PROCESS_EXITED_PID_REUSED",
                }
                or recovery_evidence.get("listenerStatus") != "NOT_LISTENING"
            ):
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "REFUSED_RECOVERY_EVIDENCE",
                    "gatewayStatus": "NOT_CHANGED",
                    "writersStatus": "OPERATOR_ATTESTED",
                    "writerAttestationSource": "OPERATOR",
                    "detail": "Matching manager-verified process and listener evidence is required.",
                }
            attestation_operation_id = recovery_evidence.get(
                "attestationOperationId"
            )
            attestation_operation = (
                transactions.get(attestation_operation_id)
                if isinstance(attestation_operation_id, str)
                else None
            )
            expected_attestation_fingerprint = hashlib.sha256(
                json.dumps(
                    attestation,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if (
                not isinstance(attestation_operation, dict)
                or attestation_operation.get("operation") != "attest-gateway-writers"
                or attestation_operation.get("status") != "OPERATOR_ATTESTED"
                or attestation_operation.get("writerAttestationFingerprint")
                != expected_attestation_fingerprint
            ):
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "REFUSED_WRITER_SAFETY",
                    "gatewayStatus": "NOT_CHANGED",
                    "writersStatus": "NOT_VERIFIED",
                    "detail": "A matching manager-recorded attestation audit event is required.",
                }
            recovery_check = self._verify_quiesced_gateway(state, quiesce)
            if not recovery_check["success"]:
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": recovery_check["status"],
                    "gatewayStatus": "NOT_CHANGED",
                    "writersStatus": "OPERATOR_ATTESTED",
                    "writerAttestationSource": "OPERATOR",
                    "detail": recovery_check["detail"],
                }

            gateway = next(
                (service for service in self.services if service.name == "gateway"),
                None,
            )
            if gateway is None:
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "REFUSED_CONFIGURATION",
                    "gatewayStatus": "NOT_CHANGED",
                    "writersStatus": "NOT_VERIFIED",
                    "detail": "No gateway service is configured.",
                }
            listeners = self._listener_details(gateway.port)
            if listeners is None or listeners:
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "LISTENER_CONFLICT",
                    "gatewayStatus": "NOT_CHANGED",
                    "writersStatus": "NOT_VERIFIED",
                    "detail": "Gateway port is occupied or cannot be safely inspected.",
                }
            for other in self.services:
                if other.name == "gateway":
                    continue
                record = state["services"].get(other.name)
                process = self._processes.get(other.name)
                try:
                    verified = (
                        process is not None
                        and process.poll() is None
                        and process.pid == int(record["pid"])
                        and self._process_matches(
                            other,
                            int(record["pid"]),
                            int(record["start_ticks"]),
                            managed=True,
                        )
                        and self._probe(other)
                    )
                except (KeyError, TypeError, ValueError):
                    verified = False
                if not verified:
                    return {
                        "success": False,
                        "operationId": operation_id,
                        "status": "REJECTED_STALE",
                        "gatewayStatus": "NOT_CHANGED",
                        "writersStatus": "NOT_VERIFIED",
                        "detail": f"Unrelated service identity for {other.name} is stale or unhealthy.",
                    }

            attestation_fingerprint = hashlib.sha256(
                json.dumps(attestation, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            transactions[operation_id] = {
                "operation": "resume-gateway",
                "service": "gateway",
                "status": "REQUESTED",
                "createdAt": time.time(),
                "updatedAt": time.time(),
                "events": [],
                "quiesceOperationId": quiesce.get("operationId"),
                "writerAttestationFingerprint": attestation_fingerprint,
                "writersStatus": "OPERATOR_ATTESTED",
                "writerAttestationSource": "OPERATOR",
                "gatewayRecoveryEvidence": {
                    "quiesceOperationId": quiesce.get("operationId"),
                    "attestationOperationId": recovery_evidence.get(
                        "attestationOperationId"
                    ),
                    **recovery_check,
                },
            }
            self._transition_gateway_lifecycle(
                state,
                "RECOVERING",
                "Live gateway recovery passed operator-attestation and manager process/listener gates.",
                operation_id=operation_id,
            )
            quiesce["status"] = "RESUME_REQUESTED"
            quiesce["resumeOperationId"] = operation_id
            self._control_transition(
                state,
                operation_id,
                "STARTING",
                "Explicit gateway resume requested through the managed launch path.",
            )
            try:
                health = self._start_service(
                    gateway,
                    state,
                    readiness_callback=lambda: self._check_unrelated_processes(
                        "gateway"
                    ),
                    persist_start_record=False,
                    started_callback=lambda candidate: self._record_restart_candidate(
                        state, operation_id, candidate
                    ),
                )
                record = state["services"].get("gateway")
                pid = int(record["pid"])
                start_ticks = int(record["start_ticks"])
                listeners = self._listener_details(gateway.port)
                if (
                    not health
                    or not self._process_matches(
                        gateway, pid, start_ticks, managed=True
                    )
                    or listeners is None
                    or len(listeners) != 1
                    or listeners[0][0] != gateway.host
                    or listeners[0][1] != [pid]
                ):
                    raise StartupFailure(
                        "gateway",
                        "Resumed gateway identity, listener, or readiness was not verified.",
                        self.statuses,
                    )
            except (StartupFailure, OSError, ValueError, TypeError) as exc:
                candidate = state["services"].get("gateway")
                stopped = (
                    self._stop_record(gateway, candidate)
                    if isinstance(candidate, dict)
                    else True
                )
                if stopped:
                    state["services"].pop("gateway", None)
                    self._processes.pop("gateway", None)
                    quiesce["status"] = "QUIESCED"
                    gateway_status = "STOPPED"
                    lifecycle_target = "QUIESCED"
                    lifecycle_reason = "Recovery candidate was stopped; gateway remains quiesced."
                else:
                    quiesce["status"] = "NEEDS_OPERATOR"
                    gateway_status = "CANDIDATE_REMAINS_MANAGED"
                    lifecycle_target = "FAILED"
                    lifecycle_reason = "Recovery candidate could not be safely stopped."
                quiesce["gatewayStatus"] = gateway_status
                self._transition_gateway_lifecycle(
                    state,
                    lifecycle_target,
                    lifecycle_reason,
                    operation_id=operation_id,
                )
                self._control_transition(
                    state,
                    operation_id,
                    "FAILED",
                    f"Gateway resume failed ({type(exc).__name__}); {gateway_status.lower()}.",
                )
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "FAILED",
                    "gatewayStatus": gateway_status,
                    "writersStatus": "OPERATOR_ATTESTED",
                    "writerAttestationSource": "OPERATOR",
                    "detail": "Gateway resume failed; operator attestation and quiesce state were preserved where safe.",
                }

            self._transition_gateway_lifecycle(
                state,
                "RUNNING",
                "Recovered gateway identity, loopback listener, and readiness probe verified.",
                operation_id=operation_id,
            )
            state.pop("gatewayQuiesce", None)
            transactions[operation_id].pop("candidateProcess", None)
            self._control_transition(
                state,
                operation_id,
                "SUCCEEDED",
                "Gateway resumed and verified ready through the managed launch path.",
            )
            return {
                "success": True,
                "operationId": operation_id,
                "status": "GATEWAY_RESUMED",
                "gatewayStatus": self.statuses.get("gateway", "READY"),
                "writersStatus": "OPERATOR_ATTESTED",
                "writerAttestationSource": "OPERATOR",
                "detail": "Gateway is ready; writer absence was operator-attested, not independently verified by software.",
            }
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _restart_gateway(self, operation_id: str) -> dict:
        descriptor = self._lock_state()
        try:
            state = self._read_state()
            transactions = state.setdefault("controlOperations", {})
            quiesce = state.get("gatewayQuiesce")
            if isinstance(quiesce, dict):
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "GATEWAY_QUIESCED",
                    "gatewayStatus": quiesce.get("gatewayStatus", "NOT_VERIFIED"),
                    "writersStatus": "NOT_VERIFIED",
                    "detail": "Gateway restart is refused while persistent quiesce state exists.",
                }
            previous = transactions.get(operation_id)
            if previous is not None:
                return {
                    "success": previous.get("status") == "SUCCEEDED",
                    "operationId": operation_id,
                    "status": previous.get("status", "UNKNOWN"),
                    "detail": previous.get("events", [{}])[-1].get("detail", ""),
                }
            if state.get("activeControlOperation") is not None:
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "BUSY",
                    "detail": "Another control operation is active.",
                }

            service_by_name = {service.name: service for service in self.services}
            gateway = service_by_name.get("gateway")
            if gateway is None or set(self._processes) != set(service_by_name):
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "NOT_MANAGER_OWNED",
                    "detail": "The manager does not own every configured service process.",
                }

            record = state.get("services", {}).get("gateway")
            process = self._processes.get("gateway")
            try:
                pid = int(record["pid"])
                start_ticks = int(record["start_ticks"])
            except (TypeError, KeyError, ValueError):
                pid = -1
                start_ticks = -1
            if (
                process is None
                or process.pid != pid
                or process.poll() is not None
                or not self._process_matches(gateway, pid, start_ticks, managed=True)
            ):
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "REJECTED_STALE",
                    "detail": "Gateway process identity is stale or ambiguous.",
                }

            for other in self.services:
                if other.name == "gateway":
                    continue
                other_record = state["services"].get(other.name)
                other_process = self._processes.get(other.name)
                try:
                    matches = (
                        other_process is not None
                        and other_process.poll() is None
                        and self._process_matches(
                            other,
                            int(other_record["pid"]),
                            int(other_record["start_ticks"]),
                            managed=True,
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    matches = False
                if not matches:
                    return {
                        "success": False,
                        "operationId": operation_id,
                        "status": "REJECTED_STALE",
                        "detail": f"Service identity for {other.name} is stale or ambiguous.",
                    }

            listeners = self._listener_details(gateway.port)
            if (
                listeners is None
                or len(listeners) != 1
                or listeners[0][0] != gateway.host
                or listeners[0][1] != [pid]
            ):
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "LISTENER_CONFLICT",
                    "detail": "The gateway listener is not uniquely verified on its expected address.",
                }

            transactions[operation_id] = {
                "operation": "restart-gateway",
                "service": "gateway",
                "status": "REQUESTED",
                "createdAt": time.time(),
                "updatedAt": time.time(),
                "oldProcess": {"pid": pid, "start_ticks": start_ticks},
                "events": [],
            }
            self._control_transition(
                state,
                operation_id,
                "AUTHORIZED",
                "Local manager request accepted for the gateway service.",
            )
            transactions[operation_id].pop("candidateProcess", None)
            self._transition_gateway_lifecycle(
                state,
                "QUIESCING",
                "Manager began a controlled gateway restart; external writers are not attested by this operation.",
                operation_id=operation_id,
            )
            self._control_transition(
                state,
                operation_id,
                "QUIESCING",
                "Intentional gateway transition recorded before graceful shutdown.",
            )

            old_pidfd = None
            shutdown_failure = None
            try:
                if not callable(getattr(os, "pidfd_open", None)) or not callable(
                    getattr(signal, "pidfd_send_signal", None)
                ):
                    raise OSError("pidfd signaling is unavailable")
                old_pidfd = os.pidfd_open(pid, 0)
                if not self._process_matches(gateway, pid, start_ticks, managed=True):
                    raise ProcessLookupError("gateway identity changed before signal")
                for requested_signal, timeout in GATEWAY_SHUTDOWN_STEPS:
                    signal.pidfd_send_signal(old_pidfd, requested_signal)
                    try:
                        process.wait(timeout=timeout)
                        break
                    except subprocess.TimeoutExpired:
                        self._check_unrelated_processes("gateway")
                if process.poll() is None:
                    if not self._process_matches(
                        gateway, pid, start_ticks, managed=True
                    ):
                        raise ProcessLookupError("gateway identity changed before fallback")
                    signal.pidfd_send_signal(old_pidfd, signal.SIGKILL)
                    process.wait(timeout=GATEWAY_KILL_WAIT)
            except (OSError, ProcessLookupError, subprocess.TimeoutExpired, StartupFailure) as exc:
                shutdown_failure = exc
            finally:
                if old_pidfd is not None:
                    os.close(old_pidfd)

            if shutdown_failure is not None:
                exited = process.poll() is not None
                if exited:
                    self._processes.pop("gateway", None)
                    state["services"].pop("gateway", None)
                actual_state = (
                    "the old gateway exited; no replacement was started"
                    if exited
                    else "the old gateway remains running and recorded"
                )
                self._control_transition(
                    state,
                    operation_id,
                    "FAILED",
                    f"Gateway shutdown failed ({type(shutdown_failure).__name__}); {actual_state}.",
                )
                self._transition_gateway_lifecycle(
                    state,
                    "FAILED",
                    f"Controlled gateway shutdown failed ({type(shutdown_failure).__name__}); {actual_state}.",
                    operation_id=operation_id,
                )
                self._write_state(state)
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "FAILED",
                    "detail": actual_state,
                    "serviceStatus": self.statuses.get("gateway", "stopped"),
                }

            self._processes.pop("gateway", None)
            state["services"].pop("gateway", None)
            self._transition_gateway_lifecycle(
                state,
                "QUIESCED",
                "Original gateway process exited for a controlled restart; this does not attest external writers.",
                operation_id=operation_id,
            )
            self._control_transition(
                state,
                operation_id,
                "OLD_EXITED",
                "Original gateway process exited and was reaped.",
            )
            self._transition_gateway_lifecycle(
                state,
                "RECOVERING",
                "Manager is starting the verified replacement gateway process.",
                operation_id=operation_id,
            )
            self._control_transition(
                state,
                operation_id,
                "STARTING",
                "Starting one replacement gateway process.",
            )
            try:
                health = self._start_service(
                    gateway,
                    state,
                    readiness_callback=lambda: self._check_unrelated_processes(
                        "gateway"
                    ),
                    persist_start_record=False,
                    started_callback=lambda candidate: self._record_restart_candidate(
                        state, operation_id, candidate
                    ),
                )
                replacement = state["services"].get("gateway")
                replacement_pid = int(replacement["pid"])
                replacement_ticks = int(replacement["start_ticks"])
                if not self._process_matches(
                    gateway, replacement_pid, replacement_ticks, managed=True
                ):
                    raise StartupFailure(
                        "gateway",
                        "Replacement gateway identity could not be verified.",
                        self.statuses,
                    )
                listeners = self._listener_details(gateway.port)
                if (
                    listeners is None
                    or len(listeners) != 1
                    or listeners[0][0] != gateway.host
                    or listeners[0][1] != [replacement_pid]
                ):
                    raise StartupFailure(
                        "gateway",
                        "Replacement gateway listener is not uniquely verified.",
                        self.statuses,
                    )
                self._check_unrelated_processes("gateway")
            except (StartupFailure, OSError, ValueError, TypeError) as exc:
                candidate = state["services"].get("gateway")
                stopped = (
                    self._stop_record(gateway, candidate)
                    if isinstance(candidate, dict)
                    else True
                )
                if stopped:
                    state["services"].pop("gateway", None)
                    self._processes.pop("gateway", None)
                else:
                    self.statuses["gateway"] = "replacement remains managed but unhealthy"
                actual_state = (
                    "no verified gateway process"
                    if stopped
                    else "replacement gateway remains running and recorded"
                )
                self._control_transition(
                    state,
                    operation_id,
                    "FAILED",
                    f"Replacement readiness failed ({type(exc).__name__}); {actual_state}.",
                )
                self._transition_gateway_lifecycle(
                    state,
                    "FAILED",
                    f"Controlled gateway replacement failed ({type(exc).__name__}); {actual_state}.",
                    operation_id=operation_id,
                )
                self._write_state(state)
                return {
                    "success": False,
                    "operationId": operation_id,
                    "status": "FAILED",
                    "detail": actual_state,
                    "serviceStatus": self.statuses.get("gateway", "stopped"),
                }

            self._transition_gateway_lifecycle(
                state,
                "RUNNING",
                "Replacement identity, loopback listener, and gateway readiness verified.",
                operation_id=operation_id,
            )
            self._control_transition(
                state,
                operation_id,
                "SUCCEEDED",
                "Replacement identity, loopback listener, and gateway readiness verified.",
            )
            self.statuses["gateway"] = f"restarted and ready ({health})"
            return {
                "success": True,
                "operationId": operation_id,
                "status": "SUCCEEDED",
                "detail": "Gateway restarted and verified.",
                "serviceStatus": self.statuses["gateway"],
            }
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def supervise(self) -> int:
        if not self._processes:
            return 0
        try:
            while self._processes:
                try:
                    operation, operation_id, completion, result, attestation = (
                        self._control_requests.get_nowait()
                    )
                except queue.Empty:
                    pass
                else:
                    try:
                        if operation == "restart-auth":
                            result.update(self._restart_auth(operation_id))
                        elif operation == "restart-gateway":
                            result.update(self._restart_gateway(operation_id))
                        elif operation == "quiesce-gateway":
                            result.update(self._quiesce_gateway(operation_id))
                        elif operation == "resume-gateway":
                            result.update(self._resume_gateway(operation_id))
                        elif operation == "attest-gateway-writers":
                            result.update(self._attest_gateway_writers(operation_id, attestation))
                        elif operation == "stop-stack":
                            services = self.stop_managed()
                            success = not any(
                                value.startswith("not stopped")
                                for value in services.values()
                            )
                            result.update({
                                "success": success,
                                "operationId": operation_id,
                                "status": "STOPPED" if success else "STOP_INCOMPLETE",
                                "services": services,
                                "detail": (
                                    "Manager-owned services stopped."
                                    if success
                                    else "One or more manager-owned services remain; supervision continues."
                                ),
                            })
                            if success:
                                self._shutdown_requested = True
                        else:
                            result.update({
                                "success": False,
                                "operationId": operation_id,
                                "status": "REJECTED",
                                "detail": "Unsupported manager operation.",
                            })
                    except (StartupFailure, OSError, ValueError) as exc:
                        result.update({
                            "success": False,
                            "operationId": operation_id,
                            "status": "FAILED",
                            "detail": f"Control operation failed ({type(exc).__name__}).",
                        })
                    finally:
                        completion.set()

                failed = [
                    name for name, process in self._processes.items()
                    if process.poll() is not None
                ]
                if failed:
                    descriptor = self._lock_state()
                    try:
                        state = self._read_state()
                    finally:
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                        os.close(descriptor)
                    unexpected = [
                        name for name in failed
                        if name in state["services"]
                        and int(state["services"][name].get("pid", -1))
                        == self._processes[name].pid
                    ]
                    if not unexpected:
                        for name in failed:
                            if name not in unexpected:
                                self._processes.pop(name, None)
                        if not self._processes:
                            return 0
                        continue
                    for name in unexpected:
                        print(f"ULTRON service exited unexpectedly: {name}.", file=sys.stderr)
                    self.stop_managed()
                    return 1
                if self._shutdown_requested and not self._processes:
                    return 0
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\nStopping launcher-managed ULTRON services gracefully.")
            self.stop_managed()
        finally:
            for reader in self._reader_threads:
                reader.join(timeout=1.0)
        return 0


def _print_statuses(statuses: dict[str, str]) -> None:
    for name in ("gateway", "frontend", "auth", "voice"):
        if name in statuses:
            print(f"{name:8} {statuses[name]}")
    if statuses.get("frontend", "").find("ready") >= 0 or "reachable" in statuses.get("frontend", ""):
        print("Frontend URL: http://127.0.0.1:8000/")


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage local ULTRON application services.")
    parser.add_argument(
        "action",
        choices=(
            "start",
            "stop",
            "restart-gateway",
            "quiesce-gateway",
            "resume-gateway",
            "attest-gateway-writers",
            "status",
        ),
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--operation-id")
    parser.add_argument("--attestation-file", type=Path)
    arguments = parser.parse_args(argv)
    manager = ServiceManager(arguments.root)
    lease_acquired = False
    try:
        if arguments.action == "status":
            result = manager.read_only_status()
            print(json.dumps(result, sort_keys=True))
            return 0 if result["stateStatus"] in {"AVAILABLE", "MISSING"} else 1
        if arguments.action == "attest-gateway-writers":
            if arguments.attestation_file is None:
                print("Writer attestation file is required.", file=sys.stderr)
                return 2
            try:
                attestation = json.loads(arguments.attestation_file.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                print(f"Cannot read writer attestation ({type(exc).__name__}).", file=sys.stderr)
                return 2
            result = request_gateway_control_operation(
                manager.state_directory,
                arguments.action,
                arguments.operation_id,
                attestation=attestation,
            )
            print(
                f"Gateway {arguments.action} {result.get('status', 'UNKNOWN')}: "
                f"{result.get('detail', '')}; writers={result.get('writersStatus', 'NOT_VERIFIED')}; "
                f"source={result.get('writerAttestationSource', 'NONE')}"
            )
            return 0 if result["success"] else 1
        if arguments.action in {
            "restart-gateway",
            "quiesce-gateway",
            "resume-gateway",
        }:
            result = request_gateway_control_operation(
                manager.state_directory,
                arguments.action,
                arguments.operation_id,
            )
            print(
                f"Gateway {arguments.action} {result.get('status', 'UNKNOWN')}: "
                f"{result.get('detail', '')}; writers={result.get('writersStatus', 'NOT_VERIFIED')}"
            )
            return 0 if result["success"] else 1
        if arguments.action == "stop":
            if manager.control_socket_path.exists():
                try:
                    result = request_gateway_control_operation(
                        manager.state_directory,
                        "stop-stack",
                        arguments.operation_id or f"stop-{secrets.token_hex(16)}",
                    )
                except (OSError, ValueError) as failure:
                    try:
                        manager.acquire_manager_lease()
                    except StartupFailure:
                        print(
                            f"Could not request managed shutdown ({type(failure).__name__}); "
                            "the active manager remains untouched.",
                            file=sys.stderr,
                        )
                        return 1
                    lease_acquired = True
                    statuses = manager.stop_managed()
                else:
                    print(
                        f"ULTRON manager stop {result.get('status', 'UNKNOWN')}: "
                        f"{result.get('detail', '')}"
                    )
                    return 0 if result["success"] else 1
            else:
                manager.acquire_manager_lease()
                lease_acquired = True
                statuses = manager.stop_managed()
            if not statuses:
                print("No launcher-managed ULTRON services are running; existing services were left untouched.")
                return 0
            _print_statuses(statuses)
            return 1 if any("not stopped" in value for value in statuses.values()) else 0
        _configure_manager_signal_policy()
        manager.acquire_manager_lease()
        lease_acquired = True
        statuses = manager.start_with_control_channel()
        _print_statuses(statuses)
        if manager.owned_processes():
            print("ULTRON services are ready. Press Ctrl+C to stop only services started by this launcher.")
            return manager.supervise()
        print("ULTRON services are ready; all healthy services were already running.")
        return 0
    except KeyboardInterrupt:
        print("\nStartup interrupted; launcher-started services were shut down.")
        return 130
    except StartupFailure as failure:
        _print_statuses(failure.statuses)
        print(f"Startup failed at {failure.service}: {failure}.", file=sys.stderr)
        if manager._startup_recovery_requires_intervention and manager.owned_processes():
            print(
                "Manager lease remains held while verified child processes remain under supervision.",
                file=sys.stderr,
            )
            return manager.supervise()
        if failure.service not in {"preflight", "state"}:
            log_path = manager.logs_directory / f"{failure.service}.log"
            if log_path.exists():
                print(f"Relevant log: {log_path}", file=sys.stderr)
        return 1
    finally:
        manager.close_control_channel()
        if lease_acquired:
            manager.release_manager_lease()


if __name__ == "__main__":
    raise SystemExit(_main())
