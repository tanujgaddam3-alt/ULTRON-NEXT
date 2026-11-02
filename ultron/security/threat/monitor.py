from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class ActivityRecord(BaseModel):
    """
    Represents observed system activity.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    source: str

    action: str

    metadata: dict = Field(default_factory=dict)

    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class BehaviorMonitor:
    """
    Observes and records activity.
    """

    def __init__(self):

        self.activities = []

    def observe(
        self,
        source: str,
        action: str,
        metadata: dict | None = None,
    ):

        record = ActivityRecord(
            source=source,
            action=action,
            metadata=(metadata or {}),
        )

        self.activities.append(record)

        return record

    def history(
        self,
    ):

        return self.activities
