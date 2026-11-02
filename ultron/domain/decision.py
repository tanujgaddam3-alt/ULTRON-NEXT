from datetime import UTC, datetime
from uuid import uuid4

from typing import Any

from pydantic import BaseModel, Field

from ultron.domain.action_types import ActionType


class Decision(BaseModel):
    """
    Represents the final decision selected by ULTRON.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    action: ActionType

    confidence: float

    parameters: dict[str, Any] = Field(default_factory=dict)

    reason: str

    alternatives: list[str] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
