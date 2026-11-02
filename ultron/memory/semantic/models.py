from typing import Any

from pydantic import Field

from ultron.domain.base import DomainModel


class MemoryAssociation(DomainModel):
    """
    Link between memory and knowledge.
    """

    memory_id: str

    knowledge_id: str

    relation: str

    confidence: float = 1.0


class SemanticMemoryItem(DomainModel):
    """
    Long-term conceptual memory.
    """

    concept: str

    description: str = ""

    associations: list[MemoryAssociation] = Field(default_factory=list)

    metadata: dict[str, Any] = Field(default_factory=dict)
