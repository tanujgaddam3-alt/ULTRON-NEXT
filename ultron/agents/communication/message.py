from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class AgentMessage(BaseModel):
    """
    Message exchanged between agents.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    sender: str

    receiver: str

    content: str

    metadata: dict = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
