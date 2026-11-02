from typing import ClassVar

from ultron.knowledge.graph import KnowledgeGraph


class KnowledgeRetriever:
    """
    Retrieves relevant knowledge.

    Uses:
    - direct matches
    - entity connections
    - graph expansion
    """

    def __init__(
        self,
        repository,
        graph: KnowledgeGraph,
    ):

        self.repository = repository
        self.graph = graph

    def retrieve(
        self,
        query: str,
        limit: int = 10,
    ) -> list:

        if not query:
            return []

        results: ClassVar = []

        # Direct knowledge search

        results.extend(self.repository.search(query))

        # Graph based expansion

        entity = self.graph.get_entity(query)

        if entity:
            connected = self.graph.neighbors(query)

            results.extend(connected)

        return results[:limit]
