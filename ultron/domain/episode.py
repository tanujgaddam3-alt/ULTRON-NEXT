from pydantic import Field

from .base import DomainModel


class Episode(DomainModel):
    summary: str

    goal_id: str | None = None

    outcome: str

    lessons: list[str] = Field(default_factory=list)
