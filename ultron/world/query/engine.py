from typing import ClassVar

from ultron.world.entity import WorldEntity
from ultron.world.relationships import Relationship


class WorldQueryEngine:
    """
    Provides queries over world knowledge.
    """

    def __init__(
        self,
        entities: list[WorldEntity],
        relationships: list[Relationship],
    ):

        self.entities = entities

        self.relationships = relationships

    def find_by_type(
        self,
        entity_type: str,
    ) -> list[WorldEntity]:

        return [entity for entity in self.entities if entity.entity_type == entity_type]

    def find_by_name(
        self,
        name: str,
    ) -> WorldEntity | None:

        for entity in self.entities:
            if entity.name == name:
                return entity

        return None

    def related_to(
        self,
        entity_id: str,
    ) -> list[Relationship]:

        return [
            relation
            for relation in self.relationships
            if (relation.source_id == entity_id or relation.target_id == entity_id)
        ]

    def find_running_processes(
        self,
    ):

        return [
            entity
            for entity in self.entities
            if (
                entity.entity_type == "process"
                and entity.properties.get("running", False)
            )
        ]


def find_applications(
    self,
):

    return [entity for entity in self.entities if entity.entity_type == "application"]


def entity_exists(
    self,
    name: str,
):

    return any(entity.name == name for entity in self.entities)


def has_entity(
    self,
    name: str,
) -> bool:
    """
    Check whether an entity exists.
    """

    return any(entity.name.lower() == name.lower() for entity in self.entities)


def find_running_applications(self):

    return [
        entity
        for entity in self.entities
        if (
            entity.entity_type == "application"
            and entity.properties.get(
                "running",
                False,
            )
        )
    ]


def find_files_by_extension(
    self,
    extension: str,
):

    if not extension.startswith("."):
        extension = "." + extension

    return [
        entity
        for entity in self.entities
        if (
            entity.entity_type == "file"
            and entity.properties.get("extension") == extension
        )
    ]


def find_project_files(self):

    return [entity for entity in self.entities if entity.entity_type == "file"]


def statistics(self):

    result: ClassVar = {}

    for entity in self.entities:
        result[entity.entity_type] = (
            result.get(
                entity.entity_type,
                0,
            )
            + 1
        )

    return result
