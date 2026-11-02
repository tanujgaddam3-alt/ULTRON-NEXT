from ultron.cognition.reasoning.calibration.confidence_calibrator import (
    ConfidenceCalibrator,
)
from ultron.cognition.reasoning.confidence import (
    ConfidenceCalculator,
)
from ultron.cognition.reasoning.hypothesis import (
    Hypothesis,
)


class ReasoningEvaluator:
    """
    Evaluates hypotheses with confidence calibration.
    """

    def __init__(
        self,
    ):

        self.confidence = ConfidenceCalculator()

        self.calibrator = ConfidenceCalibrator()

    def evaluate(
        self,
        hypotheses: list[Hypothesis],
    ) -> list[Hypothesis]:

        if not hypotheses:
            return []

        for hypothesis in hypotheses:
            hypothesis.confidence = self.confidence.calculate([hypothesis.confidence])

            hypothesis.confidence = self.calibrator.adjust(hypothesis.confidence)

        return hypotheses
