class SelfEvaluator:
    """
    Evaluates execution quality and extracts improvement signals.
    """

    def evaluate(
        self,
        execution,
        reflection,
    ) -> dict:

        record = execution.record

        if record.success:
            return {
                "score": 1.0,
                "quality": "high",
                "recommendation": ("Current execution strategy worked."),
            }

        return {
            "score": 0.5,
            "quality": "low",
            "recommendation": ("Review failed execution strategy."),
        }
