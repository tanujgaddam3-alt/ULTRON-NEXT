from ultron.domain.knowledge import Knowledge
from ultron.domain.learning_insight import LearningInsight


class KnowledgeExtractor:
    """
    Converts learning insights into factual knowledge.

    Responsible only for extraction.
    Does not store knowledge or modify memory.
    """

    def extract(
        self,
        insight: LearningInsight,
    ) -> list[Knowledge]:
        """
        Extract knowledge items from a learning insight.
        """

        return [
            Knowledge(
                key="execution_pattern",
                value={
                    "success": insight.success,
                    "lesson": insight.lesson,
                },
                source="learning",
                confidence=insight.confidence,
                metadata={
                    "source_type": "experience",
                },
            )
        ]
