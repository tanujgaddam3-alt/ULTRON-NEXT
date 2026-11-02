import asyncio
import json
import sqlite3
import stat
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ultron.application.runtime.interface_gateway import InterfaceGateway
from ultron.application.runtime.operation_store import SQLiteOperationStore
from ultron.application.runtime.state import RuntimeState
from ultron.core.event_bus import EventBus
from ultron.domain.plan import Plan, PlanStep
from ultron.execution.executor import Executor
from ultron.security.identity.authenticator import WebSocketAuthenticator
from ultron.skills.manager import SkillManager
from tests.interface.auth_test_utils import (
    authenticate_test_websocket,
    make_test_authenticator,
)

_DEFAULT_AUTHORIZER = object()


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send(self, message):
        self.messages.append(json.loads(message))


class FixedVerifier:
    verifier_id = "isolated-test-authoritative-state"

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = 0

    def verify(self, operation):
        self.calls += 1
        if self.outcome is None:
            return None
        return {
            "verified": self.outcome == "CONFIRMED_SUCCESS",
            "outcome": self.outcome,
            "operationId": operation["operationId"],
            "requestFingerprint": operation["requestFingerprint"],
            "verifier": self.verifier_id,
            "provenance": "ISOLATED_TEST_AUTHORITY",
            "observedAt": datetime.now(UTC).isoformat(),
            "expectedEffect": operation["expectedEffect"],
            "observedState": {"testState": self.outcome},
            "detail": "Evidence from a controlled test verifier.",
        }


class ConflictingVerifier(FixedVerifier):
    def verify(self, operation):
        evidence = super().verify(operation)
        evidence["requestFingerprint"] = "different-request"
        return evidence


def make_gateway(
    database,
    *,
    authorizer=_DEFAULT_AUTHORIZER,
    verifier=None,
    executor=None,
):
    if authorizer is _DEFAULT_AUTHORIZER:
        authorizer = make_test_authenticator()
    skills = SkillManager()
    container = SimpleNamespace(
        event_bus=EventBus(),
        runtime=SimpleNamespace(state=RuntimeState()),
        executor=executor or Executor(skills=skills),
        skills=skills,
        interface_approval_authorizer=authorizer,
    )
    if verifier is not None:
        container.interface_operation_verifier = verifier
    return InterfaceGateway(
        container,
        operation_store=SQLiteOperationStore(database),
    )


def dispatch_message(correlation_id="dispatch-1", command="pwd"):
    return {
        "action": "DISPATCH_COMMAND",
        "correlationId": correlation_id,
        "payload": {
            "commandText": command,
            "actionVerb": "execute",
            "parameters": {"subject": command},
        },
    }


def approval_payload(operation):
    return {
        "operationId": operation["operationId"],
        "correlationId": operation["correlationId"],
        "requestFingerprint": operation["requestFingerprint"],
    }


async def create_operation(gateway, websocket, correlation_id="dispatch-1"):
    if (
        gateway.approval_authorizer is not None
        and not gateway._is_authenticated_connection(websocket)
    ):
        authenticate_test_websocket(gateway.approval_authorizer, websocket)
    await gateway.handle(websocket, dispatch_message(correlation_id))
    return gateway._find_operation(
        websocket.messages[-1]["result"]["operationId"]
    )


async def approve(gateway, websocket, operation, request_id="approve-1"):
    await gateway.handle(
        websocket,
        {
            "action": "APPROVE_OPERATION",
            "correlationId": request_id,
            "payload": approval_payload(operation),
        },
    )
    return websocket.messages[-1]


def test_consequential_dispatch_is_disabled_without_trusted_authorizer(tmp_path):
    gateway = make_gateway(tmp_path / "operations.db", authorizer=None)
    websocket = FakeWebSocket()
    calls = 0
    execute = gateway.executor.execute

    def count_execution(plan, **kwargs):
        nonlocal calls
        calls += 1
        return execute(plan, **kwargs)

    gateway.executor.execute = count_execution
    asyncio.run(gateway.handle(websocket, dispatch_message()))

    assert websocket.messages[-1]["status"] == "AUTHENTICATION_UNAVAILABLE"
    assert calls == 0
    assert not any(
        action["approval"] == "required"
        for action in gateway._build_command_center()["supportedActions"]
    )
    assert not gateway._operations
    gateway.operation_store.close()


def test_arbitrary_authorizer_callback_is_not_a_trust_authority(tmp_path):
    with pytest.raises(TypeError, match="WebSocketAuthenticator trust authority"):
        make_gateway(
            tmp_path / "operations.db",
            authorizer=object(),
        )

    class DerivedAuthenticator(WebSocketAuthenticator):
        pass

    with pytest.raises(TypeError, match="WebSocketAuthenticator trust authority"):
        make_gateway(
            tmp_path / "derived-operations.db",
            authorizer=DerivedAuthenticator("T" * 43),
        )


