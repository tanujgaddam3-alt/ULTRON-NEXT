from ultron.cognition.reasoning.hypothesis import Hypothesis


class HypothesisEvaluator:
    """
    Evaluates hypotheses using confidence,
    cost, and available cognitive evidence.
    """

    def evaluate(
        self,
        hypotheses: list[Hypothesis],
    ) -> None:

        for hypothesis in hypotheses:
            score = hypothesis.confidence - hypothesis.cost

            metadata = hypothesis.metadata

            if metadata.get("schema_count", 0) > 0:
                score += 0.05

            if metadata.get("memory_used", False):
                score += 0.03

            hypothesis.priority = min(
                score,
                1.0,
            )
