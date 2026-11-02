class RiskLevel:
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskAssessment:
    def calculate(
        self,
        event,
    ):

        score = 0

        if event.severity == "HIGH":
            score += 50

        if event.severity == "CRITICAL":
            score += 90

        if "unauthorized" in event.description.lower():
            score += 30

        if score >= 90:
            level = RiskLevel.CRITICAL

        elif score >= 60:
            level = RiskLevel.HIGH

        elif score >= 30:
            level = RiskLevel.MEDIUM

        else:
            level = RiskLevel.LOW

        return {
            "score": score,
            "level": level,
        }
