from ultron.cognition.reasoning.state import ReasoningState

from .base import ReasoningStage


class RankingStage(ReasoningStage):
    """
    Ranks hypotheses by priority.
    """

    def run(
        self,
        state: ReasoningState,
    ) -> ReasoningState:

        state.hypotheses.sort(
            key=lambda h: h.priority,
            reverse=True,
        )

        state.trace.append("Hypotheses ranked.")

        return state