def test_operation_database_is_created_owner_only(tmp_path):
    database = tmp_path / "private" / "operations.db"
    store = SQLiteOperationStore(database)
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    store.close()


def test_unauthenticated_session_cannot_dispatch_or_read_operation_channels(tmp_path):
    gateway = make_gateway(tmp_path / "operations.db", authorizer=make_test_authenticator())
    websocket = FakeWebSocket()

    async def run():
        await gateway.handle(websocket, dispatch_message())
        gateway._record_operation({
            "title": "private operation",
            "intent": "private intent",
            "expectedEffect": "private expected effect",
            "lifecycleState": "SUGGESTED",
            "verb": "execute",
            "commandText": "pwd",
            "parameters": {"subject": "pwd"},
            "correlationId": "private-correlation",
            "requestFingerprint": "private-fingerprint",
        })
        await gateway.handle(
            websocket,
            {
                "action": "SUBSCRIBE",
                "correlationId": "subscribe-private",
                "channels": ["command", "operation"],
            },
        )

    asyncio.run(run())

    assert websocket.messages[0]["status"] == "AUTHENTICATION_REQUIRED"
    assert not any(
        item.get("channel") in {"command", "operation"}
        for item in websocket.messages
    )
    gateway.operation_store.close()


def test_authenticated_approval_binds_operation_and_pwd_is_independently_verified(
    tmp_path,
):
    authorizer = make_test_authenticator()
    gateway = make_gateway(
        tmp_path / "operations.db",
        authorizer=authorizer,
    )
    websocket = FakeWebSocket()
    calls = 0
    execute = gateway.executor.execute

    def count_execution(plan, **kwargs):
        nonlocal calls
        calls += 1
        return execute(plan, **kwargs)

    gateway.executor.execute = count_execution

    async def run():
        operation = await create_operation(gateway, websocket)
        result = await approve(gateway, websocket, operation)
        return operation, result

    operation, response = asyncio.run(run())
    persisted = gateway.operation_store.get_by_id(operation["operationId"])

    assert response["status"] == "VERIFIED"
    assert response["success"] is True
    assert calls == 1
    assert persisted["authorization"]["authorizer"] == "test-operator"
    assert persisted["authorization"]["requestFingerprint"] == operation[
        "requestFingerprint"
    ]
    assert persisted["authorization"]["action"] == "run_command"
    assert persisted["authorization"]["resourceFingerprint"] == (
        gateway._authorization_scope(operation, "execute")["resourceFingerprint"]
    )
    assert persisted["verificationAudit"]["verifier"] == (
        "ultron.local-working-directory"
    )
    assert persisted["verificationAudit"]["provenance"] == "LOCAL_PROCESS_STATE"
    assert persisted["verificationAudit"]["outcome"] == "CONFIRMED_SUCCESS"
    gateway.operation_store.close()


def test_unauthorized_approval_is_rejected_before_execution(tmp_path):
    gateway = make_gateway(tmp_path / "operations.db")
    websocket = FakeWebSocket()
    calls = 0
    execute = gateway.executor.execute

    def count_execution(plan, **kwargs):
        nonlocal calls
        calls += 1
        return execute(plan, **kwargs)

    gateway.executor.execute = count_execution

    async def run():
        operation = await create_operation(gateway, websocket)
        gateway.approval_authorizer.revoke(websocket)
        response = await approve(gateway, websocket, operation)
        return operation, response

    operation, response = asyncio.run(run())
    persisted = gateway.operation_store.get_by_id(operation["operationId"])
    assert response["status"] == "AUTHENTICATION_REQUIRED"
    assert calls == 0
    assert persisted["lifecycleState"] == "SUGGESTED"
    assert persisted["authorization"]["granted"] is False
    gateway.operation_store.close()


def test_approval_with_mismatched_reviewed_fingerprint_is_rejected(tmp_path):
    authorizer = make_test_authenticator()
    gateway = make_gateway(tmp_path / "operations.db", authorizer=authorizer)
    websocket = FakeWebSocket()
    operation = asyncio.run(create_operation(gateway, websocket))
    payload = approval_payload(operation)
    payload["requestFingerprint"] = "tampered-fingerprint"

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "APPROVE_OPERATION",
                "correlationId": "approve-mismatch",
                "payload": payload,
            },
        )
    )

    assert websocket.messages[-1]["status"] == "APPROVAL_MISMATCH"
    assert gateway.operation_store._connection.execute(
        "SELECT COUNT(*) FROM authorization_grants"
    ).fetchone()[0] == 0
    assert operation["lifecycleState"] == "SUGGESTED"
    gateway.operation_store.close()


