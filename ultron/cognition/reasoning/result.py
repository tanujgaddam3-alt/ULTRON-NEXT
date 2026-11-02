from typing import Any

from pydantic import Field

from ultron.domain.base import DomainModel

from .hypothesis import Hypothesis


class ReasoningResult(DomainModel):
    """
    Output produced by the reasoning pipeline.
    """

    # Ranked candidate actions
    hypotheses: list[Hypothesis] = Field(default_factory=list)

    # Overall reasoning confidence
    confidence: float = 0.0

    # Explainability trace
    trace: list[str] = Field(default_factory=list)

    # Extension point
    metadata: dict[str, Any] = Field(default_factory=dict)
