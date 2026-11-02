from ultron.improvement.goals.models import (
    GoalCapabilityMap,
)


class GoalEvolutionPlanner:
    """
    Converts goal capability gaps
    into evolution priorities.
    """


    def create_plan(
        self,
        mapping: GoalCapabilityMap,
    ):

        priorities = []


        for requirement in mapping.required:

            if (
                requirement.capability
                in mapping.missing
            ):

                priorities.append(
                    {
                        "capability":
                            requirement.capability,

                        "reason":
                            requirement.reason,

                        "priority":
                            requirement.importance,
                    }
                )


        priorities.sort(
            key=lambda x: x["priority"],
            reverse=True,
        )


        return {
            "goal": mapping.goal,

            "missing_capabilities":
                priorities,

            "next":
                (
                    priorities[0]
                    if priorities
                    else None
                ),
        }
