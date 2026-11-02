from ultron.cognition.reasoning.hypothesis import Hypothesis


class HypothesisSelector:
    """
    Selects the best hypothesis.
    """

    def select(
        self,
        hypotheses: list[Hypothesis],
    ) -> Hypothesis | None:

        if not hypotheses:
            return None

        return hypotheses[0]
