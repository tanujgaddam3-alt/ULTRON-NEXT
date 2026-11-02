from ultron.evaluation.result import EvaluationResult
from ultron.learning.events import LearningEvent


class EvaluationLearningAdapter:
    """
    Converts evaluation results into learning events.
    """

    def create_event(
        self,
        result: EvaluationResult,
    ) -> LearningEvent:

        return LearningEvent(
            concept="decision_evaluation",
            description=result.feedback,
            metadata={
                "success": result.success,
                "score": result.score,
                "improvements": result.improvements,
                **result.metadata,
            },
        )
