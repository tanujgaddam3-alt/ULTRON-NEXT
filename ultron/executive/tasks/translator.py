from ultron.domain.goal import Goal
from ultron.domain.task import Task


class TaskTranslator:
    """
    Converts goals into executable tasks.
    """

    def translate(
        self,
        goal: Goal,
    ) -> list[Task]:

        return [
            Task(
                goal_id=goal.id,
                title=goal.name,
                description=goal.description,
            )
        ]
