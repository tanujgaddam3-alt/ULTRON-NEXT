from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class CognitiveState:
    """
    Represents ULTRON cognitive condition.
    """

    reasoning_score: float = 0.0

    memory_score: float = 0.0

    learning_score: float = 0.0

    decision_score: float = 0.0

    stability_score: float = 0.0

    metadata: dict = field(default_factory=dict)

    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def update_metric(
        self,
        metric: str,
        value: float,
    ):

        if not hasattr(self, metric):
            raise ValueError(f"Unknown metric: {metric}")

        if not 0 <= value <= 1:
            raise ValueError("Score must be between 0 and 1")

        setattr(self, metric, value)

        self.updated_at = datetime.now(UTC)

    def snapshot(
        self,
    ):

        return {
            "reasoning": self.reasoning_score,
            "memory": self.memory_score,
            "learning": self.learning_score,
            "decision": self.decision_score,
            "stability": self.stability_score,
            "updated_at": self.updated_at,
        }
