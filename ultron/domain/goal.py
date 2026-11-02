from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from ultron.domain.base import DomainModel
from ultron.domain.enums import GoalStatus, Priority


class Goal(DomainModel):
    """
    Represents a long-term objective pursued by ULTRON.
    """

    name: str
    description: str = ""

    status: GoalStatus = GoalStatus.PENDING
    priority: Priority = Priority.MEDIUM

    progress: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
    )

    parent_goal: UUID | None = None

    subgoals: list[UUID] = Field(default_factory=list)

    deadline: datetime | None = None

    constraints: list[str] = Field(default_factory=list)

    metadata: dict[str, Any] = Field(default_factory=dict)

    def with_progress(self, progress: float) -> "Goal":
        """
        Return a new Goal with updated progress.

        Goal is immutable, so state changes produce
        a new instance rather than mutating the existing one.
        """

        if not 0.0 <= progress <= 1.0:
            raise ValueError(
                "Goal progress must be between 0.0 and 1.0."
            )

        if progress >= 1.0:
            status = GoalStatus.COMPLETED
        elif progress > 0.0:
            status = GoalStatus.ACTIVE
        else:
            status = self.status

        return self.model_copy(
            update={
                "progress": progress,
                "status": status,
            }
        )

    def complete(self) -> "Goal":
        """
        Return a completed copy of this goal.
        """

        return self.model_copy(
            update={
                "progress": 1.0,
                "status": GoalStatus.COMPLETED,
            }
        )

    def activate(self) -> "Goal":
        """
        Return an active copy of this goal.
        """

        return self.model_copy(
            update={
                "status": GoalStatus.ACTIVE,
            }
        )

    def fail(self) -> "Goal":
        """
        Return a failed copy of this goal.
        """

        return self.model_copy(
            update={
                "status": GoalStatus.FAILED,
            }
        )
