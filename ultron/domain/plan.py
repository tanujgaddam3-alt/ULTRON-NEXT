from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    """
    One executable step.
    """

    order: int

    action: str

    parameters: dict[str, object] = Field(default_factory=dict)

    depends_on: list[int] = Field(default_factory=list)

    completed: bool = False


class Plan(BaseModel):
    """
    Represents an executable plan.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    goal: str

    steps: list[PlanStep] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
