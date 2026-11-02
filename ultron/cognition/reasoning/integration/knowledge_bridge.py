class KnowledgeReasoningBridge:
    """
    Connects ULTRON knowledge retrieval
    with reasoning pipeline.
    """

    def __init__(
        self,
        knowledge_service=None,
    ):

        self.knowledge = knowledge_service

    def enrich_context(
        self,
        context,
        query: str,
    ):

        if not self.knowledge:
            return context

        results = self.knowledge.search(query)

        context.metadata["knowledge_context"] = results

        return context
