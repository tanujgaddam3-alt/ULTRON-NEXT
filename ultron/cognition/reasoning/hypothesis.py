from typing import Any

from pydantic import ConfigDict, Field

from ultron.domain.action_types import ActionType
from ultron.domain.base import DomainModel


class Hypothesis(DomainModel):
    """
    Represents a candidate interpretation or action
    produced during reasoning.
    """

    model_config = ConfigDict(
        frozen=False,
        extra="forbid",
        validate_assignment=False,
    )
    action: ActionType

    target: str | None = None

    arguments: dict[str, Any] = Field(default_factory=dict)

    reasoning: str

    confidence: float

    priority: float

    cost: float

    metadata: dict[str, Any] = Field(default_factory=dict)
