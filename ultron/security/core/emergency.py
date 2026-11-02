from datetime import UTC, datetime


class EmergencySecurityController:
    """
    Handles emergency security states.
    """

    def __init__(
        self,
        guardian=None,
        audit=None,
    ):

        self.guardian = guardian

        self.audit = audit

        self.lockdown_active = False

    def activate(
        self,
        reason: str,
    ):

        self.lockdown_active = True

        event = {
            "type": "emergency_lockdown",
            "reason": reason,
            "time": datetime.now(UTC),
        }

        if self.audit:
            self.audit.record(event)

        return {
            "status": "lockdown_active",
            "event": event,
        }

    def release(
        self,
    ):

        self.lockdown_active = False

        return {
            "status": "normal_operation",
        }
