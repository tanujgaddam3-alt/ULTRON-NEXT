from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field

from ultron.security.identity import IdentityRecord


class SecurityEvent(BaseModel):
    """
    Represents a security-related event.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    event_type: str

    description: str

    identity: IdentityRecord | None = None

    severity: str = "LOW"

    action_taken: str = "NONE"

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
