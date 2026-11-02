from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


class ProjectEventType(str, Enum):
    """
    Types of project events.
    """

    CREATED = "project_created"

    UPDATED = "project_updated"

    ARTIFACT_ADDED = "artifact_added"

    STATE_CHANGED = "project_state_changed"


class ProjectEvent(BaseModel):
    """
    Represents something that happened
    inside a project lifecycle.
    """

    type: ProjectEventType

    project_name: str

    data: dict = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
