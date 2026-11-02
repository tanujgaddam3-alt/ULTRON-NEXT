from ultron.core.event_bus import EventBus
from ultron.world.event_generator import (
    WorldEventGenerator,
)
from ultron.world.observer import WorldObserver
from ultron.world.repository.world_repository import (
    WorldRepository,
)
from ultron.world.state import WorldState


class WorldEngine:
    """
    Maintains ULTRON's world model.
    """

    def __init__(self, event_bus: EventBus | None = None):

        self.state = WorldState()

        self.observer = WorldObserver()

        self.repository = WorldRepository()

        self.event_generator = WorldEventGenerator()
        self.last_events = []

        self.event_bus = event_bus or EventBus()

    def refresh(self):

        result = self.observer.observe()

        events = self.event_generator.generate(result.changes)

        self.last_events = events

        for event in events:
            self.event_bus.publish(event)

        # Compatibility:
        # ObservationResult behaves like snapshot

        snapshot = result.snapshot

        self.state.update(snapshot)

        self.repository.save(
            snapshot.entities,
            [],
        )

        return snapshot

    def get(self):
        """
        Return current world state.
        """

        return self.state.get()

    def summary(self) -> dict:
        """
        Returns a cognitive-friendly
        representation of the current world.
        """

        snapshot = self.state.get()

        return {
            "system": snapshot.system.model_dump(),
            "project": snapshot.project.model_dump(),
            "runtime": snapshot.runtime.model_dump(),
            "entity_count": len(snapshot.entities),
            "entities": [
                {
                    "name": entity.name,
                    "type": entity.entity_type,
                    "state": entity.state,
                }
                for entity in snapshot.entities[:20]
            ],
        }
