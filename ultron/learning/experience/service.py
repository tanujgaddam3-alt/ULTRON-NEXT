from typing import ClassVar

from ultron.domain.experience import Experience
from ultron.learning.analyzer import LearningAnalyzer
from ultron.learning.knowledge_extractor import KnowledgeExtractor


class ExperienceKnowledgeService:
    """
    Converts experiences into knowledge.

    Pipeline:

    Experience
        |
        v
    LearningInsight
        |
        v
    Knowledge
    """

    def __init__(
        self,
        knowledge_manager,
    ):

        self.knowledge = knowledge_manager

        self.analyzer = LearningAnalyzer()

        self.extractor = KnowledgeExtractor()

    def learn_from_experience(
        self,
        experience: Experience,
    ):

        insight = self.analyzer.analyze(experience)

        knowledge_items = self.extractor.extract(insight)

        results: ClassVar = []

        for item in knowledge_items:
            results.append(self.knowledge.learn(item.model_dump()))

        return results
