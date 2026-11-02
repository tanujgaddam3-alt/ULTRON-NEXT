from ultron.domain.goal import Goal
from ultron.executive.goals.manager import GoalManager


def test_create_goal():

    manager = GoalManager()

    goal = Goal(
        name="Build API",
    )

    manager.create_goal(goal)

    assert manager.current_goal() == goal