from ultron.domain.experience import Experience
from ultron.domain.learning_insight import LearningInsight


class ExperienceInsightGenerator:
    """
    Converts experiences into learning insights.
    """

    def generate(
        self,
        experience: Experience,
    ) -> LearningInsight:

        return LearningInsight(
            success=experience.confidence >= 0.5,
            lesson=experience.lesson,
            confidence=experience.confidence,
            metadata={
                "task_id": experience.task_id,
                "outcome": experience.outcome,
                "source": "experience",
            },
        )
