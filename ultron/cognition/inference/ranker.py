from ultron.cognition.reasoning.hypothesis import Hypothesis


class HypothesisRanker:
    """
    Orders hypotheses.
    """

    def rank(
        self,
        hypotheses: list[Hypothesis],
    ) -> list[Hypothesis]:

        return sorted(
            hypotheses,
            key=lambda h: h.priority,
            reverse=True,
        )
