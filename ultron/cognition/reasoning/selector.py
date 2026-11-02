from ultron.cognition.reasoning.hypothesis import Hypothesis


class HypothesisRanker:
    """
    Orders hypotheses by desirability without making a decision.
    """

    def rank(
        self,
        hypotheses: list[Hypothesis],
    ) -> list[Hypothesis]:

        return sorted(
            hypotheses,
            key=lambda h: (
                h.priority,
                h.confidence,
                -h.cost,
            ),
            reverse=True,
        )
