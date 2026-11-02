from datetime import UTC, datetime


class CognitiveState:
    """
    Represents ULTRON internal intelligence state.
    """

    def __init__(self):

        self.metrics = {
            "reasoning": 0.0,
            "memory": 0.0,
            "learning": 0.0,
            "decision": 0.0,
            "stability": 0.0,
        }

        self.updated_at = datetime.now(UTC)

    def update(
        self,
        category,
        value,
    ):

        if category in self.metrics:
            self.metrics[category] = value

            self.updated_at = datetime.now(UTC)

    def status(
        self,
    ):

        return self.metrics
