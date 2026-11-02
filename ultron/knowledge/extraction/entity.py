from typing import ClassVar

from ultron.knowledge.entity import (
    Entity,
    EntityType,
)


class EntityExtractor:
    """
    Extracts entities from text.

    Current implementation:
    rule-based foundation.

    Future:
    - NLP models
    - LLM extraction
    - domain models
    """

    def extract(
        self,
        text: str,
    ) -> list[Entity]:

        if not text:
            return []

        entities: ClassVar = []

        words = text.replace(".", "").replace(",", "").split()

        for word in words:
            cleaned = word.strip()

            if not cleaned:
                continue

            entity_type = self.detect_type(cleaned)

            if entity_type != EntityType.UNKNOWN:
                entities.append(
                    Entity(
                        name=cleaned,
                        entity_type=entity_type,
                        confidence=0.5,
                    )
                )

        return entities

    def detect_type(
        self,
        word: str,
    ) -> EntityType:

        lower = word.lower()

        technology_words = [
            "python",
            "ai",
            "robot",
            "linux",
            "database",
        ]

        if lower in technology_words:
            return EntityType.TECHNOLOGY

        if word.isupper() and len(word) > 2:
            return EntityType.ORGANIZATION

        return EntityType.UNKNOWN
