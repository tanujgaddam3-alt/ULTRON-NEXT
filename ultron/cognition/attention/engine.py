from __future__ import annotations

from ultron.cognition.attention.models import AttentionItem
from ultron.cognition.attention.scorer import AttentionScorer


class AttentionEngine:
    """
    Selects what ULTRON
    should focus on.
    """

    def __init__(
        self,
        scorer: AttentionScorer | None = None,
    ):
        self.scorer = scorer or AttentionScorer()

    def prioritize(
        self,
        items: list[AttentionItem],
    ) -> list[AttentionItem]:

        ranked = sorted(
            items,
            key=self.scorer.score,
            reverse=True,
        )

        return ranked
