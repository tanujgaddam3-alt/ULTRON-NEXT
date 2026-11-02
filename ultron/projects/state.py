from enum import Enum


class ProjectState(str, Enum):
    """
    Lifecycle states of a project.
    """

    CREATED = "created"

    ACTIVE = "active"

    PAUSED = "paused"

    COMPLETED = "completed"

    ARCHIVED = "archived"

    FAILED = "failed"
