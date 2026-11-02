from datetime import UTC, datetime
from typing import Any

from pydantic import Field

from ultron.domain.base import DomainModel


class KnowledgeSource(DomainModel):
    """
    Origin of knowledge.
    """

    source_type: str

    reference: str

    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeItem(DomainModel):
    """
    Base knowledge object.
    """

    content: str

    category: str

    confidence: float = 1.0

    source: KnowledgeSource | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Concept(KnowledgeItem):
    """
    Represents an understood concept.
    """

    category: str = "concept"

    name: str


class Fact(KnowledgeItem):
    """
    Represents factual knowledge.
    """

    category: str = "fact"

    subject: str

    predicate: str

    object: str


class Relationship(DomainModel):
    """
    Connects knowledge entities.
    """

    source: str

    relation: str

    target: str

    confidence: float = 1.0
