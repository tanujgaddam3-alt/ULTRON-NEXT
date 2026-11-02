from typing import Any

from pydantic import Field

from ultron.domain.base import DomainModel


class EvaluationResult(DomainModel):
    """
    Result produced after evaluating
    a decision outcome.
    """

    success: bool

    score: float

    feedback: str = ""

    improvements: list[str] = Field(default_factory=list)

    metadata: dict[str, Any] = Field(default_factory=dict)
