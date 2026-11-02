class HealthMonitor:
    """
    Evaluates ULTRON cognitive health.
    """

    def __init__(
        self,
        metrics_collector,
    ):

        self.metrics = metrics_collector

    def evaluate(
        self,
    ):

        systems = {
            "vision": self.metrics.average_score("vision"),
            "voice": self.metrics.average_score("voice"),
            "memory": self.metrics.average_score("memory"),
            "control": self.metrics.average_score("control"),
        }

        active = [score for score in systems.values() if score > 0]

        overall = sum(active) / len(active) if active else 0

        return {
            "systems": systems,
            "overall_health": overall,
            "status": self._status(overall),
        }

    def _status(
        self,
        score,
    ):

        if score >= 0.85:
            return "optimal"

        if score >= 0.60:
            return "stable"

        return "needs_attention"
