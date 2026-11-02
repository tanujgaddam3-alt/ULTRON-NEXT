class AnomalyAnalyzer:
    """
    Detects unusual activity patterns.
    """

    def calculate_score(
        self,
        activity,
        history,
    ) -> float:

        score = 0.0

        # New unknown source
        known_sources = [item.source for item in history]

        if activity.source not in known_sources:
            score += 0.4

        # Sensitive actions
        sensitive_actions = {
            "delete_file",
            "modify_security",
            "access_credentials",
        }

        if activity.action in sensitive_actions:
            score += 0.5

        # Extreme activity frequency
        if len(history) > 100:
            score += 0.1

        return min(
            score,
            1.0,
        )

    def analyze(
        self,
        activity,
        history,
    ):

        score = self.calculate_score(
            activity,
            history,
        )

        return {
            "anomaly_score": score,
            "suspicious": (score >= 0.7),
        }
