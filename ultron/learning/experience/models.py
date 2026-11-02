from datetime import UTC, datetime
from uuid import uuid4

from pydantic import Field

from ultron.domain.base import DomainModel


class ExperienceRecord(DomainModel):
    """
    Represents a completed experience
    in ULTRON's life cycle.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    situation: str

    action: str

    result: str

    success: bool

    lesson: str = ""

    context: dict = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