def test_operation_payload_changed_after_review_cannot_be_approved(tmp_path):
    authorizer = make_test_authenticator()
    gateway = make_gateway(tmp_path / "operations.db", authorizer=authorizer)
    websocket = FakeWebSocket()
    operation = asyncio.run(create_operation(gateway, websocket))
    original_fingerprint = operation["requestFingerprint"]
    operation["parameters"]["subject"] = "pwd && whoami"
    gateway.operation_store.update({**operation, "updatedAt": datetime.now(UTC).isoformat()})

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "APPROVE_OPERATION",
                "correlationId": "approve-mutated",
                "payload": {
                    "operationId": operation["operationId"],
                    "correlationId": operation["correlationId"],
                    "requestFingerprint": original_fingerprint,
                },
            },
        )
    )

    assert websocket.messages[-1]["status"] == "APPROVAL_MISMATCH"
    assert operation["lifecycleState"] == "SUGGESTED"
    gateway.operation_store.close()


def test_expired_grant_is_rejected_before_execution(tmp_path):
    gateway = make_gateway(tmp_path / "operations.db")
    websocket = FakeWebSocket()
    operation = asyncio.run(create_operation(gateway, websocket))
    authorize = gateway._authorize_operation

    async def issue_expired_grant(*args, **kwargs):
        grant = await authorize(*args, **kwargs)
        grant["expiresAt"] = (
            datetime.now(UTC) - timedelta(seconds=1)
        ).isoformat()
        return grant

    gateway._authorize_operation = issue_expired_grant

    response = asyncio.run(approve(gateway, websocket, operation))

    assert response["status"] == "APPROVAL_INVALID"
    assert operation["lifecycleState"] == "SUGGESTED"
    gateway.operation_store.close()


def test_grant_revoked_before_execution_cannot_start_executor(tmp_path):
    gateway = make_gateway(tmp_path / "operations.db")
    websocket = FakeWebSocket()
    calls = 0
    execute = gateway.executor.execute

    def count_execution(plan, **kwargs):
        nonlocal calls
        calls += 1
        return execute(plan, **kwargs)

    gateway.executor.execute = count_execution
    operation = asyncio.run(create_operation(gateway, websocket, "grant-revoked"))
    claim = (
        gateway.operation_store
        .claim_authorization_grant_and_approve_operation
    )

    def claim_then_revoke(grant, approved_operation):
        result = claim(grant, approved_operation)
        gateway.approval_authorizer.revoke(websocket)
        return result

    gateway.operation_store.claim_authorization_grant_and_approve_operation = (
        claim_then_revoke
    )
    response = asyncio.run(approve(gateway, websocket, operation))

    assert response["status"] == "APPROVAL_EXPIRED"
    assert operation["lifecycleState"] == "FAILED"
    assert calls == 0
    gateway.operation_store.close()


def test_approval_persistence_failure_does_not_consume_grant_or_approve(
    tmp_path,
):
    gateway = make_gateway(tmp_path / "operations.db")
    websocket = FakeWebSocket()
    calls = 0
    execute = gateway.executor.execute

    def count_execution(plan, **kwargs):
        nonlocal calls
        calls += 1
        return execute(plan, **kwargs)

    gateway.executor.execute = count_execution
    operation = asyncio.run(create_operation(gateway, websocket))
    gateway.operation_store._connection.execute(
        f"""
        CREATE TRIGGER reject_approval_update
        BEFORE UPDATE ON operations
        WHEN NEW.operation_id = '{operation["operationId"]}'
        BEGIN
            SELECT RAISE(ABORT, 'injected operation persistence failure');
        END
        """
    )

    with pytest.raises(
        sqlite3.IntegrityError,
        match="injected operation persistence failure",
    ):
        asyncio.run(approve(gateway, websocket, operation))

    persisted = gateway.operation_store.get_by_id(operation["operationId"])
    consumed_grants = gateway.operation_store._connection.execute(
        "SELECT COUNT(*) FROM authorization_grants"
    ).fetchone()[0]
    assert operation["lifecycleState"] == "SUGGESTED"
    assert persisted["lifecycleState"] == "SUGGESTED"
    assert consumed_grants == 0
    assert calls == 0
    gateway.operation_store.close()


