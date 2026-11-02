from ultron.improvement.evaluation.models import (
    ImprovementEvaluation,
)


class ImprovementEvaluationEngine:
    """
    Measures whether an improvement
    actually increased capability.
    """


    def evaluate(
        self,
        capability,
        before_score,
        after_score,
    ):

        gain = (
            after_score
            -
            before_score
        )


        success = gain > 0


        return ImprovementEvaluation(

            capability=capability,

            before_score=before_score,

            after_score=after_score,

            improvement_gain=gain,

            success=success,

            reason=(
                "Capability improved"
                if success
                else
                "No measurable improvement"
            ),
        )
