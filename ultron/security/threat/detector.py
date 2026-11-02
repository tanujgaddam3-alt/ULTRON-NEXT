from typing import ClassVar


class AttackPatternDetector:
    """
    Detects known suspicious behavior patterns.
    """

    PATTERNS = {
        "brute_force": [
            "failed_login",
            "multiple_auth_attempts",
        ],
        "privilege_escalation": [
            "permission_change",
            "admin_request",
        ],
        "unauthorized_access": [
            "unknown_device",
            "blocked_action",
        ],
        "integrity_attack": [
            "modify_core",
            "modify_security",
        ],
    }

    def detect(
        self,
        activity,
    ):

        matches: ClassVar = []

        for name, indicators in self.PATTERNS.items():
            if activity.action in indicators:
                matches.append(name)

        return {
            "detected": bool(matches),
            "patterns": matches,
        }
