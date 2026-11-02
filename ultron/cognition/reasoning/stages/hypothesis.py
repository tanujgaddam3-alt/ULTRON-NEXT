from ultron.cognition.inference.engine import InferenceEngine
from ultron.cognition.reasoning.intelligence.hypothesis_enhancer import (
    HypothesisEnhancer,
)
from ultron.cognition.reasoning.state import ReasoningState

from .base import ReasoningStage


class HypothesisStage(ReasoningStage):
    """
    Generates and enhances reasoning hypotheses.
    """

    def __init__(
        self,
    ):

        self.engine = InferenceEngine()

        self.enhancer = HypothesisEnhancer()

    def run(
        self,
        state: ReasoningState,
    ) -> ReasoningState:

        hypotheses = self.engine.infer(state.context)

        hypotheses = self.enhancer.enhance(
            hypotheses,
            state.context,
        )

        state.hypotheses = hypotheses

        state.trace.append(f"{len(hypotheses)} enhanced hypotheses generated.")

        return state
