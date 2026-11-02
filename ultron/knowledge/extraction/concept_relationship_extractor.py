from typing import ClassVar


class ConceptRelationshipExtractor:
    """
    Extracts conceptual relationships.
    """

    def extract(
        self,
        text: str,
    ):

        relationships: ClassVar = []

        keywords = [
            "slope",
            "area",
            "depends",
            "proportional",
            "relationship",
        ]

        for line in text.splitlines():
            clean = line.strip()

            lower = clean.lower()

            if any(word in lower for word in keywords):
                relationships.append(clean)

        return relationships
