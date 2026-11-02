from typing import ClassVar

from ultron.cognition.dashboard.visualization_models import (
    CognitiveDashboardState,
)


class CognitiveDashboard:
    """
    Generates ULTRON cognitive status views.
    """

    def __init__(
        self,
        health_monitor=None,
    ):

        self.health_monitor = health_monitor

    def generate(
        self,
    ):

        if not self.health_monitor:
            return None

        health = self.health_monitor.evaluate()

        recommendations: ClassVar = []

        if health["overall_health"] < 0.6:
            recommendations.append("System optimization required")

        else:
            recommendations.append("Cognitive state stable")

        return CognitiveDashboardState(
            health_score=health["overall_health"],
            subsystem_scores=health["systems"],
            recommendations=recommendations,
        )
