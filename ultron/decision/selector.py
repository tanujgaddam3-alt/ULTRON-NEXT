from ultron.domain.decision import Decision


class DecisionSelector:
    """
    Chooses the highest scoring hypothesis.

    Structured arguments produced during inference are preserved
    so downstream planning can construct an executable plan without
    re-parsing the original user input.
    """

    def select(
        self,
        hypotheses,
    ):

        if not hypotheses:
            return None

        def score(item):
            if isinstance(item, dict):
                return item["score"]

            return item.metadata["score"]

        best = max(
            hypotheses,
            key=score,
        )

        if isinstance(best, dict):
            parameters = dict(
                best.get(
                    "arguments",
                    {},
                )
                or {}
            )

            return Decision(
                action=best["action"],
                confidence=best.get(
                    "confidence",
                    best["score"],
                ),
                parameters=parameters,
                reason="Highest overall score.",
                alternatives=[
                    h["action"]
                    for h in hypotheses
                    if h is not best
                ],
            )

        parameters = dict(
            best.arguments
            or {}
        )

        return Decision(
            action=best.action,
            confidence=best.confidence,
            parameters=parameters,
            reason="Highest overall score.",
            alternatives=[
                h.action
                for h in hypotheses
                if h is not best
            ],
        )
