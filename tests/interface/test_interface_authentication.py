import asyncio
import json
import os
import secrets
import socket
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import websockets
from websockets.exceptions import ConnectionClosed

from ultron.application.runtime.interface_gateway import (
    ENDPOINT_PATH,
    serve_interface,
)
from ultron.application.runtime.operation_store import SQLiteOperationStore
from ultron.application.runtime.state import RuntimeState
from ultron.application.content.store import ContentStore
from ultron.core.event_bus import EventBus
from ultron.domain.plan import Plan, PlanStep
from ultron.execution.executor import Executor
from ultron.security.identity.authorization import (
    AuthorizationContext,
    resource_fingerprint,
)
from ultron.security.identity.authenticator import WebSocketAuthenticator
from ultron.skills.financial.skill import FinancialSkill
from ultron.skills.manager import SkillManager
from ultron.skills.terminal.skill import TerminalSkill


class SessionSocket:
    pass


def test_environment_authenticator_requires_a_strong_server_side_token(
    monkeypatch,
):
    monkeypatch.delenv(WebSocketAuthenticator.TOKEN_ENV, raising=False)
    monkeypatch.delenv(WebSocketAuthenticator.CRE_REVIEWER_TOKEN_ENV, raising=False)
    monkeypatch.delenv(WebSocketAuthenticator.CRE_REVIEWER_PRINCIPAL_ENV, raising=False)
    assert WebSocketAuthenticator.from_environment() is None

    monkeypatch.setenv(WebSocketAuthenticator.TOKEN_ENV, "too-short")
    with pytest.raises(ValueError, match="high-entropy URL-safe token"):
        WebSocketAuthenticator.from_environment()

    token = secrets.token_urlsafe(32)
    monkeypatch.setenv(WebSocketAuthenticator.TOKEN_ENV, token)
    monkeypatch.setenv(WebSocketAuthenticator.PRINCIPAL_ENV, "configured-operator")
    authorizer = WebSocketAuthenticator.from_environment()

    assert authorizer is not None
    assert authorizer.principal == "configured-operator"

def test_distinct_reviewer_credential_authenticates_as_its_configured_principal(
    monkeypatch,
):
    operator_token = secrets.token_urlsafe(32)
    reviewer_token = secrets.token_urlsafe(32)
    monkeypatch.setenv(WebSocketAuthenticator.TOKEN_ENV, operator_token)
    monkeypatch.setenv(WebSocketAuthenticator.PRINCIPAL_ENV, "asset-owner")
    monkeypatch.setenv(WebSocketAuthenticator.CRE_REVIEWER_TOKEN_ENV, reviewer_token)
    monkeypatch.setenv(WebSocketAuthenticator.CRE_REVIEWER_PRINCIPAL_ENV, "rights-reviewer")

    authorizer = WebSocketAuthenticator.from_environment()
    assert authorizer is not None
    owner_socket = SessionSocket()
    reviewer_socket = SessionSocket()

    assert authorizer.authenticate(owner_socket, operator_token)["principal"] == "asset-owner"
    assert authorizer.authenticate(reviewer_socket, reviewer_token)["principal"] == "rights-reviewer"
    assert authorizer.additional_principals == ("rights-reviewer",)


def test_reviewer_credential_must_be_complete_and_distinct(monkeypatch):
    monkeypatch.setenv(WebSocketAuthenticator.TOKEN_ENV, secrets.token_urlsafe(32))
    monkeypatch.setenv(WebSocketAuthenticator.CRE_REVIEWER_TOKEN_ENV, secrets.token_urlsafe(32))
    monkeypatch.delenv(WebSocketAuthenticator.CRE_REVIEWER_PRINCIPAL_ENV, raising=False)
    with pytest.raises(ValueError, match="must be configured together"):
        WebSocketAuthenticator.from_environment()

    token = secrets.token_urlsafe(32)
    with pytest.raises(ValueError, match="unique credentials"):
        WebSocketAuthenticator(
            token,
            "asset-owner",
            additional_credentials={"rights-reviewer": token},
        )
    shared_additional_token = secrets.token_urlsafe(32)
    with pytest.raises(ValueError, match="unique credentials"):
        WebSocketAuthenticator(
            secrets.token_urlsafe(32),
            "asset-owner",
            additional_credentials={
                "reviewer-one": shared_additional_token,
                "reviewer-two": shared_additional_token,
            },
        )


