from ultron.domain.enums import GoalStatus
from ultron.domain.goal import Goal


class GoalStore:
    """
    Stores canonical ULTRON goals for autonomous execution.

    The domain Goal is immutable. Lifecycle transitions therefore
    replace the stored instance rather than mutating it.
    """

    def __init__(self):
        self.goals: dict[str, Goal] = {}

    def add(self, goal: Goal) -> Goal:
        self.goals[goal.id] = goal
        return goal

    def get(self, goal_id: str) -> Goal | None:
        return self.goals.get(goal_id)

    def active(self) -> list[Goal]:
        return [
            goal
            for goal in self.goals.values()
            if goal.status == GoalStatus.ACTIVE
        ]

    def complete(self, goal_id: str) -> Goal | None:
        goal = self.get(goal_id)

        if goal is None:
            return None

        completed = goal.complete()
        self.goals[goal_id] = completed

        return completed

    def update(self, goal: Goal) -> Goal:
        if goal.id not in self.goals:
            raise KeyError(
                f"Goal '{goal.id}' is not present in the goal store."
            )

        self.goals[goal.id] = goal
        return goal

    def remove(self, goal_id: str) -> Goal | None:
        return self.goals.pop(goal_id, None)

    def all(self) -> list[Goal]:
        return list(self.goals.values())

    def clear(self) -> None:
        self.goals.clear()

    def __len__(self) -> int:
        return len(self.goals)