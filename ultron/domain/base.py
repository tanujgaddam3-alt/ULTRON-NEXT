from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class DomainModel(BaseModel):
    """Base class for every domain model in ULTRON."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_assignment=False,
    )

    id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
