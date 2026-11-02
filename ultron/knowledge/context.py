from ultron.knowledge.retrieval import (
    KnowledgeRetriever,
)


class KnowledgeContextProvider:
    """
    Provides knowledge context
    for cognitive processing.
    """

    def __init__(
        self,
        retriever: KnowledgeRetriever,
    ):

        self.retriever = retriever

    def build_context(
        self,
        query: str,
    ) -> dict:

        knowledge = self.retriever.retrieve(query)

        return {
            "query": query,
            "knowledge": knowledge,
        }
