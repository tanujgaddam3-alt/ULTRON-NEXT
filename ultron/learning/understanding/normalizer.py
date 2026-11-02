import re
from typing import ClassVar


class KnowledgeNormalizer:
    """
    Cleans extracted knowledge before storage.
    """

    def normalize(
        self,
        knowledge,
    ):

        return {
            "definitions": self.clean_definitions(knowledge["definitions"]),
            "formulas": self.clean_formulas(knowledge["formulas"]),
            "examples": knowledge["examples"],
            "relationships": knowledge["relationships"],
            "misconceptions": knowledge["misconceptions"],
            "records": knowledge["records"],
        }

    def clean_definitions(
        self,
        items,
    ):

        result: ClassVar = []

        for item in items:
            content = item["content"]

            if not content.strip():
                continue

            result.append(item)

        return result

    def clean_formulas(
        self,
        formulas,
    ):

        result: ClassVar = []

        for formula in formulas:
            text = formula.strip()

            # remove code blocks

            if text.startswith("```"):
                continue

            # remove explanatory text

            if any(
                word in text.lower()
                for word in [
                    "inverse relationships",
                    "verification",
                    "meaning",
                ]
            ):
                continue

            # require mathematical relation

            if re.search(r"\w+\s*=\s*[\w\d]", text):
                result.append(text)

        return result
