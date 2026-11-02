from enum import Enum


class TrustLevel(str, Enum):
    """
    Trust classification.
    """

    TRUSTED = "trusted"

    VERIFY = "verify"

    BLOCK = "block"


class TrustEvaluator:
    """
    Calculates trust level.
    """

    def evaluate(
        self,
        user_verified: bool,
        device_trusted: bool,
        session_active: bool,
        risk_score: float,
    ) -> dict:

        score = 0

        if user_verified:
            score += 40

        if device_trusted:
            score += 30

        if session_active:
            score += 20

        if risk_score < 0.2:
            score += 10

        if score >= 80:
            level = TrustLevel.TRUSTED

        elif score >= 50:
            level = TrustLevel.VERIFY

        else:
            level = TrustLevel.BLOCK

        return {
            "score": score,
            "level": level,
        }
