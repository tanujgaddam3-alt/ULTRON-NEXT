from pydantic import Field

from .base import DomainModel


class Perception(DomainModel):
    source: str

    raw_input: str

    intent: str

    entities: dict[str, str] = Field(default_factory=dict)

    language: str = "en"

    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
