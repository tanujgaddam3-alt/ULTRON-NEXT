from datetime import UTC, datetime
from uuid import uuid4

from pydantic import Field

from .base import DomainModel
from .enums import Priority, TaskStatus
from .plan import Plan


class Task(DomainModel):
    """
    Executable unit of work.

    Tasks are immutable domain objects. Lifecycle transitions
    return new Task instances rather than mutating existing state.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    goal_id: str

    title: str

    description: str = ""

    status: TaskStatus = TaskStatus.PENDING

    priority: Priority = Priority.MEDIUM

    dependencies: list[str] = Field(default_factory=list)

    assigned_skill: str | None = None

    plan: Plan | None = None

    parent_operation_id: str | None = None

    step_results: list[dict[str, object]] = Field(default_factory=list)

    retries: int = 0

    max_retries: int = 3

    attempt_id: str | None = None

    attempt_count: int = 0

    progress: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
    )

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )

    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )

    claimed_at: datetime | None = None

    started_at: datetime | None = None

    completed_at: datetime | None = None

    cancellation_requested_at: datetime | None = None

    def with_progress(self, progress: float) -> "Task":
        """
        Return a new Task with updated progress.

        Progress is represented as a percentage from 0 to 100.
        """

        if not 0.0 <= progress <= 100.0:
            raise ValueError(
                "Task progress must be between 0 and 100."
            )

        if progress >= 100.0:
            status = TaskStatus.COMPLETED
            completed_at = self.completed_at or datetime.now(UTC)

        elif progress > 0.0:
            status = TaskStatus.ACTIVE
            completed_at = None

        else:
            status = self.status
            completed_at = self.completed_at

        return self.model_copy(
            update={
                "progress": progress,
                "status": status,
                "completed_at": completed_at,
                "updated_at": datetime.now(UTC),
            }
        )

    def activate(self) -> "Task":
        """
        Return an active copy of this task.
        """

        return self.model_copy(
            update={
                "status": TaskStatus.ACTIVE,
                "updated_at": datetime.now(UTC),
            }
        )

    def ready(self) -> "Task":
        """
        Return a ready copy of this task.
        """

        return self.model_copy(
            update={
                "status": TaskStatus.READY,
                "updated_at": datetime.now(UTC),
            }
        )

    def block(self) -> "Task":
        """
        Return a blocked copy of this task.
        """

        return self.model_copy(
            update={
                "status": TaskStatus.BLOCKED,
                "updated_at": datetime.now(UTC),
            }
        )

    def complete(self) -> "Task":
        """
        Return a completed copy of this task.
        """

        return self.model_copy(
            update={
                "progress": 100.0,
                "status": TaskStatus.COMPLETED,
                "completed_at": self.completed_at or datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
        )

    def fail(self) -> "Task":
        """
        Return a failed copy of this task.
        """

        return self.model_copy(
            update={
                "status": TaskStatus.FAILED,
                "updated_at": datetime.now(UTC),
            }
        )

    def uncertain(self) -> "Task":
        return self.model_copy(
            update={
                "status": TaskStatus.UNCERTAIN,
                "updated_at": datetime.now(UTC),
            }
        )

    def retry(self) -> "Task":
        """
        Return a retryable copy of this task.

        Raises ValueError when the configured retry budget
        has already been exhausted.
        """

        if self.retries >= self.max_retries:
            raise ValueError(
                "Task retry limit has been exhausted."
            )

        return self.model_copy(
            update={
                "retries": self.retries + 1,
                "status": TaskStatus.READY,
                "updated_at": datetime.now(UTC),
            }
        )
