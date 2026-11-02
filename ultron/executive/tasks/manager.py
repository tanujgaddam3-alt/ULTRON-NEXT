from datetime import UTC, datetime
from pathlib import Path

from ultron.domain.enums import TaskStatus
from ultron.domain.plan import Plan
from ultron.domain.task import Task
from ultron.security.identity.authorization import AuthorizationContext
from ultron.application.runtime.operation_verification import (
    valid_verification_evidence,
)

from .queue import TaskQueue
from .sqlite_queue import SQLiteTaskQueue


class TaskManager:
    def __init__(
        self,
        *,
        database: str | Path | None = None,
        queue: TaskQueue | None = None,
        operation_store=None,
    ):
        if database is not None and queue is not None:
            raise ValueError("Provide either a task database or a queue, not both.")
        self.queue = (
            queue
            if queue is not None
            else SQLiteTaskQueue(database)
            if database is not None
            else TaskQueue()
        )
        self.operation_store = operation_store

    def create_tasks(
        self,
        tasks: list[Task],
    ):

        for task in tasks:
            self.queue.add(task)

    def next_task(self):

        return self.queue.next()

    def claim_task(self, task_id: str | None = None):
        claim_next = getattr(self.queue, "claim_next", None)
        if not callable(claim_next):
            raise RuntimeError(
                "Atomic task claims require a SQLite-backed task queue."
            )
        return claim_next(task_id)

    def start_task(self, task_id: str, attempt_id: str):
        return self.queue.transition(
            task_id,
            TaskStatus.EXECUTING,
            attempt_id=attempt_id,
        )

    def release_task_claim(self, task_id: str, attempt_id: str):
        return self.queue.transition(
            task_id,
            TaskStatus.READY,
            attempt_id=attempt_id,
        )

    def request_cancellation(
        self,
        task_id: str,
        *,
        attempt_id: str | None = None,
    ):
        cancel = getattr(self.queue, "request_cancellation", None)
        if not callable(cancel):
            raise RuntimeError(
                "Durable cancellation requires a SQLite-backed task queue."
            )
        return cancel(task_id, attempt_id=attempt_id)

    def complete_task(
        self,
        task_id: str,
        attempt_id: str,
        *,
        operation_id: str,
    ):
        return self._resolve_with_operation_evidence(
            task_id,
            attempt_id,
            operation_id,
            require_uncertain=False,
        )

    def fail_task(
        self,
        task_id: str,
        attempt_id: str,
        *,
        failure_classification: str,
    ):
        return self.queue.transition(
            task_id,
            TaskStatus.FAILED,
            attempt_id=attempt_id,
            failure_classification=failure_classification,
        )

    def mark_task_uncertain(
        self,
        task_id: str,
        attempt_id: str,
        *,
        failure_classification: str,
    ):
        return self.queue.transition(
            task_id,
            TaskStatus.UNCERTAIN,
            attempt_id=attempt_id,
            failure_classification=failure_classification,
        )

    def resolve_uncertain_task(
        self,
        task_id: str,
        attempt_id: str,
        *,
        operation_id: str,
    ):
        return self._resolve_with_operation_evidence(
            task_id,
            attempt_id,
            operation_id,
            require_uncertain=True,
        )

    def _resolve_with_operation_evidence(
        self,
        task_id: str,
        attempt_id: str,
        operation_id: str,
        *,
        require_uncertain: bool,
    ):
        if self.operation_store is None:
            raise RuntimeError(
                "Task verification requires the shared operation store."
            )
        task = self.get_task(task_id)
        expected_state = (
            TaskStatus.UNCERTAIN
            if require_uncertain
            else TaskStatus.EXECUTING
        )
        if task is None or task.status != expected_state:
            raise ValueError(
                f"Task must be {expected_state.value} before verification."
            )
        if task.attempt_id != attempt_id:
            raise ValueError("Task attempt does not match current state.")

        linked_audit = self.operation_store.execution_audit(
            task_id=task_id,
            operation_id=operation_id,
        )
        if not any(
            row["attempt_id"] == attempt_id
            and row["event"] in {
                "DISPATCH_BOUNDARY",
                "EXECUTION_RESULT",
                "EXECUTION_EXCEPTION",
            }
            for row in linked_audit
        ):
            raise ValueError(
                "Verifier operation is not linked to this task attempt."
            )
        operation = self.operation_store.get_by_id(operation_id)
        if operation is None:
            raise ValueError("Verifier operation is not persisted.")
        evidence = operation.get("verificationAudit")
        if not valid_verification_evidence(operation, evidence):
            raise ValueError("Persisted verifier evidence is invalid.")
        outcome = evidence["outcome"]
        lifecycle = operation.get("lifecycleState")
        if (
            outcome == "CONFIRMED_SUCCESS"
            and lifecycle != "VERIFIED"
        ) or (
            outcome == "CONFIRMED_FAILURE"
            and lifecycle != "FAILED"
        ):
            raise ValueError(
                "Operation lifecycle conflicts with verifier evidence."
            )
        target = {
            "CONFIRMED_SUCCESS": TaskStatus.COMPLETED,
            "CONFIRMED_FAILURE": TaskStatus.FAILED,
        }.get(outcome)
        if target is None:
            raise ValueError("A confirmed verifier outcome is required.")
        classification = (
            None
            if target == TaskStatus.COMPLETED
            else "verification_confirmed_failure"
        )
        evidence_id = "|".join(
            (
                operation_id,
                evidence["requestFingerprint"],
                evidence["verifier"],
                evidence["observedAt"],
            )
        )
        return self.queue.transition(
            task_id,
            target,
            attempt_id=attempt_id,
            failure_classification=classification,
            verification_outcome=outcome,
            evidence_id=evidence_id,
        )

    def recover_interrupted_task(self, task_id: str):
        recover = getattr(self.queue, "recover_interrupted", None)
        if not callable(recover):
            raise RuntimeError(
                "Task recovery requires a SQLite-backed task queue."
            )
        return recover(task_id)

    def execute_claimed_task(
        self,
        task_id: str,
        executor,
        plan,
        *,
        operation_id: str | None = None,
        correlation_id: str | None = None,
    ):
        task = self.queue.get(task_id)
        if task is None or task.status != TaskStatus.CLAIMED or not task.attempt_id:
            raise ValueError("Task must be claimed before execution.")

        self.start_task(task.id, task.attempt_id)
        try:
            result = executor.execute(
                plan,
                task_id=task.id,
                operation_id=operation_id,
                correlation_id=correlation_id,
                attempt_id=task.attempt_id,
            )
        except Exception:
            self.mark_task_uncertain(
                task.id,
                task.attempt_id,
                failure_classification="executor_exception_after_execution_started",
            )
            raise

        if any(
            step.get("status") == "UNCERTAIN"
            for step in result.get("results", [])
        ):
            state = self.mark_task_uncertain(
                task.id,
                task.attempt_id,
                failure_classification="executor_reported_uncertain",
            )
        elif result.get("success") is True:
            state = self.mark_task_uncertain(
                task.id,
                task.attempt_id,
                failure_classification="executor_reported_uncertain",
            )
        else:
            state = self.fail_task(
                task.id,
                task.attempt_id,
                failure_classification="executor_reported_failure",
            )
        return {"task": state, "execution": result}

    def execute_claimed_plan(
        self,
        task_id: str,
        executor,
        plan,
        authorization_contexts: list[AuthorizationContext],
        *,
        parent_operation_id: str,
    ):
        task = self.queue.get(task_id)
        if task is None or task.status != TaskStatus.CLAIMED or not task.attempt_id:
            raise ValueError("Task must be claimed before execution.")
        if len(plan.steps) != len(authorization_contexts):
            raise ValueError("Every task step requires an authorization context.")

        self.start_task(task.id, task.attempt_id)
        step_results = []
        current_context = None
        try:
            for step_index, (step, context) in enumerate(
                zip(plan.steps, authorization_contexts)
            ):
                current_context = context
                child_operation = self.operation_store.get_by_id(
                    context.operation_id
                )
                if child_operation is None:
                    raise RuntimeError(
                        "Authorized task step operation is not persisted."
                    )
                current_task = self.get_task(task.id)
                if (
                    current_task is None
                    or current_task.status == TaskStatus.CANCELLED
                    or current_task.cancellation_requested_at is not None
                ):
                    if child_operation.get("lifecycleState") == "APPROVED":
                        child_operation.update(
                            {
                                "lifecycleState": "CANCELLED",
                                "executionResult": {
                                    "success": False,
                                    "status": "CANCELLED",
                                    "reason": (
                                        "Cancellation was recorded before "
                                        "this step's dispatch boundary."
                                    ),
                                },
                                "updatedAt": datetime.now(UTC).isoformat(
                                    timespec="milliseconds"
                                ),
                            }
                        )
                        self.operation_store.update(child_operation)
                    for pending_context in authorization_contexts[
                        step_index + 1:
                    ]:
                        pending = self.operation_store.get_by_id(
                            pending_context.operation_id
                        )
                        if pending is not None and pending.get(
                            "lifecycleState"
                        ) == "APPROVED":
                            pending.update(
                                {
                                    "lifecycleState": "CANCELLED",
                                    "executionResult": {
                                        "success": False,
                                        "status": "CANCELLED",
                                        "reason": (
                                            "Task cancellation stopped "
                                            "undispatched steps."
                                        ),
                                    },
                                    "updatedAt": datetime.now(UTC).isoformat(
                                        timespec="milliseconds"
                                    ),
                                }
                            )
                            self.operation_store.update(pending)
                    break
                child_operation.update({
                    "lifecycleState": "EXECUTING",
                    "updatedAt": datetime.now(UTC).isoformat(
                        timespec="milliseconds"
                    ),
                })
                activate_grant = getattr(
                    self.operation_store,
                    "activate_child_authorization_dispatch",
                    None,
                )
                if not callable(activate_grant) or not activate_grant(
                    grant_id=context.grant_id,
                    operation_id=context.operation_id,
                    correlation_id=context.request_correlation_id,
                    request_fingerprint=context.request_fingerprint,
                    purpose=context.purpose,
                    principal=context.principal,
                    action=context.action,
                    resource_fingerprint=context.resource_fingerprint,
                    expires_at=context.expires_at,
                    parent_operation_id=parent_operation_id,
                    task_id=task.id,
                    attempt_id=task.attempt_id,
                    child_operation=child_operation,
                ):
                    latest_task = self.get_task(task.id)
                    if (
                        latest_task is not None
                        and (
                            latest_task.status == TaskStatus.CANCELLED
                            or latest_task.cancellation_requested_at is not None
                        )
                    ):
                        child_operation = self.operation_store.get_by_id(
                            context.operation_id
                        )
                        if (
                            child_operation is not None
                            and child_operation.get("lifecycleState")
                            == "APPROVED"
                        ):
                            child_operation.update(
                                {
                                    "lifecycleState": "CANCELLED",
                                    "executionResult": {
                                        "success": False,
                                        "status": "CANCELLED",
                                        "reason": (
                                            "Cancellation won the race before "
                                            "the final dispatch claim."
                                        ),
                                    },
                                    "updatedAt": datetime.now(UTC).isoformat(
                                        timespec="milliseconds"
                                    ),
                                }
                            )
                            self.operation_store.update(child_operation)
                        for pending_context in authorization_contexts[
                            step_index + 1:
                        ]:
                            pending = self.operation_store.get_by_id(
                                pending_context.operation_id
                            )
                            if pending is not None and pending.get(
                                "lifecycleState"
                            ) == "APPROVED":
                                pending.update(
                                    {
                                        "lifecycleState": "CANCELLED",
                                        "executionResult": {
                                            "success": False,
                                            "status": "CANCELLED",
                                            "reason": (
                                                "Task cancellation stopped "
                                                "undispatched steps."
                                            ),
                                        },
                                        "updatedAt": datetime.now(UTC).isoformat(
                                            timespec="milliseconds"
                                        ),
                                    }
                                )
                                self.operation_store.update(pending)
                        break
                    raise PermissionError(
                        "Task child grant is not active for this parent operation."
                    )
                single_step_plan = Plan(goal=plan.goal, steps=[step])
                result = executor.execute(
                    single_step_plan,
                    task_id=task.id,
                    operation_id=context.operation_id,
                    correlation_id=context.operation_correlation_id,
                    attempt_id=task.attempt_id,
                    authorization_context=context,
                )
                result_steps = result.get("results")
                step_result = (
                    result_steps[0]
                    if isinstance(result_steps, list)
                    and len(result_steps) == 1
                    and isinstance(result_steps[0], dict)
                    else {"status": "UNCERTAIN"}
                )
                checkpoint = {
                    "operationId": context.operation_id,
                    "status": step_result.get("status", "UNCERTAIN"),
                    "action": step.action,
                    "result": step_result.get("result"),
                }
                step_status = step_result.get("status", "UNCERTAIN")
                child_lifecycle = (
                    "COMPLETED"
                    if step_status == "COMPLETED"
                    else "FAILED"
                    if step_status in {"BLOCKED", "FAILED"}
                    else "UNCERTAIN"
                )
                child_operation.update(
                    {
                        "lifecycleState": child_lifecycle,
                        "executionResult": {
                            "success": child_lifecycle == "COMPLETED",
                            "status": child_lifecycle,
                            "result": step_result,
                        },
                    }
                )
                self.operation_store.update(child_operation)
                record_result = getattr(self.queue, "record_step_result", None)
                if not callable(record_result):
                    raise RuntimeError(
                        "Durable task execution requires step checkpoints."
                    )
                self.queue.record_step_result(
                    task.id,
                    task.attempt_id,
                    step.order,
                    checkpoint,
                )
                step_results.append(step_result)
                if step_result.get("status") != "COMPLETED":
                    break
                current_context = None
                current_task = self.get_task(task.id)
                if (
                    current_task is not None
                    and current_task.cancellation_requested_at is not None
                ):
                    break
        except Exception as error:
            if current_context is not None:
                try:
                    interrupted_operation = self.operation_store.get_by_id(
                        current_context.operation_id
                    )
                    if interrupted_operation is not None and interrupted_operation.get(
                        "lifecycleState"
                    ) == "EXECUTING":
                        interrupted_operation.update(
                            {
                                "lifecycleState": "UNCERTAIN",
                                "executionResult": {
                                    "success": False,
                                    "status": "UNCERTAIN",
                                    "reason": (
                                        "Execution interrupted before a "
                                        "durable step result was recorded."
                                    ),
                                },
                                "updatedAt": datetime.now(UTC).isoformat(
                                    timespec="milliseconds"
                                ),
                            }
                        )
                        self.operation_store.update(interrupted_operation)
                    elif (
                        interrupted_operation is not None
                        and interrupted_operation.get("lifecycleState")
                        == "APPROVED"
                    ):
                        interrupted_operation.update(
                            {
                                "lifecycleState": "CANCELLED",
                                "executionResult": {
                                    "success": False,
                                    "status": "CANCELLED",
                                    "reason": (
                                        "The failure occurred before the "
                                        "child grant was activated for dispatch."
                                    ),
                                },
                                "updatedAt": datetime.now(UTC).isoformat(
                                    timespec="milliseconds"
                                ),
                            }
                        )
                        self.operation_store.update(interrupted_operation)
                except Exception as recovery_error:
                    error.add_note(
                        "Child operation recovery failed "
                        f"({type(recovery_error).__name__}); inspect the "
                        "persisted operation before retrying."
                    )
            try:
                self.mark_task_uncertain(
                    task.id,
                    task.attempt_id,
                    failure_classification=(
                        "executor_exception_after_execution_started"
                    ),
                )
            except Exception as recovery_error:
                error.add_note(
                    "Task recovery failed "
                    f"({type(recovery_error).__name__}); inspect persisted "
                    "task state before retrying."
                )
            if current_context is not None:
                unstarted_contexts = authorization_contexts[
                    len(step_results) + 1:
                ]
                for context in unstarted_contexts:
                    try:
                        unstarted_operation = self.operation_store.get_by_id(
                            context.operation_id
                        )
                        if unstarted_operation is None:
                            raise RuntimeError(
                                "Authorized task step operation is not persisted."
                            )
                        unstarted_operation.update(
                            {
                                "lifecycleState": "CANCELLED",
                                "executionResult": {
                                    "success": False,
                                    "status": "CANCELLED",
                                    "reason": (
                                        "A prior task step did not complete."
                                    ),
                                },
                                "updatedAt": datetime.now(UTC).isoformat(
                                    timespec="milliseconds"
                                ),
                            }
                        )
                        self.operation_store.update(unstarted_operation)
                    except Exception as recovery_error:
                        error.add_note(
                            "Undispatched step cancellation could not be "
                            f"persisted ({type(recovery_error).__name__})."
                        )
            raise

        for context in authorization_contexts[len(step_results):]:
            child_operation = self.operation_store.get_by_id(
                context.operation_id
            )
            if child_operation is None:
                raise RuntimeError(
                    "Unstarted authorized task step is not persisted."
                )
            child_operation.update(
                {
                    "lifecycleState": "CANCELLED",
                    "executionResult": {
                        "success": False,
                        "status": "CANCELLED",
                        "reason": "A prerequisite or prior task step did not complete.",
                    },
                }
            )
            self.operation_store.update(child_operation)

        all_steps_completed = (
            len(step_results) == len(plan.steps)
            and all(
                result.get("status") == "COMPLETED"
                for result in step_results
            )
        )
        current_task = self.get_task(task.id)
        if current_task is not None and current_task.status == TaskStatus.CANCELLED:
            state = current_task
        elif (
            current_task is not None
            and current_task.cancellation_requested_at is not None
        ):
            state = self.mark_task_uncertain(
                task.id,
                task.attempt_id,
                failure_classification="cancellation_requested_after_dispatch",
            )
        elif all_steps_completed:
            state = self.mark_task_uncertain(
                task.id,
                task.attempt_id,
                failure_classification="executor_reported_uncertain",
            )
        elif any(
            result.get("status") == "UNCERTAIN"
            for result in step_results
        ):
            state = self.mark_task_uncertain(
                task.id,
                task.attempt_id,
                failure_classification="executor_reported_uncertain",
            )
        else:
            state = self.fail_task(
                task.id,
                task.attempt_id,
                failure_classification="executor_reported_failure",
            )
        parent_audit = getattr(self.operation_store, "record_execution_audit", None)
        if callable(parent_audit):
            parent_audit(
                {
                    "task_id": task.id,
                    "operation_id": parent_operation_id,
                    "plan_id": plan.id,
                    "attempt_id": task.attempt_id,
                    "correlation_id": parent_operation_id,
                    "action": "execute_task",
                    "event": "EXECUTION_RESULT",
                    "execution_outcome": (
                        "COMPLETED" if all_steps_completed else "NOT_COMPLETED"
                    ),
                    "verification_outcome": "NOT_PERFORMED",
                    "evidence": {"attempt_number": task.attempt_count},
                }
            )
        return {
            "task": state,
            "execution": {
                "success": all_steps_completed,
                "results": step_results,
            },
        }

    def task_events(self, task_id: str):
        events = getattr(self.queue, "events", None)
        if not callable(events):
            return []
        return events(task_id)

    def get_task(self, task_id: str):
        get_task = getattr(self.queue, "get", None)
        if not callable(get_task):
            return next(
                (task for task in self.queue.all() if task.id == task_id),
                None,
            )
        return get_task(task_id)

    def remove_task(
        self,
        task_id: str,
    ):

        self.queue.remove(task_id)

    def all_tasks(self):

        return self.queue.all()

    def close(self):
        close = getattr(self.queue, "close", None)
        if callable(close):
            close()
