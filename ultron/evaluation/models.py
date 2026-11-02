from typing import Any

from pydantic import Field

from ultron.domain.base import DomainModel


class EvaluationRecord(DomainModel):
    """
    Represents evaluation of an executed decision.
    """

    decision_id: str

    success: bool

    expected_outcome: str = ""

    actual_outcome: str = ""

    score: float = 0.0

    metadata: dict[str, Any] = Field(default_factory=dict)
