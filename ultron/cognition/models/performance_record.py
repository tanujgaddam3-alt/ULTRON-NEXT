from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4


@dataclass
class PerformanceRecord:
    """
    Stores system performance events.
    """

    system: str

    score: float

    event: str

    id: str = None

    created_at: datetime = None

    def __post_init__(self):

        if self.id is None:
            self.id = str(uuid4())

        if self.created_at is None:
            self.created_at = datetime.now(UTC)

        if not 0 <= self.score <= 1:
            raise ValueError("Score must be between 0 and 1")
