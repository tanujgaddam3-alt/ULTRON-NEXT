from datetime import UTC, datetime
from uuid import uuid4

from pydantic import Field

from ultron.domain.base import DomainModel


class BehaviorPattern(DomainModel):
    """
    Represents a discovered behavior pattern.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    action: str

    occurrences: int = 0

    successes: int = 0

    failures: int = 0

    confidence: float = 0.0

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def success_rate(self) -> float:

        if self.occurrences == 0:
            return 0.0

        return self.successes / self.occurrences
