from ultron.cognition.reasoning.intelligence.evaluation_intelligence import (
    EvaluationIntelligence,
)
from ultron.cognition.reasoning.state import ReasoningState

from .base import ReasoningStage


class EvaluationStage(ReasoningStage):
    """
    Evaluates generated hypotheses using
    advanced evaluation intelligence.
    """

    def __init__(
        self,
    ):

        self.evaluator = EvaluationIntelligence()

    def run(
        self,
        state: ReasoningState,
    ) -> ReasoningState:

        state.hypotheses = self.evaluator.evaluate_all(state.hypotheses)

        state.trace.append("Hypotheses evaluated with intelligence layer.")

        return state
