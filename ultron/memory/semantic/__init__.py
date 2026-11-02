from .manager import (
    SemanticMemoryManager,
)

from .models import (
    MemoryAssociation,
    SemanticMemoryItem,
)

from .repository import (
    SemanticMemoryRepository,
)


class SemanticMemory:
    """
    Persistent semantic memory interface.

    Compatibility layer between
    MemoryManager and new semantic architecture.
    """

    def __init__(self):

        self.repository = SemanticMemoryRepository()

        self.manager = SemanticMemoryManager(
            self.repository
        )


    def add(
        self,
        memory,
    ):

        if not isinstance(memory, dict):
            return memory


        for key, value in memory.items():

            self.manager.remember(
    concept=key,
    description=str(value),
    category="identity" if key == "name" else "general",
    source="conversation",
    confidence=1.0,
)

        return memory


    def recall(
        self,
        query: str,
    ):

        results = self.manager.recall(
            query
        )

        output = []

        for item in results:

            output.append(
                {
                    item.concept: item.description
                }
            )

        return output


    def search(
        self,
        query: str,
    ):

        return self.recall(query)
