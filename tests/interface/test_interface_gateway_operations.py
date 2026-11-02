import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest

from ultron.application.runtime.interface_gateway import (
    InterfaceGateway,
    _CONSEQUENTIAL_VERBS,
)
from ultron.application.runtime.operation_store import SQLiteOperationStore
from tests.interface.auth_test_utils import (
    authenticate_test_websocket,
    make_test_authenticator,
)


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send(self, message):
        self.messages.append(json.loads(message))


class RuntimeThatMustNotRun:
    def run_cycle(self, event):
        raise AssertionError("Unsupported operation reached the cognitive runtime.")


def make_gateway():
    container = SimpleNamespace(
        event_bus=SimpleNamespace(),
        runtime=RuntimeThatMustNotRun(),
        executor=SimpleNamespace(),
        skills=SimpleNamespace(),
        interface_approval_authorizer=make_test_authenticator(),
    )
    return InterfaceGateway(
        container,
        operation_store=SQLiteOperationStore(":memory:"),
    )


def test_gateway_advertises_only_approved_consequential_actions():
    gateway = make_gateway()

    required_actions = {
        action["actionVerb"]
        for action in gateway._build_command_center()["supportedActions"]
        if action["approval"] == "required"
    }

    assert required_actions == set(_CONSEQUENTIAL_VERBS)


def test_unmapped_consequential_dispatch_is_rejected_without_proposal():
    gateway = make_gateway()
    websocket = FakeWebSocket()

    asyncio.run(
        gateway._handle_dispatch(
            websocket,
            {
                "correlationId": "dispatch-trade",
                "payload": {
                    "commandText": "place a trade",
                    "actionVerb": "trade",
                    "parameters": {},
                },
            },
        )
    )

    assert websocket.messages[-1]["status"] == "UNSUPPORTED"
    assert not gateway._operations


def test_approved_legacy_operation_without_mapping_fails_honestly():
    gateway = make_gateway()
    websocket = FakeWebSocket()
    authenticate_test_websocket(gateway.approval_authorizer, websocket)
    operation = {
        "title": "trade: legacy request",
        "intent": "legacy request",
        "expectedEffect": "A real trade",
        "lifecycleState": "SUGGESTED",
        "verb": "trade",
        "commandText": "place a trade",
        "parameters": {},
    }
    gateway._record_operation(operation)

    asyncio.run(
        gateway._handle_approve(
            websocket,
            {
                "correlationId": "approve-trade",
                "payload": {"operationId": operation["operationId"]},
            },
        )
    )

    assert operation["lifecycleState"] == "FAILED"
    assert operation["executionResult"]["status"] == "UNSUPPORTED"
    assert websocket.messages[-1]["action"] == "OPERATION_RESULT"
    assert websocket.messages[-1]["success"] is False
    assert websocket.messages[-1]["status"] == "UNSUPPORTED"


@pytest.mark.parametrize(
    "changed_fields",
    (
        {"lifecycleState": "VERIFIED"},
        {"lifecycleState": "APPROVED"},
        {"correlationId": "tampered-correlation"},
        {"requestFingerprint": "tampered-fingerprint"},
    ),
)
def test_operation_store_rejects_invalid_transitions_and_identity_mutation(
    changed_fields,
):
    gateway = make_gateway()
    websocket = FakeWebSocket()
    authenticate_test_websocket(gateway.approval_authorizer, websocket)
    operation = {
        "title": "read: pwd",
        "intent": "read current directory",
        "expectedEffect": "No persistent change",
        "correlationId": "operation-transition-test",
        "requestFingerprint": "operation-transition-fingerprint",
        "lifecycleState": "SUGGESTED",
        "verb": "execute",
        "commandText": "pwd",
        "parameters": {"subject": "pwd"},
    }
    gateway._record_operation(operation)
    update = {**operation, **changed_fields}

    with pytest.raises(ValueError):
        gateway.operation_store.update(update)

    persisted = gateway.operation_store.get_by_id(operation["operationId"])
    assert persisted["lifecycleState"] == "SUGGESTED"
    assert persisted["correlationId"] == operation["correlationId"]
    assert persisted["requestFingerprint"] == operation["requestFingerprint"]
    assert gateway.operation_store._connection.execute(
        "SELECT COUNT(*) FROM authorization_grants"
    ).fetchone()[0] == 0
    gateway.operation_store.close()


def test_operation_update_preserves_primary_error_if_rollback_fails():
    gateway = make_gateway()
    websocket = FakeWebSocket()
    authenticate_test_websocket(gateway.approval_authorizer, websocket)
    operation = {
        "title": "read: pwd",
        "intent": "read current directory",
        "expectedEffect": "No persistent change",
        "lifecycleState": "SUGGESTED",
        "verb": "execute",
        "commandText": "pwd",
        "parameters": {"subject": "pwd"},
    }
    gateway._record_operation(operation)
    store = gateway.operation_store
    connection = store._connection
    connection.execute(
        """
        CREATE TRIGGER reject_state_update
        BEFORE UPDATE ON operations
        BEGIN
            SELECT RAISE(ABORT, 'injected state persistence failure');
        END
        """
    )

    class RollbackFailureProxy:
        def __getattr__(self, name):
            return getattr(connection, name)

        def rollback(self):
            raise OSError("injected rollback cleanup failure")

    store._connection = RollbackFailureProxy()
    operation["lifecycleState"] = "FAILED"

    with pytest.raises(
        sqlite3.IntegrityError,
        match="injected state persistence failure",
    ) as raised:
        store.update(operation)

    assert any(
        "SQLite rollback failed (OSError)" in note
        for note in raised.value.__notes__
    )
    store._connection = connection
    connection.rollback()
    assert store.get_by_id(operation["operationId"])["lifecycleState"] == "SUGGESTED"
    store.close()