def test_credentials_sessions_expiry_revocation_and_approval_binding():
    token = secrets.token_urlsafe(32)
    authorizer = WebSocketAuthenticator(token, "configured-operator")
    first_socket = SessionSocket()
    other_socket = SessionSocket()

    assert authorizer.authenticate(first_socket, None) is None
    tampered_token = token[:-1] + ("A" if token[-1] != "A" else "B")
    assert authorizer.authenticate(first_socket, tampered_token) is None
    assert not authorizer.is_authenticated(first_socket)

    session = authorizer.authenticate(first_socket, token)
    assert session is not None
    assert session["principal"] == "configured-operator"
    assert "credential" not in session

    operation = {
        "operationId": "operation-1",
        "correlationId": "dispatch-1",
        "requestFingerprint": "fingerprint-1",
    }
    grant = authorizer.authorize(
        websocket=first_socket,
        operation=operation,
        purpose="execute",
        request_correlation_id="approve-1",
        action="run_command",
        resource_fingerprint="command-scope-fingerprint",
    )
    assert grant is not None
    assert authorizer.validate(
        grant=grant,
        operation=operation,
        purpose="execute",
        action="run_command",
        resource_fingerprint="command-scope-fingerprint",
    )
    assert not authorizer.validate(
        grant=grant,
        operation=operation,
        purpose="execute",
        action="write_file",
        resource_fingerprint="command-scope-fingerprint",
    )
    assert not authorizer.validate(
        grant=grant,
        operation=operation,
        purpose="execute",
        action="run_command",
        resource_fingerprint="different-resource",
    )

    other_session = authorizer.authenticate(other_socket, token)
    assert other_session is not None
    assert other_session["sessionId"] != session["sessionId"]
    authorizer.revoke(first_socket)
    assert not authorizer.validate(
        grant=grant,
        operation=operation,
        purpose="execute",
        action="run_command",
        resource_fingerprint="command-scope-fingerprint",
    )

    expiring = WebSocketAuthenticator(
        token,
        "configured-operator",
        session_ttl_seconds=1,
    )
    expiring_socket = SessionSocket()
    expiring.authenticate(expiring_socket, token)
    assert expiring.is_authenticated(expiring_socket)
    time.sleep(1.05)
    assert not expiring.is_authenticated(expiring_socket)

    blocked_socket = SessionSocket()
    blocked = WebSocketAuthenticator(token, "configured-operator")
    for _ in range(blocked.MAX_AUTH_FAILURES):
        assert blocked.authenticate(blocked_socket, "invalid") is None
    assert blocked.authenticate(blocked_socket, token) is None


def test_authenticated_activity_refreshes_idle_timeout_but_not_absolute_lifetime(
    monkeypatch,
):
    token = secrets.token_urlsafe(32)
    authorizer = WebSocketAuthenticator(
        token,
        session_ttl_seconds=30,
        idle_ttl_seconds=10,
    )
    now = [100.0]
    monkeypatch.setattr("ultron.security.identity.authenticator.time.monotonic", lambda: now[0])
    websocket = SessionSocket()
    session = authorizer.authenticate(websocket, token)
    assert session is not None

    now[0] = 109.0
    assert authorizer.touch(websocket)
    now[0] = 118.0
    assert authorizer.touch(websocket)

    now[0] = 127.0
    assert authorizer.touch(websocket)
    assert authorizer.seconds_until_expiry(websocket) == 3.0
    now[0] = 130.0
    assert not authorizer.is_authenticated(websocket)
    assert not authorizer.touch(websocket)


def test_unauthenticated_or_expired_activity_cannot_refresh_session(monkeypatch):
    token = secrets.token_urlsafe(32)
    authorizer = WebSocketAuthenticator(
        token,
        session_ttl_seconds=20,
        idle_ttl_seconds=5,
    )
    now = [200.0]
    monkeypatch.setattr("ultron.security.identity.authenticator.time.monotonic", lambda: now[0])
    unauthenticated = SessionSocket()
    assert not authorizer.touch(unauthenticated)
    assert authorizer.authenticate(unauthenticated, "invalid") is None
    assert not authorizer.touch(unauthenticated)

    websocket = SessionSocket()
    authorizer.authenticate(websocket, token)
    now[0] = 205.0
    assert not authorizer.touch(websocket)
    assert not authorizer.is_authenticated(websocket)