def test_approval_event_failure_rolls_back_grant_and_approval(tmp_path):
    gateway = make_gateway(tmp_path / "operations.db")
    websocket = FakeWebSocket()
    calls = 0
    execute = gateway.executor.execute

    def count_execution(plan, **kwargs):
        nonlocal calls
        calls += 1
        return execute(plan, **kwargs)

    gateway.executor.execute = count_execution
    operation = asyncio.run(create_operation(gateway, websocket))
    gateway.operation_store._connection.execute(
        """
        CREATE TRIGGER reject_approval_event
        BEFORE INSERT ON operation_lifecycle_events
        WHEN NEW.event_type = 'AUTHORIZATION_APPROVED'
        BEGIN
            SELECT RAISE(ABORT, 'injected approval event failure');
        END
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="injected approval event failure"):
        asyncio.run(approve(gateway, websocket, operation))

    persisted = gateway.operation_store.get_by_id(operation["operationId"])
    assert persisted["lifecycleState"] == "SUGGESTED"
    assert operation["lifecycleState"] == "SUGGESTED"
    assert gateway.operation_store._connection.execute(
        "SELECT COUNT(*) FROM authorization_grants"
    ).fetchone()[0] == 0
    assert calls == 0
    gateway.operation_store.close()


def test_final_dispatch_event_failure_rolls_back_claim_and_blocks_skill(
    tmp_path,
):
    gateway = make_gateway(tmp_path / "operations.db")
    websocket = FakeWebSocket()
    operation = asyncio.run(create_operation(gateway, websocket))
    skill = gateway.executor.dispatcher.skills.get_skill("run_command")
    calls = 0
    execute_skill = skill.execute

    def count_skill_invocation(action, **kwargs):
        nonlocal calls
        calls += 1
        return execute_skill(action, **kwargs)

    skill.execute = count_skill_invocation
    gateway.operation_store._connection.execute(
        """
        CREATE TRIGGER reject_final_dispatch_event
        BEFORE INSERT ON operation_lifecycle_events
        WHEN NEW.event_type = 'FINAL_DISPATCH_CLAIMED'
        BEGIN
            SELECT RAISE(ABORT, 'injected final dispatch event failure');
        END
        """
    )

    response = asyncio.run(approve(gateway, websocket, operation))
    persisted = gateway.operation_store.get_by_id(operation["operationId"])
    claimed_at = gateway.operation_store._connection.execute(
        "SELECT dispatch_claimed_at FROM authorization_grants"
    ).fetchone()[0]

    assert response["status"] == "UNCERTAIN"
    assert persisted["lifecycleState"] == "UNCERTAIN"
    assert calls == 0
    assert claimed_at is None
    assert not any(
        event["event_type"] == "FINAL_DISPATCH_CLAIMED"
        for event in gateway.operation_store.operation_lifecycle_events(
            operation["operationId"]
        )
    )
    gateway.operation_store.close()


def test_recovery_event_failure_is_atomic_and_never_replays_grant(tmp_path):
    database = tmp_path / "operations.db"
    gateway = make_gateway(database)
    websocket = FakeWebSocket()
    operation = asyncio.run(create_operation(gateway, websocket))

    async def stop_after_approval(*_args):
        return None

    gateway._execute_approved_operation = stop_after_approval
    asyncio.run(approve(gateway, websocket, operation))
    operation = gateway._find_operation(operation["operationId"])
    gateway._op_update(operation, lifecycleState="EXECUTING")
    authorization_context = operation["authorization"]["authorizationContext"]
    gateway.operation_store._connection.execute(
        """
        CREATE TRIGGER reject_recovery_event
        BEFORE INSERT ON operation_lifecycle_events
        WHEN NEW.event_type = 'INTERRUPTION_RECOVERED'
        BEGIN
            SELECT RAISE(ABORT, 'injected recovery event failure');
        END
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="injected recovery event failure"):
        gateway.operation_store.recover_interrupted()

    assert gateway.operation_store.get_by_id(
        operation["operationId"]
    )["lifecycleState"] == "EXECUTING"
    gateway.operation_store._connection.execute(
        "DROP TRIGGER reject_recovery_event"
    )
    gateway.operation_store.close()

    restarted_store = SQLiteOperationStore(database)
    assert restarted_store.recover_interrupted() == 1
    assert restarted_store.recover_interrupted() == 0
    assert restarted_store.get_by_id(
        operation["operationId"]
    )["lifecycleState"] == "UNCERTAIN"
    assert gateway.approval_authorizer.validate_and_claim_dispatch(
        authorization_context,
        restarted_store,
        action="run_command",
        parameters={"command": "pwd"},
        operation_id=operation["operationId"],
        correlation_id=operation["correlationId"],
    ) is False
    assert len(
        [
            event
            for event in restarted_store.operation_lifecycle_events(
                operation["operationId"]
            )
            if event["event_type"] == "INTERRUPTION_RECOVERED"
        ]
    ) == 1
    restarted_store.close()


def test_grant_is_revalidated_at_final_dispatch_boundary(tmp_path):
    gateway = make_gateway(tmp_path / "operations.db")
    websocket = FakeWebSocket()
    calls = 0
    execute = gateway.executor.execute

    def count_execution(plan, **kwargs):
        nonlocal calls
        calls += 1
        return execute(plan, **kwargs)

    gateway.executor.execute = count_execution
    operation = asyncio.run(create_operation(gateway, websocket))
    broadcast = gateway._broadcast

    async def revoke_after_dispatching_state(payload):
        if payload.get("channel") == "operation" and any(
            item.get("operationId") == operation["operationId"]
            and item.get("lifecycleState") == "EXECUTING"
            for item in payload.get("payload", [])
        ):
            gateway.approval_authorizer.revoke(websocket)
        await broadcast(payload)

    gateway._broadcast = revoke_after_dispatching_state
    response = asyncio.run(approve(gateway, websocket, operation))

    assert response["status"] == "APPROVAL_EXPIRED"
    assert operation["lifecycleState"] == "FAILED"
    assert calls == 0
    gateway.operation_store.close()


