from ultron.improvement.goals.models import (
    CapabilityRequirement,
    GoalCapabilityMap,
)


class GoalCapabilityAnalyzer:
    """
    Converts goals into required capabilities.
    """


    def __init__(
        self,
        capability_registry,
    ):

        self.registry = capability_registry



    def analyze(
        self,
        goal,
    ):

        requirements = (
            self._infer_requirements(
                goal
            )
        )


        existing = [

            item.name

            for item in
            self.registry.list_capabilities()

        ]


        missing = []


        available = []


        for requirement in requirements:

            if requirement.capability in existing:

                available.append(
                    requirement.capability
                )

            else:

                missing.append(
                    requirement.capability
                )


        return GoalCapabilityMap(

            goal=goal,

            required=requirements,

            missing=missing,

            available=available,

        )



    def _infer_requirements(
        self,
        goal,
    ):

        goal = goal.lower()


        if "financial" in goal or "trading" in goal:

            return [

                CapabilityRequirement(
                    "market_data",
                    "Collect market information",
                    10,
                ),

                CapabilityRequirement(
                    "financial_analysis",
                    "Analyze companies and markets",
                    10,
                ),

                CapabilityRequirement(
                    "risk_management",
                    "Evaluate risk",
                    9,
                ),

                CapabilityRequirement(
                    "backtesting",
                    "Test strategies",
                    8,
                ),

            ]


        return [

            CapabilityRequirement(
                "reasoning",
                "General problem solving",
                5,
            )

        ]
