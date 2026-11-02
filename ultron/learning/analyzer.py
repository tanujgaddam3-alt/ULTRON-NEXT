from ultron.domain.experience import Experience
from ultron.domain.learning_insight import LearningInsight


class LearningAnalyzer:
    """
    Extracts knowledge from completed experiences.

    Responsible for interpretation only.
    Does not store memory or modify schemas.
    """

    def analyze(
        self,
        experience: Experience,
    ) -> LearningInsight:
        """
        Convert an experience into a learning insight.
        """

        success = (
            "success" in experience.outcome.lower()
            or "completed" in experience.outcome.lower()
        )

        return LearningInsight(
            success=success,
            lesson=experience.lesson,
            confidence=experience.confidence,
            metadata={
                "source": "experience",
                "task_id": experience.task_id,
            },
        )