def test_approval_grant_replay_cannot_authorize_a_second_operation(
    tmp_path,
    monkeypatch,
):
    authorizer = make_test_authenticator()
    gateway = make_gateway(tmp_path / "operations.db", authorizer=authorizer)
    websocket = FakeWebSocket()
    authenticate_test_websocket(authorizer, websocket)
    import ultron.security.identity.authenticator as authenticator_module

    monkeypatch.setattr(
        authenticator_module,
        "uuid4",
        lambda: "replayed-grant-id",
    )
    calls = 0
    execute = gateway.executor.execute

    def count_execution(plan, **kwargs):
        nonlocal calls
        calls += 1
        return execute(plan, **kwargs)

    gateway.executor.execute = count_execution

    async def run():
        first = await create_operation(gateway, websocket, "dispatch-first")
        first_response = await approve(gateway, websocket, first, "approve-first")
        second = await create_operation(gateway, websocket, "dispatch-second")
        second_response = await approve(gateway, websocket, second, "approve-second")
        return first, first_response, second, second_response

    first, first_response, second, second_response = asyncio.run(run())

    assert first_response["status"] == "VERIFIED"
    assert second_response["status"] == "AUTHORIZATION_REPLAY"
    assert first["lifecycleState"] == "VERIFIED"
    assert second["lifecycleState"] == "SUGGESTED"
    assert calls == 1
    gateway.operation_store.close()


def test_concurrent_approvals_dispatch_one_operation_only(tmp_path):
    gateway = make_gateway(tmp_path / "operations.db")
    websocket = FakeWebSocket()
    calls = 0
    execute = gateway.executor.execute

    def count_execution(plan, **kwargs):
        nonlocal calls
        calls += 1
        return execute(plan, **kwargs)

    gateway.executor.execute = count_execution
    operation = asyncio.run(create_operation(gateway, websocket))

    async def run_concurrent_approvals():
        await asyncio.gather(
            *(
                gateway.handle(
                    websocket,
                    {
                        "action": "APPROVE_OPERATION",
                        "correlationId": f"approve-concurrent-{index}",
                        "payload": approval_payload(operation),
                    },
                )
                for index in range(2)
            )
        )

    asyncio.run(run_concurrent_approvals())
    responses = [
        message
        for message in websocket.messages
        if message.get("action") == "OPERATION_RESULT"
        and message.get("correlationId", "").startswith(
            "approve-concurrent-"
        )
    ]
    persisted = gateway.operation_store.get_by_id(operation["operationId"])

    assert len(responses) == 2
    assert sum(response["status"] == "VERIFIED" for response in responses) == 1
    assert all(
        response["status"] in {
            "VERIFIED",
            "AUTHORIZATION_REPLAY",
            "INVALID_STATE",
        }
        for response in responses
    )
    assert calls == 1
    assert persisted["lifecycleState"] == "VERIFIED"
    assert gateway.operation_store._connection.execute(
        "SELECT COUNT(*) FROM authorization_grants"
    ).fetchone()[0] == 1
    gateway.operation_store.close()


def test_malformed_approval_is_rejected_without_execution(tmp_path):
    authorizer = make_test_authenticator()
    gateway = make_gateway(tmp_path / "operations.db", authorizer=authorizer)
    websocket = FakeWebSocket()
    operation = asyncio.run(create_operation(gateway, websocket))

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "APPROVE_OPERATION",
                "correlationId": "approve-malformed",
                "payload": {"operationId": operation["operationId"]},
            },
        )
    )

    assert websocket.messages[-1]["status"] == "APPROVAL_MISMATCH"
    assert operation["lifecycleState"] == "SUGGESTED"
    gateway.operation_store.close()


