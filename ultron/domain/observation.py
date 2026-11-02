from pydantic import Field

from .base import DomainModel


class Observation(DomainModel):
    success: bool

    result: str = ""

    errors: list[str] = Field(default_factory=list)

    duration: float = 0.0
