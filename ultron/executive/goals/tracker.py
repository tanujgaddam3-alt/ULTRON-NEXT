from ultron.domain.goal import Goal


class GoalTracker:
    """
    Tracks goal progress using immutable domain updates.
    """

    def update_progress(
        self,
        goal: Goal,
        progress: float,
    ) -> Goal:

        if not 0.0 <= progress <= 1.0:
            raise ValueError("Goal progress must be between 0.0 and 1.0.")

        return goal.model_copy(
            update={
                "progress": progress,
            }
        )
