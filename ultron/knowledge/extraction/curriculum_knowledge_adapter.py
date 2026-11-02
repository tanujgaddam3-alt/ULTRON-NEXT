from typing import ClassVar

from ultron.knowledge.models import KnowledgeItem


class CurriculumKnowledgeAdapter:
    """
    Converts structured curriculum knowledge
    into ULTRON KnowledgeItems.
    """

    def convert(
        self,
        knowledge_units: list[dict],
    ):

        items: ClassVar = []

        for unit in knowledge_units:
            item = KnowledgeItem(
                content=(unit["concept"]),
                category="concept",
                confidence=1.0,
                metadata={
                    "source": "curriculum",
                    "definitions": unit.get("definitions", []),
                    "formulas": unit.get("formulas", []),
                    "relationships": unit.get("relationships", []),
                },
            )

            items.append(item)

        return items