def prepare_uncertain_operation(gateway, websocket, correlation_id):
    operation = asyncio.run(create_operation(gateway, websocket, correlation_id))
    approval_correlation_id = f"approve-{correlation_id}"
    grant = asyncio.run(
        gateway._authorize_operation(
            websocket,
            operation,
            purpose="execute",
            request_correlation_id=approval_correlation_id,
        )
    )
    assert grant is not None
    approved = {
        **operation,
        "lifecycleState": "APPROVED",
        "updatedAt": datetime.now(UTC).isoformat(),
        "authorization": {
            "policy": "TRUSTED_INTERFACE_AUTHORIZER",
            "required": True,
            "granted": True,
            "authorizer": grant["principal"],
            "authenticated": True,
            "grantId": grant["grantId"],
            "operationId": operation["operationId"],
            "operationCorrelationId": operation["correlationId"],
            "requestCorrelationId": approval_correlation_id,
            "requestFingerprint": operation["requestFingerprint"],
            "action": grant["action"],
            "resourceFingerprint": grant["resourceFingerprint"],
            "grantedAt": grant["issuedAt"],
            "expiresAt": grant["expiresAt"],
            "signature": grant["signature"],
            "authorizationContext": grant["authorizationContext"],
        },
    }
    persisted = gateway.operation_store.claim_authorization_grant_and_approve_operation(
        {
            "grantId": grant["grantId"],
            "operationId": grant["operationId"],
            "correlationId": grant["requestCorrelationId"],
            "requestFingerprint": grant["requestFingerprint"],
            "purpose": grant["purpose"],
            "principal": grant["principal"],
            "expiresAt": grant["expiresAt"],
            "issuedAt": grant["issuedAt"],
            "action": grant["action"],
            "resourceFingerprint": grant["resourceFingerprint"],
        },
        approved,
    )
    assert persisted
    operation.clear()
    operation.update(approved)
    gateway._op_update(operation, lifecycleState="EXECUTING")
    gateway._op_update(
        operation,
        lifecycleState="UNCERTAIN",
        executionResult={
            "success": False,
            "status": "UNCERTAIN",
            "error": "simulated process interruption after dispatch",
        },
    )
    return operation


def reconcile_message(operation, correlation_id):
    return {
        "action": "RECONCILE_OPERATION",
        "correlationId": correlation_id,
        "payload": {
            "operationId": operation["operationId"],
            "requestFingerprint": operation["requestFingerprint"],
        },
    }


def test_reconciliation_verifies_persisted_pwd_without_reexecution_and_is_idempotent(
    tmp_path,
):
    database = tmp_path / "operations.db"
    authorizer = make_test_authenticator()
    gateway = make_gateway(database, authorizer=authorizer)
    websocket = FakeWebSocket()
    calls = 0
    execute = gateway.executor.execute

    def count_execution(plan, **kwargs):
        nonlocal calls
        calls += 1
        return execute(plan, **kwargs)

    gateway.executor.execute = count_execution
    operation = prepare_uncertain_operation(
        gateway,
        websocket,
        "reconciliation-fixture",
    )
    result = gateway.executor.execute(
        Plan(
            goal="read-only reconciliation fixture",
            steps=[
                PlanStep(
                    order=1,
                    action="run_command",
                    parameters={"command": "pwd"},
                )
            ],
        )
    )
    gateway._op_update(
        operation,
        lifecycleState="UNCERTAIN",
        executionResult={
            "success": False,
            "status": "UNCERTAIN",
            "result": result,
        },
    )
    gateway.add_client(websocket)
    gateway._subscriptions[websocket].add("operation")
    request = reconcile_message(operation, "reconcile-once")

    async def run():
        await gateway.handle(websocket, request)
        first_result = websocket.messages[-1]
        await gateway.handle(websocket, request)
        second_result = websocket.messages[-1]
        return first_result, second_result

    first, replay = asyncio.run(run())
    persisted = gateway.operation_store.get_by_id(operation["operationId"])

    assert first["status"] == "VERIFIED"
    assert replay["status"] == "VERIFIED"
    assert calls == 1
    assert len(persisted["reconciliationHistory"]) == 1
    assert persisted["reconciliationHistory"][0]["principal"] == "test-operator"
    assert any(item.get("channel") == "operation" for item in websocket.messages)
    gateway.operation_store.close()

    restarted_gateway = make_gateway(
        database,
        authorizer=make_test_authenticator(),
    )
    replay_socket = FakeWebSocket()
    authenticate_test_websocket(
        restarted_gateway.approval_authorizer,
        replay_socket,
    )
    asyncio.run(
        restarted_gateway.handle(
            replay_socket,
            request,
        )
    )
    assert replay_socket.messages[-1]["status"] == "VERIFIED"
    persisted_after_restart = restarted_gateway.operation_store.get_by_id(
        operation["operationId"]
    )
    assert len(persisted_after_restart["reconciliationHistory"]) == 1
    assert calls == 1
    restarted_gateway.operation_store.close()


def test_reconciliation_preserves_confirmed_failure(tmp_path):
    verifier = FixedVerifier("CONFIRMED_FAILURE")
    gateway = make_gateway(
        tmp_path / "operations.db",
        authorizer=make_test_authenticator(),
        verifier=verifier,
    )
    websocket = FakeWebSocket()
    operation = prepare_uncertain_operation(gateway, websocket, "failed-op")

    asyncio.run(
        gateway.handle(
            websocket,
            reconcile_message(operation, "reconcile-failure"),
        )
    )

    persisted = gateway.operation_store.get_by_id(operation["operationId"])
    assert websocket.messages[-1]["status"] == "FAILED"
    assert persisted["lifecycleState"] == "FAILED"
    assert persisted["verificationAudit"]["outcome"] == "CONFIRMED_FAILURE"
    assert len(persisted["reconciliationHistory"]) == 1
    gateway.operation_store.close()


