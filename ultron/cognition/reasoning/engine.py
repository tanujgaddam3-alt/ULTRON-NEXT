from ultron.cognition.context.models import CognitiveContext
from ultron.cognition.reasoning.pipeline import ReasoningPipeline
from ultron.cognition.reasoning.result import ReasoningResult
from ultron.cognition.reasoning.stages import (
    EvaluationStage,
    HypothesisStage,
    IntentStage,
    RankingStage,
)
from ultron.cognition.reasoning.state import ReasoningState


class ReasoningEngine:
    """
    Executes ULTRON's reasoning pipeline.
    """

    def __init__(self):
        self.pipeline = ReasoningPipeline()

        self.pipeline.add_stage(IntentStage())
        self.pipeline.add_stage(HypothesisStage())
        self.pipeline.add_stage(EvaluationStage())
        self.pipeline.add_stage(RankingStage())

    def reason(
        self,
        context: CognitiveContext,
    ) -> ReasoningResult:

        state = ReasoningState(
            context=context,
        )

        state = self.pipeline.run(state)

        # The highest-ranked hypothesis represents the current
        # reasoning confidence.
        if state.hypotheses:
            state.confidence = max(
                0.0,
                min(
                    state.hypotheses[0].confidence,
                    1.0,
                ),
            )

        return ReasoningResult(
            hypotheses=state.hypotheses,
            confidence=state.confidence,
            trace=state.trace,
            metadata=state.metadata,
        )

    def health_check(self):
        return {
            "engine": True,
            "stages": len(self.pipeline.stages),
        }
