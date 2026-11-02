from datetime import UTC, datetime


class SecurityAuditLogger:
    """
    Stores security activity history.
    """

    def __init__(self):

        self.records = []

    def record(
        self,
        event,
        evidence=None,
    ):

        audit_entry = {
            "event_id": event.id,
            "type": event.event_type,
            "severity": event.severity,
            "description": event.description,
            "action_taken": event.action_taken,
            "evidence": evidence,
            "timestamp": datetime.now(UTC),
        }

        self.records.append(audit_entry)

        return audit_entry

    def history(
        self,
    ):

        return self.records
