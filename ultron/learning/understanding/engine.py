from typing import ClassVar

from ultron.learning.understanding.knowledge_unit import KnowledgeUnit


class UnderstandingEngine:
    """
    Converts documents into multiple knowledge units.
    """

    def understand(
        self,
        document,
    ):

        units: ClassVar = []

        current = None

        for section in document.sections:
            title = section.get("title", "")

            content = section.get("content", "")

            title_clean = title.strip()

            if "CONCEPT" in title_clean.upper():
                if current:
                    units.append(current)

                current = KnowledgeUnit()

                current.metadata = document.metadata

                current.concepts.append(title_clean)

                current.examples.append(content)

            elif current:
                upper = title_clean.upper()

                if "DEFINITION" in upper:
                    current.definitions.append(content)

                elif "FORMULA" in upper or "EQUATION" in upper:
                    current.rules.append(content)

                elif "EXAMPLE" in upper:
                    current.examples.append(content)

                else:
                    current.relationships.append(content)

        if current:
            units.append(current)

        return units
