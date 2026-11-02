from enum import Enum
from typing import Any

from pydantic import Field

from ultron.domain.base import DomainModel


class EntityType(str, Enum):
    """
    Types of entities ULTRON can understand.
    """

    PERSON = "person"

    ORGANIZATION = "organization"

    PROJECT = "project"

    PRODUCT = "product"

    TECHNOLOGY = "technology"

    CONCEPT = "concept"

    LOCATION = "location"

    EVENT = "event"

    UNKNOWN = "unknown"


class Entity(DomainModel):
    """
    Represents a real-world object or concept.
    """

    name: str

    entity_type: EntityType = EntityType.UNKNOWN

    description: str = ""

    aliases: list[str] = Field(default_factory=list)

    attributes: dict[str, Any] = Field(default_factory=dict)

    confidence: float = 1.0
