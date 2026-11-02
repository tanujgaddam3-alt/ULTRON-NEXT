from collections.abc import Sequence


class ConfidenceCalculator:
    """
    Computes an overall confidence score from multiple confidence values.
    """

    def calculate(
        self,
        values: Sequence[float],
    ) -> float:

        if not values:
            return 0.0

        values = [max(0.0, min(1.0, value)) for value in values]

        return sum(values) / len(values)
