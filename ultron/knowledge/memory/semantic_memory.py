from ultron.knowledge.memory.vector_store import (
    VectorStore,
)


class SemanticMemory:
    """
    Stores knowledge using semantic representations.
    """

    def __init__(
        self,
        vector_store=None,
    ):

        self.store = vector_store or VectorStore()

    def remember(
        self,
        identifier: str,
        embedding: list,
        metadata=None,
    ):

        self.store.add(
            identifier,
            embedding,
        )

    def retrieve(
        self,
        identifier: str,
    ):

        return self.store.get(identifier)
