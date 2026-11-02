from dataclasses import dataclass, field


@dataclass
class ImprovementStrategy:

    action: str

    target: str

    reason: str

    risk: str

    steps: list[str] = field(
        default_factory=list
    )


class ImprovementStrategyEngine:
    """
    Decides the safest implementation
    strategy for an improvement.
    """


    def evaluate(
        self,
        proposal,
        context=None,
    ) -> ImprovementStrategy:


        target = proposal.target


        # Critical system areas
        critical_components = [

            "cognition",

            "self_awareness",

            "reasoning",

            "memory",

        ]


        for item in critical_components:

            if item in target:

                return ImprovementStrategy(

                    action="create_new_component",

                    target=target,

                    reason=(
                        "Existing component is "
                        "critical. Avoid direct modification."
                    ),

                    risk="LOW",

                    steps=[

                        "Create isolated module",

                        "Implement capability",

                        "Register component",

                        "Run validation",

                    ],
                )


        return ImprovementStrategy(

    action="create_new_component",

    target=target,

    reason=(
        "Capability does not exist. "
        "Create isolated component first."
    ),

    risk="LOW",

    steps=[

        "Design component",

        "Create files",

        "Register capability",

        "Run validation",

    ],
)