from datetime import UTC, datetime


class ConfidenceCalibrator:
    """
    Adjusts confidence based on historical performance.
    """

    def __init__(
        self,
    ):

        self.records = []

    def record(
        self,
        predicted_confidence: float,
        success: bool,
    ):

        if not 0 <= predicted_confidence <= 1:
            raise ValueError("Confidence must be between 0 and 1")

        self.records.append(
            {
                "confidence": predicted_confidence,
                "success": success,
                "timestamp": datetime.now(UTC),
            }
        )

    def calibration_score(
        self,
    ):

        if not self.records:
            return 0.0

        correct = sum(
            1
            for record in self.records
            if (record["success"] and record["confidence"] >= 0.5)
        )

        return correct / len(self.records)

    def adjust(
        self,
        confidence: float,
    ):

        score = self.calibration_score()

        if score == 0:
            return confidence

        adjusted = (confidence * 0.7) + (score * 0.3)

        return round(min(adjusted, 1), 3)
