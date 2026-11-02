from datetime import UTC, datetime


class PerformanceMetrics:
    """
    Tracks ULTRON performance.
    """

    def __init__(
        self,
    ):

        self.history = []

    def record(
        self,
        system,
        score,
    ):

        self.history.append(
            {"system": system, "score": score, "time": datetime.now(UTC)}
        )

    def average(
        self,
        system,
    ):

        values = [item["score"] for item in self.history if item["system"] == system]

        if not values:
            return 0

        return sum(values) / len(values)
