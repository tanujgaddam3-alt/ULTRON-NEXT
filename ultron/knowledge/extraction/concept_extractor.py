from typing import ClassVar

from ultron.knowledge.models import (
    Concept,
)


class ConceptExtractor:
    """
    Extracts concepts from knowledge chunks.
    """

    def __init__(
        self,
        entity_detector=None,
    ):

        self.entity_detector = entity_detector

    def extract(
        self,
        text: str,
    ):

        entities: ClassVar = []

        if self.entity_detector:
            entities = self.entity_detector.detect(text)

        concepts: ClassVar = []

        for entity in entities:
            concepts.append(
                Concept(
                    name=entity,
                    content=text,
                    category="concept",
                    confidence=0.5,
                )
            )

        return concepts
