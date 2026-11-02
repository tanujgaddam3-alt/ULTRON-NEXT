from typing import Any

from pydantic import Field

from ultron.domain.base import DomainModel


class Formula(DomainModel):
    content: str

    name: str

    category: str = "formula"

    confidence: float = 1.0

    metadata: dict[str, Any] = Field(default_factory=dict)


class Misconception(DomainModel):
    content: str

    statement: str

    correction: str

    category: str = "misconception"

    confidence: float = 1.0

    metadata: dict[str, Any] = Field(default_factory=dict)


class Chapter(DomainModel):
    content: str

    name: str

    category: str = "chapter"

    confidence: float = 1.0

    metadata: dict[str, Any] = Field(default_factory=dict)