def test_pending_reconciliation_after_restart_is_not_replayed(tmp_path):
    database = tmp_path / "operations.db"
    gateway = make_gateway(
        database,
        authorizer=make_test_authenticator(),
        verifier=FixedVerifier("CONFIRMED_SUCCESS"),
    )
    websocket = FakeWebSocket()
    operation = prepare_uncertain_operation(gateway, websocket, "pending-op")
    request = reconcile_message(operation, "pending-reconciliation")
    reconciliation_fingerprint = gateway._request_fingerprint(
        "reconcile",
        operation["operationId"],
        {"requestFingerprint": operation["requestFingerprint"]},
    )
    gateway.operation_store.begin_reconciliation(
        "pending-reconciliation",
        operation["operationId"],
        reconciliation_fingerprint,
    )
    gateway.operation_store.close()

    verifier = FixedVerifier("CONFIRMED_SUCCESS")
    restarted_gateway = make_gateway(
        database,
        authorizer=make_test_authenticator(),
        verifier=verifier,
    )
    restarted_socket = FakeWebSocket()
    authenticate_test_websocket(
        restarted_gateway.approval_authorizer,
        restarted_socket,
    )
    asyncio.run(restarted_gateway.handle(restarted_socket, request))
    persisted = restarted_gateway.operation_store.get_by_id(
        operation["operationId"]
    )

    assert restarted_socket.messages[-1]["status"] == "RECONCILIATION_REQUIRED"
    assert persisted["lifecycleState"] == "UNCERTAIN"
    assert verifier.calls == 0
    restarted_gateway.operation_store.close()


def test_ambiguous_or_unavailable_verifier_leaves_operation_unresolved(tmp_path):
    verifier = FixedVerifier(None)
    gateway = make_gateway(
        tmp_path / "operations.db",
        authorizer=make_test_authenticator(),
        verifier=verifier,
    )
    websocket = FakeWebSocket()
    operation = prepare_uncertain_operation(gateway, websocket, "ambiguous-op")

    asyncio.run(
        gateway.handle(
            websocket,
            reconcile_message(operation, "reconcile-ambiguous"),
        )
    )

    persisted = gateway.operation_store.get_by_id(operation["operationId"])
    assert websocket.messages[-1]["status"] == "RECONCILIATION_REQUIRED"
    assert persisted["lifecycleState"] == "UNCERTAIN"
    assert persisted["reconciliationHistory"][0]["outcome"] == "UNRESOLVED"
    gateway.operation_store.close()


def test_reconciliation_persistence_failure_keeps_operation_uncertain(
    tmp_path,
):
    verifier = FixedVerifier("CONFIRMED_SUCCESS")
    gateway = make_gateway(
        tmp_path / "operations.db",
        authorizer=make_test_authenticator(),
        verifier=verifier,
    )
    websocket = FakeWebSocket()
    operation = prepare_uncertain_operation(
        gateway,
        websocket,
        "failed-reconciliation-write",
    )
    request = reconcile_message(operation, "failed-reconciliation-write")
    gateway.operation_store._connection.execute(
        """
        CREATE TRIGGER reject_reconciliation_event
        BEFORE INSERT ON operation_lifecycle_events
        WHEN NEW.event_type = 'RECONCILIATION_COMPLETED'
        BEGIN
            SELECT RAISE(ABORT, 'injected reconciliation event failure');
        END
        """
    )

    with pytest.raises(
        sqlite3.IntegrityError,
        match="injected reconciliation event failure",
    ):
        asyncio.run(gateway.handle(websocket, request))

    assert gateway.operation_store.get_by_id(
        operation["operationId"]
    )["lifecycleState"] == "UNCERTAIN"
    assert gateway.operation_store.get_reconciliation(
        "failed-reconciliation-write"
    )["status"] == "PENDING"
    assert verifier.calls == 1

    replay_socket = FakeWebSocket()
    authenticate_test_websocket(gateway.approval_authorizer, replay_socket)
    asyncio.run(gateway.handle(replay_socket, request))
    assert verifier.calls == 1
    assert gateway.operation_store.get_by_id(
        operation["operationId"]
    )["lifecycleState"] == "UNCERTAIN"
    gateway.operation_store.close()


