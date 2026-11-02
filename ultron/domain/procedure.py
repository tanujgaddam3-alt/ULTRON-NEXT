from pydantic import Field

from .base import DomainModel


class Procedure(DomainModel):
    name: str

    steps: list[str] = Field(default_factory=list)

    success_rate: float = 1.0
