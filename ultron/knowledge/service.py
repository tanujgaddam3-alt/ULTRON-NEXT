from ultron.knowledge.models import (
    KnowledgeItem,
)


class KnowledgeService:
    """
    Application service for knowledge operations.
    """

    def __init__(
        self,
        repository,
    ):

        self.repository = repository

    def add(
        self,
        item: KnowledgeItem,
    ) -> KnowledgeItem:
        """
        Store knowledge.
        """

        return self.repository.save(item)

    def get(
        self,
        identifier: str,
    ):

        return self.repository.get(identifier)

    def search(
        self,
        query: str,
    ):

        return self.repository.search(query)

    def all(
        self,
    ):

        return self.repository.list_all()

    def delete(
        self,
        identifier: str,
    ):

        return self.repository.delete(identifier)
