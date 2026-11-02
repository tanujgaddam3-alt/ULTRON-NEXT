from typing import ClassVar

from ultron.knowledge.models import (
    Concept,
    KnowledgeSource,
)


class KnowledgeExtractor:
    """
    Extracts structured knowledge from documents.
    """

    def extract(
        self,
        document: dict,
    ) -> list:

        content = document.get(
            "content",
            "",
        )

        source = KnowledgeSource(
            source_type="document",
            reference=document.get(
                "source",
                "",
            ),
        )

        knowledge: ClassVar = []

        # Basic concept extraction
        words = content.replace(".", "").replace(",", "").split()

        for word in words:
            if len(word) > 5:
                knowledge.append(
                    Concept(
                        name=word,
                        content=(f"Concept extracted from document: {word}"),
                        source=source,
                        confidence=0.5,
                    )
                )

        return knowledge
