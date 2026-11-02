from collections import defaultdict

from ultron.learning.experience.store import (
    ExperienceStore,
)


class ExperienceAnalyzer:
    """
    Extracts patterns from experiences.
    """

    def __init__(
        self,
        store: ExperienceStore,
    ):

        self.store = store

    def success_rate(
        self,
        action: str,
    ) -> float:

        experiences = self.store.load()

        matching = [exp for exp in experiences if exp["action"] == action]

        if not matching:
            return 0.0

        successful = sum(1 for exp in matching if exp["success"])

        return successful / len(matching)

    def action_statistics(self):

        stats = defaultdict(
            lambda: {
                "total": 0,
                "success": 0,
            }
        )

        for exp in self.store.load():
            action = exp["action"]

            stats[action]["total"] += 1

            if exp["success"]:
                stats[action]["success"] += 1

        return dict(stats)
