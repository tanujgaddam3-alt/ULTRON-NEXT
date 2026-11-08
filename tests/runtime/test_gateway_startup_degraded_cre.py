from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import sqlite3
import sys
import threading
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
import websockets

from scripts.ultron_service_manager import (
    PROJECT_ROOT,
    Service,
    ServiceManager,
    _gateway_identity,
    _probe_gateway,
    request_gateway_control_operation,
    request_gateway_restart,
)
from ultron.application.content.store import ContentStore
from ultron.application.runtime.operation_store import SQLiteOperationStore
from ultron.application.runtime import interface_gateway


AUTH_TOKEN = "T" * 43


class EventBus:
    def subscribe(self, *_args):
        return None

    def unsubscribe(self, *_args):
        return None


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _create_v7_fixture(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE content_schema_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                schema_version INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO content_schema_meta(singleton, schema_version) VALUES (1, 7)"
        )


def _container(operation_database: Path):
    return SimpleNamespace(
        event_bus=EventBus(),
        runtime=SimpleNamespace(
            state=SimpleNamespace(cycle_count=0, running=True),
        ),
        executor=SimpleNamespace(),
        skills=SimpleNamespace(),
        execution_audit_store=SQLiteOperationStore(operation_database),
    )


async def _connect_and_check(
    port: int,
    operation_database: Path,
    expected_cre_status: str,
    reason: str | None,
):
    server_task = asyncio.create_task(
        interface_gateway.serve_interface(
            _container(operation_database),
            host="127.0.0.1",
            port=port,
        )
    )
    try:
        connection = None
        deadline = asyncio.get_running_loop().time() + 5
        while connection is None:
            if server_task.done():
                await server_task
                raise AssertionError("Gateway server exited before becoming ready.")
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError("Gateway listener did not become ready.")
            try:
                connection = await websockets.connect(
                    f"ws://127.0.0.1:{port}/ultron/stream",
                    open_timeout=0.25,
                    proxy=None,
                )
            except (OSError, TimeoutError):
                await asyncio.sleep(0.05)

        async with connection:
            greeting = json.loads(await asyncio.wait_for(connection.recv(), timeout=2))
            assert greeting["type"] == "connected"
            assert greeting["subsystems"]["core"]["status"] == "AVAILABLE"
            assert greeting["subsystems"]["cre"]["status"] == expected_cre_status
            if reason:
                assert greeting["subsystems"]["cre"]["reason"] == reason
                assert greeting["subsystems"]["cre"]["execution"] == "DISABLED"
            assert await asyncio.to_thread(_probe_gateway, port)

            await connection.send(
                json.dumps(
                    {
                        "action": "AUTHENTICATE",
                        "correlationId": "startup-auth",
                        "credential": AUTH_TOKEN,
                    }
                )
            )
            authentication = json.loads(
                await asyncio.wait_for(connection.recv(), timeout=2)
            )
            assert authentication["status"] == "AUTHENTICATED"

            await connection.send(
                json.dumps(
                    {
                        "action": "CRE_STATUS_REQUEST",
                        "correlationId": "startup-cre-status",
                        "payload": {},
                    }
                )
            )
            response = json.loads(await asyncio.wait_for(connection.recv(), timeout=2))
            assert response["action"] == "CRE_STATUS_RESULT"
            assert response["success"] is (expected_cre_status == "AVAILABLE")
            if reason:
                assert response["reason"] == reason
                assert response["execution"] == "DISABLED"
                assert "counts" not in response
            else:
                assert response["payload"]["status"] == "AVAILABLE"
    finally:
        server_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await server_task


