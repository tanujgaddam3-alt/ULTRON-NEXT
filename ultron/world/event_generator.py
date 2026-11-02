from typing import ClassVar

from ultron.domain.event import EventType
from ultron.world.events import WorldEvent


class WorldEventGenerator:
    """
    Converts world changes into events.
    """

    def generate(
        self,
        changes: list[str],
    ) -> list[WorldEvent]:

        events: ClassVar = []

        for change in changes:
            event_type = EventType.WORLD_CHANGED

            entity_name = change

            if "started" in change.lower():
                event_type = EventType.PROCESS_STARTED

            elif "stopped" in change.lower():
                event_type = EventType.PROCESS_STOPPED

            events.append(
                WorldEvent(
                    event_type=event_type,
                    entity_name=entity_name,
                    details={"message": change},
                )
            )

        return events
