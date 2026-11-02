from typing import ClassVar


class TaskDecomposer:
    """
    Splits complex tasks into smaller agent tasks.
    """

    def __init__(
        self,
    ):

        self.history = []

    def decompose(
        self,
        task,
    ):

        subtasks: ClassVar = []

        if hasattr(task, "steps"):
            subtasks = task.steps

        else:
            subtasks.append(task)

        self.history.append(
            {
                "task": task,
                "subtasks": subtasks,
            }
        )

        return subtasks
