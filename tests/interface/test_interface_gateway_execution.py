import asyncio
import json
import secrets
import socket
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import websockets
import pytest
import sqlite3

from ultron.application.runtime.interface_gateway import (
    ENDPOINT_PATH,
    InterfaceGateway,
    serve_interface,
)
from ultron.application.runtime.operation_store import SQLiteOperationStore
from ultron.application.runtime.state import RuntimeState
from ultron.application.task_service import TaskService
from ultron.core.event_bus import EventBus
from ultron.domain.enums import TaskStatus
from ultron.domain.task import Task
from ultron.execution.executor import Executor
from ultron.executive.tasks.manager import TaskManager
from ultron.skills.manager import SkillManager
from ultron.skills.terminal.skill import TerminalSkill
from tests.interface.auth_test_utils import (
    authenticate_test_websocket,
    make_test_authenticator,
)


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send(self, message):
        self.messages.append(json.loads(message))


def authenticated_websocket(gateway):
    websocket = FakeWebSocket()
    authenticate_test_websocket(gateway.approval_authorizer, websocket)
    return websocket


def make_gateway(database, executor=None, runtime=None, task_execution=False):
    skills = SkillManager()
    operation_store = SQLiteOperationStore(database)
    container = SimpleNamespace(
        event_bus=EventBus(),
        runtime=runtime or SimpleNamespace(state=RuntimeState()),
        executor=executor or Executor(skills=skills),
        skills=skills,
        interface_approval_authorizer=make_test_authenticator(),
    )
    if task_execution:
        manager = TaskManager(
            database=database,
            operation_store=operation_store,
        )
        container.task_service = TaskService(
            task_manager=manager,
            executor=container.executor,
            operation_store=operation_store,
        )
    return InterfaceGateway(
        container,
        operation_store=operation_store,
    )


def dispatch_payload(command, correlation_id="operation-test"):
    return {
        "action": "DISPATCH_COMMAND",
        "correlationId": correlation_id,
        "payload": {
            "commandText": command,
            "actionVerb": "execute",
            "parameters": {"subject": command},
        },
    }


def approval_payload(gateway, operation_id):
    operation = gateway._find_operation(operation_id)
    return {
        "operationId": operation_id,
        "correlationId": operation["correlationId"],
        "requestFingerprint": operation["requestFingerprint"],
    }


def task_dispatch_payload(correlation_id="task-operation-test"):
    return {
        "action": "DISPATCH_COMMAND",
        "correlationId": correlation_id,
        "payload": {
            "commandText": "Inspect local process identity",
            "actionVerb": "execute_task",
            "parameters": {
                "goal": "Inspect local process identity",
                "steps": [
                    {
                        "order": 1,
                        "action": "run_command",
                        "parameters": {"command": "pwd"},
                        "depends_on": [],
                    },
                    {
                        "order": 2,
                        "action": "run_command",
                        "parameters": {"command": "whoami"},
                        "depends_on": [1],
                    },
                ],
            },
        },
    }


async def dispatch_and_approve(gateway, websocket, command, correlation_id):
    await gateway.handle(
        websocket,
        dispatch_payload(command, correlation_id),
    )
    proposed = websocket.messages[-1]
    operation_id = proposed["result"]["operationId"]
    await gateway.handle(
        websocket,
        {
            "action": "APPROVE_OPERATION",
            "correlationId": f"approve-{correlation_id}",
            "payload": approval_payload(gateway, operation_id),
        },
    )
    return proposed, websocket.messages[-1], gateway._find_operation(operation_id)


async def dispatch_and_approve_task(gateway, websocket, correlation_id):
    await gateway.handle(websocket, task_dispatch_payload(correlation_id))
    proposed = websocket.messages[-1]
    operation_id = proposed["result"]["operationId"]
    await gateway.handle(
        websocket,
        {
            "action": "APPROVE_OPERATION",
            "correlationId": f"approve-{correlation_id}",
            "payload": approval_payload(gateway, operation_id),
        },
    )
    return proposed, websocket.messages[-1], gateway._find_operation(operation_id)


async def prepare_cancellable_task(gateway, websocket, correlation_id):
    async def pause_after_parent_claim(*_args):
        return None

    gateway._execute_approved_task = pause_after_parent_claim
    await gateway.handle(websocket, task_dispatch_payload(correlation_id))
    proposal = websocket.messages[-1]
    operation_id = proposal["result"]["operationId"]
    await gateway.handle(
        websocket,
        {
            "action": "APPROVE_OPERATION",
            "correlationId": f"approve-{correlation_id}",
            "payload": approval_payload(gateway, operation_id),
        },
    )
    operation = gateway.operation_store.get_by_id(operation_id)
    grant = operation["authorization"]
    assert operation["lifecycleState"] == "EXECUTING"
    assert gateway.operation_store.claim_authorization_dispatch(
        grant_id=grant["grantId"],
        operation_id=operation_id,
        correlation_id=grant["requestCorrelationId"],
        request_fingerprint=grant["requestFingerprint"],
        purpose="execute",
        principal=grant["authorizer"],
        action=grant["action"],
        resource_fingerprint=grant["resourceFingerprint"],
        expires_at=grant["expiresAt"],
    )
    task = Task(
        id=f"task-{operation_id}",
        goal_id=f"goal-{operation_id}",
        title="approved task",
        description="controlled cancellation fixture",
        parent_operation_id=operation_id,
    )
    gateway.task_service.task_manager.create_tasks([task])
    return operation, task


def cancellation_message(task_id, correlation_id="cancel-task-1"):
    return {
        "action": "CANCEL_TASK",
        "correlationId": correlation_id,
        "payload": {"taskId": task_id},
    }


def deliver_cancellation(gateway, websocket, task_id, correlation_id):
    asyncio.run(
        gateway.handle(
            websocket,
            cancellation_message(task_id, correlation_id),
        )
    )
    return websocket.messages[-1]


def test_approved_task_executes_and_verifies_durable_multistep_plan(tmp_path):
    database = tmp_path / "operations.db"
    gateway = make_gateway(database, task_execution=True)
    websocket = authenticated_websocket(gateway)

    proposed, response, operation = asyncio.run(
        dispatch_and_approve_task(gateway, websocket, "trusted-task-run")
    )

    task = gateway.task_service.task_manager.get_task(
        f"task-{operation['operationId']}"
    )
    assert proposed["status"] == "PENDING_APPROVAL"
    assert response["status"] == "VERIFIED", operation.get("executionResult")
    assert operation["lifecycleState"] == "VERIFIED"
    assert operation["verificationAudit"]["outcome"] == "CONFIRMED_SUCCESS"
    assert task.status == TaskStatus.COMPLETED
    assert [step["action"] for step in operation["executionResult"]["result"]["results"]] == [
        "run_command",
        "run_command",
    ]
    assert [step["result"]["command"] for step in task.step_results] == [
        "pwd",
        "whoami",
    ]
    assert [
        gateway.operation_store.get_by_id(step["operationId"])["lifecycleState"]
        for step in task.step_results
    ] == ["COMPLETED", "COMPLETED"]
    step_events = [
        event
        for event in gateway.task_service.task_manager.task_events(task.id)
        if event["event_type"] == "STEP_RESULT"
    ]
    assert [event["step_order"] for event in step_events] == [1, 2]
    assert gateway.operation_store.get_by_id(operation["operationId"])[
        "lifecycleState"
    ] == "VERIFIED"
    gateway.task_service.task_manager.close()
    gateway.operation_store.close()


def test_unsupported_task_step_is_rejected_before_operation_creation(tmp_path):
    gateway = make_gateway(
        tmp_path / "operations.db",
        task_execution=True,
    )
    websocket = authenticated_websocket(gateway)
    payload = task_dispatch_payload("unsupported-task-step")
    payload["payload"]["parameters"]["steps"][0]["parameters"]["command"] = (
        "rm -rf /tmp"
    )

    asyncio.run(gateway.handle(websocket, payload))

    assert websocket.messages[-1]["status"] == "INVALID_TASK_PLAN"
    assert gateway.operation_store.get_by_correlation(
        "unsupported-task-step"
    ) is None
    assert gateway.task_service.task_manager.all_tasks() == []
    gateway.task_service.task_manager.close()
    gateway.operation_store.close()


def test_revoked_session_cannot_derive_task_step_grant(tmp_path):
    gateway = make_gateway(
        tmp_path / "operations.db",
        task_execution=True,
    )
    websocket = authenticated_websocket(gateway)
    authority = gateway.approval_authorizer
    original_derive = authority.authorize_child_operation
    calls = 0

    def revoke_before_derivation(**kwargs):
        nonlocal calls
        calls += 1
        authority.revoke(websocket)
        return original_derive(**kwargs)

    authority.authorize_child_operation = revoke_before_derivation
    _, response, operation = asyncio.run(
        dispatch_and_approve_task(gateway, websocket, "revoked-task-session")
    )

    assert calls == 1
    assert response["status"] == "UNCERTAIN"
    assert operation["lifecycleState"] == "UNCERTAIN"
    assert gateway.task_service.task_manager.all_tasks() == []
    gateway.task_service.task_manager.close()
    gateway.operation_store.close()