@pytest.mark.parametrize(
    ("schema_version", "use_database_override"),
    [(7, True), (10, True), (10, False)],
)
def test_gateway_listens_with_real_v7_degraded_or_v10_cre(
    tmp_path,
    monkeypatch,
    schema_version,
    use_database_override,
):
    database_path = tmp_path / "backup-copy" / "cre-v7.sqlite"
    database_path.parent.mkdir()
    if schema_version == 7:
        _create_v7_fixture(database_path)
    else:
        store = ContentStore(database_path)
        store.migrate_publishing_attempt_schema()
        store.migrate_private_render_operation_schema()
        store.close()
    before_bytes = database_path.read_bytes()
    if use_database_override:
        monkeypatch.setenv("ULTRON_CRE_DATABASE", str(database_path))
    else:
        monkeypatch.delenv("ULTRON_CRE_DATABASE", raising=False)
        monkeypatch.setattr(
            "ultron.application.content.status.default_content_database_path",
            lambda: database_path,
        )
    monkeypatch.setenv("ULTRON_INTERFACE_AUTH_TOKEN", AUTH_TOKEN)
    monkeypatch.setenv("ULTRON_INTERFACE_AUTH_PRINCIPAL", "fixture-operator")
    monkeypatch.delenv("ULTRON_INTERFACE_CRE_REVIEWER_TOKEN", raising=False)
    monkeypatch.delenv("ULTRON_INTERFACE_CRE_REVIEWER_PRINCIPAL", raising=False)
    resolved_paths = []
    actual_resolve_content_database_path = (
        interface_gateway.resolve_content_database_path
    )

    def record_resolved_path(*, allow_missing=False):
        path = actual_resolve_content_database_path(allow_missing=allow_missing)
        resolved_paths.append(path)
        return path

    monkeypatch.setattr(
        interface_gateway,
        "resolve_content_database_path",
        record_resolved_path,
    )
    runtime_paths = []
    actual_create_content_runtime = interface_gateway.create_content_runtime

    def record_runtime_path(authenticator, *, database_path):
        runtime_paths.append(database_path)
        return actual_create_content_runtime(
            authenticator,
            database_path=database_path,
        )

    monkeypatch.setattr(
        interface_gateway,
        "create_content_runtime",
        record_runtime_path,
    )
    port = _free_port()

    asyncio.run(
        _connect_and_check(
            port,
            tmp_path / "operation.db",
            "UNAVAILABLE" if schema_version == 7 else "AVAILABLE",
            "SCHEMA_VERSION_MISMATCH" if schema_version == 7 else None,
        )
    )

    assert database_path.read_bytes() == before_bytes
    assert runtime_paths == resolved_paths == [database_path.resolve()]
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT schema_version FROM content_schema_meta"
        ).fetchone() == (schema_version,)


