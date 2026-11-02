from typing import ClassVar


class SecurityRecommendationEngine:
    """
    Generates security improvement suggestions.
    """

    def analyze(
        self,
        threat,
    ):

        recommendations: ClassVar = []

        description = threat.description.lower()

        if "authentication" in description:
            recommendations.append("Strengthen authentication requirements.")

        if "permission" in description:
            recommendations.append("Review permission policies.")

        if "file" in description:
            recommendations.append("Verify file integrity protection.")

        if not recommendations:
            recommendations.append("Continue monitoring and collect more evidence.")

        return {
            "threat": threat.id,
            "recommendations": recommendations,
        }