def test_child_grant_persistence_failure_never_creates_task_or_dispatches(
    tmp_path,
):
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
    gateway = make_gateway(
        tmp_path / "child-grant-failure.db",
        executor=Executor(skills=skills),
        task_execution=True,
    )
    websocket = authenticated_websocket(gateway)
    operation_store = gateway.operation_store
    persist_grant = operation_store.claim_authorization_grant_and_approve_operation
    attempts = 0

    def fail_second_child(grant, operation, **kwargs):
        nonlocal attempts
        if operation["operationId"].endswith(":step:2"):
            attempts += 1
            raise OSError("injected child-grant persistence failure")
        return persist_grant(grant, operation, **kwargs)

    operation_store.claim_authorization_grant_and_approve_operation = (
        fail_second_child
    )
    _, response, operation = asyncio.run(
        dispatch_and_approve_task(gateway, websocket, "child-grant-failure")
    )

    assert attempts == 1
    assert response["status"] == "UNCERTAIN"
    assert gateway.task_service.task_manager.all_tasks() == []
    assert terminal.calls == 0
    assert operation_store.get_by_id(
        f"{operation['operationId']}:step:1"
    )["lifecycleState"] == "CANCELLED"
    assert operation_store.get_by_id(
        f"{operation['operationId']}:step:2"
    )["lifecycleState"] == "CANCELLED"
    gateway.task_service.task_manager.close()
    operation_store.close()


def test_child_grant_cleanup_failure_preserves_primary_error_and_never_dispatches(
    tmp_path,
    caplog,
):
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
    gateway = make_gateway(
        tmp_path / "child-grant-cleanup-failure.db",
        executor=Executor(skills=skills),
        task_execution=True,
    )
    websocket = authenticated_websocket(gateway)
    operation_store = gateway.operation_store
    persist_child_grant = (
        operation_store.claim_authorization_grant_and_approve_operation
    )
    attempts = 0

    def reject_second_child_grant(grant, operation, **kwargs):
        nonlocal attempts
        if operation["operationId"].endswith(":step:2"):
            attempts += 1
            return False
        return persist_child_grant(grant, operation, **kwargs)

    def fail_cleanup(_operation_ids):
        raise OSError("injected child-operation cleanup failure")

    operation_store.claim_authorization_grant_and_approve_operation = (
        reject_second_child_grant
    )
    gateway.task_service._cancel_unstarted_child_operations = fail_cleanup

    _, response, operation = asyncio.run(
        dispatch_and_approve_task(
            gateway,
            websocket,
            "child-grant-cleanup-failure",
        )
    )

    assert attempts == 1
    assert response["status"] == "UNCERTAIN"
    assert "PermissionError" in operation["executionResult"]["error"]
    assert "Child operation cleanup failed (OSError)" in caplog.text
    assert "injected child-operation cleanup failure" not in caplog.text
    assert gateway.task_service.task_manager.all_tasks() == []
    assert terminal.calls == 0
    assert operation_store.get_by_id(
        f"{operation['operationId']}:step:1"
    )["lifecycleState"] == "APPROVED"
    assert operation_store.get_by_id(
        f"{operation['operationId']}:step:2"
    )["lifecycleState"] == "SUGGESTED"
    stranded_child = operation_store.get_by_id(
        f"{operation['operationId']}:step:1"
    )
    stranded_grant = stranded_child["authorization"]
    database = operation_store.database_identity
    operation_store._connection.execute(
        "UPDATE operations SET parent_operation_id = NULL WHERE operation_id = ?",
        (stranded_child["operationId"],),
    )
    operation_store._connection.execute(
        """
        UPDATE authorization_grants
        SET parent_operation_id = NULL, dispatch_enabled = 1
        WHERE operation_id = ?
        """,
        (stranded_child["operationId"],),
    )
    operation_store._connection.commit()
    gateway.task_service.task_manager.close()
    operation_store.close()

    recovered_store = SQLiteOperationStore(database)
    recovered_store.recover_interrupted()
    recovered_child = recovered_store.get_by_id(stranded_child["operationId"])
    assert recovered_child["lifecycleState"] == "CANCELLED"
    assert not recovered_store.claim_authorization_dispatch(
        grant_id=stranded_grant["grantId"],
        operation_id=stranded_grant["operationId"],
        correlation_id=stranded_grant["requestCorrelationId"],
        request_fingerprint=stranded_grant["requestFingerprint"],
        purpose="execute",
        principal=stranded_grant["authorizer"],
        action=stranded_grant["action"],
        resource_fingerprint=stranded_grant["resourceFingerprint"],
        expires_at=stranded_grant["expiresAt"],
    )
    assert terminal.calls == 0
    recovered_store.close()


def test_restart_cancels_child_operation_even_when_its_grant_was_not_persisted(
    tmp_path,
):
    store = SQLiteOperationStore(tmp_path / "missing-child-grant.db")
    now = "2026-10-22T00:00:00.000+00:00"
    store.create(
        {
            "operationId": "parent-operation",
            "lifecycleState": "UNCERTAIN",
            "createdAt": now,
            "updatedAt": now,
        }
    )
    store.create(
        {
            "operationId": "parent-operation:step:1",
            "parentOperationId": "parent-operation",
            "lifecycleState": "SUGGESTED",
            "createdAt": now,
            "updatedAt": now,
        }
    )

    store.recover_interrupted()

    child = store.get_by_id("parent-operation:step:1")
    assert child["lifecycleState"] == "CANCELLED"
    assert child["executionResult"]["status"] == "CANCELLED"
    store.close()


def test_child_activation_failure_rolls_back_grant_and_never_dispatches(
    tmp_path,
):
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
    database = tmp_path / "child-activation-failure.db"
    gateway = make_gateway(
        database,
        executor=Executor(skills=skills),
        task_execution=True,
    )
    websocket = authenticated_websocket(gateway)
    gateway.operation_store._connection.execute(
        """
        CREATE TRIGGER fail_child_activation
        BEFORE UPDATE OF lifecycle_state ON operations
        WHEN NEW.lifecycle_state = 'EXECUTING'
          AND NEW.parent_operation_id IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'injected child activation failure'); END
        """
    )

    _, response, operation = asyncio.run(
        dispatch_and_approve_task(
            gateway,
            websocket,
            "child-activation-failure",
        )
    )

    child_id = f"{operation['operationId']}:step:1"
    child = gateway.operation_store.get_by_id(child_id)
    grant_id = child["authorization"]["grantId"]
    grant = gateway.operation_store._connection.execute(
        """
        SELECT dispatch_enabled, dispatch_claimed_at
        FROM authorization_grants
        WHERE grant_id = ?
        """,
        (grant_id,),
    ).fetchone()
    task = gateway.task_service.task_manager.get_task(
        f"task-{operation['operationId']}"
    )
    assert response["status"] != "VERIFIED"
    assert child["lifecycleState"] == "CANCELLED"
    assert grant == (0, None)
    assert task is not None and task.status == TaskStatus.UNCERTAIN
    assert terminal.calls == 0
    gateway.task_service.task_manager.close()
    gateway.operation_store.close()


