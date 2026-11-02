from ultron.world.entity import WorldEntity
from ultron.world.query.engine import WorldQueryEngine
from ultron.world.relationships import Relationship
from ultron.world.repository.world_repository import WorldRepository


class WorldManager:
    """
    Main interface for ULTRON's world model.
    """

    def __init__(
        self,
        repository: WorldRepository | None = None,
    ):

        self.repository = repository or WorldRepository()

        data = self.repository.load()

        self.entities: list[WorldEntity] = [
            WorldEntity(**entity) for entity in data["entities"]
        ]

        self.relationships: list[Relationship] = [
            Relationship(**relation) for relation in data["relationships"]
        ]

        self.query = WorldQueryEngine(
            self.entities,
            self.relationships,
        )

    def add_entity(
        self,
        entity: WorldEntity,
    ):

        self.entities.append(entity)

        self.save()

    def add_relationship(
        self,
        relationship: Relationship,
    ):

        self.relationships.append(relationship)

        self.save()

    def save(self):

        self.repository.save(
            self.entities,
            self.relationships,
        )

    def get_entity(
        self,
        name: str,
    ):

        return self.query.find_by_name(name)

    def find_entities_by_type(
        self,
        entity_type: str,
    ):

        return self.query.find_by_type(entity_type)

    def get_relationships(
        self,
        entity_id: str,
    ):

        return self.query.related_to(entity_id)

    def summary(self) -> dict:
        """
        Creates a compact representation
        of the current world for cognition.
        """

        applications = [entity.name for entity in self.query.find_applications()]

        running_processes = [
            entity.name for entity in self.query.find_running_processes()
        ]

        return {
            "entity_count": len(self.entities),
            "relationship_count": len(self.relationships),
            "applications": applications,
            "running_processes": running_processes,
            "entities": [
                {
                    "name": entity.name,
                    "type": entity.entity_type,
                    "state": entity.state,
                }
                for entity in self.entities[:20]
            ],
        }
