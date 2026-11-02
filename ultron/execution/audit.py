from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class ExecutionAudit:
    """
    Records executed actions.
    """

    action: str

    parameters: dict[str, Any]

    success: bool | None

    message: str

    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    task_id: str | None = None
    operation_id: str | None = None
    plan_id: str | None = None
    attempt_id: str | None = None
    correlation_id: str | None = None
    event: str = "EXECUTION"
    outcome: str = "UNKNOWN"
    verification_outcome: str = "NOT_PERFORMED"
    failure_classification: str | None = None
    evidence: dict[str, bool | int] = field(default_factory=dict)


class ExecutionAuditor:
    """
    Maintains execution history.
    """

    def __init__(self, audit_store=None):

        self.records = []
        self.audit_store = audit_store

    def record(
        self,
        action: str,
        parameters: dict,
        success: bool | None,
        message: str,
        *,
        task_id: str | None = None,
        operation_id: str | None = None,
        plan_id: str | None = None,
        attempt_id: str | None = None,
        correlation_id: str | None = None,
        event: str = "EXECUTION",
        outcome: str = "UNKNOWN",
        verification_outcome: str = "NOT_PERFORMED",
        failure_classification: str | None = None,
        evidence: dict[str, bool | int] | None = None,
    ):

        audit = ExecutionAudit(
            action=action,
            parameters={"keys": sorted(parameters)},
            success=success,
            message=message,
            task_id=task_id,
            operation_id=operation_id,
            plan_id=plan_id,
            attempt_id=attempt_id,
            correlation_id=correlation_id,
            event=event,
            outcome=outcome,
            verification_outcome=verification_outcome,
            failure_classification=failure_classification,
            evidence=dict(evidence or {}),
        )

        self.records.append(audit)
        if self.audit_store is not None:
            self.audit_store.record_execution_audit(
                {
                    "task_id": task_id,
                    "operation_id": operation_id,
                    "plan_id": plan_id,
                    "attempt_id": attempt_id,
                    "correlation_id": correlation_id,
                    "action": action,
                    "event": event,
                    "execution_outcome": outcome,
                    "verification_outcome": verification_outcome,
                    "failure_classification": failure_classification,
                    "evidence": evidence or {},
                }
            )

        return audit

    def history(
        self,
    ):

        return self.records