def test_signed_authorization_context_binds_exact_action_and_parameters():
    token = secrets.token_urlsafe(32)
    authorizer = WebSocketAuthenticator(token, "context-binding-operator")
    websocket = SessionSocket()
    authorizer.authenticate(websocket, token)
    parameters = {"subject": "pwd"}
    operation = {
        "operationId": "bound-operation",
        "correlationId": "bound-correlation",
        "requestFingerprint": "bound-request",
    }
    scope = resource_fingerprint("run_command", parameters)

    grant = authorizer.authorize(
        websocket=websocket,
        operation=operation,
        purpose="execute",
        request_correlation_id="bound-request-correlation",
        action="run_command",
        resource_fingerprint=scope,
    )
    assert grant is not None
    context = AuthorizationContext.from_grant(grant)

    assert authorizer.validate_context(
        context,
        action="run_command",
        parameters=parameters,
        operation_id=operation["operationId"],
        correlation_id=operation["correlationId"],
    )
    assert not authorizer.validate_context(
        context,
        action="run_command",
        parameters={"subject": "ls"},
        operation_id=operation["operationId"],
        correlation_id=operation["correlationId"],
    )

    tampered_grant = dict(grant)
    tampered_grant["action"] = "write_file"
    assert not authorizer.validate(
        grant=tampered_grant,
        operation=operation,
        purpose="execute",
        action="write_file",
        resource_fingerprint=scope,
    )


def test_paper_trade_requires_claimed_signed_dispatch_and_records_replay_denial(
    tmp_path,
):
    class PaperAgentStub:
        def __init__(self):
            self.calls = 0

        def paper_trade(self, *, analysis):
            self.calls += 1
            return {"success": True, "status": "paper-test"}

    token = secrets.token_urlsafe(32)
    authority = WebSocketAuthenticator(token, "paper-operator")
    websocket = SessionSocket()
    authority.authenticate(websocket, token)
    parameters = {"symbol": "ABC", "analysis": {"symbol": "ABC"}}
    operation = {
        "operationId": "paper-order-operation",
        "correlationId": "paper-order-correlation",
        "requestFingerprint": "paper-order-request",
    }
    grant = authority.authorize(
        websocket=websocket,
        operation=operation,
        purpose="execute",
        request_correlation_id="paper-order-request-correlation",
        action="paper_trade",
        resource_fingerprint=resource_fingerprint(
            "paper_trade",
            parameters,
        ),
    )
    assert grant is not None
    store = SQLiteOperationStore(tmp_path / "paper-dispatch.db")
    now = datetime.now(UTC).isoformat()
    store.create(
        {
            "operationId": operation["operationId"],
            "correlationId": operation["correlationId"],
            "requestFingerprint": operation["requestFingerprint"],
            "lifecycleState": "EXECUTING",
            "createdAt": now,
            "updatedAt": now,
        }
    )
    persisted_grant = dict(grant)
    persisted_grant["correlationId"] = grant["requestCorrelationId"]
    assert store.claim_authorization_grant(persisted_grant)
    context = AuthorizationContext.from_grant(grant)

    manager = SkillManager()
    financial_agent = PaperAgentStub()
    manager.register(FinancialSkill(financial_agent))
    executor = Executor(
        skills=manager,
        authorization_authority=authority,
        operation_store=store,
    )
    result = executor.execute(
        Plan(
            goal="Test explicitly authorized paper dispatch",
            steps=[
                PlanStep(
                    order=1,
                    action="paper_trade",
                    parameters=parameters,
                )
            ],
        ),
        operation_id=context.operation_id,
        correlation_id=context.operation_correlation_id,
        authorization_context=context,
    )

    assert result["success"] is True
    assert financial_agent.calls == 1

    with pytest.raises(PermissionError, match="unclaimed, expired, or replayed"):
        manager.execute(
            "paper_trade",
            authorization_context=context,
            **parameters,
        )
    assert financial_agent.calls == 1
    assert store.execution_audit()[-1]["event"] == "SKILL_PERMISSION_DENIED"
    store.close()


