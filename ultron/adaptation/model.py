from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class BehaviourAdjustment(BaseModel):
    """
    Represents a learned behaviour change.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    behaviour: str

    reason: str

    confidence: float = 0.0

    source: str = "reflection"

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
