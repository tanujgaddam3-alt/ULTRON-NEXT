from typing import ClassVar

from ultron.knowledge.models import (
    Concept,
)


class ConceptExtractor:
    """
    Extracts abstract concepts from text.

    Future upgrades:
    - embeddings
    - semantic similarity
    - ontology matching
    - LLM reasoning
    """

    def extract(
        self,
        text: str,
    ) -> list[Concept]:

        if not text:
            return []

        concepts: ClassVar = []

        keywords = {
            "artificial intelligence": "AI",
            "machine learning": "Machine Learning",
            "deep learning": "Deep Learning",
            "neural network": "Neural Networks",
            "computer vision": "Computer Vision",
            "robotics": "Robotics",
            "database": "Database Systems",
            "software": "Software Engineering",
            "algorithm": "Algorithms",
        }

        content = text.lower()

        for keyword, name in keywords.items():
            if keyword in content:
                concepts.append(
                    Concept(
                        name=name,
                        content=(f"Concept detected: {name}"),
                        confidence=0.7,
                    )
                )

        return concepts
