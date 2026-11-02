from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class ExecutionRecord(BaseModel):
    """
    Result of executing a single plan.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    success: bool

    completed_steps: int

    message: str

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
