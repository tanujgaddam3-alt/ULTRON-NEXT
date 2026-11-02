from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class WorldEntity(BaseModel):
    """
    Represents an entity existing in ULTRON's world.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    name: str

    entity_type: str

    properties: dict[str, Any] = Field(default_factory=dict)

    state: dict[str, Any] = Field(default_factory=dict)

    location: str | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    active: bool = True
