import json
from pathlib import Path

from ultron.world.entity import WorldEntity
from ultron.world.relationships import Relationship


class WorldRepository:
    """
    Persistent storage for world knowledge.
    """

    def __init__(
        self,
        path: str = "data/world.json",
    ):

        self.path = Path(path)

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    def save(
        self,
        entities: list[WorldEntity],
        relationships: list[Relationship],
    ):

        data = {
            "entities": [entity.model_dump() for entity in entities],
            "relationships": [relation.model_dump() for relation in relationships],
        }

        self.path.write_text(
            json.dumps(
                data,
                indent=4,
                default=str,
            ),
            encoding="utf-8",
        )

    def load(self):

        if not self.path.exists():
            return {
                "entities": [],
                "relationships": [],
            }

        return json.loads(self.path.read_text(encoding="utf-8"))
