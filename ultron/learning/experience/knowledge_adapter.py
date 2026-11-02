from ultron.knowledge.models import (
    KnowledgeItem,
    KnowledgeSource,
)
from ultron.learning.experience.models import (
    ExperienceRecord,
)


class ExperienceKnowledgeAdapter:
    """
    Converts experiences into knowledge.
    """

    def convert(
        self,
        experience: ExperienceRecord,
    ) -> KnowledgeItem:
        """
        Convert an experience into
        reusable knowledge.
        """

        content = (
            f"Situation: {experience.situation}. "
            f"Action: {experience.action}. "
            f"Result: {experience.result}. "
            f"Lesson: {experience.lesson}"
        )

        confidence = 1.0 if experience.success else 0.5

        return KnowledgeItem(
            content=content,
            category="experience",
            confidence=confidence,
            source=KnowledgeSource(
                source_type="experience",
                reference=experience.id,
                metadata={"origin": "reflection"},
            ),
            metadata={
                "success": experience.success,
                "action": experience.action,
                "context": experience.context,
            },
        )
