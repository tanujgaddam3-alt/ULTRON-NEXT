import asyncio
import json
from types import SimpleNamespace

from ultron.application.runtime.interface_gateway import InterfaceGateway
from ultron.application.runtime.operation_store import SQLiteOperationStore
from ultron.application.runtime.system_intelligence import SystemIntelligence
from tests.interface.auth_test_utils import (
    authenticate_test_websocket,
    make_test_authenticator,
)


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send(self, message):
        self.messages.append(json.loads(message))


def _gateway(tmp_path):
    authenticator = make_test_authenticator("system-operator")
    collector = SystemIntelligence(
        root=tmp_path,
        state_directory=tmp_path / "state",
    )
    container = SimpleNamespace(
        event_bus=SimpleNamespace(),
        runtime=SimpleNamespace(),
        executor=SimpleNamespace(),
        skills=SimpleNamespace(),
        interface_approval_authorizer=authenticator,
        system_intelligence=collector,
    )
    gateway = InterfaceGateway(
        container,
        operation_store=SQLiteOperationStore(":memory:"),
    )
    return gateway, authenticator, collector


def test_system_inventory_requires_authenticated_operator_and_returns_fresh_evidence(tmp_path):
    gateway, authenticator, _collector = _gateway(tmp_path)
    websocket = FakeWebSocket()

    asyncio.run(gateway.handle(websocket, {
        "action": "SYSTEM_INTELLIGENCE_REQUEST",
        "correlationId": "inventory-unauthorized",
        "payload": {},
    }))
    assert websocket.messages[-1]["status"] == "AUTHENTICATION_REQUIRED"

    authenticate_test_websocket(authenticator, websocket)
    asyncio.run(gateway.handle(websocket, {
        "action": "SYSTEM_INTELLIGENCE_REQUEST",
        "correlationId": "inventory-authorized",
        "payload": {},
    }))
    response = websocket.messages[-1]
    assert response["action"] == "SYSTEM_INTELLIGENCE_RESULT"
    assert response["success"] is True
    assert response["payload"]["generatedAt"]
    assert response["payload"]["observations"]
    handoff = response["payload"]["managerHandoff"]
    assert set(handoff) == {
        "status",
        "phase",
        "detail",
        "observedAt",
        "managerPids",
        "eligible",
        "ownershipStatus",
        "leaseActive",
        "controlSocketAvailable",
    }
    assert handoff["eligible"] is False
    assert handoff["status"] in {"HANDOFF_BLOCKED", "OWNERSHIP_UNCERTAIN"}
    assert response["payload"]["actions"][0]["status"] == "UNAVAILABLE"
    gateway.operation_store.close()


def test_unauthenticated_helper_restart_request_is_rejected(tmp_path):
    gateway, _authenticator, _collector = _gateway(tmp_path)
    websocket = FakeWebSocket()

    asyncio.run(gateway.handle(websocket, {
        "action": "SYSTEM_RESTART_AUTH_REQUEST",
        "correlationId": "restart-unauthorized",
        "payload": {"operationId": "restart-test-unauthorized"},
    }))

    response = websocket.messages[-1]
    assert response["action"] == "ERROR"
    assert response["success"] is False
    assert response["status"] == "AUTHENTICATION_REQUIRED"
    gateway.operation_store.close()


def test_helper_restart_rejects_malformed_requests_and_surfaces_actual_unavailable_state(tmp_path):
    gateway, authenticator, _collector = _gateway(tmp_path)
    websocket = FakeWebSocket()
    authenticate_test_websocket(authenticator, websocket)

    asyncio.run(gateway.handle(websocket, {
        "action": "SYSTEM_RESTART_AUTH_REQUEST",
        "correlationId": "invalid-restart",
        "payload": {"operationId": "bad id"},
    }))
    assert websocket.messages[-1]["status"] == "INVALID_REQUEST"

    asyncio.run(gateway.handle(websocket, {
        "action": "SYSTEM_RESTART_AUTH_REQUEST",
        "correlationId": "restart-without-manager",
        "payload": {"operationId": "restart-test-1"},
    }))
    response = websocket.messages[-1]
    assert response["action"] == "SYSTEM_RESTART_AUTH_RESULT"
    assert response["success"] is False
    assert response["status"] == "FAILED"
    assert "credential" not in json.dumps(response).lower()
    gateway.operation_store.close()
