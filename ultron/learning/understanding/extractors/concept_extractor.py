from typing import ClassVar


class ConceptExtractor:
    """
    Extracts concepts from document text.

    Domain independent.
    """

    def extract(
        self,
        document,
    ):

        concepts: ClassVar = []

        for section in document.sections:
            title = section.get("title", "")

            if "concept" in title.lower():
                concepts.append(title)

        return concepts
