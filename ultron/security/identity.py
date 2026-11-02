from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class IdentityRecord(BaseModel):
    """
    Represents the source of an action.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    user_id: str | None = None

    username: str | None = None

    device_name: str | None = None

    device_id: str | None = None

    ip_address: str | None = None

    session_id: str | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
