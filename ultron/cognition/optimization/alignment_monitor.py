from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class AlignmentResult:
    """
    Represents alignment evaluation.
    """

    goal: str

    behaviour: str

    score: float

    recommendation: str

    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class AlignmentMonitor:
    """
    Measures ULTRON goal-behaviour alignment.
    """

    def __init__(
        self,
    ):

        self.history = []

    def evaluate(
        self,
        goal: str,
        behaviour: str,
        score: float,
    ):

        if not 0 <= score <= 1:
            raise ValueError("Alignment score must be between 0 and 1")

        if score >= 0.8:
            recommendation = "Behaviour aligned with goal"

        elif score >= 0.5:
            recommendation = "Minor optimization required"

        else:
            recommendation = "Major correction required"

        result = AlignmentResult(
            goal=goal,
            behaviour=behaviour,
            score=score,
            recommendation=recommendation,
        )

        self.history.append(result)

        return result
