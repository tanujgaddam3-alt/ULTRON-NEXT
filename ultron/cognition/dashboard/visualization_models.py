from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class CognitiveDashboardState:
    """
    Data model for cognitive dashboard.
    """

    health_score: float

    subsystem_scores: dict[str, float]

    recommendations: list

    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def summary(
        self,
    ):

        return {
            "health": self.health_score,
            "systems": self.subsystem_scores,
            "recommendations": self.recommendations,
            "timestamp": self.created_at,
        }
