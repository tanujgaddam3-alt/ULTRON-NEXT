from datetime import UTC, datetime
from typing import Any

from pydantic import Field

from ultron.domain.base import DomainModel


class ImprovementStatus:
    """
    Improvement lifecycle states.
    """

    PROPOSED = "proposed"
    TESTING = "testing"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ImprovementProposal(DomainModel):
    """
    Represents a proposed improvement.
    """

    target: str

    reason: str

    description: str = ""

    suggestion: str

    confidence: float = 0.0

    status: str = ImprovementStatus.PROPOSED

    evidence: dict[str, Any] = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
