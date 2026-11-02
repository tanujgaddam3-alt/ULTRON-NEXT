from enum import Enum
from typing import Any

from pydantic import Field

from .base import DomainModel


class EventType(str, Enum):
    USER_MESSAGE = "user_message"
    GOAL_CREATED = "goal_created"
    TASK_CREATED = "task_created"
    TASK_COMPLETED = "task_completed"
    SKILL_EXECUTED = "skill_executed"
    MEMORY_STORED = "memory_stored"
    REFLECTION_CREATED = "reflection_created"
    SYSTEM = "system"
    LEARNING = "learning"
    FAILURE = "failure"
    WORLD_CHANGED = "world_changed"
    PROCESS_STARTED = "process_started"
    PROCESS_STOPPED = "process_stopped"


class Event(DomainModel):
    type: EventType
    source: str
    payload: dict[str, Any] = Field(default_factory=dict)
