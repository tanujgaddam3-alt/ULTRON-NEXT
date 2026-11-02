from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from uuid import uuid4

from ultron.application.runtime.operation_verification import (
    LocalReadOnlyOperationVerifier,
    LocalReadOnlyTaskVerifier,
)
from ultron.application.runtime.operation_store import SQLiteOperationStore
from ultron.domain.enums import TaskStatus
from ultron.domain.plan import Plan, PlanStep
from ultron.domain.task import Task
from ultron.executive.tasks.sqlite_queue import SQLiteTaskQueue
from ultron.security.identity.authorization import (
    AuthorizationContext,
    resource_fingerprint,
)
from ultron.security.identity.authenticator import WebSocketAuthenticator


class TaskService:
    """
    Coordinates task operations.

    The task service is the real application boundary that turns a queued task
    into a durable execution attempt through the executor and verification store.
    """

    def __init__(self, task_manager, executor=None, operation_store=None):
        self.task_manager = task_manager
        self.executor = executor
        self.operation_store = operation_store or getattr(
            task_manager,
            "operation_store",
            None,
        )

    @staticmethod
    def _request_fingerprint(
        verb: str,
        command_text: str,
        parameters: dict,
    ) -> str:
        canonical = json.dumps(
            {
                "verb": verb,
                "commandText": command_text.strip(),
                "parameters": parameters,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def validate_authorized_plan(parameters: dict) -> Plan:
        goal = parameters.get("goal")
        raw_steps = parameters.get("steps")
        if (
            set(parameters) != {"goal", "steps"}
            or not isinstance(goal, str)
            or not goal.strip()
            or not isinstance(raw_steps, list)
            or not 1 <= len(raw_steps) <= 8
        ):
            raise ValueError("Task plan must have a goal and between 1 and 8 steps.")

        plan = Plan.model_validate({"goal": goal, "steps": raw_steps})
        orders: set[int] = set()
        for raw_step, step in zip(raw_steps, plan.steps):
            if not isinstance(raw_step, dict) or set(raw_step) != {
                "order",
                "action",
                "parameters",
                "depends_on",
            }:
                raise ValueError("Task steps must use the canonical step fields.")
            if (
                isinstance(step.order, bool)
                or not isinstance(step.order, int)
                or step.order < 1
                or any(
                    isinstance(dependency, bool)
                    or not isinstance(dependency, int)
                    for dependency in step.depends_on
                )
            ):
                raise ValueError("Task step identifiers and dependencies must be integers.")
            command = step.parameters.get("command")
            if (
                step.order in orders
                or step.action != "run_command"
                or set(step.parameters) != {"command"}
                or command not in {"pwd", "whoami"}
            ):
                raise ValueError(
                    "Task steps are limited to uniquely ordered read-only pwd "
                    "and whoami commands."
                )
            if any(
                dependency not in orders
                for dependency in step.depends_on
            ):
                raise ValueError(
                    "Task dependencies must reference an earlier step."
                )
            orders.add(step.order)
        if [step.order for step in plan.steps] != sorted(orders):
            raise ValueError("Task steps must be ordered by their step identifiers.")
        return plan

    def execute_authorized_plan(
        self,
        parent_operation: dict,
        parent_context: AuthorizationContext,
    ) -> dict:
        if (
            self.executor is None
            or not isinstance(self.operation_store, SQLiteOperationStore)
        ):
            raise RuntimeError(
                "Authorized task execution requires the configured executor "
                "and shared operation store."
            )
        if not isinstance(self.task_manager.queue, SQLiteTaskQueue):
            raise RuntimeError(
                "Authorized task execution requires a durable SQLite task queue."
            )
        if (
            self.task_manager.queue.database_identity == ":memory:"
            or self.task_manager.queue.database_identity
            != self.operation_store.database_identity
        ):
            raise RuntimeError(
                "Authorized task and operation records must share one durable SQLite database."
            )
        authority = getattr(self.executor, "authorization_authority", None)
        if type(authority) is not WebSocketAuthenticator:
            raise RuntimeError("Authorized task execution has no trusted authority.")

        persisted_parent = self.operation_store.get_by_id(
            parent_operation.get("operationId", "")
        )
        parameters = parent_operation.get("parameters")
        if (
            not isinstance(parameters, dict)
            or persisted_parent is None
            or persisted_parent.get("lifecycleState") != "EXECUTING"
            or persisted_parent.get("requestFingerprint")
            != parent_operation.get("requestFingerprint")
            or persisted_parent.get("parameters") != parameters
            or not self.operation_store.authorization_dispatch_claimed(
                grant_id=parent_context.grant_id,
                operation_id=parent_context.operation_id,
                correlation_id=parent_context.request_correlation_id,
                request_fingerprint=parent_context.request_fingerprint,
                principal=parent_context.principal,
                action=parent_context.action,
                resource_fingerprint=parent_context.resource_fingerprint,
            )
            or not authority.validate_context(
                parent_context,
                action="run_task",
                parameters=parameters,
                operation_id=parent_operation.get("operationId", ""),
                correlation_id=parent_operation.get("correlationId", ""),
            )
        ):
            raise PermissionError(
                "The task plan is not bound to an active approved operation."
            )

        plan = self.validate_authorized_plan(parameters)
        task_id = f"task-{parent_operation['operationId']}"
        if self.task_manager.get_task(task_id) is not None:
            raise ValueError("This approved task already has a durable task record.")
        contexts = []
        for step in plan.steps:
            command = str(step.parameters["command"])
            child_id = f"{parent_operation['operationId']}:step:{step.order}"
            child_correlation = str(uuid4())
            child_parameters = {"subject": command}
            child_command_text = f"Task step {step.order}: {command}"
            child_fingerprint = self._request_fingerprint(
                "execute",
                child_command_text,
                child_parameters,
            )
            child_operation = {
                "operationId": child_id,
                "parentOperationId": parent_context.operation_id,
                "correlationId": child_correlation,
                "requestFingerprint": child_fingerprint,
                "verb": "execute",
                "commandText": child_command_text,
                "parameters": child_parameters,
                "expectedEffect": (
                    "Return the current working directory without changing state."
                    if command == "pwd"
                    else "Return the current operating-system user without changing state."
                ),
                "lifecycleState": "SUGGESTED",
                "createdAt": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "updatedAt": datetime.now(UTC).isoformat(timespec="milliseconds"),
            }
            child_grant = authority.authorize_child_operation(
                parent_context=parent_context,
                parent_operation=parent_operation,
                operation_store=self.operation_store,
                child_operation=child_operation,
                action="run_command",
                resource_fingerprint=resource_fingerprint(
                    "run_command",
                    {"command": command},
                ),
            )
            if child_grant is None:
                raise PermissionError(
                    f"Trusted authority refused task step {step.order}."
                )
            self.operation_store.create(child_operation)
            approved_child = {
                **child_operation,
                "lifecycleState": "APPROVED",
                "authorization": {
                    "policy": "DERIVED_FROM_APPROVED_TASK_PLAN",
                    "required": True,
                    "granted": True,
                    "authorizer": child_grant["principal"],
                    "authenticated": True,
                    "grantId": child_grant["grantId"],
                    "operationId": child_grant["operationId"],
                    "operationCorrelationId": child_grant[
                        "operationCorrelationId"
                    ],
                    "requestCorrelationId": child_grant[
                        "requestCorrelationId"
                    ],
                    "requestFingerprint": child_grant["requestFingerprint"],
                    "action": child_grant["action"],
                    "resourceFingerprint": child_grant[
                        "resourceFingerprint"
                    ],
                    "grantedAt": child_grant["issuedAt"],
                    "expiresAt": child_grant["expiresAt"],
                    "signature": child_grant["signature"],
                    "authorizationContext": child_grant[
                        "authorizationContext"
                    ],
                },
                "updatedAt": datetime.now(UTC).isoformat(timespec="milliseconds"),
            }
            try:
                grant_persisted = (
                    self.operation_store
                    .claim_authorization_grant_and_approve_operation(
                        {
                            "grantId": child_grant["grantId"],
                            "operationId": child_grant["operationId"],
                            "correlationId": child_grant["requestCorrelationId"],
                            "requestFingerprint": child_grant["requestFingerprint"],
                            "purpose": child_grant["purpose"],
                            "principal": child_grant["principal"],
                            "expiresAt": child_grant["expiresAt"],
                            "issuedAt": child_grant["issuedAt"],
                            "action": child_grant["action"],
                            "resourceFingerprint": child_grant[
                                "resourceFingerprint"
                            ],
                        },
                        approved_child,
                        parent_operation_id=parent_context.operation_id,
                    )
                )
            except Exception as error:
                try:
                    self._cancel_unstarted_child_operations(
                        [
                            *(context.operation_id for context in contexts),
                            child_id,
                        ]
                    )
                except Exception as cleanup_error:
                    error.add_note(
                        "Child operation cleanup failed "
                        f"({type(cleanup_error).__name__}); inspect the "
                        "persisted task-step operation records."
                    )
                raise
            if not grant_persisted:
                error = PermissionError(
                    f"Task step {step.order} could not be durably approved."
                )
                try:
                    self._cancel_unstarted_child_operations(
                        [
                            *(context.operation_id for context in contexts),
                            child_id,
                        ]
                    )
                except Exception as cleanup_error:
                    error.add_note(
                        "Child operation cleanup failed "
                        f"({type(cleanup_error).__name__}); inspect the "
                        "persisted task-step operation records."
                    )
                raise error
            contexts.append(AuthorizationContext.from_grant(child_grant))

        task = Task(
            id=task_id,
            goal_id=parent_operation["operationId"],
            title=plan.goal,
            description=plan.goal,
            plan=plan,
            parent_operation_id=parent_operation["operationId"],
        )
        self.task_manager.create_tasks([task])
        claimed = self.task_manager.claim_task(task_id)
        if claimed is None:
            raise RuntimeError("The newly persisted task could not be claimed.")
        result = self.task_manager.execute_claimed_plan(
            task_id,
            self.executor,
            plan,
            contexts,
            parent_operation_id=parent_operation["operationId"],
        )
        operation_for_verification = {
            **parent_operation,
            "expectedEffect": LocalReadOnlyTaskVerifier._EXPECTED_EFFECT,
            "executionResult": {
                "success": result["execution"]["success"],
                "status": "UNVERIFIED",
                "result": result["execution"],
            },
        }
        evidence = LocalReadOnlyTaskVerifier().verify(operation_for_verification)
        return {
            **result,
            "verification": evidence,
            "task_id": task_id,
        }

    def _cancel_unstarted_child_operations(
        self,
        operation_ids: list[str],
    ) -> None:
        for operation_id in operation_ids:
            operation = self.operation_store.get_by_id(operation_id)
            if operation is None or operation.get("lifecycleState") not in {
                "SUGGESTED",
                "APPROVED",
            }:
                continue
            operation.update(
                {
                    "lifecycleState": "CANCELLED",
                    "executionResult": {
                        "success": False,
                        "status": "CANCELLED",
                        "reason": (
                            "The full task plan could not be durably authorized."
                        ),
                    },
                    "updatedAt": datetime.now(UTC).isoformat(
                        timespec="milliseconds"
                    ),
                }
            )
            self.operation_store.update(operation)

    def next_task(self):
        return self.task_manager.next_task()

    def complete(self, task):
        self.task_manager.remove_task(task.id)

    @staticmethod
    def _safe_task_plan(task):
        title = (task.title or task.description or "").strip().lower()
        command_map = {
            "pwd": "pwd",
            "print working directory": "pwd",
            "show current directory": "pwd",
        }
        command = command_map.get(title)
        if command is None:
            raise ValueError(
                "Task does not map to a supported safe operation. "
                f"Unsupported task: {task.title!r}"
            )
        return Plan(
            goal=task.title or "Read working directory",
            steps=[
                PlanStep(
                    order=1,
                    action="run_command",
                    parameters={"command": command},
                )
            ],
        )

    def execute_next_task(
        self,
        *,
        plan_builder=None,
        operation_id=None,
        correlation_id=None,
    ):
        if self.executor is None:
            raise RuntimeError("Task execution requires an executor.")

        task = self.task_manager.claim_task()
        if task is None:
            return None
        if task.parent_operation_id is not None or task.plan is not None:
            self.task_manager.release_task_claim(task.id, task.attempt_id)
            raise PermissionError(
                "Approved task plans must execute through the trusted task lifecycle."
            )

        try:
            builder = plan_builder or self._safe_task_plan
            plan = builder(task)
            operation_id = operation_id or f"task-{task.id}-{task.attempt_id}"
            correlation_id = correlation_id or operation_id
            result = self.task_manager.execute_claimed_task(
                task.id,
                self.executor,
                plan,
                operation_id=operation_id,
                correlation_id=correlation_id,
            )
            if result["task"].status != TaskStatus.UNCERTAIN:
                return result
            if self.operation_store is None:
                return result

            operation = {
                "operationId": operation_id,
                "correlationId": correlation_id,
                "verb": "execute",
                "commandText": "pwd",
                "expectedEffect": (
                    "Return the current working directory without changing state."
                ),
                "parameters": {"subject": "pwd"},
                "lifecycleState": "VERIFIED",
                "createdAt": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "updatedAt": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "executionResult": {"result": result["execution"]},
            }
            verifier = LocalReadOnlyOperationVerifier()
            operation["requestFingerprint"] = verifier._request_fingerprint(operation)
            evidence = verifier.verify(operation)
            if evidence is None:
                return result
            operation["verificationAudit"] = evidence
            self.operation_store.create(operation)
            resolved = self.task_manager.resolve_uncertain_task(
                task.id,
                task.attempt_id,
                operation_id=operation_id,
            )
            return {
                **result,
                "operation": operation,
                "verification": evidence,
                "task": resolved,
            }
        except Exception as task_error:
            try:
                if getattr(task, "attempt_id", None):
                    self.task_manager.recover_interrupted_task(task.id)
            except Exception as recovery_error:
                task_error.add_note(
                    "Task recovery failed "
                    f"({type(recovery_error).__name__}); the persisted task state "
                    "may require operator inspection."
                )
            raise

    def run_pending_tasks(self, *, plan_builder=None, limit: int = 1):
        results = []
        for _ in range(limit):
            result = self.execute_next_task(plan_builder=plan_builder)
            if result is None:
                break
            results.append(result)
        return results
