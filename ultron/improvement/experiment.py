from dataclasses import field
from datetime import UTC, datetime

from ultron.domain.base import DomainModel


class ExperimentResult(DomainModel):
    """
    Result of an improvement experiment.
    """

    success: bool

    score: float

    details: dict = field(default_factory=dict)

    created_at: datetime = datetime.now(UTC)


class ExperimentEngine:
    """
    Executes controlled improvement experiments.
    """

    def run(
        self,
        improvement: dict,
    ) -> ExperimentResult:
        """
        Initial implementation.

        Future:
        - sandbox execution
        - A/B testing
        - simulations
        """

        return ExperimentResult(
            success=False,
            score=0.0,
            details={
                "status": "simulation_only",
                "improvement": improvement,
            },
        )
