from typing import ClassVar

from ultron.knowledge.models import (
    Chapter,
    Concept,
    Formula,
    Misconception,
)


class CurriculumKnowledgeAdapter:
    """
    Converts curriculum structures into
    ULTRON knowledge objects.
    """

    def convert(
        self,
        curriculum: dict,
    ):

        knowledge: ClassVar = []

        for concept in curriculum.get("concepts", []):
            knowledge.append(
                Concept(
                    name=concept.strip(),
                    content=concept.strip(),
                    category="concept",
                    confidence=0.9,
                    metadata={"source": "curriculum"},
                )
            )

        for formula in curriculum.get("formulas", []):
            knowledge.append(
                Formula(
                    name="Physics Formula",
                    content=formula.strip(),
                    confidence=0.9,
                    metadata={"source": "curriculum"},
                )
            )

        for chapter in curriculum.get("chapters", []):
            knowledge.append(
                Chapter(
                    name=chapter.strip(),
                    content=chapter.strip(),
                    confidence=0.9,
                    metadata={"source": "curriculum"},
                )
            )

        for misconception in curriculum.get("misconceptions", []):
            knowledge.append(
                Misconception(
                    statement=misconception.strip(),
                    correction="Requires verification",
                    content=misconception.strip(),
                    confidence=0.8,
                    metadata={"source": "curriculum"},
                )
            )

        return knowledge
