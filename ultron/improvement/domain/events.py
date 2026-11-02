from dataclasses import dataclass
from typing import Any


@dataclass
class FailureEvent:
    """
    Event emitted when ULTRON detects
    a system failure.
    """

    component: str

    error: str

    metadata: dict[str, Any] | None = None

    @property
    def type(self):
        from ultron.domain.event import EventType

        return EventType.FAILURE
