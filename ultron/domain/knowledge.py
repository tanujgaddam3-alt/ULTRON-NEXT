from typing import Any

from pydantic import Field

from .base import DomainModel


class Knowledge(DomainModel):
    """
    Represents factual knowledge acquired by ULTRON.

    Knowledge is different from Experience:
    - Experience = something that happened
    - Knowledge = something ULTRON believes to be true
    """

    key: str

    value: Any

    source: str

    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
    )

    metadata: dict[str, Any] = Field(default_factory=dict)
