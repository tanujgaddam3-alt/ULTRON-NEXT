from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4


class TaskStatus(str, Enum):
    PENDING = "pending"

    RUNNING = "running"

    COMPLETED = "completed"

    CANCELLED = "cancelled"


class Task:
    """
    Represents a user task.
    """

    def __init__(
        self,
        title: str,
        description: str = "",
    ):

        self.id = str(uuid4())

        self.title = title

        self.description = description

        self.status = TaskStatus.PENDING

        self.created_at = datetime.now(UTC)


class TaskManager:
    """
    Manages user tasks.
    """

    def __init__(
        self,
    ):

        self.tasks = []

    def create(
        self,
        title,
        description="",
    ):

        task = Task(
            title,
            description,
        )

        self.tasks.append(task)

        return task

    def complete(
        self,
        task_id,
    ):

        for task in self.tasks:
            if task.id == task_id:
                task.status = TaskStatus.COMPLETED

                return task

        return None

    def list_tasks(
        self,
    ):

        return self.tasks
