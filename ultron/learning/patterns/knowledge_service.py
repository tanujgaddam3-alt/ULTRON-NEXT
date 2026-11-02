from typing import ClassVar

from ultron.knowledge.extraction.pattern import PatternExtractor
from ultron.knowledge.service import KnowledgeService
from ultron.learning.patterns.models import BehaviorPattern


class PatternKnowledgeService:
    """
    Converts learned patterns into knowledge.
    """

    def __init__(
        self,
        knowledge_service: KnowledgeService,
    ):

        self.knowledge = knowledge_service

        self.extractor = PatternExtractor()

    def store_pattern(
        self,
        pattern: BehaviorPattern,
    ):

        facts = self.extractor.extract(pattern)

        results: ClassVar = []

        for fact in facts:
            results.append(self.knowledge.add(fact))

        return results
