class OptimizationEngine:
    """
    Generates improvement suggestions.
    """

    def __init__(
        self,
    ):

        self.optimizations = []

    def analyze(
        self,
        reflection,
    ):

        suggestion = {
            "source": reflection.experience_id,
            "lesson": reflection.lesson,
            "action": self._generate_action(reflection),
        }

        self.optimizations.append(suggestion)

        return suggestion

    def _generate_action(
        self,
        reflection,
    ):

        if reflection.confidence >= 0.8:
            return "Apply learned pattern"

        return "Collect more experience before changing behaviour"

    def get_history(
        self,
    ):

        return self.optimizations
