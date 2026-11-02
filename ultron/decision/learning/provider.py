from ultron.learning.experience.analyzer import (
    ExperienceAnalyzer,
)


class DecisionLearningProvider:
    """
    Provides learned evidence
    for decision scoring.
    """

    def __init__(
        self,
        experience_analyzer: ExperienceAnalyzer,
    ):

        self.experience = experience_analyzer

    def get_action_score(
        self,
        action: str,
    ) -> float:
        """
        Returns historical success score.
        """

        try:
            return self.experience.success_rate(action)

        except Exception:
            return 0.0