def test_conflicting_verification_evidence_cannot_mark_operation_verified(tmp_path):
    gateway = make_gateway(
        tmp_path / "operations.db",
        authorizer=make_test_authenticator(),
        verifier=ConflictingVerifier("CONFIRMED_SUCCESS"),
    )
    websocket = FakeWebSocket()
    operation = prepare_uncertain_operation(gateway, websocket, "conflict-op")

    asyncio.run(
        gateway.handle(
            websocket,
            reconcile_message(operation, "reconcile-conflict"),
        )
    )

    persisted = gateway.operation_store.get_by_id(operation["operationId"])
    assert websocket.messages[-1]["status"] == "RECONCILIATION_REQUIRED"
    assert persisted["lifecycleState"] == "UNCERTAIN"
    assert persisted["reconciliationHistory"][0]["outcome"] == "UNRESOLVED"
    assert "did not bind" in persisted["reconciliationHistory"][0]["error"]
    gateway.operation_store.close()


def test_successful_executor_with_verifier_failure_remains_uncertain(tmp_path):
    gateway = make_gateway(
        tmp_path / "operations.db",
        authorizer=make_test_authenticator(),
        verifier=ConflictingVerifier("CONFIRMED_SUCCESS"),
    )
    websocket = FakeWebSocket()
    operation = asyncio.run(create_operation(gateway, websocket, "verify-fail"))

    response = asyncio.run(approve(gateway, websocket, operation))

    persisted = gateway.operation_store.get_by_id(operation["operationId"])
    assert response["status"] == "UNCERTAIN"
    assert persisted["lifecycleState"] == "UNCERTAIN"
    assert persisted["executionResult"]["success"] is False
    assert "did not bind" in persisted["verificationError"]
    assert "verificationAudit" not in persisted
    gateway.operation_store.close()


def test_missing_verifier_keeps_successful_executor_result_unverified(tmp_path):
    gateway = make_gateway(
        tmp_path / "operations.db",
        authorizer=make_test_authenticator(),
        verifier=FixedVerifier(None),
    )
    websocket = FakeWebSocket()
    operation = asyncio.run(create_operation(gateway, websocket, "unverifiable"))

    response = asyncio.run(approve(gateway, websocket, operation))

    persisted = gateway.operation_store.get_by_id(operation["operationId"])
    assert response["status"] == "UNVERIFIED"
    assert persisted["lifecycleState"] == "UNVERIFIED"
    assert persisted["executionResult"]["success"] is True
    assert "verificationAudit" not in persisted
    gateway.operation_store.close()


def test_verifier_does_not_generalize_from_a_similar_but_different_request(tmp_path):
    gateway = make_gateway(
        tmp_path / "operations.db",
        authorizer=make_test_authenticator(),
    )
    websocket = FakeWebSocket()
    authenticate_test_websocket(gateway.approval_authorizer, websocket)
    message = dispatch_message("similar-request", "pwd")
    message["payload"]["commandText"] = "please show the current directory"

    async def run():
        await gateway.handle(websocket, message)
        operation = gateway._find_operation(
            websocket.messages[-1]["result"]["operationId"]
        )
        return operation, await approve(gateway, websocket, operation)

    operation, response = asyncio.run(run())
    assert response["status"] == "UNVERIFIED"
    assert operation["lifecycleState"] == "UNVERIFIED"
    assert "verificationAudit" not in operation
    gateway.operation_store.close()


def test_reconciliation_rejects_stale_fingerprint_and_unauthenticated_request(
    tmp_path,
):
    gateway = make_gateway(
        tmp_path / "operations.db",
        authorizer=make_test_authenticator(),
    )
    websocket = FakeWebSocket()
    operation = prepare_uncertain_operation(gateway, websocket, "stale-op")
    stale = reconcile_message(operation, "stale-reconciliation")
    stale["payload"]["requestFingerprint"] = "stale"

    asyncio.run(gateway.handle(websocket, stale))

    assert websocket.messages[-1]["status"] == "RECONCILIATION_MISMATCH"
    assert not operation.get("reconciliationHistory")
    gateway.approval_authorizer = None
    asyncio.run(
        gateway.handle(
            websocket,
            reconcile_message(operation, "unauthenticated-reconciliation"),
        )
    )
    assert websocket.messages[-1]["status"] == "AUTHENTICATION_UNAVAILABLE"
    gateway.operation_store.close()


def test_retry_action_is_not_implemented_and_never_reexecutes(tmp_path):
    gateway = make_gateway(
        tmp_path / "operations.db",
        authorizer=make_test_authenticator(),
    )
    websocket = FakeWebSocket()
    calls = 0
    execute = gateway.executor.execute

    def count_execution(plan, **kwargs):
        nonlocal calls
        calls += 1
        return execute(plan, **kwargs)

    gateway.executor.execute = count_execution
    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "RETRY_OPERATION",
                "correlationId": "retry-unauthorized",
                "payload": {"operationId": "missing"},
            },
        )
    )

    assert websocket.messages[-1]["action"] == "ERROR"
    assert calls == 0
    gateway.operation_store.close()
