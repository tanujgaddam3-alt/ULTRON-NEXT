from datetime import UTC, datetime
from uuid import uuid4


class SecurityLogger:
    """
    Stores security events.
    """

    def __init__(self):

        self.logs = []

    def log(
        self,
        event_type: str,
        message: str,
        severity: str = "LOW",
    ):

        record = {
            "id": str(uuid4()),
            "event_type": event_type,
            "message": message,
            "severity": severity,
            "timestamp": (datetime.now(UTC)),
        }

        self.logs.append(record)

        return record

    def get_logs(
        self,
    ):

        return self.logs
