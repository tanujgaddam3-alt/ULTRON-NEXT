from datetime import UTC, datetime
from uuid import uuid4

from pydantic import Field

from ultron.domain.event import Event, EventType


class WorldEvent(Event):
    """
    Represents a change detected in ULTRON's world.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    entity_name: str

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def __init__(
        self,
        event_type: EventType,
        entity_name: str,
        details: dict | None = None,
        **kwargs,
    ):

        super().__init__(
            type=event_type,
            source="world",
            payload={
                "entity_name": entity_name,
                "details": details or {},
            },
            entity_name=entity_name,
            **kwargs,
        )
