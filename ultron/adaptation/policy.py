class AdaptationPolicy:
    """
    Controls allowed behaviour changes.
    """

    MAX_CONFIDENCE_REQUIRED = 0.8

    def allow(
        self,
        confidence: float,
    ) -> bool:

        return confidence >= self.MAX_CONFIDENCE_REQUIRED
