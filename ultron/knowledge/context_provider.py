class KnowledgeContextProvider:
    """
    Provides retrieved and inferred knowledge
    to cognitive reasoning.
    """

    def __init__(
        self,
        knowledge_manager,
        inference_engine,
    ):

        self.knowledge = knowledge_manager
        self.inference = inference_engine

    def build(
        self,
        query: str,
    ):

        retrieved = self.knowledge.recall(query)

        return {
            "query": query,
            "knowledge": retrieved,
            "inferences": [],
        }
