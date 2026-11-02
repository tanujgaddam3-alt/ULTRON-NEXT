from typing import Any

from ultron.interfaces.memory import MemoryStore
from ultron.memory.storage.repository import MemoryRepository


class SemanticMemory(MemoryStore):
    """
    Stores long-term factual knowledge.
    """

    def __init__(self):

     self.repository = MemoryRepository()

    def add(
    self,
    item: dict[str, Any],
) -> None:

     for key, value in item.items():

        self.repository.save(
            "semantic",
            key,
            str(value),
        )

    def get(
        self,
    ) -> dict[str, Any]:
        return self._facts.copy()

    def recall(
    self,
    query: str,
):

     return self.repository.search(
        query
    )

    def add_knowledge(
        self,
        key: str,
        value: Any,
        confidence: float = 1.0,
        source: str = "learning",
    ) -> None:

        self._facts[key] = {
            "value": value,
            "confidence": confidence,
            "source": source,
        }

    def clear(
        self,
    ) -> None:
        self._facts.clear()
