from datetime import UTC, datetime


class ExecutionFeedback:
    """
    Converts execution results into learning signals.
    """

    def __init__(
        self,
    ):

        self.records = []

    def record(
        self,
        action: str,
        success: bool,
        message: str = "",
    ):

        result = {
            "action": action,
            "success": success,
            "message": message,
            "timestamp": datetime.now(UTC),
        }

        self.records.append(result)

        return result

    def success_rate(
        self,
    ):

        if not self.records:
            return 0.0

        successful = sum(1 for record in self.records if record["success"])

        return successful / len(self.records)
