from ultron.adaptation.model import (
    BehaviourAdjustment,
)
from ultron.adaptation.policy import (
    AdaptationPolicy,
)


class AdaptationEngine:
    """
    Converts learning signals into behaviour changes.
    """

    def __init__(self):

        self.policy = AdaptationPolicy()

        self.adjustments = []

    def adapt(
        self,
        lesson: str,
        confidence: float,
    ):

        if not self.policy.allow(confidence):
            return None

        adjustment = BehaviourAdjustment(
            behaviour="strategy_update",
            reason=lesson,
            confidence=confidence,
        )

        self.adjustments.append(adjustment)

        return adjustment
