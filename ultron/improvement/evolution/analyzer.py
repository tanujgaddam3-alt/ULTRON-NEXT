from ultron.improvement.evolution.models import (
    CapabilityStatus,
)


class CapabilityEvolutionAnalyzer:
    """
    Determines whether a capability
    should be created or improved.
    """


    def __init__(
        self,
        capability_registry,
        metrics,
    ):

        self.registry = capability_registry

        self.metrics = metrics



    def analyze(
        self,
        capability,
    ):

        exists = False


        for item in self.registry.list_capabilities():

            if item.name == capability:

                exists = True



        score = (
            self.metrics.measure(
                capability
            )
        )


        weaknesses = []


        if score < 50:

            weaknesses.append(
                "Low capability performance"
            )


        if score < 80:

            weaknesses.append(
                "Needs optimization"
            )


        return CapabilityStatus(

            name=capability,

            exists=exists,

            score=score,

            weaknesses=weaknesses,

        )
