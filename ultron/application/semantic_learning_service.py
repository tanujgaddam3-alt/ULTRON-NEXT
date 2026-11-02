class SemanticLearningService:
    """
    Converts experiences into semantic memories.
    """

    def __init__(
        self,
        semantic_memory,
        knowledge_manager,
    ):

        self.semantic_memory = semantic_memory
        self.knowledge = knowledge_manager

    def learn(
        self,
        experience: dict,
    ):

        concept = experience.get("concept")

        description = experience.get(
            "description",
            "",
        )

        if not concept:
            return {
                "success": False,
                "reason": "No concept found",
            }

        memory = self.semantic_memory.remember(
            concept=concept,
            description=description,
        )

        return {
            "success": True,
            "memory": memory,
        }
