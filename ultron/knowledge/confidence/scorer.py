class ConfidenceScorer:
    """
    Calculates confidence for knowledge.
    """

    def calculate(self, source_type="unknown", repetitions=1, has_explanation=False):

        score = 0.5

        source_scores = {
            "textbook": 0.9,
            "research": 0.95,
            "document": 0.75,
            "unknown": 0.4,
        }

        score = source_scores.get(source_type, score)

        if repetitions > 1:
            score += min(0.1 * repetitions, 0.2)

        if has_explanation:
            score += 0.05

        return min(score, 1.0)
