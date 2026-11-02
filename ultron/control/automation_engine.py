from datetime import UTC, datetime
from typing import ClassVar
from uuid import uuid4


class AutomationTask:
    """
    Represents an automated sequence.
    """

    def __init__(
        self,
        name,
        steps=None,
    ):

        self.id = str(uuid4())

        self.name = name

        self.steps = steps or []

        self.created_at = datetime.now(UTC)


class AutomationEngine:
    """
    Executes multi-step automations.
    """

    def __init__(
        self,
        executor=None,
    ):

        self.executor = executor

        self.tasks = []

    def create(
        self,
        name,
        steps,
    ):

        task = AutomationTask(
            name,
            steps,
        )

        self.tasks.append(task)

        return task

    def execute(
        self,
        task,
    ):

        results: ClassVar = []

        for step in task.steps:
            if self.executor:
                result = self.executor.execute(step)

            else:
                result = {"status": "no_executor"}

            results.append(result)

        return {
            "task": task.name,
            "results": results,
        }