def test_service_manager_starts_and_restarts_authenticated_fresh_v10_gateway(
    tmp_path,
    monkeypatch,
):
    database_path = tmp_path / "fresh-cre.sqlite"
    operation_database = tmp_path / "isolated-operation.sqlite"
    content_root = tmp_path / "isolated-content"
    state_directory = tmp_path / "manager-state"
    auth_directory = tmp_path / "auth"
    auth_directory.mkdir(mode=0o700)
    token_path = auth_directory / "interface-token"
    descriptor = os.open(
        token_path,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="ascii") as token_file:
        token_file.write(AUTH_TOKEN)
    port = _free_port()

    monkeypatch.setenv("ULTRON_RUNTIME_ID", "fresh-v10-acceptance")
    monkeypatch.setenv("ULTRON_CRE_DATABASE", str(database_path))
    monkeypatch.setenv("ULTRON_OPERATION_DB", str(operation_database))
    monkeypatch.setenv("ULTRON_CRE_CONTENT_STORAGE_ROOT", str(content_root))
    monkeypatch.setenv("ULTRON_CRE_WORKER_ENABLED", "false")
    monkeypatch.setenv("ULTRON_CRE_YOUTUBE_PUBLISHING_ENABLED", "false")
    monkeypatch.setenv("ULTRON_CRE_PUBLISHING_DRY_RUN_ENABLED", "false")
    monkeypatch.setenv("ULTRON_INTERFACE_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("ULTRON_INTERFACE_BIND_PORT", str(port))
    monkeypatch.setenv("ULTRON_INTERFACE_AUTH_PRINCIPAL", "fixture-operator")

    gateway = Service(
        "gateway",
        "127.0.0.1",
        port,
        (
            str(PROJECT_ROOT / ".venv/bin/python"),
            "-m",
            "ultron.application.runtime.service_entrypoint",
        ),
        PROJECT_ROOT,
        lambda: _probe_gateway(port),
        lambda arguments: _gateway_identity(PROJECT_ROOT, arguments),
    )
    manager = ServiceManager(
        PROJECT_ROOT,
        state_directory=state_directory,
        services=(gateway,),
        readiness_timeout=30,
        poll_interval=0.1,
        auth_config_directory=auth_directory,
        cre_database_path=database_path,
    )

    async def authenticated_status() -> dict:
        async with websockets.connect(
            f"ws://127.0.0.1:{port}/ultron/stream",
            open_timeout=5,
            proxy=None,
        ) as connection:
            greeting = json.loads(await asyncio.wait_for(connection.recv(), timeout=5))
            assert greeting["subsystems"]["cre"]["status"] == "AVAILABLE"
            await connection.send(
                json.dumps(
                    {
                        "action": "AUTHENTICATE",
                        "correlationId": "fresh-v10-auth",
                        "credential": AUTH_TOKEN,
                    }
                )
            )
            authentication = json.loads(
                await asyncio.wait_for(connection.recv(), timeout=5)
            )
            assert authentication["status"] == "AUTHENTICATED"
            await connection.send(
                json.dumps(
                    {
                        "action": "CRE_STATUS_REQUEST",
                        "correlationId": "fresh-v10-status",
                        "payload": {},
                    }
                )
            )
            response = json.loads(await asyncio.wait_for(connection.recv(), timeout=5))
            assert response["payload"]["publishing"]["mode"] == "MOCK_ONLY"
            return response

    manager.acquire_manager_lease()
    supervisor = None
    try:
        statuses = manager.start_with_control_channel()
        assert "started and ready" in statuses["gateway"]
        supervisor = threading.Thread(target=manager.supervise, daemon=True)
        supervisor.start()
        assert asyncio.run(authenticated_status())["payload"]["status"] == "AVAILABLE"
        assert database_path.is_file()
        with sqlite3.connect(database_path) as connection:
            assert connection.execute(
                "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
            ).fetchone() == (10,)

        restarted = request_gateway_restart(
            state_directory,
            "fresh-v10-gateway-restart",
            timeout=60,
        )
        assert restarted["success"] is True, restarted
        assert asyncio.run(authenticated_status())["payload"]["status"] == "AVAILABLE"
        with sqlite3.connect(database_path) as connection:
            assert connection.execute(
                "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
            ).fetchone() == (10,)
        assert operation_database.is_file()
        assert content_root.is_dir()
    finally:
        if supervisor is not None and supervisor.is_alive():
            try:
                request_gateway_control_operation(
                    state_directory,
                    "stop-stack",
                    "fresh-v10-gateway-stop",
                    timeout=30,
                )
            except OSError:
                manager.stop_managed()
            supervisor.join(timeout=20)
        manager.close_control_channel()
        manager.release_manager_lease()


def test_unexpected_cre_initialization_failure_still_fails_gateway_startup(
    tmp_path,
    monkeypatch,
    caplog,
):
    database_path = tmp_path / "data" / "ultron_cre.db"
    _create_v7_fixture(database_path)
    monkeypatch.setattr(
        "ultron.application.content.status.default_content_database_path",
        lambda: database_path,
    )
    monkeypatch.setenv("ULTRON_CRE_DATABASE", str(database_path))
    monkeypatch.setenv("ULTRON_INTERFACE_AUTH_TOKEN", AUTH_TOKEN)
    monkeypatch.delenv("ULTRON_INTERFACE_CRE_REVIEWER_TOKEN", raising=False)
    monkeypatch.delenv("ULTRON_INTERFACE_CRE_REVIEWER_PRINCIPAL", raising=False)
    monkeypatch.setattr(
        interface_gateway,
        "create_content_runtime",
        lambda _authenticator, *, database_path: (
            _ for _ in ()
        ).throw(RuntimeError("unexpected failure")),
    )
    container = _container(tmp_path / "operation.db")
    operation_store = container.execution_audit_store

    with pytest.raises(RuntimeError, match="unexpected failure"):
        asyncio.run(
            interface_gateway.serve_interface(
                container,
                host="127.0.0.1",
                port=_free_port(),
            )
        )

    assert "phase=content_runtime_initialization" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    assert "unexpected failure" not in caplog.text
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        operation_store._connection.execute("SELECT 1")


def test_gateway_listener_startup_failure_logs_phase_and_closes_operation_store(
    tmp_path,
    monkeypatch,
    caplog,
):
    monkeypatch.delenv("ULTRON_INTERFACE_AUTH_TOKEN", raising=False)
    container = _container(tmp_path / "operation.db")
    operation_store = container.execution_audit_store
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()

    try:
        with pytest.raises(OSError):
            asyncio.run(
                interface_gateway.serve_interface(
                    container,
                    host="127.0.0.1",
                    port=listener.getsockname()[1],
                )
            )
    finally:
        listener.close()

    assert "phase=websocket_listener_startup" in caplog.text
    assert "exception_type=OSError" in caplog.text
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        operation_store._connection.execute("SELECT 1")


def test_gateway_recovery_failure_logs_phase_and_closes_operation_store(
    tmp_path,
    monkeypatch,
    caplog,
):
    monkeypatch.delenv("ULTRON_INTERFACE_AUTH_TOKEN", raising=False)
    container = _container(tmp_path / "operation.db")
    operation_store = container.execution_audit_store

    def fail_recovery(_store):
        raise RuntimeError("private recovery detail")

    monkeypatch.setattr(
        SQLiteOperationStore,
        "recover_gateway_state",
        fail_recovery,
    )

    with pytest.raises(RuntimeError, match="private recovery detail"):
        asyncio.run(
            interface_gateway.serve_interface(
                container,
                host="127.0.0.1",
                port=_free_port(),
            )
        )

    assert "phase=gateway_initialization" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    assert "private recovery detail" not in caplog.text
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        operation_store._connection.execute("SELECT 1")


def test_gateway_quiesce_rejects_new_work_and_waits_for_active_request(tmp_path):
    container = _container(tmp_path / "gateway-operations.db")
    operation_store = container.execution_audit_store
    gateway = interface_gateway.InterfaceGateway(
        container,
        operation_store=operation_store,
    )

    class Socket:
        def __init__(self):
            self.messages = []

        async def send(self, message):
            self.messages.append(json.loads(message))

    async def exercise_quiesce():
        started = asyncio.Event()
        release = asyncio.Event()

        async def active_request(_websocket, _message):
            started.set()
            await release.wait()

        gateway._handle_request = active_request
        active_socket = Socket()
        active_task = asyncio.create_task(
            gateway.handle(active_socket, {"action": "READ"})
        )
        await started.wait()
        gateway.begin_quiesce()

        rejected_socket = Socket()
        await gateway.handle(rejected_socket, {"action": "WRITE"})
        assert rejected_socket.messages[0]["status"] == "GATEWAY_QUIESCED"
        assert not await gateway.wait_for_inflight_requests(0.01)

        release.set()
        await active_task
        assert await gateway.wait_for_inflight_requests(0.1)

    try:
        asyncio.run(exercise_quiesce())
    finally:
        operation_store.close()


def test_isolated_managed_gateway_handshake_and_identity_do_not_touch_live_cre(
    tmp_path,
    monkeypatch,
):
    project_root = Path(__file__).resolve().parents[2]
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    isolated_operation_db = tmp_path / "operations.db"
    isolated_cre_db = tmp_path / "cre-fixture.db"
    content_store = ContentStore(isolated_cre_db)
    content_store.migrate_publishing_attempt_schema()
    content_store.migrate_private_render_operation_schema()
    content_store.close()

    live_cre_db = (project_root / "data" / "ultron_cre.db").resolve()
    port = _free_port()
    marker = "isolated-managed-gateway"
    gateway_script = textwrap.dedent(
        f"""
        import asyncio
        import os
        import signal
        import sys
        from pathlib import Path
        from types import SimpleNamespace

        sys.path.insert(0, {str(project_root)!r})
        live_database = {str(live_cre_db)!r}
        protected_paths = {{live_database, live_database + "-wal", live_database + "-shm"}}

        def deny_live_cre_access(event, arguments):
            if event == "sqlite3.connect" and arguments:
                target = os.path.realpath(str(arguments[0]))
                if target in protected_paths:
                    raise PermissionError("isolated test denied live CRE database access")
            if event == "open" and arguments:
                try:
                    target = os.path.realpath(os.fsdecode(arguments[0]))
                except (TypeError, ValueError):
                    return
                if target in protected_paths:
                    raise PermissionError("isolated test denied live CRE database access")

        sys.addaudithook(deny_live_cre_access)
        for event in ("open", "sqlite3.connect"):
            for protected_path in protected_paths:
                try:
                    deny_live_cre_access(event, (protected_path,))
                except PermissionError:
                    continue
                raise RuntimeError(
                    "live CRE database access guard did not reject a protected path"
                )

        from ultron.application.runtime.operation_store import SQLiteOperationStore
        from ultron.application.runtime import interface_gateway

        class EventBus:
            def subscribe(self, *_args):
                return None
            def unsubscribe(self, *_args):
                return None

        database = Path(os.environ["ULTRON_OPERATION_DB"])
        container = SimpleNamespace(
            event_bus=EventBus(),
            runtime=SimpleNamespace(
                state=SimpleNamespace(cycle_count=0, running=True),
            ),
            executor=SimpleNamespace(),
            skills=SimpleNamespace(),
            execution_audit_store=SQLiteOperationStore(database),
        )

        async def serve():
            stop = asyncio.Event()
            asyncio.get_running_loop().add_signal_handler(signal.SIGINT, stop.set)
            await interface_gateway.serve_interface(
                container,
                host="127.0.0.1",
                port={port},
                shutdown_event=stop,
            )

        asyncio.run(serve())
        """
    )
    service = Service(
        "gateway",
        "127.0.0.1",
        port,
        (
            sys.executable,
            "-u",
            "-c",
            gateway_script,
            marker,
        ),
        tmp_path,
        lambda: _probe_gateway(port),
        lambda arguments: marker in arguments,
    )
    state_directory = tmp_path / "manager-state"
    manager = ServiceManager(
        tmp_path,
        state_directory=state_directory,
        services=[service],
        readiness_timeout=8,
        poll_interval=0.05,
        validate_project=False,
    )
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(isolated_home),
        "PYTHONPATH": str(project_root),
        "PYTHONDONTWRITEBYTECODE": "1",
        "ULTRON_OPERATION_DB": str(isolated_operation_db),
        "ULTRON_CRE_DATABASE": str(isolated_cre_db),
        "ULTRON_INTERFACE_AUTH_TOKEN": AUTH_TOKEN,
        "ULTRON_INTERFACE_AUTH_PRINCIPAL": "isolated-operator",
    }
    monkeypatch.setattr(manager, "_environment_for", lambda _service: environment)
    manager.acquire_manager_lease()
    try:
        statuses = manager.start_all()
        pid = manager.owned_processes()["gateway"]
        record = manager._read_state()["services"]["gateway"]

        assert "ready" in statuses["gateway"]
        assert manager._process_matches(
            service, pid, record["start_ticks"], managed=True
        )
        listeners = manager._listener_details(port)
        assert listeners == [("127.0.0.1", [pid])]

        async def handshake():
            async with websockets.connect(
                f"ws://127.0.0.1:{port}/ultron/stream",
                open_timeout=2,
                proxy=None,
            ) as connection:
                greeting = json.loads(await asyncio.wait_for(connection.recv(), 2))
                assert greeting["type"] == "connected"
                assert greeting["authenticationRequired"] is True
                assert greeting["subsystems"]["core"]["status"] == "AVAILABLE"

        asyncio.run(handshake())
        assert manager._read_state()["gatewayLifecycle"]["state"] == "RUNNING"
        assert str(live_cre_db) not in environment.values()
        assert isolated_cre_db.resolve() != live_cre_db
        assert isolated_cre_db.exists()
    finally:
        manager.stop_managed()
        manager.release_manager_lease()
