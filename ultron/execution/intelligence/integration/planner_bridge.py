from typing import ClassVar


class PlannerExecutionBridge:
    """
    Connects planning decisions
    with execution capabilities.
    """

    def __init__(
        self,
        planner=None,
        capability_registry=None,
    ):

        self.planner = planner

        self.registry = capability_registry

    def validate_plan(
        self,
        plan,
    ):

        results: ClassVar = []

        for step in plan.steps:
            action = step.action

            available = self.registry.exists(action) if self.registry else True

            results.append(
                {
                    "action": action,
                    "available": available,
                }
            )

        return results
