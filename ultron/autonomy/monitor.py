from typing import ClassVar


class AutonomyMonitor:
    """
    Observes system state and detects
    opportunities or problems.
    """

    def __init__(
        self,
        goal_manager=None,
    ):

        self.goal_manager = goal_manager

    def observe(
        self,
        context: dict,
    ) -> list:

        proposals: ClassVar = []

        if context.get("failure"):
            proposals.append(
                {
                    "type": "recovery",
                    "reason": context["failure"],
                }
            )

        if context.get("unfinished_goal"):
            proposals.append(
                {
                    "type": "continue_goal",
                    "goal": context["unfinished_goal"],
                }
            )

        return proposals
