from typing import Any

from pydantic import Field

from ultron.domain.base import DomainModel


class RelationType(str):
    """
    Common relationship types.

    Kept extensible because
    knowledge is messy.
    """

    CREATED = "created"

    USES = "uses"

    DEPENDS_ON = "depends_on"

    PART_OF = "part_of"

    RELATED_TO = "related_to"

    LOCATED_IN = "located_in"

    WORKS_ON = "works_on"

    UNKNOWN = "unknown"


class Relationship(DomainModel):
    """
    Connection between two entities.
    """

    source_id: str

    target_id: str

    relation: str

    description: str = ""

    confidence: float = 1.0

    metadata: dict[str, Any] = Field(default_factory=dict)
