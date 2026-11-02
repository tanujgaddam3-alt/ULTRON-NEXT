from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class ImprovementRequest(BaseModel):
    """
    Represents a proposed ULTRON improvement.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    component: str

    description: str

    risk_level: str = "LOW"

    approved: bool = False

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
