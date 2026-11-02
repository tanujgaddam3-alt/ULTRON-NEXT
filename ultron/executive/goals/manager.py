from ultron.domain.goal import Goal

from .queue import GoalQueue


class GoalManager:
    """
    Central manager for all goals.
    """

    def __init__(self):
        self.queue = GoalQueue()

    def create_goal(self, goal: Goal) -> Goal:
        self.queue.add(goal)
        return goal

    def current_goal(self) -> Goal | None:
        return self.queue.next()

    def remove_goal(self, goal_id: str) -> None:
        self.queue.remove(goal_id)

    def goals(self) -> list[Goal]:
        return self.queue.all()