def test_executor_blocks_context_scope_mismatch_before_skill_invocation(
    tmp_path,
):
    token = secrets.token_urlsafe(32)
    authorizer = WebSocketAuthenticator(token, "executor-context-operator")
    websocket = SessionSocket()
    authorizer.authenticate(websocket, token)
    approved_parameters = {"command": "pwd"}
    operation = {
        "operationId": "executor-bound-operation",
        "correlationId": "executor-bound-correlation",
        "requestFingerprint": "executor-bound-request",
    }
    grant = authorizer.authorize(
        websocket=websocket,
        operation=operation,
        purpose="execute",
        request_correlation_id="executor-bound-request-correlation",
        action="run_command",
        resource_fingerprint=resource_fingerprint(
            "run_command",
            approved_parameters,
        ),
    )
    assert grant is not None
    context = AuthorizationContext.from_grant(grant)

    class CountingTerminalSkill(TerminalSkill):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def execute(self, action, **kwargs):
            self.calls += 1
            return super().execute(action, **kwargs)

    skills = SkillManager()
    skills.registry.unregister("run_command")
    terminal = CountingTerminalSkill()
    skills.register(terminal)
    operation_store = SQLiteOperationStore(
        tmp_path / "executor-context.db"
    )
    executor = Executor(
        skills=skills,
        authorization_authority=authorizer,
        operation_store=operation_store,
    )

    result = executor.execute(
        Plan(
            goal="Run the differently scoped command",
            steps=[
                PlanStep(
                    order=1,
                    action="run_command",
                    parameters={"command": "ls"},
                )
            ],
        ),
        operation_id=context.operation_id,
        correlation_id=context.operation_correlation_id,
        authorization_context=context,
    )

    assert result["success"] is False
    assert result["results"][0]["status"] == "BLOCKED"
    assert terminal.calls == 0
    operation_store.close()


