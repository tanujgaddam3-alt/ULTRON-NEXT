from ultron.knowledge.models import Fact
from ultron.learning.patterns.models import (
    BehaviorPattern,
)


class PatternExtractor:
    """
    Converts learned behavior patterns
    into knowledge facts.
    """

    def extract(
        self,
        pattern: BehaviorPattern,
    ) -> list[Fact]:

        if not pattern:
            return []

        return [
            Fact(
                subject=pattern.action,
                predicate="has_success_rate",
                object=str(pattern.success_rate()),
                content=(
                    f"{pattern.action} has success rate {pattern.success_rate():.2f}"
                ),
                confidence=pattern.confidence,
            )
        ]
