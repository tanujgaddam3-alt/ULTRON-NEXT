from enum import Enum

from pydantic import Field

from .base import DomainModel


class UnderstandingType(str, Enum):
    COMMAND = "command"
    QUESTION = "question"
    CONVERSATION = "conversation"
    MEMORY = "memory"
    SEARCH = "search"
    UNKNOWN = "unknown"


class Understanding(DomainModel):
    type: UnderstandingType

    summary: str

    goal: str | None = None

    requires_reasoning: bool = False

    requires_planning: bool = False

    requires_execution: bool = False

    context: dict[str, object] = Field(default_factory=dict)
