from ultron.domain.task import Task


class TaskTracker:
    """
    Tracks task progress through immutable lifecycle transitions.
    """

    def update_progress(
        self,
        task: Task,
        progress: float,
    ) -> Task:

        return task.with_progress(progress)
