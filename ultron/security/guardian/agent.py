from ultron.security.guardian.state import (
    SecurityState,
)


class SecurityGuardianAgent:
    """
    Central security intelligence agent.
    """

    def __init__(
        self,
        threat_detector=None,
        response_manager=None,
    ):

        self.state = SecurityState.NORMAL

        self.threat_detector = threat_detector

        self.response_manager = response_manager

        self.incidents = []

    def observe(
        self,
        activity,
    ):

        self.state = SecurityState.MONITORING

        return {
            "state": self.state,
            "activity": activity,
        }

    def handle_threat(
        self,
        event,
        risk_level,
    ):

        self.incidents.append(event)

        if risk_level == "CRITICAL":
            self.state = SecurityState.LOCKDOWN

        elif risk_level == "HIGH":
            self.state = SecurityState.DEFENDING

        else:
            self.state = SecurityState.ALERT

        if self.response_manager:
            return self.response_manager.handle(
                event,
                risk_level,
            )

        return {
            "state": self.state,
            "event": event,
        }
