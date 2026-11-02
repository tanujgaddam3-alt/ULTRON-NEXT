from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4


class IncidentStatus(str, Enum):
    OPEN = "open"

    INVESTIGATING = "investigating"

    RESOLVED = "resolved"

    CLOSED = "closed"


class SecurityIncident:
    """
    Represents a security incident.
    """

    def __init__(
        self,
        title: str,
        severity: str,
    ):

        self.id = str(uuid4())

        self.title = title

        self.severity = severity

        self.status = IncidentStatus.OPEN

        self.created_at = datetime.now(UTC)


class IncidentManager:
    """
    Manages security incidents.
    """

    def __init__(self):

        self.incidents = []

    def create(
        self,
        title: str,
        severity: str,
    ):

        incident = SecurityIncident(
            title,
            severity,
        )

        self.incidents.append(incident)

        return incident

    def update_status(
        self,
        incident_id,
        status,
    ):

        for incident in self.incidents:
            if incident.id == incident_id:
                incident.status = status

                return incident

        return None
