from __future__ import annotations

from ultron.domain.enums import Priority


class GoalPriorityEngine:
    """
    Calculates autonomous goal importance.

    Priority is derived from the canonical domain Priority enum
    and augmented by urgency/security signals.
    """

    PRIORITY_SCORES = {
        Priority.LOW: 1.0,
        Priority.MEDIUM: 2.0,
        Priority.HIGH: 3.0,
        Priority.CRITICAL: 4.0,
    }

    def calculate(self, goal) -> float:
        priority = getattr(
            goal,
            "priority",
            Priority.MEDIUM,
        )

        score = self.PRIORITY_SCORES.get(
            priority,
            2.0,
        )

        description = getattr(
            goal,
            "description",
            "",
        ).lower()

        if "urgent" in description:
            score += 5.0

        if "security" in description:
            score += 4.0

        return score

    def rank(self, goals: list) -> list:
        return sorted(
            goals,
            key=self.calculate,
            reverse=True,
        )
