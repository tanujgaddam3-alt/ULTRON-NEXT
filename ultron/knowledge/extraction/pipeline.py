from typing import Any


class KnowledgeExtractionPipeline:
    """
    Production pipeline for converting
    documents into structured knowledge.

    Stages:
    - preprocessing
    - entity extraction
    - concept extraction
    - relationship extraction
    - fact generation
    """

    def __init__(
        self,
        preprocessors=None,
        entity_extractor=None,
        concept_extractor=None,
        relationship_extractor=None,
        fact_extractor=None,
    ):

        self.preprocessors = preprocessors or []

        self.entity_extractor = entity_extractor

        self.concept_extractor = concept_extractor

        self.relationship_extractor = relationship_extractor

        self.fact_extractor = fact_extractor

    def process(
        self,
        document: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Execute extraction pipeline.
        """

        content = document.get(
            "content",
            "",
        )

        for processor in self.preprocessors:
            content = processor.process(content)

        result = {
            "source": document.get("source"),
            "entities": [],
            "concepts": [],
            "relationships": [],
            "facts": [],
        }

        if self.entity_extractor:
            result["entities"] = self.entity_extractor.extract(content)

        if self.concept_extractor:
            result["concepts"] = self.concept_extractor.extract(content)

        # Ignore generic document-level concepts
        result["concepts"] = [c for c in result["concepts"] if len(c.content) < 500]

        if self.relationship_extractor:
            result["relationships"] = self.relationship_extractor.extract(content)

        if self.fact_extractor:
            result["facts"] = self.fact_extractor.extract(result["relationships"])

        return result
