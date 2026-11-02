from __future__ import annotations

from ultron.domain.goal import Goal
from ultron.domain.enums import GoalStatus

class GoalRepository:
    """
    Stores ULTRON's canonical immutable goals.
    """

    def __init__(self) -> None:
        self._goals: dict[str, Goal] = {}

    def add(self, goal: Goal) -> None:
        self._goals[goal.id] = goal

    def get(self, goal_id: str) -> Goal | None:
        return self._goals.get(goal_id)

    def remove(self, goal_id: str) -> None:
        self._goals.pop(goal_id, None)

    def exists(self, goal_id: str) -> bool:
        return goal_id in self._goals

    def all(self) -> list[Goal]:
        return list(self._goals.values())

    def active(self) -> list[Goal]:
        return [
            goal
            for goal in self._goals.values()
            if goal.status == GoalStatus.ACTIVE
        ]

    def clear(self) -> None:
        self._goals.clear()