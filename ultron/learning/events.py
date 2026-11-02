from dataclasses import dataclass
from typing import Any

from ultron.domain.event import EventType


@dataclass
class LearningEvent:
    """
    Event emitted when ULTRON learns.
    """

    concept: str

    description: str

    metadata: dict[str, Any] | None = None

    @property
    def type(self):
        return EventType.LEARNING
