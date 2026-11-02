from ultron.cognition.reasoning.hypothesis import Hypothesis


class DecisionPolicy:
    """
    Controls whether a hypothesis is eligible
    for decision selection.
    """

    MIN_CONFIDENCE = 0.1

    def allows(
        self,
        hypothesis,
    ) -> bool:

        if isinstance(
            hypothesis,
            Hypothesis,
        ):
            confidence = hypothesis.confidence
            cost = hypothesis.cost
            action = hypothesis.action

        elif isinstance(
            hypothesis,
            dict,
        ):
            confidence = hypothesis.get(
                "confidence",
                0.0,
            )
            cost = hypothesis.get(
                "cost",
                0.0,
            )
            action = hypothesis.get(
                "action",
            )

        else:
            return False

        if confidence < self.MIN_CONFIDENCE:
            return False

        if cost < 0:
            return False

        if action is None:
            return False

        return True
