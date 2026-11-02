from enum import Enum


class ResponseLevel(str, Enum):
    """
    Security response levels.
    """

    MONITOR = "monitor"

    PREPARE = "prepare_defense"

    PROTECT = "active_protection"

    GUARDIAN = "guardian_mode"


class SecurityResponseEngine:
    """
    Determines security response.
    """

    def decide(
        self,
        risk_level: str,
    ) -> ResponseLevel:

        if risk_level == "LOW":
            return ResponseLevel.MONITOR

        if risk_level == "MEDIUM":
            return ResponseLevel.PREPARE

        if risk_level == "HIGH":
            return ResponseLevel.PROTECT

        if risk_level == "CRITICAL":
            return ResponseLevel.GUARDIAN

        return ResponseLevel.MONITOR
