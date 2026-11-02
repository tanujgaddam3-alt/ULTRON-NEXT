class ThreatResponsePipeline:
    """
    Handles complete threat response workflow.
    """

    def __init__(
        self,
        incident_manager=None,
        guardian=None,
        recovery=None,
    ):

        self.incidents = incident_manager

        self.guardian = guardian

        self.recovery = recovery

    def respond(
        self,
        threat,
        risk_level,
    ):

        result = {
            "threat": threat,
            "risk": risk_level,
        }

        if self.incidents:
            incident = self.incidents.create(
                title=str(threat),
                severity=risk_level,
            )

            result["incident"] = incident

        if self.guardian:
            result["guardian"] = self.guardian.handle_threat(
                threat,
                risk_level,
            )

        return result
