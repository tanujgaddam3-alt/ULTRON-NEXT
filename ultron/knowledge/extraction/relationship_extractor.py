from typing import ClassVar


class ConceptRelationshipExtractor:
    def extract(self, text):

        relationships: ClassVar = []

        keywords = [
            "slope",
            "area",
            "graph",
            "depends",
            "proportional",
        ]

        for line in text.splitlines():
            clean = line.strip()

            if any(word in clean.lower() for word in keywords):
                relationships.append(clean)

        return relationships
