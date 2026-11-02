from ultron.agents.models import AgentResult


class PrimeEvaluator:
    """
    Evaluates results produced by agents.
    """

    def evaluate(
        self,
        result: AgentResult,
    ) -> dict:

        score = 0.0

        if result.success:
            score += 0.5

        if result.output:
            score += 0.5

        if score >= 0.8:
            return {
                "accepted": True,
                "score": score,
                "reason": "Result quality acceptable.",
            }

        return {
            "accepted": False,
            "score": score,
            "reason": "Result requires improvement.",
        }
