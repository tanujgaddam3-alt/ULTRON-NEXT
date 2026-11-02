from typing import Any

from pydantic import Field

from .base import DomainModel


class LearningInsight(DomainModel):
    """
    Represents knowledge extracted from an experience.
    """

    success: bool

    lesson: str = ""

    confidence: float = 1.0

    metadata: dict[str, Any] = Field(default_factory=dict)
