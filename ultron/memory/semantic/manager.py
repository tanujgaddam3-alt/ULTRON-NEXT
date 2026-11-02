from ultron.memory.semantic.models import (
    MemoryAssociation,
    SemanticMemoryItem,
)
from datetime import datetime


class SemanticMemoryManager:
    """
    High-level semantic memory interface.
    """

    def __init__(
        self,
        repository,
    ):

        self.repository = repository

    def remember(
    self,
    concept: str,
    description: str = "",
    category: str = "general",
    source: str = "conversation",
    confidence: float = 1.0,
) -> SemanticMemoryItem:
        """
        Create semantic memory.
        """

        existing = self.repository.get(concept)

        if existing:
            return existing

        memory = SemanticMemoryItem(
    concept=concept,
    description=description,
    metadata={
        "category": category,
        "source": source,
        "confidence": confidence,
        "created_at": datetime.now().isoformat(),
        "last_used": datetime.now().isoformat(),
    },
)

        return self.repository.save(memory)

    def associate(
        self,
        concept: str,
        memory_id: str,
        knowledge_id: str,
        relation: str,
        confidence: float = 1.0,
    ):

        semantic = self.repository.get(concept)

        if semantic is None:
            raise ValueError("Semantic memory not found.")

        semantic.associations.append(
            MemoryAssociation(
                memory_id=memory_id,
                knowledge_id=knowledge_id,
                relation=relation,
                confidence=confidence,
            )
        )

        return self.repository.save(semantic)

    def recall(
        self,
        query: str,
    ):

        return self.repository.search(query)