@pytest.mark.parametrize(
    "race",
    ["revocation", "parent_cancellation", "expiry", "task_cancellation"],
)
def test_child_final_dispatch_rejects_races_after_grant_activation(
    tmp_path,
    race,
):
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
    gateway = make_gateway(
        tmp_path / f"{race}.db",
        executor=Executor(skills=skills),
        task_execution=True,
    )
    websocket = authenticated_websocket(gateway)
    store = gateway.operation_store
    activate = store.activate_child_authorization_dispatch
    activated_grants = []

    def activate_then_race(**kwargs):
        activated = activate(**kwargs)
        if not activated:
            return False
        activated_grants.append(kwargs["grant_id"])
        if race == "revocation":
            gateway.approval_authorizer.revoke(websocket)
        elif race == "parent_cancellation":
            parent_operation = store.get_by_id(
                kwargs["parent_operation_id"]
            )
            parent_operation.update(
                {
                    "lifecycleState": "UNCERTAIN",
                    "executionResult": {
                        "success": False,
                        "status": "UNCERTAIN",
                        "error": "Parent was conservatively stopped.",
                    },
                    "updatedAt": datetime.now(UTC).isoformat(),
                }
            )
            store.update(parent_operation)
        elif race == "task_cancellation":
            gateway.task_service.task_manager.request_cancellation(
                kwargs["task_id"],
                attempt_id=kwargs["attempt_id"],
            )
        else:
            store._connection.execute(
                """
                UPDATE authorization_grants SET expires_at = ?
                WHERE grant_id = ?
                """,
                (
                    (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
                    kwargs["grant_id"],
                ),
            )
            store._connection.commit()
        return True

    store.activate_child_authorization_dispatch = activate_then_race
    _, response, operation = asyncio.run(
        dispatch_and_approve_task(
            gateway,
            websocket,
            f"child-dispatch-{race}",
        )
    )

    assert activated_grants
    assert response["status"] != "VERIFIED"
    assert operation["lifecycleState"] != "VERIFIED"
    assert terminal.calls == 0
    task = gateway.task_service.task_manager.get_task(
        f"task-{operation['operationId']}"
    )
    assert task is not None
    if race == "task_cancellation":
        assert task.status == TaskStatus.CANCELLED
        assert task.cancellation_requested_at is not None
    else:
        assert task.status != TaskStatus.CANCELLED
    store.close()
    gateway.task_service.task_manager.close()


def test_cancellation_during_dispatched_step_preserves_uncertainty_and_stops_plan(
    tmp_path,
):
    task_manager_holder = {}

    class CancellingTerminalSkill(TerminalSkill):
        def __init__(self):
            super().__init__()
            self.calls = 0
            self.cancelled = False

        def execute(self, action, **kwargs):
            self.calls += 1
            if not self.cancelled:
                self.cancelled = True
                executing = next(
                    task
                    for task in task_manager_holder["manager"].all_tasks()
                    if task.status == TaskStatus.EXECUTING
                )
                task_manager_holder["manager"].request_cancellation(
                    executing.id,
                    attempt_id=executing.attempt_id,
                )
            return super().execute(action, **kwargs)

    skills = SkillManager()
    skills.registry.unregister("run_command")
    terminal = CancellingTerminalSkill()
    skills.register(terminal)
    gateway = make_gateway(
        tmp_path / "inflight-cancellation.db",
        executor=Executor(skills=skills),
        task_execution=True,
    )
    task_manager_holder["manager"] = gateway.task_service.task_manager
    websocket = authenticated_websocket(gateway)

    _, response, operation = asyncio.run(
        dispatch_and_approve_task(gateway, websocket, "cancel-during-step")
    )

    task = gateway.task_service.task_manager.get_task(
        f"task-{operation['operationId']}"
    )
    assert task is not None
    assert terminal.calls == 1
    assert task.status == TaskStatus.UNCERTAIN
    assert task.cancellation_requested_at is not None
    assert response["status"] != "VERIFIED"
    assert gateway.task_service.task_manager.get_task(task.id).step_results[0][
        "status"
    ] == "COMPLETED"
    assert gateway.task_service.task_manager.get_task(task.id).step_results == [
        gateway.task_service.task_manager.get_task(task.id).step_results[0]
    ]
    assert gateway.task_service.task_manager.get_task(task.id).step_results[0][
        "order"
    ] == 1
    second_child = gateway.operation_store.get_by_id(
        f"{operation['operationId']}:step:2"
    )
    assert second_child["lifecycleState"] == "CANCELLED"
    cancellation_events = [
        event
        for event in gateway.task_service.task_manager.task_events(task.id)
        if event["event_type"] == "CANCELLATION_REQUESTED"
    ]
    assert len(cancellation_events) == 1
    gateway.task_service.task_manager.close()
    gateway.operation_store.close()


def test_gateway_cancellation_during_inflight_skill_preserves_uncertainty(
    tmp_path,
):
    skill_started = threading.Event()
    allow_skill_to_finish = threading.Event()

    class BlockingTerminalSkill(TerminalSkill):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def execute(self, action, **kwargs):
            self.calls += 1
            skill_started.set()
            if not allow_skill_to_finish.wait(timeout=5):
                raise TimeoutError("Test did not release the in-flight skill.")
            return super().execute(action, **kwargs)

    skills = SkillManager()
    skills.registry.unregister("run_command")
    terminal = BlockingTerminalSkill()
    skills.register(terminal)
    gateway = make_gateway(
        tmp_path / "gateway-inflight-cancellation.db",
        executor=Executor(skills=skills),
        task_execution=True,
    )
    websocket = authenticated_websocket(gateway)
    execution_results = []
    execution_errors = []
    correlation_id = "gateway-inflight-cancellation"

    def execute_approved_task():
        try:
            execution_results.append(
                asyncio.run(
                    dispatch_and_approve_task(
                        gateway,
                        websocket,
                        correlation_id,
                    )
                )
            )
        except Exception as error:
            execution_errors.append(error)

    worker = threading.Thread(target=execute_approved_task)
    worker.start()
    try:
        assert skill_started.wait(timeout=5)
        operation_id = gateway.operation_store._connection.execute(
            "SELECT operation_id FROM operations WHERE correlation_id = ?",
            (correlation_id,),
        ).fetchone()[0]
        task_id = f"task-{operation_id}"

        response = deliver_cancellation(
            gateway,
            websocket,
            task_id,
            "gateway-inflight-cancellation-request",
        )

        assert response["status"] == "UNCERTAIN"
        assert response["success"] is False
        assert "did not stop an in-flight skill" in response["message"]
    finally:
        allow_skill_to_finish.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert execution_errors == []
    assert len(execution_results) == 1
    assert terminal.calls == 1
    task = gateway.task_service.task_manager.get_task(task_id)
    assert task is not None and task.status == TaskStatus.UNCERTAIN
    assert gateway.operation_store.get_by_id(
        f"{operation_id}:step:2"
    )["lifecycleState"] == "CANCELLED"
    asyncio.run(
        gateway.handle(
            websocket,
            task_dispatch_payload(correlation_id),
        )
    )
    assert websocket.messages[-1]["result"]["duplicate"] is True
    assert terminal.calls == 1
    gateway.task_service.task_manager.close()
    gateway.operation_store.close()


def test_gateway_cancellation_is_durable_and_blocks_restart_claims(tmp_path):
    database = tmp_path / "gateway-cancellation.db"
    gateway = make_gateway(database, task_execution=True)
    websocket = authenticated_websocket(gateway)
    operation, task = asyncio.run(
        prepare_cancellable_task(gateway, websocket, "cancel-pending-task")
    )

    response = deliver_cancellation(
        gateway,
        websocket,
        task.id,
        "cancel-pending-task-once",
    )
    cancelled = gateway.task_service.task_manager.get_task(task.id)
    assert response["action"] == "TASK_CANCELLATION_RESULT"
    assert response["status"] == "CANCELLED"
    assert response["success"] is True
    assert response["operationId"] == operation["operationId"]
    assert cancelled is not None and cancelled.status == TaskStatus.CANCELLED
    assert cancelled.cancellation_requested_at is not None
    assert gateway.task_service.task_manager.claim_task(task.id) is None
    persisted_request = gateway.operation_store._connection.execute(
        """
        SELECT operation_id, task_id, principal, session_id, action, status
        FROM task_cancellation_requests
        WHERE correlation_id = ?
        """,
        ("cancel-pending-task-once",),
    ).fetchone()
    assert persisted_request == (
        operation["operationId"],
        task.id,
        "test-operator",
        operation["authorization"]["authorizationContext"]["sessionId"],
        "cancel_task_before_dispatch",
        "CANCELLED",
    )

    gateway.task_service.task_manager.queue.close()
    gateway.operation_store.close()
    restarted = make_gateway(database, task_execution=True)
    assert restarted.task_service.task_manager.get_task(task.id).status == TaskStatus.CANCELLED
    assert restarted.task_service.task_manager.claim_task(task.id) is None
    restarted.task_service.task_manager.close()
    restarted.operation_store.close()


@pytest.mark.parametrize("denial", ["different_session", "revoked", "expired"])
def test_gateway_cancellation_rejects_session_mismatch_and_inactive_session(
    tmp_path,
    denial,
):
    gateway = make_gateway(
        tmp_path / f"cancel-denied-{denial}.db",
        task_execution=True,
    )
    websocket = authenticated_websocket(gateway)
    _, task = asyncio.run(
        prepare_cancellable_task(gateway, websocket, f"cancel-denied-{denial}")
    )
    if denial == "different_session":
        requester = authenticated_websocket(gateway)
        expected_status = "UNAUTHORIZED"
    else:
        requester = websocket
        if denial == "revoked":
            gateway.approval_authorizer.revoke(requester)
        else:
            gateway.approval_authorizer._sessions[requester]["expiresAt"] = 0
        expected_status = "AUTHENTICATION_REQUIRED"

    response = deliver_cancellation(
        gateway,
        requester,
        task.id,
        f"cancel-denied-request-{denial}",
    )

    assert response["status"] == expected_status
    assert gateway.task_service.task_manager.get_task(task.id).status == TaskStatus.PENDING
    assert gateway.operation_store._connection.execute(
        "SELECT COUNT(*) FROM task_cancellation_requests"
    ).fetchone()[0] == 0
    gateway.task_service.task_manager.close()
    gateway.operation_store.close()


def test_gateway_cancellation_rejects_expired_signed_grant(tmp_path, monkeypatch):
    gateway = make_gateway(
        tmp_path / "cancel-expired-grant.db",
        task_execution=True,
    )
    websocket = authenticated_websocket(gateway)
    _, task = asyncio.run(
        prepare_cancellable_task(gateway, websocket, "cancel-expired-grant")
    )
    authorize = gateway.approval_authorizer.authorize

    def issue_expired(**kwargs):
        grant = authorize(**kwargs)
        grant["expiresAt"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        grant["signature"] = gateway.approval_authorizer._sign_grant(grant)
        return grant

    monkeypatch.setattr(gateway.approval_authorizer, "authorize", issue_expired)
    response = deliver_cancellation(
        gateway,
        websocket,
        task.id,
        "cancel-expired-grant-request",
    )

    assert response["status"] == "AUTHORIZATION_EXPIRED"
    assert gateway.task_service.task_manager.get_task(task.id).status == TaskStatus.PENDING
    assert gateway.operation_store._connection.execute(
        "SELECT COUNT(*) FROM task_cancellation_requests"
    ).fetchone()[0] == 0
    gateway.task_service.task_manager.close()
    gateway.operation_store.close()


@pytest.mark.parametrize(
    ("boundary", "trigger"),
    [
        (
            "grant_insert",
            """
            CREATE TRIGGER reject_cancel_grant
            BEFORE INSERT ON authorization_grants
            WHEN NEW.purpose = 'cancel_task'
            BEGIN SELECT RAISE(ABORT, 'injected cancel grant failure'); END
            """,
        ),
        (
            "task_update",
            """
            CREATE TRIGGER reject_cancel_task_update
            BEFORE UPDATE ON executive_tasks
            WHEN OLD.task_id = '{task_id}'
            BEGIN SELECT RAISE(ABORT, 'injected cancel task update failure'); END
            """,
        ),
        (
            "task_event",
            """
            CREATE TRIGGER reject_cancel_task_event
            BEFORE INSERT ON executive_task_events
            WHEN NEW.event_type IN ('CANCELLATION_CONFIRMED', 'CANCELLATION_REQUESTED')
            BEGIN SELECT RAISE(ABORT, 'injected cancel task event failure'); END
            """,
        ),
        (
            "request_insert",
            """
            CREATE TRIGGER reject_cancel_request
            BEFORE INSERT ON task_cancellation_requests
            BEGIN SELECT RAISE(ABORT, 'injected cancel request failure'); END
            """,
        ),
        (
            "operation_event",
            """
            CREATE TRIGGER reject_cancel_operation_event
            BEFORE INSERT ON operation_lifecycle_events
            WHEN NEW.event_type = 'TASK_CANCELLATION_REQUESTED'
            BEGIN SELECT RAISE(ABORT, 'injected cancel operation event failure'); END
            """,
        ),
    ],
)
def test_gateway_cancellation_transaction_rolls_back_every_boundary(
    tmp_path,
    boundary,
    trigger,
):
    gateway = make_gateway(
        tmp_path / f"cancel-fault-{boundary}.db",
        task_execution=True,
    )
    websocket = authenticated_websocket(gateway)
    _, task = asyncio.run(
        prepare_cancellable_task(gateway, websocket, f"cancel-fault-{boundary}")
    )
    gateway.operation_store._connection.execute(
        trigger.format(task_id=task.id)
    )

    with pytest.raises(sqlite3.IntegrityError, match="injected cancel"):
        deliver_cancellation(
            gateway,
            websocket,
            task.id,
            f"cancel-fault-request-{boundary}",
        )

    assert gateway.task_service.task_manager.get_task(task.id).status == TaskStatus.PENDING
    assert gateway.operation_store._connection.execute(
        "SELECT COUNT(*) FROM task_cancellation_requests"
    ).fetchone()[0] == 0
    assert gateway.operation_store._connection.execute(
        "SELECT COUNT(*) FROM operation_cancellations"
    ).fetchone()[0] == 0
    assert gateway.operation_store._connection.execute(
        "SELECT COUNT(*) FROM authorization_grants WHERE purpose = 'cancel_task'"
    ).fetchone()[0] == 0
    gateway.task_service.task_manager.close()
    gateway.operation_store.close()


def test_gateway_cancellation_replay_and_concurrent_duplicates_are_one_use(
    tmp_path,
    monkeypatch,
):
    gateway = make_gateway(
        tmp_path / "cancel-replay.db",
        task_execution=True,
    )
    websocket = authenticated_websocket(gateway)
    _, task = asyncio.run(
        prepare_cancellable_task(gateway, websocket, "cancel-replay")
    )
    barrier = threading.Barrier(3)
    transaction_barrier = threading.Barrier(2)
    responses = []
    errors = []
    thread_messages = threading.local()
    original_send = websocket.send

    async def record_thread_message(message):
        response = json.loads(message)
        await original_send(message)
        thread_messages.values.append(response)

    monkeypatch.setattr(websocket, "send", record_thread_message)

    validate_and_cancel = gateway.approval_authorizer.validate_and_cancel_task

    def synchronize_before_transaction(*args, **kwargs):
        transaction_barrier.wait(timeout=5)
        return validate_and_cancel(*args, **kwargs)

    monkeypatch.setattr(
        gateway.approval_authorizer,
        "validate_and_cancel_task",
        synchronize_before_transaction,
    )

    def submit_cancel():
        try:
            thread_messages.values = []
            barrier.wait(timeout=5)
            asyncio.run(
                gateway.handle(
                    websocket,
                    cancellation_message(task.id, "cancel-replay-request"),
                )
            )
            responses.append(thread_messages.values[-1])
        except Exception as error:
            errors.append(error)
            transaction_barrier.abort()

    threads = [threading.Thread(target=submit_cancel) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert not any(thread.is_alive() for thread in threads)
    assert not errors
    assert len(responses) == 2
    assert sum(response["status"] == "CANCELLED" for response in responses) == 1
    assert sum(
        response["status"] == "AUTHORIZATION_REPLAY_OR_EXPIRED"
        for response in responses
    ) == 1
    assert gateway.operation_store._connection.execute(
        "SELECT COUNT(*) FROM task_cancellation_requests"
    ).fetchone()[0] == 1
    assert gateway.task_service.task_manager.get_task(task.id).status == TaskStatus.CANCELLED
    gateway.task_service.task_manager.close()
    gateway.operation_store.close()


def test_gateway_cancellation_racing_with_task_start_prevents_dispatch(tmp_path):
    gateway = make_gateway(
        tmp_path / "cancel-start-race.db",
        task_execution=True,
    )
    websocket = authenticated_websocket(gateway)
    _, task = asyncio.run(
        prepare_cancellable_task(gateway, websocket, "cancel-start-race")
    )
    claimed = gateway.task_service.task_manager.claim_task(task.id)
    assert claimed is not None and claimed.attempt_id is not None
    barrier = threading.Barrier(3)
    start_errors = []
    cancellation_responses = []

    def start_task():
        barrier.wait()
        try:
            gateway.task_service.task_manager.start_task(
                task.id,
                claimed.attempt_id,
            )
        except ValueError as error:
            start_errors.append(error)

    def cancel_task():
        barrier.wait()
        cancellation_responses.append(
            deliver_cancellation(
                gateway,
                websocket,
                task.id,
                "cancel-start-race-request",
            )
        )

    workers = [
        threading.Thread(target=start_task),
        threading.Thread(target=cancel_task),
    ]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(timeout=5)

    persisted = gateway.task_service.task_manager.get_task(task.id)
    assert persisted is not None and persisted.status == TaskStatus.CANCELLED
    assert cancellation_responses[0]["status"] == "CANCELLED"
    assert gateway.task_service.task_manager.claim_task(task.id) is None
    assert (
        not start_errors
        or "cancellation request" in str(start_errors[0])
        or "Invalid task transition" in str(start_errors[0])
    )
    gateway.task_service.task_manager.close()
    gateway.operation_store.close()


def test_gateway_cancellation_keeps_potentially_dispatched_task_uncertain(
    tmp_path,
):
    gateway = make_gateway(
        tmp_path / "cancel-dispatched.db",
        task_execution=True,
    )
    websocket = authenticated_websocket(gateway)
    operation, task = asyncio.run(
        prepare_cancellable_task(gateway, websocket, "cancel-dispatched")
    )
    claimed = gateway.task_service.task_manager.claim_task(task.id)
    assert claimed is not None and claimed.attempt_id is not None
    gateway.task_service.task_manager.start_task(task.id, claimed.attempt_id)
    now = datetime.now(UTC).isoformat()
    gateway.operation_store._connection.execute(
        """
        INSERT INTO authorization_grants (
            grant_id, operation_id, correlation_id, request_fingerprint,
            purpose, principal, expires_at, created_at, action,
            resource_fingerprint, dispatch_claimed_at, parent_operation_id,
            dispatch_enabled
        ) VALUES (?, ?, ?, ?, 'execute', ?, ?, ?, 'run_command', ?, ?, ?, 1)
        """,
        (
            "already-dispatched-child-grant",
            f"{operation['operationId']}:step:1",
            "child-step-1",
            "child-fingerprint",
            "test-operator",
            (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
            now,
            "child-resource",
            now,
            operation["operationId"],
        ),
    )
    gateway.operation_store._connection.commit()

    response = deliver_cancellation(
        gateway,
        websocket,
        task.id,
        "cancel-dispatched-request",
    )

    persisted = gateway.task_service.task_manager.get_task(task.id)
    assert response["status"] == "UNCERTAIN"
    assert response["success"] is False
    assert "did not stop an in-flight skill" in response["message"]
    assert persisted is not None and persisted.status == TaskStatus.UNCERTAIN
    assert gateway.task_service.task_manager.claim_task(task.id) is None
    gateway.task_service.task_manager.close()
    gateway.operation_store.close()


def test_gateway_cancellation_between_child_activation_and_skill_claim_blocks_dispatch(
    tmp_path,
):
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
    gateway = make_gateway(
        tmp_path / "cancel-activation-race.db",
        executor=Executor(skills=skills),
        task_execution=True,
    )
    websocket = authenticated_websocket(gateway)
    activate = gateway.operation_store.activate_child_authorization_dispatch
    cancellation_results = []

    def activate_then_cancel(**kwargs):
        activated = activate(**kwargs)
        if activated:
            worker = threading.Thread(
                target=lambda: cancellation_results.append(
                    deliver_cancellation(
                        gateway,
                        websocket,
                        kwargs["task_id"],
                        "cancel-after-activation",
                    )
                )
            )
            worker.start()
            worker.join(timeout=5)
        return activated

    gateway.operation_store.activate_child_authorization_dispatch = (
        activate_then_cancel
    )
    _, response, operation = asyncio.run(
        dispatch_and_approve_task(
            gateway,
            websocket,
            "cancel-after-child-activation",
        )
    )

    task = gateway.task_service.task_manager.get_task(
        f"task-{operation['operationId']}"
    )
    assert cancellation_results[0]["status"] == "CANCELLED"
    assert terminal.calls == 0
    assert task is not None and task.status == TaskStatus.CANCELLED
    assert response["status"] != "VERIFIED"
    assert gateway.operation_store._connection.execute(
        """
        SELECT dispatch_claimed_at FROM authorization_grants
        WHERE parent_operation_id = ?
        """,
        (operation["operationId"],),
    ).fetchone()[0] is None
    gateway.task_service.task_manager.close()
    gateway.operation_store.close()


def test_interruption_after_activation_before_skill_claim_is_not_replayed(
    tmp_path,
):
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
    database = tmp_path / "interrupted-before-claim.db"
    gateway = make_gateway(
        database,
        executor=Executor(skills=skills),
        task_execution=True,
    )
    websocket = authenticated_websocket(gateway)
    dispatch_attempts = 0

    def interrupt_before_skill(plan, **kwargs):
        nonlocal dispatch_attempts
        dispatch_attempts += 1
        raise RuntimeError("injected interruption before skill claim")

    gateway.executor.execute = interrupt_before_skill
    _, response, operation = asyncio.run(
        dispatch_and_approve_task(
            gateway,
            websocket,
            "interrupted-after-activation",
        )
    )

    assert dispatch_attempts == 1
    assert response["status"] == "UNCERTAIN"
    assert operation["lifecycleState"] == "UNCERTAIN"
    child_id = f"{operation['operationId']}:step:1"
    child = gateway.operation_store.get_by_id(child_id)
    grant_id = child["authorization"]["grantId"]
    assert child["lifecycleState"] == "UNCERTAIN"
    assert terminal.calls == 0
    gateway.task_service.task_manager.close()
    gateway.operation_store.close()

    recovered = SQLiteOperationStore(database)
    recovered.recover_interrupted()
    assert recovered.get_by_id(child_id)["lifecycleState"] == "UNCERTAIN"
    assert not recovered.authorization_dispatch_claimed(
        grant_id=grant_id,
        operation_id=child_id,
        correlation_id=child["authorization"]["requestCorrelationId"],
        request_fingerprint=child["authorization"]["requestFingerprint"],
        principal=child["authorization"]["authorizer"],
        action=child["authorization"]["action"],
        resource_fingerprint=child["authorization"]["resourceFingerprint"],
    )
    recovered.close()

    gateway = make_gateway(
        database,
        executor=Executor(skills=skills),
        task_execution=True,
    )
    websocket = authenticated_websocket(gateway)
    asyncio.run(
        gateway.handle(
            websocket,
            task_dispatch_payload("interrupted-after-activation"),
        )
    )
    assert websocket.messages[-1]["result"]["duplicate"] is True
    assert terminal.calls == 0
    assert dispatch_attempts == 1
    gateway.task_service.task_manager.close()
    gateway.operation_store.close()


def test_interrupted_task_step_is_not_retried_or_reported_verified(tmp_path):
    class TerminalSkillThatRaisesAfterDispatch(TerminalSkill):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def execute(self, action, **kwargs):
            self.calls += 1
            super().execute(action, **kwargs)
            raise RuntimeError("simulated interruption after dispatch")

    skills = SkillManager()
    skills.registry.unregister("run_command")
    terminal = TerminalSkillThatRaisesAfterDispatch()
    skills.register(terminal)
    executor = Executor(skills=skills)
    database = tmp_path / "operations.db"
    gateway = make_gateway(
        database,
        executor=executor,
        task_execution=True,
    )
    websocket = authenticated_websocket(gateway)

    _, response, operation = asyncio.run(
        dispatch_and_approve_task(gateway, websocket, "interrupted-task-step")
    )

    task = gateway.task_service.task_manager.get_task(
        f"task-{operation['operationId']}"
    )
    assert response["status"] == "UNCERTAIN"
    assert task.status == TaskStatus.UNCERTAIN
    assert len(task.step_results) == 1
    assert task.step_results[0]["status"] == "UNCERTAIN"
    assert gateway.operation_store.get_by_id(
        f"{operation['operationId']}:step:1"
    )["lifecycleState"] == "UNCERTAIN"
    assert gateway.operation_store.get_by_id(
        f"{operation['operationId']}:step:2"
    )["lifecycleState"] == "CANCELLED"
    assert terminal.calls == 1
    asyncio.run(
        gateway.handle(
            websocket,
            task_dispatch_payload("interrupted-task-step"),
        )
    )
    assert websocket.messages[-1]["result"]["duplicate"] is True
    assert terminal.calls == 1
    gateway.task_service.task_manager.close()
    recovered_manager = TaskManager(
        database=database,
        operation_store=gateway.operation_store,
    )
    recovered = recovered_manager.get_task(task.id)
    assert recovered.status == TaskStatus.UNCERTAIN
    assert len(recovered.step_results) == 1
    assert recovered_manager.claim_task() is None
    recovered_manager.close()
    gateway.operation_store.close()


def test_completed_dispatch_with_checkpoint_failure_stays_uncertain_and_is_not_replayed(
    tmp_path,
):
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
    database = tmp_path / "checkpoint-failure.db"
    gateway = make_gateway(
        database,
        executor=Executor(skills=skills),
        task_execution=True,
    )
    websocket = authenticated_websocket(gateway)
    manager = gateway.task_service.task_manager

    def fail_checkpoint(*_args, **_kwargs):
        raise OSError("injected checkpoint persistence failure")

    manager.queue.record_step_result = fail_checkpoint
    _, response, operation = asyncio.run(
        dispatch_and_approve_task(gateway, websocket, "checkpoint-failure-task")
    )

    task = manager.get_task(f"task-{operation['operationId']}")
    first_child = gateway.operation_store.get_by_id(
        f"{operation['operationId']}:step:1"
    )
    second_child = gateway.operation_store.get_by_id(
        f"{operation['operationId']}:step:2"
    )
    assert response["status"] == "UNCERTAIN"
    assert task.status == TaskStatus.UNCERTAIN
    assert task.step_results == []
    assert first_child["lifecycleState"] == "COMPLETED"
    assert second_child["lifecycleState"] == "CANCELLED"
    assert terminal.calls == 1

    asyncio.run(
        gateway.handle(
            websocket,
            task_dispatch_payload("checkpoint-failure-task"),
        )
    )
    assert websocket.messages[-1]["result"]["duplicate"] is True
    assert terminal.calls == 1
    manager.close()
    gateway.operation_store.close()


def test_actual_gateway_executor_and_terminal_skill_independently_verify_pwd(tmp_path):
    gateway = make_gateway(tmp_path / "operations.db")
    websocket = authenticated_websocket(gateway)

    proposed, response, operation = asyncio.run(
        dispatch_and_approve(gateway, websocket, "pwd", "successful-run")
    )

    assert proposed["status"] == "PENDING_APPROVAL"
    assert response["action"] == "OPERATION_RESULT"
    assert response["status"] == "VERIFIED"
    assert response["success"] is True
    assert operation["lifecycleState"] == "VERIFIED"
    assert operation["verificationAudit"]["outcome"] == "CONFIRMED_SUCCESS"
    assert operation["verificationAudit"]["operationId"] == operation["operationId"]
    assert operation["verificationAudit"]["requestFingerprint"] == operation["requestFingerprint"]
    assert operation["executionResult"]["success"] is True
    command_result = operation["executionResult"]["result"]
    assert command_result["success"] is True
    assert command_result["results"][0]["result"]["returncode"] == 0
    assert gateway.operation_store.get_by_id(operation["operationId"])[
        "lifecycleState"
    ] ==     "VERIFIED"
    gateway.operation_store.close()


def test_post_dispatch_exception_stays_uncertain_without_retry_or_error_leak(
    tmp_path,
    capsys,
):
    class TerminalSkillThatRaisesAfterExecution(TerminalSkill):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def execute(self, action, **kwargs):
            self.calls += 1
            super().execute(action, **kwargs)
            raise RuntimeError("sensitive simulated executor detail")

    skills = SkillManager()
    skills.registry.unregister("run_command")
    terminal = TerminalSkillThatRaisesAfterExecution()
    skills.register(terminal)
    gateway = make_gateway(
        tmp_path / "operations.db",
        executor=Executor(skills=skills, max_retries=3),
    )
    websocket = authenticated_websocket(gateway)

    _, response, operation = asyncio.run(
        dispatch_and_approve(gateway, websocket, "pwd", "uncertain-terminal")
    )

    assert response["status"] == "UNCERTAIN"
    assert operation["lifecycleState"] == "UNCERTAIN"
    assert operation["executionResult"]["success"] is False
    assert operation["executionResult"]["result"]["results"][0]["status"] == (
        "UNCERTAIN"
    )
    assert operation["executionResult"]["result"]["recovery"][0]["retry"] is False
    assert terminal.calls == 1
    assert "sensitive simulated executor detail" not in json.dumps(operation)
    assert "sensitive simulated executor detail" not in capsys.readouterr().out

    asyncio.run(
        gateway.handle(
            websocket,
            dispatch_payload("pwd", "uncertain-terminal"),
        )
    )
    assert websocket.messages[-1]["result"]["duplicate"] is True
    assert websocket.messages[-1]["status"] == "UNCERTAIN"
    assert terminal.calls == 1
    gateway.operation_store.close()


def test_rejected_final_grant_claim_is_blocked_without_skill_invocation(
    tmp_path,
    monkeypatch,
):
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
    gateway = make_gateway(
        tmp_path / "operations.db",
        executor=Executor(skills=skills),
    )
    websocket = authenticated_websocket(gateway)
    monkeypatch.setattr(
        gateway.operation_store,
        "claim_authorization_dispatch",
        lambda **_kwargs: False
    )

    _, response, operation = asyncio.run(
        dispatch_and_approve(
            gateway,
            websocket,
            "pwd",
            "rejected-final-claim",
        )
    )

    assert response["status"] == "FAILED"
    assert operation["lifecycleState"] == "FAILED"
    assert operation["executionResult"]["result"]["results"][0]["status"] == (
        "BLOCKED"
    )
    assert operation["executionResult"]["result"]["recovery"] == []
    assert terminal.calls == 0
    gateway.operation_store.close()


def _seed_interrupted_gateway_task(
    database,
    *,
    task_status=TaskStatus.EXECUTING,
    parent_state="EXECUTING",
    dispatched_child=False,
):
    store = SQLiteOperationStore(database)
    manager = TaskManager(database=database, operation_store=store)
    parent_id = "recovery-parent"
    now = datetime.now(UTC).isoformat(timespec="milliseconds")
    store.create(
        {
            "operationId": parent_id,
            "correlationId": "recovery-correlation",
            "requestFingerprint": "recovery-fingerprint",
            "verb": "execute_task",
            "lifecycleState": parent_state,
            "createdAt": now,
            "updatedAt": now,
        }
    )
    task = Task(
        id=f"task-{parent_id}",
        goal_id=parent_id,
        title="Interrupted gateway task",
        status=TaskStatus.PENDING,
        parent_operation_id=parent_id,
    )
    manager.create_tasks([task])
    if task_status == TaskStatus.EXECUTING:
        claimed = manager.claim_task(task.id)
        assert claimed is not None and claimed.attempt_id is not None
        manager.start_task(task.id, claimed.attempt_id)

    if dispatched_child:
        child_id = f"{parent_id}:step:1"
        child_correlation = "recovery-child-correlation"
        child_fingerprint = "recovery-child-fingerprint"
        store.create(
            {
                "operationId": child_id,
                "parentOperationId": parent_id,
                "correlationId": child_correlation,
                "requestFingerprint": child_fingerprint,
                "verb": "execute",
                "lifecycleState": "EXECUTING",
                "createdAt": now,
                "updatedAt": now,
            }
        )
        store._connection.execute(
            """
            INSERT INTO authorization_grants (
                grant_id, operation_id, correlation_id,
                request_fingerprint, purpose, principal, expires_at,
                created_at, action, resource_fingerprint,
                dispatch_claimed_at, parent_operation_id, dispatch_enabled
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                "recovery-child-grant",
                child_id,
                child_correlation,
                child_fingerprint,
                "execute",
                "recovery-test",
                (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                now,
                "run_command",
                "recovery-resource",
                now,
                parent_id,
            ),
        )
        store._connection.commit()

    return store, manager, parent_id, task.id


def test_gateway_startup_reconciles_both_stores_idempotently(tmp_path):
    database = tmp_path / "coordinated-recovery-pending.db"
    store, manager, parent_id, task_id = _seed_interrupted_gateway_task(
        database,
        task_status=TaskStatus.PENDING,
    )
    manager.queue.close()
    store.close()

    first = make_gateway(database, task_execution=True)
    recovered_task = first.task_service.task_manager.get_task(task_id)
    assert recovered_task is not None
    assert recovered_task.status == TaskStatus.CANCELLED
    assert recovered_task.cancellation_requested_at is not None
    assert first.operation_store.get_by_id(parent_id)["lifecycleState"] == "UNCERTAIN"
    assert first.task_service.task_manager.claim_task(task_id) is None
    first_task_events = first.task_service.task_manager.queue._connection.execute(
        "SELECT COUNT(*) FROM executive_task_events WHERE task_id = ?",
        (task_id,),
    ).fetchone()[0]
    first_operation_events = len(
        first.operation_store.operation_lifecycle_events(parent_id)
    )
    first.task_service.task_manager.queue.close()
    first.operation_store.close()

    second = make_gateway(database, task_execution=True)
    assert second.task_service.task_manager.get_task(task_id).status == (
        TaskStatus.CANCELLED
    )
    assert second.task_service.task_manager.claim_task(task_id) is None
    assert second.task_service.task_manager.queue._connection.execute(
        "SELECT COUNT(*) FROM executive_task_events WHERE task_id = ?",
        (task_id,),
    ).fetchone()[0] == first_task_events
    assert len(second.operation_store.operation_lifecycle_events(parent_id)) == (
        first_operation_events
    )
    second.task_service.task_manager.queue.close()
    second.operation_store.close()


def test_gateway_startup_recovers_existing_task_store_without_task_service(
    tmp_path,
):
    database = tmp_path / "coordinated-recovery-unconfigured-service.db"
    store, manager, parent_id, task_id = _seed_interrupted_gateway_task(
        database,
        task_status=TaskStatus.PENDING,
    )
    manager.queue.close()
    store.close()

    gateway = make_gateway(database)
    recovered_manager = TaskManager(
        database=database,
        operation_store=gateway.operation_store,
    )
    assert gateway.operation_store.get_by_id(parent_id)["lifecycleState"] == (
        "UNCERTAIN"
    )
    assert recovered_manager.get_task(task_id).status == TaskStatus.CANCELLED
    assert recovered_manager.claim_task(task_id) is None
    recovered_manager.queue.close()
    gateway.operation_store.close()


def test_gateway_startup_preserves_uncertain_dispatched_task_without_replay(
    tmp_path,
):
    database = tmp_path / "coordinated-recovery-dispatched.db"
    store, manager, parent_id, task_id = _seed_interrupted_gateway_task(
        database,
        dispatched_child=True,
    )
    manager.queue.close()
    store.close()

    gateway = make_gateway(database, task_execution=True)
    task = gateway.task_service.task_manager.get_task(task_id)
    child = gateway.operation_store.get_by_id(f"{parent_id}:step:1")
    grant = gateway.operation_store._connection.execute(
        """
        SELECT dispatch_enabled, dispatch_claimed_at
        FROM authorization_grants
        WHERE grant_id = ?
        """,
        ("recovery-child-grant",),
    ).fetchone()
    assert task is not None and task.status == TaskStatus.UNCERTAIN
    assert child["lifecycleState"] == "UNCERTAIN"
    assert grant[0] == 0 and grant[1] is not None
    assert gateway.task_service.task_manager.claim_task(task_id) is None
    assert gateway.task_service.task_manager.claim_task() is None
    gateway.task_service.task_manager.queue.close()
    gateway.operation_store.close()


def test_coordinated_recovery_rolls_back_both_stores_on_task_event_failure(
    tmp_path,
):
    database = tmp_path / "coordinated-recovery-fault.db"
    store, manager, parent_id, task_id = _seed_interrupted_gateway_task(
        database,
        dispatched_child=True,
    )
    store._connection.execute(
        """
        CREATE TRIGGER fail_recovery_task_event
        BEFORE INSERT ON executive_task_events
        WHEN NEW.event_type = 'INTERRUPTION_RECOVERED'
        BEGIN SELECT RAISE(ABORT, 'injected task event failure'); END
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="injected task event failure"):
        store.recover_interrupted_with_tasks()

    assert store.get_by_id(parent_id)["lifecycleState"] == "EXECUTING"
    assert store.get_by_id(f"{parent_id}:step:1")["lifecycleState"] == (
        "EXECUTING"
    )
    assert store._connection.execute(
        """
        SELECT dispatch_enabled FROM authorization_grants
        WHERE grant_id = ?
        """,
        ("recovery-child-grant",),
    ).fetchone() == (1,)
    assert manager.get_task(task_id).status == TaskStatus.EXECUTING
    store._connection.execute("DROP TRIGGER fail_recovery_task_event")
    store.recover_interrupted_with_tasks()
    assert manager.get_task(task_id).status == TaskStatus.UNCERTAIN
    manager.queue.close()
    store.close()


def test_coordinated_recovery_fails_closed_on_orphaned_or_inconsistent_tasks(
    tmp_path,
):
    orphan_database = tmp_path / "orphan-task-recovery.db"
    orphan_store = SQLiteOperationStore(orphan_database)
    orphan_manager = TaskManager(
        database=orphan_database,
        operation_store=orphan_store,
    )
    orphan_manager.create_tasks(
        [
            Task(
                id="task-orphan",
                goal_id="orphan",
                title="Orphan task",
                parent_operation_id="missing-parent",
            )
        ]
    )
    with pytest.raises(RuntimeError, match="missing parent operation"):
        orphan_store.recover_interrupted_with_tasks()
    assert orphan_manager.get_task("task-orphan").status == TaskStatus.PENDING
    orphan_manager.queue.close()
    orphan_store.close()

    orphan_child_database = tmp_path / "orphan-child-recovery.db"
    orphan_child_store = SQLiteOperationStore(orphan_child_database)
    orphan_child_manager = TaskManager(
        database=orphan_child_database,
        operation_store=orphan_child_store,
    )
    orphan_child_store.create(
        {
            "operationId": "orphan-child",
            "parentOperationId": "missing-parent",
            "lifecycleState": "SUGGESTED",
            "createdAt": datetime.now(UTC).isoformat(),
            "updatedAt": datetime.now(UTC).isoformat(),
        }
    )
    with pytest.raises(RuntimeError, match="missing parent operation"):
        orphan_child_store.recover_interrupted_with_tasks()
    assert orphan_child_store.get_by_id("orphan-child")["lifecycleState"] == (
        "SUGGESTED"
    )
    orphan_child_manager.queue.close()
    orphan_child_store.close()

    orphan_grant_database = tmp_path / "orphan-grant-recovery.db"
    orphan_grant_store, orphan_grant_manager, parent_id, _ = (
        _seed_interrupted_gateway_task(
            orphan_grant_database,
            task_status=TaskStatus.PENDING,
        )
    )
    child_id = f"{parent_id}:step:orphan-grant"
    now = datetime.now(UTC).isoformat(timespec="milliseconds")
    orphan_grant_store.create(
        {
            "operationId": child_id,
            "parentOperationId": parent_id,
            "lifecycleState": "SUGGESTED",
            "createdAt": now,
            "updatedAt": now,
        }
    )
    orphan_grant_store._connection.execute(
        """
        INSERT INTO authorization_grants (
            grant_id, operation_id, correlation_id,
            request_fingerprint, purpose, principal, expires_at,
            created_at, parent_operation_id, dispatch_enabled
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            "orphan-child-grant",
            child_id,
            "orphan-child-correlation",
            "orphan-child-fingerprint",
            "execute",
            "recovery-test",
            now,
            now,
            "missing-parent",
        ),
    )
    orphan_grant_store._connection.commit()
    with pytest.raises(RuntimeError, match="child authorization grant"):
        orphan_grant_store.recover_interrupted_with_tasks()
    assert orphan_grant_manager.get_task("task-recovery-parent").status == (
        TaskStatus.PENDING
    )
    orphan_grant_manager.queue.close()
    orphan_grant_store.close()

    inconsistent_database = tmp_path / "inconsistent-task-recovery.db"
    inconsistent_store, inconsistent_manager, _, task_id = (
        _seed_interrupted_gateway_task(
            inconsistent_database,
            task_status=TaskStatus.PENDING,
            parent_state="VERIFIED",
        )
    )
    with pytest.raises(RuntimeError, match="terminal or unapproved parent"):
        inconsistent_store.recover_interrupted_with_tasks()
    assert inconsistent_manager.get_task(task_id).status == TaskStatus.PENDING
    inconsistent_manager.queue.close()
    inconsistent_store.close()

    lifecycle_database = tmp_path / "task-lifecycle-mismatch-recovery.db"
    lifecycle_store, lifecycle_manager, _, lifecycle_task_id = (
        _seed_interrupted_gateway_task(
            lifecycle_database,
            task_status=TaskStatus.PENDING,
        )
    )
    row = lifecycle_store._connection.execute(
        "SELECT task_data FROM executive_tasks WHERE task_id = ?",
        (lifecycle_task_id,),
    ).fetchone()
    task_document = json.loads(row[0])
    task_document["status"] = TaskStatus.READY.value
    lifecycle_store._connection.execute(
        "UPDATE executive_tasks SET task_data = ? WHERE task_id = ?",
        (json.dumps(task_document), lifecycle_task_id),
    )
    lifecycle_store._connection.commit()
    with pytest.raises(RuntimeError, match="inconsistent persisted lifecycle"):
        lifecycle_store.recover_interrupted_with_tasks()
    assert lifecycle_manager.get_task(lifecycle_task_id).status == (
        TaskStatus.PENDING
    )
    lifecycle_manager.queue.close()
    lifecycle_store.close()


def test_concurrent_coordinated_recovery_records_one_task_transition(tmp_path):
    database = tmp_path / "concurrent-coordinated-recovery.db"
    store, manager, parent_id, task_id = _seed_interrupted_gateway_task(
        database,
        task_status=TaskStatus.PENDING,
    )
    other_store = SQLiteOperationStore(database)

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda current: current.recover_interrupted_with_tasks(),
                (store, other_store),
            )
        )

    assert sum(results) == 2
    assert manager.get_task(task_id).status == TaskStatus.CANCELLED
    assert manager.queue._connection.execute(
        """
        SELECT COUNT(*) FROM executive_task_events
        WHERE task_id = ? AND event_type = 'RECOVERY_CANCELLED_BEFORE_DISPATCH'
        """,
        (task_id,),
    ).fetchone() == (1,)
    assert len(store.operation_lifecycle_events(parent_id)) == 2
    manager.queue.close()
    store.close()
    other_store.close()


def test_confirmation_rejection_is_recorded_before_terminal_dispatch(
    tmp_path,
    monkeypatch,
):
    gateway = make_gateway(tmp_path / "operations.db")
    websocket = authenticated_websocket(gateway)

    def reject_if_dispatched(*_args, **_kwargs):
        raise AssertionError("A command requiring confirmation was dispatched.")

    monkeypatch.setattr(
        "ultron.skills.terminal.skill.subprocess.run",
        reject_if_dispatched,
    )

    _, response, operation = asyncio.run(
        dispatch_and_approve(
            gateway,
            websocket,
            "echo this command must not run",
            "confirmation-required",
        )
    )

    assert response["status"] == "FAILED"
    assert operation["lifecycleState"] == "FAILED"
    command_result = operation["executionResult"]["result"]["results"][0]["result"]
    assert command_result["blocked"] is True
    assert gateway.operation_store.get_by_id(operation["operationId"])[
        "lifecycleState"
    ] == "FAILED"
    gateway.operation_store.close()


def test_nonzero_command_outcome_is_uncertain_not_reported_complete(
    tmp_path,
    monkeypatch,
):
    gateway = make_gateway(tmp_path / "operations.db")
    websocket = authenticated_websocket(gateway)

    def report_nonzero_exit(arguments, **_kwargs):
        return subprocess.CompletedProcess(arguments, 17, "", "test failure")

    monkeypatch.setattr(
        "ultron.skills.terminal.skill.subprocess.run",
        report_nonzero_exit,
    )

    _, response, operation = asyncio.run(
        dispatch_and_approve(gateway, websocket, "pwd", "nonzero-exit")
    )

    assert response["status"] == "UNCERTAIN"
    assert response["success"] is False
    assert operation["lifecycleState"] == "UNCERTAIN"
    command_result = operation["executionResult"]["result"]["results"][0]["result"]
    assert command_result["returncode"] == 17
    assert gateway.operation_store.get_by_id(operation["operationId"])[
        "lifecycleState"
    ] == "UNCERTAIN"
    gateway.operation_store.close()


def test_executor_exception_after_approval_is_persisted_as_uncertain(tmp_path):
    class ExecutorThatMayHaveExecuted:
        def execute(self, plan, **kwargs):
            raise RuntimeError("executor interrupted after dispatch")

    gateway = make_gateway(
        tmp_path / "operations.db",
        executor=ExecutorThatMayHaveExecuted(),
    )
    websocket = authenticated_websocket(gateway)

    _, response, operation = asyncio.run(
        dispatch_and_approve(
            gateway,
            websocket,
            "pwd",
            "executor-interrupted",
        )
    )

    assert response["status"] == "UNCERTAIN"
    assert operation["lifecycleState"] == "UNCERTAIN"
    assert operation["executionResult"]["error"] == (
        "executor interrupted after dispatch"
    )
    assert gateway.operation_store.get_by_id(operation["operationId"])[
        "lifecycleState"
    ] == "UNCERTAIN"
    gateway.operation_store.close()


def test_missing_command_executable_is_reported_failed(tmp_path, monkeypatch):
    missing_path = tmp_path / "empty-path"
    missing_path.mkdir()
    monkeypatch.setenv("PATH", str(missing_path))
    gateway = make_gateway(tmp_path / "operations.db")
    websocket = authenticated_websocket(gateway)

    _, response, operation = asyncio.run(
        dispatch_and_approve(
            gateway,
            websocket,
            "pwd",
            "missing-python",
        )
    )

    assert response["status"] == "FAILED"
    assert operation["lifecycleState"] == "FAILED"
    command_result = operation["executionResult"]["result"]["results"][0]["result"]
    assert command_result["not_dispatched"] is True
    assert command_result["returncode"] is None
    assert "not found" in command_result["stderr"].lower()
    gateway.operation_store.close()


def test_duplicate_dispatch_and_approval_do_not_execute_twice(tmp_path):
    gateway = make_gateway(tmp_path / "operations.db")
    websocket = authenticated_websocket(gateway)
    calls = 0
    execute = gateway.executor.execute

    def count_executions(plan, **kwargs):
        nonlocal calls
        calls += 1
        return execute(plan, **kwargs)

    gateway.executor.execute = count_executions
    payload = dispatch_payload("pwd", "same-request")

    asyncio.run(gateway.handle(websocket, payload))
    first_id = websocket.messages[-1]["result"]["operationId"]
    asyncio.run(gateway.handle(websocket, payload))
    duplicate = websocket.messages[-1]

    assert duplicate["result"]["operationId"] == first_id
    assert duplicate["result"]["duplicate"] is True
    assert len(gateway._operations) == 1

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "APPROVE_OPERATION",
                "correlationId": "approve-once",
                "payload": approval_payload(gateway, first_id),
            },
        )
    )
    assert calls == 1

    asyncio.run(gateway.handle(websocket, payload))
    assert websocket.messages[-1]["status"] == "VERIFIED"
    assert websocket.messages[-1]["result"]["operationId"] == first_id
    assert calls == 1

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "APPROVE_OPERATION",
                "correlationId": "approve-twice",
                "payload": approval_payload(gateway, first_id),
            },
        )
    )
    assert websocket.messages[-1]["status"] == "INVALID_STATE"
    assert calls == 1
    gateway.operation_store.close()


def test_correlation_reuse_for_different_command_is_rejected(tmp_path):
    gateway = make_gateway(tmp_path / "operations.db")
    websocket = authenticated_websocket(gateway)

    async def run():
        await gateway.handle(websocket, dispatch_payload("pwd", "reused-id"))
        await gateway.handle(
            websocket,
            dispatch_payload("echo changed", "reused-id"),
        )

    asyncio.run(run())

    assert websocket.messages[-1]["status"] == "IDEMPOTENCY_CONFLICT"
    assert len(gateway._operations) == 1
    gateway.operation_store.close()


def test_interrupted_operation_survives_restart_as_uncertain(tmp_path):
    database = tmp_path / "operations.db"
    first_gateway = make_gateway(database)
    websocket = authenticated_websocket(first_gateway)
    asyncio.run(
        first_gateway.handle(
            websocket,
            dispatch_payload("pwd", "interrupted-run"),
        )
    )
    operation_id = websocket.messages[-1]["result"]["operationId"]
    operation = first_gateway._find_operation(operation_id)
    async def stop_after_approval(*_args):
        return None

    first_gateway._execute_approved_operation = stop_after_approval
    asyncio.run(
        first_gateway.handle(
            websocket,
            {
                "action": "APPROVE_OPERATION",
                "correlationId": "approve-interrupted-run",
                "payload": approval_payload(first_gateway, operation_id),
            },
        )
    )
    operation = first_gateway._find_operation(operation_id)
    assert operation["lifecycleState"] == "APPROVED"
    first_gateway._op_update(operation, lifecycleState="EXECUTING")
    first_gateway.operation_store.close()

    restarted_gateway = make_gateway(database)
    recovered = restarted_gateway._find_operation(operation_id)

    assert recovered["lifecycleState"] == "UNCERTAIN"
    assert recovered["executionResult"]["status"] == "UNCERTAIN"
    assert restarted_gateway.operation_store.get_by_correlation(
        "interrupted-run"
    )["operationId"] == operation_id

    replay_socket = authenticated_websocket(restarted_gateway)
    asyncio.run(
        restarted_gateway.handle(
            replay_socket,
            dispatch_payload("pwd", "interrupted-run"),
        )
    )
    assert replay_socket.messages[-1]["status"] == "UNCERTAIN"
    assert replay_socket.messages[-1]["result"]["operationId"] == operation_id
    restarted_gateway.operation_store.close()


def test_malformed_dispatch_does_not_create_or_persist_operation(tmp_path):
    gateway = make_gateway(tmp_path / "operations.db")
    websocket = authenticated_websocket(gateway)

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "DISPATCH_COMMAND",
                "correlationId": "malformed",
                "payload": {
                    "commandText": "execute pwd",
                    "actionVerb": "execute",
                },
            },
        )
    )

    assert websocket.messages[-1]["action"] == "ERROR"
    assert websocket.messages[-1]["success"] is False
    assert not gateway._operations
    assert gateway.operation_store.get_by_correlation("malformed") is None
    gateway.operation_store.close()


def test_completed_operation_cannot_be_approved_into_false_verification(tmp_path):
    gateway = make_gateway(tmp_path / "operations.db")
    websocket = authenticated_websocket(gateway)
    operation = {
        "title": "legacy completed operation",
        "intent": "legacy intent",
        "expectedEffect": "legacy effect",
        "lifecycleState": "COMPLETED",
        "verb": "execute",
        "commandText": "pwd",
        "parameters": {"subject": "pwd"},
        "executionResult": {"success": True},
    }
    gateway._record_operation(operation)

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "APPROVE_OPERATION",
                "correlationId": "second-approval",
                "payload": {"operationId": operation["operationId"]},
            },
        )
    )

    assert websocket.messages[-1]["status"] == "INVALID_STATE"
    assert operation["lifecycleState"] == "COMPLETED"
    assert operation.get("verificationAudit") is None
    gateway.operation_store.close()


@pytest.mark.parametrize("verb", ["ask", "plan", "debug"])
def test_cognitive_plan_verbs_are_rejected_before_runtime_or_persistence(
    tmp_path,
    verb,
):
    class RuntimeThatMustNotRun:
        state = RuntimeState()

        def run_cycle(self, _event):
            raise AssertionError("Broad cognitive approval reached execution.")

    gateway = make_gateway(
        tmp_path / "operations.db",
        runtime=RuntimeThatMustNotRun(),
    )
    websocket = authenticated_websocket(gateway)
    message = {
        "action": "DISPATCH_COMMAND",
        "correlationId": f"blocked-{verb}",
        "payload": {
            "commandText": "perform a high-level objective",
            "actionVerb": verb,
            "parameters": {},
        },
    }

    asyncio.run(gateway.handle(websocket, message))

    assert websocket.messages[-1]["success"] is False
    assert websocket.messages[-1]["status"] == "PLAN_APPROVAL_UNAVAILABLE"
    assert gateway._find_request(f"blocked-{verb}") is None
    gateway.operation_store.close()


def test_legacy_cognitive_operation_cannot_be_approved_or_executed(tmp_path):
    class RuntimeThatMustNotRun:
        state = RuntimeState()

        def run_cycle(self, _event):
            raise AssertionError("Persisted broad approval reached execution.")

    gateway = make_gateway(
        tmp_path / "operations.db",
        runtime=RuntimeThatMustNotRun(),
    )
    websocket = authenticated_websocket(gateway)
    operation = {
        "title": "ask: objective",
        "intent": "legacy broad approval",
        "expectedEffect": "unknown",
        "lifecycleState": "SUGGESTED",
        "verb": "ask",
        "commandText": "perform a high-level objective",
        "parameters": {},
        "correlationId": "legacy-cognitive",
        "requestFingerprint": "legacy-fingerprint",
    }
    gateway._record_operation(operation)

    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "APPROVE_OPERATION",
                "correlationId": "approve-legacy",
                "payload": approval_payload(gateway, operation["operationId"]),
            },
        )
    )
    assert websocket.messages[-1]["status"] == "PLAN_APPROVAL_UNAVAILABLE"
    assert operation["lifecycleState"] == "SUGGESTED"

    asyncio.run(
        gateway._execute_approved_operation(
            websocket,
            "direct-legacy-execution",
            operation,
        )
    )
    assert operation["lifecycleState"] == "FAILED"
    assert operation["executionResult"]["status"] == "PLAN_APPROVAL_UNAVAILABLE"
    gateway.operation_store.close()


def test_live_websocket_interface_delivers_real_execution_result(tmp_path, monkeypatch):
    async def run():
        database = tmp_path / "live-interface.db"
        token = secrets.token_urlsafe(32)
        monkeypatch.setenv(
            "ULTRON_OPERATION_DB",
            str(database),
        )
        monkeypatch.setenv("ULTRON_INTERFACE_AUTH_TOKEN", token)
        monkeypatch.setenv(
            "ULTRON_INTERFACE_AUTH_PRINCIPAL",
            "checkpoint4-integration-operator",
        )
        skills = SkillManager()
        container = SimpleNamespace(
            event_bus=EventBus(),
            runtime=SimpleNamespace(state=RuntimeState()),
            executor=Executor(skills=skills),
            skills=skills,
        )
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]

        server_task = asyncio.create_task(
            serve_interface(container, host="127.0.0.1", port=port)
        )
        connection = None
        try:
            for _ in range(40):
                try:
                    connection = await websockets.connect(
                        f"ws://127.0.0.1:{port}{ENDPOINT_PATH}"
                    )
                    break
                except OSError:
                    await asyncio.sleep(0.05)
            assert connection is not None, "Gateway WebSocket did not start."

            async with connection:
                connected = json.loads(await connection.recv())
                assert connected["type"] == "connected"
                assert connected["authenticationRequired"] is True
                await connection.send(
                    json.dumps({
                        "action": "AUTHENTICATE",
                        "correlationId": "socket-authentication",
                        "credential": token,
                    })
                )
                authenticated = json.loads(await connection.recv())
                assert authenticated["status"] == "AUTHENTICATED"
                assert authenticated["principal"] == "checkpoint4-integration-operator"
                await connection.send(
                    json.dumps(dispatch_payload("pwd", "socket-operation"))
                )
                proposed = json.loads(await connection.recv())
                assert proposed["status"] == "PENDING_APPROVAL"
                operation_id = proposed["result"]["operationId"]
                await connection.send(
                    json.dumps(
                        {
                            "action": "APPROVE_OPERATION",
                            "correlationId": "socket-approval",
                            "payload": {
                                "operationId": operation_id,
                                "correlationId": proposed["result"]["correlationId"],
                                "requestFingerprint": proposed["result"]["requestFingerprint"],
                            },
                        }
                    )
                )
                result = json.loads(await connection.recv())

            assert result["action"] == "OPERATION_RESULT"
            assert result["status"] == "VERIFIED"
            assert result["success"] is True
            assert result["result"]["executionResult"]["result"]["success"] is True
        finally:
            if connection is not None:
                await connection.close()
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass

        store = SQLiteOperationStore(database)
        operation = store.get_by_correlation("socket-operation")
        assert operation["lifecycleState"] == "VERIFIED"
        assert operation["authorization"]["authorizer"] == "checkpoint4-integration-operator"
        store.close()

    asyncio.run(run())
