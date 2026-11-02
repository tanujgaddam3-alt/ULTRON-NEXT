from datetime import UTC, datetime
from typing import Any


class RecoveryManager:
    """
    Manages execution recovery and retry decisions.

    max_retries represents the number of retries allowed after
    the initial execution attempt.

    Example:
        max_retries=1
        attempt 1 -> failure -> retry
        attempt 2 -> failure -> stop
    """

    def __init__(
        self,
        max_retries: int = 1,
        audit_store=None,
    ):
        if max_retries < 0:
            raise ValueError(
                "max_retries cannot be negative."
            )

        self.max_retries = max_retries
        self._history: list[dict[str, Any]] = []
        self.audit_store = audit_store

    def should_retry(
        self,
        retries_used: int,
    ) -> bool:
        """
        Return True when another retry is available.

        retries_used is the number of retries already consumed,
        not the total number of attempts.
        """
        if retries_used < 0:
            raise ValueError(
                "retries_used cannot be negative."
            )

        return retries_used < self.max_retries

    def recover(
        self,
        action: str,
        error: Exception,
        attempts: int,
        *,
        retry_allowed: bool = True,
        audit_context: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        """
        Record a failure and determine whether another attempt
        should be made.

        attempts is the total number of attempts already made,
        including the failed attempt.
        """

        if attempts < 1:
            raise ValueError(
                "attempts must be at least 1."
            )

        retries_used = attempts - 1

        retry = retry_allowed and self.should_retry(retries_used)

        event = {
            "action": action,
            "attempt": attempts,
            "max_retries": self.max_retries,
            "recovered": False,
            "retry": retry,
            "reason": (
                "Execution exception; details omitted."
                if not retry_allowed
                else "Pre-dispatch exception; retry policy evaluated."
            ),
            "timestamp": datetime.now(UTC).isoformat(),
        }

        self._history.append(event)
        self._persist_event(
            event,
            action=action,
            audit_context=audit_context,
            failure_classification=type(error).__name__,
            evidence={
                "retry_allowed": retry_allowed,
                "retry": retry,
                "attempt_number": attempts,
                "max_retries": self.max_retries,
            },
        )

        return event

    def mark_recovered(
        self,
        action: str,
        attempts: int,
        *,
        audit_context: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        """
        Record that an action eventually succeeded after recovery.
        """

        event = {
            "action": action,
            "attempt": attempts,
            "max_retries": self.max_retries,
            "recovered": attempts > 1,
            "retry": False,
            "reason": (
                "Execution succeeded."
                if attempts == 1
                else "Execution recovered successfully."
            ),
            "timestamp": datetime.now(UTC).isoformat(),
        }

        self._history.append(event)
        self._persist_event(
            event,
            action=action,
            audit_context=audit_context,
            failure_classification=None,
            evidence={
                "retry_allowed": False,
                "retry": False,
                "attempt_number": attempts,
                "max_retries": self.max_retries,
            },
        )

        return event

    def _persist_event(
        self,
        event: dict[str, Any],
        *,
        action: str,
        audit_context: dict[str, str | None] | None,
        failure_classification: str | None,
        evidence: dict[str, bool | int],
    ) -> None:
        if self.audit_store is None:
            return
        context = audit_context or {}
        self.audit_store.record_execution_audit(
            {
                "task_id": context.get("task_id"),
                "operation_id": context.get("operation_id"),
                "plan_id": context.get("plan_id"),
                "attempt_id": context.get("attempt_id"),
                "correlation_id": context.get("correlation_id"),
                "action": action,
                "event": "RECOVERY_DECISION",
                "execution_outcome": (
                    "RETRY_SCHEDULED"
                    if event["retry"]
                    else "RECOVERY_STOPPED"
                ),
                "verification_outcome": "NOT_PERFORMED",
                "failure_classification": failure_classification,
                "evidence": evidence,
            }
        )

    def get_history(self) -> list[dict[str, Any]]:
        """
        Return a copy of recovery history.
        """
        return [
            dict(event)
            for event in self._history
        ]

    def clear_history(self) -> None:
        """
        Clear recovery history.
        """
        self._history.clear()
