from ultron.domain.task import Task


class TaskEvaluator:
    """
    Evaluates task lifecycle state.

    Task state transitions are delegated to the immutable
    Task domain model.
    """

    def evaluate(
        self,
        task: Task,
    ) -> Task:

        if task.progress >= 100.0:
            return task.complete()

        if task.progress > 0.0:
            return task.activate()

        return task