async def start_authenticated_server(
    monkeypatch,
    tmp_path,
    *,
    ttl_seconds=60,
    token_configured=True,
    injected_authorizer=None,
):
    token = secrets.token_urlsafe(32) if token_configured else None
    database = tmp_path / "authenticated-interface.db"
    if token is not None:
        monkeypatch.setenv("ULTRON_INTERFACE_AUTH_TOKEN", token)
        monkeypatch.setenv(
            "ULTRON_INTERFACE_AUTH_PRINCIPAL",
            "websocket-integration-operator",
        )
    else:
        monkeypatch.delenv("ULTRON_INTERFACE_AUTH_TOKEN", raising=False)
    monkeypatch.setenv(
        "ULTRON_INTERFACE_SESSION_TTL_SECONDS",
        str(ttl_seconds),
    )
    monkeypatch.setenv("ULTRON_OPERATION_DB", str(database))

    skills = SkillManager()

    class CountingExecutor(Executor):
        def __init__(self):
            super().__init__(skills=skills)
            self.calls = 0

        def execute(self, plan, **kwargs):
            self.calls += 1
            return super().execute(plan, **kwargs)

    executor = CountingExecutor()
    container = SimpleNamespace(
        event_bus=EventBus(),
        runtime=SimpleNamespace(state=RuntimeState()),
        executor=executor,
        skills=skills,
        interface_approval_authorizer=injected_authorizer,
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    server_task = asyncio.create_task(
        serve_interface(container, host="127.0.0.1", port=port)
    )
    connection = None
    for _ in range(40):
        try:
            connection = await websockets.connect(
                f"ws://127.0.0.1:{port}{ENDPOINT_PATH}"
            )
            break
        except OSError:
            await asyncio.sleep(0.05)
    if connection is None:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        raise AssertionError("Authenticated WebSocket server did not start.")

    return token, database, connection, server_task, port, executor


async def authenticate_socket(connection, token, *, expect_connected=True):
    if expect_connected:
        connected = json.loads(await connection.recv())
        assert connected["type"] == "connected"
        assert connected["authenticationRequired"] is True
    await connection.send(
        json.dumps({
            "action": "AUTHENTICATE",
            "correlationId": "authenticate",
            "credential": token,
            "principal": "client-supplied-impersonation",
        })
    )
    return json.loads(await connection.recv())


async def receive_correlation(connection, correlation_id):
    received = []
    while True:
        message = json.loads(await connection.recv())
        received.append(message)
        if message.get("correlationId") == correlation_id:
            return message, received


def test_gateway_starts_cre_runtime_against_isolated_v10_database(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "data" / "ultron_cre.db"
    database.parent.mkdir()
    store = ContentStore(database)
    store.migrate_publishing_attempt_schema()
    store.migrate_private_render_operation_schema()
    store.close()
    monkeypatch.setattr(
        "ultron.application.content.status.default_content_database_path",
        lambda: database,
    )
    monkeypatch.setenv("ULTRON_CRE_DATABASE", str(database))
    monkeypatch.setenv("ULTRON_OPERATION_DB", str(tmp_path / "operations.db"))
    monkeypatch.delenv("ULTRON_CRE_FASTER_WHISPER_MODEL", raising=False)

    async def run():
        token, _, connection, server_task, _, _ = await start_authenticated_server(
            monkeypatch,
            tmp_path,
        )
        try:
            authenticated = await authenticate_socket(connection, token)
            assert authenticated["status"] == "AUTHENTICATED"
            await connection.send(
                json.dumps(
                    {
                        "action": "CRE_STATUS_REQUEST",
                        "correlationId": "isolated-cre-startup",
                        "payload": {},
                    }
                )
            )
            response = json.loads(await connection.recv())
            assert response["action"] == "CRE_STATUS_RESULT"
            assert response["success"] is True
            assert response["status"] == "AVAILABLE"
        finally:
            await connection.close()
            server_task.cancel()
            await asyncio.gather(server_task, return_exceptions=True)

    asyncio.run(run())


def test_real_websocket_authentication_binds_proposal_approval_and_recovery(
    tmp_path,
    monkeypatch,
):
    async def run():
        (
            token,
            database,
            first,
            server_task,
            port,
            first_executor,
        ) = await start_authenticated_server(monkeypatch, tmp_path)
        other = None
        reconnect = None
        restarted = None
        rotated = None
        server_tasks = [server_task]
        operation_id = None
        try:
            async with first:
                connected = json.loads(await first.recv())
                assert connected["authenticationRequired"] is True

                await first.send(json.dumps({
                    "action": "SUBSCRIBE",
                    "correlationId": "before-auth",
                    "channels": ["command", "operation"],
                }))
                unauthenticated = json.loads(await first.recv())
                assert unauthenticated["status"] == "AUTHENTICATION_REQUIRED"
                await first.send(json.dumps({
                    "action": "DISPATCH_COMMAND",
                    "correlationId": "unauthenticated-command",
                    "payload": {
                        "commandText": "pwd",
                        "actionVerb": "execute",
                        "parameters": {"subject": "pwd"},
                    },
                }))
                unauthorized_dispatch = json.loads(await first.recv())
                assert unauthorized_dispatch["status"] == "AUTHENTICATION_REQUIRED"

                await first.send(json.dumps({
                    "action": "AUTHENTICATE",
                    "correlationId": "missing-credential",
                }))
                missing = json.loads(await first.recv())
                assert missing["status"] == "AUTHENTICATION_FAILED"
                assert missing["error"] == "Authentication failed."
                assert "credential" not in missing

                await first.send(json.dumps({
                    "action": "AUTHENTICATE",
                    "correlationId": "tampered-credential",
                    "credential": token[:-1] + ("A" if token[-1] != "A" else "B"),
                }))
                tampered = json.loads(await first.recv())
                assert tampered["status"] == "AUTHENTICATION_FAILED"
                assert "credential" not in tampered

                authenticated = await authenticate_socket(
                    first,
                    token,
                    expect_connected=False,
                )
                assert authenticated["status"] == "AUTHENTICATED"
                assert authenticated["principal"] == "websocket-integration-operator"
                assert "credential" not in json.dumps(authenticated)

                await first.send(json.dumps({
                    "action": "SUBSCRIBE",
                    "correlationId": "subscribe-after-auth",
                    "channels": ["command", "operation"],
                }))
                subscribed = json.loads(await first.recv())
                assert subscribed["action"] == "SUBSCRIBE_ACK"
                initial_snapshots = [
                    json.loads(await first.recv()),
                    json.loads(await first.recv()),
                ]
                assert {frame["channel"] for frame in initial_snapshots} == {
                    "command",
                    "operation",
                }

                await first.send(json.dumps({
                    "action": "DISPATCH_COMMAND",
                    "correlationId": "authenticated-pwd",
                    "payload": {
                        "commandText": "pwd",
                        "actionVerb": "execute",
                        "parameters": {"subject": "pwd"},
                    },
                }))
                proposed, proposal_frames = await receive_correlation(
                    first,
                    "authenticated-pwd",
                )
                assert proposed["status"] == "PENDING_APPROVAL"
                operation_id = proposed["result"]["operationId"]
                request_fingerprint = proposed["result"]["requestFingerprint"]
                assert any(frame.get("channel") == "operation" for frame in proposal_frames)

                other = await websockets.connect(
                    f"ws://127.0.0.1:{port}{ENDPOINT_PATH}"
                )
                other_auth = await authenticate_socket(other, token)
                assert other_auth["status"] == "AUTHENTICATED"
                await other.send(json.dumps({
                    "action": "APPROVE_OPERATION",
                    "correlationId": "cross-session-approval",
                    "payload": {
                        "operationId": operation_id,
                        "correlationId": proposed["result"]["correlationId"],
                        "requestFingerprint": proposed["result"]["requestFingerprint"],
                    },
                }))
                rejected = json.loads(await other.recv())
                assert rejected["status"] == "IDENTITY_MISMATCH"
                await other.close()
                other = None

                await first.send(json.dumps({
                    "action": "APPROVE_OPERATION",
                    "correlationId": "approve-pwd-mismatched-payload",
                    "payload": {
                        "operationId": operation_id,
                        "correlationId": proposed["result"]["correlationId"],
                        "requestFingerprint": "changed-" + request_fingerprint,
                    },
                }))
                mismatched_approval = json.loads(await first.recv())
                assert mismatched_approval["status"] == "APPROVAL_MISMATCH"
                assert first_executor.calls == 0

                await first.send(json.dumps({
                    "action": "APPROVE_OPERATION",
                    "correlationId": "approve-pwd",
                    "payload": {
                        "operationId": operation_id,
                        "correlationId": proposed["result"]["correlationId"],
                        "requestFingerprint": proposed["result"]["requestFingerprint"],
                    },
                }))
                result, approval_frames = await receive_correlation(
                    first,
                    "approve-pwd",
                )
                assert result["status"] == "VERIFIED"
                assert result["success"] is True
                assert any(frame.get("channel") == "operation" for frame in approval_frames)
                assert first_executor.calls == 1

                await first.close()

            reconnect = await websockets.connect(
                f"ws://127.0.0.1:{port}{ENDPOINT_PATH}"
            )
            reconnect_auth = await authenticate_socket(reconnect, token)
            assert reconnect_auth["status"] == "AUTHENTICATED"
            assert reconnect_auth["sessionId"] != authenticated["sessionId"]
            await reconnect.send(json.dumps({
                "action": "APPROVE_OPERATION",
                "correlationId": "approval-replay-after-reconnect",
                "payload": {
                    "operationId": operation_id,
                    "correlationId": "authenticated-pwd",
                    "requestFingerprint": request_fingerprint,
                },
            }))
            replayed_approval = json.loads(await reconnect.recv())
            assert replayed_approval["status"] == "INVALID_STATE"
            await reconnect.send(json.dumps({
                "action": "DISPATCH_COMMAND",
                "correlationId": "authenticated-pwd",
                "payload": {
                    "commandText": "pwd",
                    "actionVerb": "execute",
                    "parameters": {"subject": "pwd"},
                },
            }))
            duplicate = json.loads(await reconnect.recv())
            assert duplicate["status"] == "VERIFIED"
            assert duplicate["result"]["duplicate"] is True
            assert first_executor.calls == 1
            await reconnect.close()

            server_task.cancel()
            await asyncio.gather(server_task, return_exceptions=True)
            (
                restart_token,
                restarted_database,
                restarted,
                restarted_server_task,
                restarted_port,
                restarted_executor,
            ) = await start_authenticated_server(monkeypatch, tmp_path)
            server_tasks.append(restarted_server_task)
            assert restarted_database == database
            assert restart_token is not None
            rotated = await websockets.connect(
                f"ws://127.0.0.1:{restarted_port}{ENDPOINT_PATH}"
            )
            old_credential_result = await authenticate_socket(rotated, token)
            assert old_credential_result["status"] == "AUTHENTICATION_FAILED"
            await rotated.close()
            restarted_auth = await authenticate_socket(restarted, restart_token)
            assert restarted_auth["status"] == "AUTHENTICATED"
            await restarted.send(json.dumps({
                "action": "DISPATCH_COMMAND",
                "correlationId": "authenticated-pwd",
                "payload": {
                    "commandText": "pwd",
                    "actionVerb": "execute",
                    "parameters": {"subject": "pwd"},
                },
            }))
            after_restart = json.loads(await restarted.recv())
            assert after_restart["status"] == "VERIFIED"
            assert after_restart["result"]["duplicate"] is True
            assert restarted_executor.calls == 0
        finally:
            for connection in (other, reconnect, restarted, rotated, first):
                if connection is not None:
                    await connection.close()
            for running_server in server_tasks:
                running_server.cancel()
            await asyncio.gather(*server_tasks, return_exceptions=True)

        store = SQLiteOperationStore(database)
        persisted = store.get_by_id(operation_id)
        assert persisted["lifecycleState"] == "VERIFIED"
        assert persisted["authorization"]["authorizer"] == (
            "websocket-integration-operator"
        )
        persisted_json = json.dumps(persisted)
        assert not any(
            credential in persisted_json
            for credential in (token, token[:-1] + ("A" if token[-1] != "A" else "B"))
        )
        assert store.get_by_correlation("unauthenticated-command") is None
        store.close()

    asyncio.run(run())


def test_authenticated_websocket_expires_while_connected(tmp_path, monkeypatch):
    async def run():
        token, _, connection, server_task, _, _ = await start_authenticated_server(
            monkeypatch, tmp_path, ttl_seconds=1
        )
        try:
            authenticated = await authenticate_socket(connection, token)
            assert authenticated["status"] == "AUTHENTICATED"
            with pytest.raises(ConnectionClosed) as closed:
                await asyncio.wait_for(connection.recv(), timeout=2.5)
            assert closed.value.rcvd.code == 1008
        finally:
            await connection.close()
            server_task.cancel()
            await asyncio.gather(server_task, return_exceptions=True)

    asyncio.run(run())


def test_websocket_server_ignores_injected_test_authorizers_without_provisioned_token(
    tmp_path,
    monkeypatch,
):
    class TestOnlyAuthorizer:
        def is_authenticated(self, websocket):
            return True

    async def run():
        _, database, connection, server_task, _, _ = await start_authenticated_server(
            monkeypatch,
            tmp_path,
            token_configured=False,
            injected_authorizer=TestOnlyAuthorizer(),
        )
        try:
            connected = json.loads(await connection.recv())
            assert connected["authenticationRequired"] is False
            await connection.send(json.dumps({
                "action": "DISPATCH_COMMAND",
                "correlationId": "unprovisioned-operation",
                "payload": {
                    "commandText": "pwd",
                    "actionVerb": "execute",
                    "parameters": {"subject": "pwd"},
                },
            }))
            response = json.loads(await connection.recv())
            assert response["status"] == "AUTHENTICATION_UNAVAILABLE"
        finally:
            await connection.close()
            server_task.cancel()
            await asyncio.gather(server_task, return_exceptions=True)

        store = SQLiteOperationStore(database)
        assert store.get_by_correlation("unprovisioned-operation") is None
        store.close()

    asyncio.run(run())


def test_authenticated_operation_is_not_reexecuted_after_process_restart(tmp_path):
    async def run():
        workspace = Path(__file__).resolve().parents[2]
        database = tmp_path / "process-restart.db"
        execution_counter = tmp_path / "executor-calls.log"
        first_token = secrets.token_urlsafe(32)
        rotated_token = secrets.token_urlsafe(32)
        process_tasks = []
        connections = []

        worker = """
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from ultron.application.runtime.interface_gateway import serve_interface
from ultron.application.runtime.state import RuntimeState
from ultron.core.event_bus import EventBus
from ultron.execution.executor import Executor
from ultron.skills.manager import SkillManager

counter_path = Path(sys.argv[1])
skills = SkillManager()

class CountingExecutor(Executor):
    def execute(self, plan, **kwargs):
        result = super().execute(plan, **kwargs)
        with counter_path.open("a", encoding="ascii") as counter:
            counter.write("called\\n")
        return result

container = SimpleNamespace(
    event_bus=EventBus(),
    runtime=SimpleNamespace(state=RuntimeState()),
    executor=CountingExecutor(skills=skills),
    skills=skills,
)
asyncio.run(
    serve_interface(
        container,
        host="127.0.0.1",
        port=int(sys.argv[2]),
    )
)
"""

        def reserve_port():
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                return listener.getsockname()[1]

        def child_environment(token):
            return {
                "HOME": str(tmp_path),
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": str(workspace),
                WebSocketAuthenticator.TOKEN_ENV: token,
                WebSocketAuthenticator.PRINCIPAL_ENV: (
                    "process-restart-integration-operator"
                ),
                "ULTRON_EXECUTION_BACKEND": "simulation",
                "ULTRON_OPERATION_DB": str(database),
            }

        async def start_process(token):
            port = reserve_port()
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                worker,
                str(execution_counter),
                str(port),
                cwd=workspace,
                env=child_environment(token),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            process_tasks.append(process)
            connection = None
            for _ in range(100):
                if process.returncode is not None:
                    break
                try:
                    connection = await websockets.connect(
                        f"ws://127.0.0.1:{port}{ENDPOINT_PATH}",
                        open_timeout=0.2,
                    )
                    break
                except (OSError, TimeoutError):
                    await asyncio.sleep(0.05)
            if connection is None:
                raise AssertionError(
                    "Subprocess WebSocket server did not become available."
                )
            connections.append(connection)
            return process, connection, port

        async def stop_process(process):
            if process.returncode is None:
                process.terminate()
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=10,
            )
            output = stdout + stderr
            if first_token.encode() in output or rotated_token.encode() in output:
                raise AssertionError(
                    "Authentication credential appeared in subprocess output."
                )

        try:
            first_process, first_connection, _ = await start_process(first_token)
            authenticated = await authenticate_socket(first_connection, first_token)
            assert authenticated["status"] == "AUTHENTICATED"
            assert authenticated["principal"] == (
                "process-restart-integration-operator"
            )

            request = {
                "action": "DISPATCH_COMMAND",
                "correlationId": "process-restart-pwd",
                "payload": {
                    "commandText": "pwd",
                    "actionVerb": "execute",
                    "parameters": {"subject": "pwd"},
                },
            }
            await first_connection.send(json.dumps(request))
            proposed, _ = await receive_correlation(
                first_connection,
                "process-restart-pwd",
            )
            assert proposed["status"] == "PENDING_APPROVAL"
            operation_id = proposed["result"]["operationId"]
            await first_connection.send(json.dumps({
                "action": "APPROVE_OPERATION",
                "correlationId": "process-restart-approve",
                "payload": {
                    "operationId": operation_id,
                    "correlationId": "process-restart-pwd",
                    "requestFingerprint": proposed["result"]["requestFingerprint"],
                },
            }))
            completed, _ = await receive_correlation(
                first_connection,
                "process-restart-approve",
            )
            assert completed["status"] == "VERIFIED"
            assert execution_counter.read_text(encoding="ascii").splitlines() == [
                "called"
            ]

            await first_connection.close()
            await stop_process(first_process)

            restarted_process, old_credential_socket, port = await start_process(
                rotated_token
            )
            old_authentication = await authenticate_socket(
                old_credential_socket,
                first_token,
            )
            assert old_authentication["status"] == "AUTHENTICATION_FAILED"
            await old_credential_socket.close()

            restarted_connection = await websockets.connect(
                f"ws://127.0.0.1:{port}{ENDPOINT_PATH}"
            )
            connections.append(restarted_connection)
            restarted_authentication = await authenticate_socket(
                restarted_connection,
                rotated_token,
            )
            assert restarted_authentication["status"] == "AUTHENTICATED"
            await restarted_connection.send(json.dumps(request))
            duplicate, _ = await receive_correlation(
                restarted_connection,
                "process-restart-pwd",
            )
            assert duplicate["status"] == "VERIFIED"
            assert duplicate["result"]["duplicate"] is True
            assert duplicate["result"]["operationId"] == operation_id
            assert execution_counter.read_text(encoding="ascii").splitlines() == [
                "called"
            ]
            await stop_process(restarted_process)
        finally:
            for connection in connections:
                await connection.close()
            for process in process_tasks:
                if process.returncode is None:
                    process.terminate()
                await asyncio.gather(process.wait(), return_exceptions=True)

    asyncio.run(run())


def test_interface_listener_rejects_non_loopback_hosts():
    with pytest.raises(ValueError, match="restricted to loopback"):
        asyncio.run(
            serve_interface(SimpleNamespace(), host="0.0.0.0")
        )
