from ultron.domain.goal import Goal
from ultron.domain.understanding import Understanding


class GoalService:
    """
    Coordinates goal creation and task generation.
    """

    def __init__(
        self,
        goal_manager,
        goal_translator,
        task_manager,
        task_translator,
        blackboard,
        event_bus,
    ):
        self.goal_manager = goal_manager
        self.goal_translator = goal_translator
        self.task_manager = task_manager
        self.task_translator = task_translator
        self.blackboard = blackboard
        self.event_bus = event_bus

    def process(
        self,
        understanding: Understanding,
    ) -> Goal:

        goal = self.goal_translator.translate(understanding)

        self.goal_manager.create_goal(goal)

        tasks = self.task_translator.translate(goal)

        self.task_manager.create_tasks(tasks)

        workspace = self.blackboard.workspace

        self.blackboard.update(
            goal_stack=[
                *workspace.goal_stack,
                goal,
            ],
            task_stack=[
                *workspace.task_stack,
                *tasks,
            ],
        )

        return goal
