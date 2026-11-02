from ultron.learning.events import LearningEvent


class LearningEventHandler:
    """
    Converts learning events into
    semantic memories and knowledge.
    """

    def __init__(
        self,
        learning_service,
        experience_knowledge_service=None,
    ):

        self.learning = learning_service

        self.experience_knowledge = experience_knowledge_service

    def handle(
        self,
        event: LearningEvent,
    ):

        result = self.learning.learn(
            {
                "concept": event.concept,
                "description": event.description,
                "metadata": event.metadata or {},
            }
        )

        if self.experience_knowledge:
            self.experience_knowledge.learn_from_event(event)

        return result
