from pydantic import Field

from .base import DomainModel


class Thought(DomainModel):
    reasoning: str

    alternatives: list[str] = Field(default_factory=list)

    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
