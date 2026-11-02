from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


@dataclass
class ReflectionRecord:
    """
    Represents a lesson extracted from experience.
    """

    experience_id: str

    observation: str

    lesson: str

    confidence: float

    metadata: dict[str, Any] = field(default_factory=dict)

    id: str = field(default_factory=lambda: str(uuid4()))

    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self):

        if not 0 <= self.confidence <= 1:
            raise ValueError("Confidence must be between 0 and 1")
