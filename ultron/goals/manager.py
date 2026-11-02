from __future__ import annotations

from ultron.domain.enums import GoalStatus, Priority
from ultron.domain.goal import Goal
from ultron.domain.understanding import Understanding

from ultron.goals.repository import GoalRepository


class GoalManager:
    """
    Manages application-level goals.

    Uses the canonical immutable domain Goal model.
    """

    def __init__(
        self,
        repository: GoalRepository,
    ):
        self.repository = repository

    def create_goal(
        self,
        name: str,
        description: str = "",
        priority: Priority = Priority.MEDIUM,
    ) -> Goal:
        goal = Goal(
            name=name,
            description=description,
            priority=priority,
            status=GoalStatus.ACTIVE,
        )

        self.repository.add(goal)

        return goal

    def add(self, goal: Goal) -> Goal:
        self.repository.add(goal)
        return goal

    def get(self, goal_id: str) -> Goal | None:
        return self.repository.get(goal_id)

    def active_goals(self) -> list[Goal]:
        return self.repository.active()

    def complete(self, goal_id: str) -> Goal | None:
        goal = self.repository.get(goal_id)

        if goal is None:
            return None

        completed = goal.complete()
        self.repository.add(completed)

        return completed

    def fail(self, goal_id: str) -> Goal | None:
        goal = self.repository.get(goal_id)

        if goal is None:
            return None

        failed = goal.fail()
        self.repository.add(failed)

        return failed

    def activate(self, goal_id: str) -> Goal | None:
        goal = self.repository.get(goal_id)

        if goal is None:
            return None

        activated = goal.activate()
        self.repository.add(activated)

        return activated

    def update_progress(
        self,
        goal_id: str,
        progress: float,
    ) -> Goal | None:
        goal = self.repository.get(goal_id)

        if goal is None:
            return None

        updated = goal.with_progress(progress)
        self.repository.add(updated)

        return updated

    def infer_goal(
        self,
        understanding: Understanding,
    ) -> Goal:
        return self.create_goal(
            name=understanding.intent,
            description=understanding.text,
            priority=Priority.HIGH,
        )