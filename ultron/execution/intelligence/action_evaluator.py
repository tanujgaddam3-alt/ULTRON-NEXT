class ActionEvaluator:
    """
    Evaluates actions before execution.
    """

    def __init__(
        self,
    ):

        self.history = []

    def evaluate(
        self,
        action: str,
        risk: float = 0.0,
        confidence: float = 1.0,
    ):

        score = (confidence * 0.7) - (risk * 0.3)

        score = max(
            min(score, 1),
            0,
        )

        result = {
            "action": action,
            "score": score,
            "approved": score >= 0.5,
        }

        self.history.append(result)

        return result
