from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


@dataclass
class ExperienceRecord:
    """
    Represents a single ULTRON experience.
    """

    system: str

    action: str

    outcome: str

    success: bool

    score: float

    context: dict[str, Any] = field(default_factory=dict)

    id: str = field(default_factory=lambda: str(uuid4()))

    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self):

        if not 0 <= self.score <= 1:
            raise ValueError("Experience score must be between 0 and 1")
