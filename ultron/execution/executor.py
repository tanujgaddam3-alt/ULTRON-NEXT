from typing import Any
from uuid import uuid4

from ultron.domain.plan import Plan
from ultron.security.identity.authorization import (
    AuthorizationContext,
    AuthorizationRejectedError,
)
from ultron.security.identity.authenticator import WebSocketAuthenticator
from ultron.application.runtime.operation_store import SQLiteOperationStore
from ultron.execution.audit import ExecutionAuditor
from ultron.execution.dispatcher import Dispatcher
from ultron.execution.permission import ExecutionPermission
from ultron.execution.recovery import RecoveryManager
from ultron.execution.router import ActionRouter


class Executor:
    """
    Executes plan steps using registered skills.

    Provides:
    - permission validation
    - capability routing
    - execution
    - auditing
    - automatic retry/recovery
    - structured execution results
    """

    def __init__(
        self,
        skills=None,
        max_retries: int = 1,
        audit_store=None,
        authorization_authority: WebSocketAuthenticator | None = None,
        operation_store: SQLiteOperationStore | None = None,
    ):
        self.router = ActionRouter(
            skills=skills
        )

        self.dispatcher = Dispatcher(
            self.router.skills
        )

        self.permission = ExecutionPermission()

        self.auditor = ExecutionAuditor(audit_store=audit_store)

        self.recovery = RecoveryManager(
            max_retries=max_retries,
            audit_store=audit_store,
        )
        self.authorization_authority: WebSocketAuthenticator | None = None
        self.operation_store: SQLiteOperationStore | None = None
        if authorization_authority is not None or operation_store is not None:
            if authorization_authority is None or operation_store is None:
                raise ValueError(
                    "Trusted authorization requires both an authority and operation store."
                )
            self.configure_trusted_authorization(
                authorization_authority,
                operation_store,
            )

    def configure_trusted_authorization(
        self,
        authority: WebSocketAuthenticator,
        operation_store: SQLiteOperationStore,
    ) -> None:
        if type(authority) is not WebSocketAuthenticator:
            raise TypeError("Executor requires WebSocketAuthenticator.")
        if not isinstance(operation_store, SQLiteOperationStore):
            raise TypeError("Executor requires the SQLite operation store.")
        self.authorization_authority = authority
        self.operation_store = operation_store
        self.auditor.audit_store = operation_store
        self.recovery.audit_store = operation_store
        configure = getattr(
            self.dispatcher.skills,
            "configure_trusted_authorization",
            None,
        )
        if not callable(configure):
            raise TypeError(
                "Executor skill manager cannot validate trusted authorization."
            )
        configure(authority, operation_store)

    def execute(
        self,
        plan: Plan,
        *,
        task_id: str | None = None,
        operation_id: str | None = None,
        correlation_id: str | None = None,
        attempt_id: str | None = None,
        authorization_context: AuthorizationContext | None = None,
    ) -> dict[str, Any]:
        """
        Execute every step in the plan.

        Only exceptions raised before skill dispatch can be retried. Once
        dispatch begins, an exception has an uncertain outcome and is never
        replayed automatically.

        A structured skill result indicating failure, denial, or
        confirmation requirement is not treated as successful execution.
        """

        completed = 0
        results = []
        authorization_context_valid = True
        if authorization_context is not None:
            authorization_context_valid = (
                type(authorization_context) is AuthorizationContext
                and len(plan.steps) == 1
                and self.authorization_authority is not None
                and operation_id == authorization_context.operation_id
                and correlation_id
                == authorization_context.operation_correlation_id
                and "authorization_context"
                not in plan.steps[0].parameters
                and self.authorization_authority.validate_context(
                    authorization_context,
                    action=plan.steps[0].action,
                    parameters=plan.steps[0].parameters,
                    operation_id=operation_id or "",
                    correlation_id=correlation_id or "",
                )
            )

        for step in plan.steps:

            attempts = 0
            recovered = False

            while True:
                attempts += 1
                dispatch_started = False
                step_attempt_id = attempt_id or str(uuid4())
                audit_context = {
                    "task_id": task_id,
                    "operation_id": operation_id,
                    "plan_id": plan.id,
                    "attempt_id": step_attempt_id,
                    "correlation_id": correlation_id,
                }

                try:
                    if not authorization_context_valid:
                        result = {
                            "success": False,
                            "blocked": True,
                            "message": (
                                "Execution blocked by invalid or mismatched "
                                "trusted authorization context."
                            ),
                        }
                        self.auditor.record(
                            action=step.action,
                            parameters=step.parameters,
                            success=False,
                            message=(
                                "Trusted authorization context did not match "
                                "the operation dispatch."
                            ),
                            **audit_context,
                            event="EXECUTION_BLOCKED",
                            outcome="NOT_DISPATCHED",
                            evidence={
                                "dispatch_started": False,
                                "attempt_number": attempts,
                            },
                        )
                        step.completed = False
                        results.append(
                            {
                                "action": step.action,
                                "result": result,
                                "attempts": attempts,
                                "recovered": False,
                                "status": "BLOCKED",
                            }
                        )
                        break

                    # Permission is authoritative. Do not ignore its result.
                    permission_allowed = self.permission.check(
                        step.action,
                        **step.parameters,
                    )

                    if permission_allowed is False:
                        result = {
                            "success": False,
                            "blocked": True,
                            "message": (
                                "Execution blocked by permission policy."
                            ),
                        }

                        self.auditor.record(
                            action=step.action,
                            parameters=step.parameters,
                            success=False,
                            message="Execution blocked by permission policy.",
                            **audit_context,
                            event="EXECUTION_BLOCKED",
                            outcome="NOT_DISPATCHED",
                            evidence={
                                "dispatch_started": False,
                                "attempt_number": attempts,
                            },
                        )

                        step.completed = False

                        results.append(
                            {
                                "action": step.action,
                                "result": result,
                                "attempts": attempts,
                                "recovered": False,
                                "status": "BLOCKED",
                            }
                        )

                        break

                    # Resolve the capability before dispatch.
                    self.router.resolve(
                        step.action
                    )

                    self.auditor.record(
                        action=step.action,
                        parameters=step.parameters,
                        success=None,
                        message="Dispatch boundary recorded.",
                        **audit_context,
                        event="DISPATCH_BOUNDARY",
                        outcome="DISPATCH_MAY_HAVE_STARTED",
                        evidence={
                            "dispatch_started": True,
                            "attempt_number": attempts,
                        },
                    )
                    dispatch_started = True
                    result = self.dispatcher.dispatch(
                        step.action,
                        authorization_context=authorization_context,
                        **step.parameters,
                    )

                    # Skills may return structured execution outcomes.
                    # A returned failure is NOT an executor success.
                    if isinstance(result, dict) and result.get("success") is False:

                        if result.get("requires_confirmation"):
                            status = "REQUIRES_CONFIRMATION"
                        elif result.get("blocked"):
                            status = "BLOCKED"
                        else:
                            status = "FAILED"

                        self.auditor.record(
                            action=step.action,
                            parameters=step.parameters,
                            success=False,
                            message="Executor returned a non-success result.",
                            **audit_context,
                            event="EXECUTION_RESULT",
                            outcome=status,
                            evidence={
                                "dispatch_started": True,
                                "attempt_number": attempts,
                            },
                        )

                        step.completed = False

                        results.append(
                            {
                                "action": step.action,
                                "result": result,
                                "attempts": attempts,
                                "recovered": False,
                                "status": status,
                            }
                        )

                        # Do not retry policy decisions such as confirmation
                        # or denial. They require an external state change.
                        break

                    recovered = attempts > 1

                    self.auditor.record(
                        action=step.action,
                        parameters=step.parameters,
                        success=None,
                        message="Executor returned without a structured failure.",
                        **audit_context,
                        event="EXECUTION_RESULT",
                        outcome="DISPATCH_RETURNED",
                        evidence={
                            "dispatch_started": True,
                            "attempt_number": attempts,
                        },
                    )

                    step.completed = True

                    completed += 1

                    results.append(
                        {
                            "action": step.action,
                            "result": result,
                            "attempts": attempts,
                            "recovered": recovered,
                            "status": "COMPLETED",
                        }
                    )

                    if recovered:
                        self.recovery.mark_recovered(
                            action=step.action,
                            attempts=attempts,
                            audit_context=audit_context,
                        )

                    break

                except AuthorizationRejectedError as error:
                    self.auditor.record(
                        action=step.action,
                        parameters=step.parameters,
                        success=False,
                        message="Trusted authorization rejected before skill invocation.",
                        **audit_context,
                        event="EXECUTION_BLOCKED",
                        outcome="NOT_DISPATCHED",
                        failure_classification=type(error).__name__,
                        evidence={
                            "dispatch_started": False,
                            "attempt_number": attempts,
                        },
                    )
                    step.completed = False
                    results.append(
                        {
                            "action": step.action,
                            "result": {
                                "success": False,
                                "blocked": True,
                                "message": "Trusted authorization was rejected.",
                            },
                            "attempts": attempts,
                            "recovered": False,
                            "status": "BLOCKED",
                        }
                    )
                    break

                except Exception as error:
                    print("EXECUTION ERROR:", type(error).__name__)

                    recovery = self.recovery.recover(
                        action=step.action,
                        error=error,
                        attempts=attempts,
                        retry_allowed=not dispatch_started,
                        audit_context=audit_context,
                    )
                    self.auditor.record(
                        action=step.action,
                        parameters=step.parameters,
                        success=False,
                        message=(
                            "Dispatch outcome is uncertain."
                            if dispatch_started
                            else "Failure occurred before dispatch."
                        ),
                        **audit_context,
                        event="EXECUTION_EXCEPTION",
                        outcome=(
                            "UNCERTAIN"
                            if dispatch_started
                            else "PRE_DISPATCH_FAILED"
                        ),
                        failure_classification=type(error).__name__,
                        evidence={
                            "dispatch_started": dispatch_started,
                            "attempt_number": attempts,
                        },
                    )

                    if recovery["retry"]:
                        continue

                    step.completed = False

                    results.append(
                        {
                            "action": step.action,
                            "result": None,
                            "attempts": attempts,
                            "recovered": False,
                            "status": (
                                "UNCERTAIN"
                                if dispatch_started
                                else "FAILED"
                            ),
                            "error": (
                                "Skill execution raised an exception after "
                                "dispatch began; the outcome is uncertain."
                                if dispatch_started
                                else "Execution failed before skill dispatch."
                            ),
                        }
                    )

                    break

            if not step.completed:
                break

        return {
            "completed": completed,
            "results": results,
            "success": (
                completed == len(plan.steps)
            ),
            "recovery": self.recovery.get_history(),
        }
