from pydantic import Field

from .base import DomainModel


class Reflection(DomainModel):
    what_worked: list[str] = Field(default_factory=list)

    what_failed: list[str] = Field(default_factory=list)

    improvements: list[str] = Field(default_factory=list)
