from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class ThreatLevel(str):
    LOW = "LOW"

    MEDIUM = "MEDIUM"

    HIGH = "HIGH"

    CRITICAL = "CRITICAL"


class ThreatRecord(BaseModel):
    """
    Represents detected suspicious activity.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    source: str

    description: str

    category: str

    level: str = ThreatLevel.LOW

    confidence: float = 0.0

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
