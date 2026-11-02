from pydantic import Field

from ultron.domain.base import DomainModel


class ImprovementResult(DomainModel):
    """
    Result after evaluating an improvement.
    """

    proposal_id: str

    accepted: bool

    score: float = 0.0

    reason: str = ""

    metadata: dict = Field(default_factory=dict)
