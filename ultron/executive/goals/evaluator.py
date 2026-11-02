from ultron.domain.enums import GoalStatus
from ultron.domain.goal import Goal


class GoalEvaluator:
    """
    Evaluates goal state from its progress.
    """

    def evaluate(
        self,
        goal: Goal,
    ) -> Goal:

        if goal.progress >= 1.0:
            status = GoalStatus.COMPLETED

        elif goal.progress > 0.0:
            status = GoalStatus.ACTIVE

        else:
            status = goal.status

        if status == goal.status:
            return goal

        return goal.model_copy(
            update={
                "status": status,
            }
        )
