class ReflectionLearningAdapter:
    """
    Converts reflection output into learning events.
    """

    def __init__(
        self,
        learning_service,
    ):

        self.learning = learning_service

    def process(
        self,
        reflection,
    ):

        if not reflection:
            return {"success": False}

        experience = {
            "concept": reflection.get("topic"),
            "description": reflection.get(
                "summary",
                "",
            ),
        }

        return self.learning.learn(experience)
