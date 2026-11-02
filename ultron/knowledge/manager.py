from typing import Any


class KnowledgeManager:
    """
    High-level interface for ULTRON knowledge.

    Coordinates:
    - ingestion
    - storage
    - retrieval
    - graph access
    """

    def __init__(
        self,
        ingestion_service,
        retrieval_engine,
        graph,
        repository,
    ):

        self.ingestion = ingestion_service

        self.retrieval = retrieval_engine

        self.graph = graph

        self.repository = repository

    def learn(
        self,
        document: dict[str, Any],
    ) -> dict:
        """
        Process new information.
        """

        return self.ingestion.ingest(document)

    def recall(
        self,
        query: str,
    ) -> list:
        """
        Retrieve related knowledge.
        """

        return self.retrieval.retrieve(query)

    def get_related(
        self,
        entity: str,
    ) -> list:
        """
        Explore connected knowledge.
        """

        return self.graph.neighbors(entity)

    def health_check(self):

        count = len(self.repository.list_all())

        return {"repository": True, "items": count}
