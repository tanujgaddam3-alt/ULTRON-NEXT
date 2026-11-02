from ultron.domain.goal import Goal
from ultron.domain.understanding import Understanding


class GoalTranslator:
    """
    Converts Understanding into Goals.
    """

    def translate(
        self,
        understanding: Understanding,
    ) -> Goal:

        return Goal(
            name=understanding.summary,
            description=understanding.summary,
        )
