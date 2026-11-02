from ultron.improvement.evolution.models import (
    EvolutionDecision,
)


class CapabilityEvolutionEngine:
    """
    Decides whether to create
    or upgrade capabilities.
    """


    def decide(
        self,
        status,
    ):


        if not status.exists:

            return EvolutionDecision(

                capability=status.name,

                action="create",

                reason=(
                    "Capability does not exist"
                ),

                priority=10,

            )


        if status.score < 80:

            return EvolutionDecision(

                capability=status.name,

                action="improve",

                reason=(
                    "Existing capability "
                    "requires improvement"
                ),

                priority=8,

            )


        return EvolutionDecision(

            capability=status.name,

            action="maintain",

            reason=(
                "Capability performing well"
            ),

            priority=2,

        )
