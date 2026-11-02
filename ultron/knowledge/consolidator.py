from ultron.knowledge.models import KnowledgeItem


class KnowledgeConsolidator:
    """
    Maintains consistency of knowledge.

    Handles:
    - duplicate detection
    - merging
    - confidence updates
    """

    def similarity(
        self,
        first: KnowledgeItem,
        second: KnowledgeItem,
    ) -> float:
        """
        Basic similarity calculation.

        Future versions can use:
        - embeddings
        - semantic models
        """

        first_text = first.content.lower()

        second_text = second.content.lower()

        first_words = set(first_text.split())

        second_words = set(second_text.split())

        if not first_words or not second_words:
            return 0.0

        intersection = first_words & second_words

        union = first_words | second_words

        return len(intersection) / len(union)

    def merge(
        self,
        existing: KnowledgeItem,
        incoming: KnowledgeItem,
    ) -> KnowledgeItem:
        """
        Merge knowledge entries without mutating
        immutable domain objects.
        """

        new_confidence = max(
            existing.confidence,
            incoming.confidence,
        )

        new_metadata = {
            **existing.metadata,
            **incoming.metadata,
        }

        return existing.model_copy(
            update={
                "confidence": new_confidence,
                "metadata": new_metadata,
            }
        )

    def consolidate(
        self,
        incoming: KnowledgeItem,
        existing_items: list[KnowledgeItem],
    ) -> KnowledgeItem:

        for item in existing_items:
            score = self.similarity(
                item,
                incoming,
            )

            if score > 0.7:
                return self.merge(
                    item,
                    incoming,
                )

        return incoming
