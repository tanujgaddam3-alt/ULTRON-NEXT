from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
import websockets

from scripts.ultron_service_manager import _probe_gateway
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

    def record_resolved_path():
        path = actual_resolve_content_database_path()
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


def test_unexpected_cre_initialization_failure_still_fails_gateway_startup(
    tmp_path,
    monkeypatch,
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

    with pytest.raises(RuntimeError, match="unexpected failure"):
        asyncio.run(
            interface_gateway.serve_interface(
                _container(tmp_path / "operation.db"),
                host="127.0.0.1",
                port=_free_port(),
            )
        )
