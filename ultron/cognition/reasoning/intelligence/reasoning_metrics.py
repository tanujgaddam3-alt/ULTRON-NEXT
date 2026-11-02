from datetime import UTC, datetime


class ReasoningMetrics:
    """
    Tracks reasoning performance.
    """

    def __init__(
        self,
    ):

        self.records = []

    def record(
        self,
        confidence: float,
        success: bool,
        metadata=None,
    ):

        if not 0 <= confidence <= 1:
            raise ValueError("Confidence must be between 0 and 1")

        self.records.append(
            {
                "confidence": confidence,
                "success": success,
                "metadata": metadata or {},
                "timestamp": datetime.now(UTC),
            }
        )

    def accuracy(
        self,
    ):

        if not self.records:
            return 0.0

        successful = sum(1 for record in self.records if record["success"])

        return successful / len(self.records)
