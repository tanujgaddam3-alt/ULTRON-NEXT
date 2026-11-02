class EvaluationIntelligence:
    """
    Advanced evaluation layer for hypotheses.
    """

    def __init__(
        self,
    ):

        self.history = []

    def evaluate(
        self,
        hypothesis,
    ):

        confidence = hypothesis.confidence

        cost = hypothesis.cost

        quality = (confidence * 0.7) - (cost * 0.3)

        quality = max(min(quality, 1), 0)

        hypothesis.metadata["evaluation_score"] = quality

        hypothesis.priority = quality

        self.history.append(
            {
                "hypothesis": str(hypothesis),
                "score": quality,
            }
        )

        return hypothesis

    def evaluate_all(
        self,
        hypotheses,
    ):

        return [self.evaluate(hypothesis) for hypothesis in hypotheses]
