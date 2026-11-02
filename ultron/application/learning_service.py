from ultron.learning.analyzer import LearningAnalyzer
from ultron.learning.experience_recorder import ExperienceRecorder
from ultron.learning.knowledge_extractor import KnowledgeExtractor


class LearningService:
    """
    Coordinates ULTRON's learning pipeline.

    Responsibilities:
    - create experiences
    - store episodic memories
    - analyze experiences
    - extract knowledge

    Does not:
    - make decisions
    - execute actions
    - modify schemas directly
    """

    def __init__(
        self,
        episodic_memory,
        semantic_memory,
    ):
        self.episodic_memory = episodic_memory
        self.semantic_memory = semantic_memory

        self.recorder = ExperienceRecorder()
        self.analyzer = LearningAnalyzer()
        self.knowledge_extractor = KnowledgeExtractor()

        self._insights = []

    def learn(self, execution, reflection):
        """
        Learn from a completed cognitive cycle.
        """

        experience = self.recorder.record(
            execution,
            reflection,
        )

        self.episodic_memory.add(experience)

        insight = self.analyzer.analyze(experience)

        self._insights.append(insight)

        knowledge = self.knowledge_extractor.extract(insight)

        for item in knowledge:
            self.semantic_memory.add({item.key: item.value})

        return experience

    def get_insights(self):
        """
        Return extracted learning insights.
        """

        return self._insights.copy()

    def get_knowledge(self):
        """
        Return extracted knowledge.
        """

        return self.semantic_memory.get()

    def health_check(self):

        return {
            "episodic_memory": self.episodic_memory is not None,
            "semantic_memory": self.semantic_memory is not None,
        }
