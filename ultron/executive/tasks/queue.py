from ultron.domain.enums import Priority
from ultron.domain.task import Task


class TaskQueue:
    PRIORITY_ORDER = {
        Priority.CRITICAL: 4,
        Priority.HIGH: 3,
        Priority.MEDIUM: 2,
        Priority.LOW: 1,
    }

    def __init__(self):
        self._tasks: list[Task] = []

    def add(self, task: Task):

        self._tasks.append(task)

        self._tasks.sort(
            key=lambda t: self.PRIORITY_ORDER[t.priority],
            reverse=True,
        )

    def next(self):

        if not self._tasks:
            return None

        return self._tasks[0]

    def remove(self, task_id: str):

        self._tasks = [t for t in self._tasks if t.id != task_id]

    def all(self):

        return list(self._tasks)
