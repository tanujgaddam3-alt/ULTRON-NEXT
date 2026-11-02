from ultron.domain.enums import Priority
from ultron.domain.goal import Goal


class GoalQueue:
    """
    Priority queue for active goals.
    """

    PRIORITY_ORDER = {
        Priority.CRITICAL: 4,
        Priority.HIGH: 3,
        Priority.MEDIUM: 2,
        Priority.LOW: 1,
    }

    def __init__(self):
        self._goals: list[Goal] = []

    def add(self, goal: Goal) -> None:
        self._goals.append(goal)

        self._goals.sort(
            key=lambda g: self.PRIORITY_ORDER[g.priority],
            reverse=True,
        )

    def next(self) -> Goal | None:
        if not self._goals:
            return None

        return self._goals[0]

    def remove(self, goal_id: str) -> None:
        self._goals = [goal for goal in self._goals if goal.id != goal_id]

    def all(self) -> list[Goal]:
        return list(self._goals)

    def clear(self) -> None:
        self._goals.clear()

    def __len__(self) -> int:
        return len(self._goals)
