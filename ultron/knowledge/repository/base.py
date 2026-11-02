from abc import ABC, abstractmethod

from ultron.knowledge.models import (
    KnowledgeItem,
)


class KnowledgeRepository(ABC):
    """
    Persistence interface for knowledge.

    Supports:
    - storing knowledge
    - retrieving knowledge
    - searching knowledge
    - deleting knowledge
    """

    @abstractmethod
    def save(
        self,
        item: KnowledgeItem,
    ) -> KnowledgeItem:
        raise NotImplementedError

    @abstractmethod
    def get(
        self,
        identifier: str,
    ) -> KnowledgeItem | None:
        raise NotImplementedError

    @abstractmethod
    def search(
        self,
        query: str,
    ) -> list[KnowledgeItem]:
        raise NotImplementedError

    @abstractmethod
    def list_all(
        self,
    ) -> list[KnowledgeItem]:
        raise NotImplementedError

    @abstractmethod
    def delete(
        self,
        identifier: str,
    ) -> bool:
        raise NotImplementedError
