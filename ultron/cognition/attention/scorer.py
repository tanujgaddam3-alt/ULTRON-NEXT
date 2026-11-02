from __future__ import annotations

from ultron.cognition.attention.models import AttentionItem


class AttentionScorer:
    """
    Scores cognitive items.
    """

    def score(
        self,
        item: AttentionItem,
    ) -> float:
        """
        Placeholder implementation.

        Future versions will combine
        novelty,
        importance,
        urgency,
        uncertainty,
        and relevance.
        """

        return item.score
