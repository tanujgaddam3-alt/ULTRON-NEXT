from datetime import UTC, datetime

from ultron.world.entity import WorldEntity
from ultron.world.models import WorldSnapshot


class WorldState:
    """
    Stores ULTRON's current world knowledge.
    """

    def __init__(self):

        self.snapshot = WorldSnapshot()

        self.entities: dict[str, WorldEntity] = {}

        self.history: list[WorldSnapshot] = []

        self.last_updated = None

    def update(
        self,
        snapshot: WorldSnapshot,
    ):

        # Preserve history
        if self.snapshot:
            self.history.append(self.snapshot)

        self.snapshot = snapshot

        # Sync entities
        self.entities = {entity.name: entity for entity in snapshot.entities}

        self.last_updated = datetime.now(UTC)

    def add_entity(
        self,
        entity: WorldEntity,
    ):

        self.entities[entity.name] = entity

    def get_entity(
        self,
        name: str,
    ):

        return self.entities.get(name)

    def get(self):

        return self.snapshot
