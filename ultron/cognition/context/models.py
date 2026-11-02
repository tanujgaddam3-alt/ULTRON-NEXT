from __future__ import annotations

from typing import Any

from pydantic import Field

from ultron.cognition.schema.models import ActivatedSchema
from ultron.domain.base import DomainModel
from ultron.domain.goal import Goal


class CognitiveContext(DomainModel):
    """
    Unified cognitive state supplied to reasoning.

    Every reasoning cycle receives exactly one
    CognitiveContext.
    """

    query: str

    understanding: Any

    goals: list[Goal] = Field(default_factory=list)

    memories: dict[str, Any] = Field(default_factory=dict)

    schemas: list[ActivatedSchema] = Field(default_factory=list)

    world: dict[str, Any] = Field(default_factory=dict)

    skills: list[dict[str, Any]] = Field(default_factory=list)
    knowledge: dict[str, Any] = Field(default_factory=dict)

    attention: dict[str, float] = Field(default_factory=dict)

    metadata: dict[str, Any] = Field(default_factory=dict)
