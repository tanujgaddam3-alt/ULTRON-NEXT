from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Concept:
    """
    A single concept within a schema.
    """

    id: str
    name: str
    kind: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Relation:
    """
    Connects two concepts.
    """

    source: str
    target: str
    relation: str
    weight: float = 1.0


@dataclass(slots=True)
class Schema:
    """
    A reusable cognitive model.
    """

    id: str
    name: str
    description: str

    concepts: dict[str, Concept] = field(default_factory=dict)
    relations: list[Relation] = field(default_factory=list)

    confidence: float = 1.0

    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ActivatedSchema:
    """
    A schema activated for the current reasoning cycle.
    """

    schema: Schema

    activation: float

    supporting_memories: list[Any] = field(default_factory=list)

    predicted_goals: list[str] = field(default_factory=list)

    predicted_actions: list[str] = field(default_factory=list)
