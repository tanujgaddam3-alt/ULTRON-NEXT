from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class HardeningAction(BaseModel):
    """
    Represents a security improvement.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    target: str

    improvement: str

    applied: bool = False

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AutomaticHardening:
    """
    Applies security improvements.
    """

    def __init__(self):

        self.actions = []

    def create(
        self,
        target: str,
        improvement: str,
    ):

        action = HardeningAction(
            target=target,
            improvement=improvement,
            applied=True,
        )

        self.actions.append(action)

        return action

    def history(
        self,
    ):

        return self.actions
