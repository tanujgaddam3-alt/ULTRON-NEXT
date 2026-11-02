from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class Relationship(BaseModel):
    """
    Represents a connection between two entities.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    source_id: str

    target_id: str

    relation: str

    properties: dict = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RelationshipGraph(BaseModel):
    """
    Stores relationships between world entities.
    """

    relationships: list[Relationship] = Field(default_factory=list)

    def add(
        self,
        relationship: Relationship,
    ):

        self.relationships.append(relationship)

    def find_related(
        self,
        entity_id: str,
    ):

        return [
            r
            for r in self.relationships
            if (r.source_id == entity_id or r.target_id == entity_id)
        ]
