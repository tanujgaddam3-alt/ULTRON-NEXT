from typing import ClassVar

from ultron.evaluation.models import EvaluationRecord
from ultron.evaluation.result import EvaluationResult


class EvaluationAnalyzer:
    """
    Analyzes decision outcomes and produces
    improvement feedback.
    """

    def analyze(
        self,
        record: EvaluationRecord,
    ) -> EvaluationResult:

        improvements: ClassVar = []

        if record.success:
            feedback = "Decision produced expected outcome."

        else:
            feedback = "Decision failed to produce expected outcome."

            improvements.append("Review strategy and alternative actions.")

        return EvaluationResult(
            success=record.success,
            score=record.score,
            feedback=feedback,
            improvements=improvements,
            metadata={
                "decision_id": record.decision_id,
            },
        )
