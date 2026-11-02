from ultron.improvement.result import (
    ImprovementResult,
)


class AdoptionManager:
    """
    Decides whether improvements
    should be accepted.
    """

    MIN_SCORE = 0.8

    def evaluate(
        self,
        experiment,
    ) -> ImprovementResult:

        accepted = experiment.success and experiment.score >= self.MIN_SCORE

        if accepted:
            reason = "Improvement showed positive results."

        else:
            reason = "Improvement did not meet acceptance criteria."

        return ImprovementResult(
            proposal_id=(
                experiment.details.get(
                    "proposal_id",
                    "unknown",
                )
            ),
            accepted=accepted,
            score=experiment.score,
            reason=reason,
            metadata={
                "source": "experiment",
            },
        )
