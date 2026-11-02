from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class AgentPerformance(BaseModel):
    """
    Stores agent execution history.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    agent: str

    capability: str

    success: bool

    score: float

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
