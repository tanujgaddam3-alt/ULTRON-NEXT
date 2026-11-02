from typing import Any, ClassVar

from ultron.knowledge.extraction.curriculum_adapter import (
    CurriculumKnowledgeAdapter,
)
from ultron.knowledge.extraction.curriculum_extractor import (
    CurriculumExtractor,
)


class KnowledgeIngestionService:
    """
    Main entry point for adding knowledge.

    Pipeline:

    Document
        |
        v
    Extraction
        |
        v
    Fusion
        |
        v
    Validation
        |
        v
    Consolidation
        |
        v
    Storage
    """

    def __init__(
        self,
        extraction_pipeline,
        fusion_engine,
        validator,
        consolidator,
        knowledge_service,
    ):

        self.pipeline = extraction_pipeline

        self.fusion = fusion_engine

        self.validator = validator

        self.consolidator = consolidator

        self.knowledge = knowledge_service

        self.curriculum_extractor = CurriculumExtractor()

        self.curriculum_adapter = CurriculumKnowledgeAdapter()

    def ingest(
        self,
        document: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Process a document into knowledge.
        """

        extracted = self.pipeline.process(document)
        curriculum_data = self.curriculum_extractor.extract(document["content"])

        curriculum_items = self.curriculum_adapter.convert(curriculum_data)

        fused = self.fusion.fuse(extracted)

        stored: ClassVar = []

        for item in fused["facts"] + fused["concepts"] + curriculum_items:
            validation = self.validator.validate(item)

            if not validation.accepted:
                continue

            consolidated = self.consolidator.consolidate(
                item,
                self.knowledge.all(),
            )

            stored.append(self.knowledge.add(consolidated))

        return {
            "success": True,
            "stored": stored,
            "metadata": fused["metadata"],
        }
