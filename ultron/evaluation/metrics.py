from dataclasses import dataclass


@dataclass
class EvaluationMetrics:
    """
    Tracks evaluation statistics.
    """

    total_decisions: int = 0

    successful_decisions: int = 0

    failed_decisions: int = 0

    @property
    def success_rate(self) -> float:

        if self.total_decisions == 0:
            return 0.0

        return self.successful_decisions / self.total_decisions

    def record(
        self,
        success: bool,
    ):

        self.total_decisions += 1

        if success:
            self.successful_decisions += 1

        else:
            self.failed_decisions += 1
