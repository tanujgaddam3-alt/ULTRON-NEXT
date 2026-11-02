from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4


class SecurityEventType(str, Enum):
    ACCESS = "access"

    THREAT = "threat"

    INCIDENT = "incident"

    RECOVERY = "recovery"

    SYSTEM = "system"


class SecurityEvent:
    """
    Standard ULTRON security event.
    """

    def __init__(
        self,
        event_type: SecurityEventType,
        description: str,
        severity: str = "LOW",
    ):

        self.id = str(uuid4())

        self.type = event_type

        self.description = description

        self.severity = severity

        self.timestamp = datetime.now(UTC)
