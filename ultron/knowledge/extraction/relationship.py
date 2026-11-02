from typing import ClassVar

from ultron.knowledge.relationship import (
    Relationship,
)


class RelationshipExtractor:
    """
    Extracts relationships between
    entities and concepts.

    Future upgrades:
    - dependency parsing
    - transformer models
    - LLM extraction
    """

    def extract(
        self,
        text: str,
    ) -> list[Relationship]:

        if not text:
            return []

        relationships: ClassVar = []

        sentences = text.replace("?", ".").split(".")

        relation_words = {
            "uses": "uses",
            "use": "uses",
            "created": "created",
            "developed": "created",
            "depends": "depends_on",
            "contains": "contains",
            "part of": "part_of",
            "belongs": "part_of",
            "helps": "supports",
        }

        for sentence in sentences:
            words = sentence.lower().split()

            for index, word in enumerate(words):
                if word in relation_words:
                    if index > 0 and index < len(words) - 1:
                        source = words[index - 1]

                        target = words[index + 1]

                        relationships.append(
                            Relationship(
                                source_id=source,
                                target_id=target,
                                relation=(relation_words[word]),
                                confidence=0.5,
                            )
                        )

        return relationships
