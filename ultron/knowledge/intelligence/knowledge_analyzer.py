from typing import ClassVar

from ultron.knowledge.intelligence.graph_metrics import (
    GraphMetrics,
)


class KnowledgeAnalyzer:
    """
    Analyzes ULTRON knowledge quality.
    """

    def __init__(
        self,
        metrics=None,
    ):

        self.metrics = metrics or GraphMetrics()

    def analyze(
        self,
        graph,
    ):

        statistics = self.metrics.analyze(graph)

        return {
            "knowledge_health": self._health(statistics),
            "statistics": statistics,
            "recommendations": self._recommend(statistics),
        }

    def _health(
        self,
        stats,
    ):

        confidence = stats["average_confidence"]

        density = stats["knowledge_density"]

        score = (confidence * 0.6) + (min(density, 1) * 0.4)

        return round(score, 3)

    def _recommend(
        self,
        stats,
    ):

        recommendations: ClassVar = []

        if stats["entities"] == 0:
            recommendations.append("No concepts available")

        if stats["relationships"] == 0:
            recommendations.append("Increase knowledge connections")

        if stats["average_confidence"] < 0.5:
            recommendations.append("Improve knowledge validation")

        if not recommendations:
            recommendations.append("Knowledge structure healthy")

        return recommendations
