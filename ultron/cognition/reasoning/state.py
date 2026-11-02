from typing import Any

from pydantic import ConfigDict, Field

from ultron.cognition.context.models import CognitiveContext
from ultron.cognition.reasoning.hypothesis import Hypothesis
from ultron.domain.base import DomainModel


class ReasoningState(DomainModel):
    """
    Mutable workspace used during a reasoning cycle.
    """

    model_config = ConfigDict(
        frozen=False,
        extra="forbid",
        validate_assignment=False,
    )

    context: CognitiveContext

    hypotheses: list[Hypothesis] = Field(default_factory=list)

    confidence: float = 0.0

    trace: list[str] = Field(default_factory=list)

    metadata: dict[str, Any] = Field(default_factory=dict)
