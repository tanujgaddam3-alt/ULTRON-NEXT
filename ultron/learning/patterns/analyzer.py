from collections import defaultdict
from typing import ClassVar

from ultron.learning.experience.store import (
    ExperienceStore,
)
from ultron.learning.patterns.models import (
    BehaviorPattern,
)


class PatternAnalyzer:
    """
    Discovers behavioral patterns
    from past experiences.
    """

    def __init__(
        self,
        experience_store: ExperienceStore,
    ):

        self.store = experience_store

    def analyze(self) -> list[BehaviorPattern]:
        """
        Generate patterns from experiences.
        """

        experiences = self.store.load()

        grouped = defaultdict(list)

        for experience in experiences:
            action = experience.get(
                "action",
                "unknown",
            )

            grouped[action].append(experience)

        patterns: ClassVar = []

        for action, records in grouped.items():
            successes = sum(1 for record in records if record.get("success", False))

            failures = len(records) - successes

            pattern = BehaviorPattern(
                action=action,
                occurrences=len(records),
                successes=successes,
                failures=failures,
                confidence=min(
                    len(records) / 10,
                    1.0,
                ),
            )

            patterns.append(pattern)

        return patterns
