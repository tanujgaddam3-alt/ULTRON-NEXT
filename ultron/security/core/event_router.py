class SecurityEventRouter:
    """
    Routes security events
    between ULTRON security modules.
    """

    def __init__(
        self,
        audit=None,
        threat=None,
        guardian=None,
    ):

        self.audit = audit

        self.threat = threat

        self.guardian = guardian

    def route(
        self,
        event,
    ):

        result = {
            "event": event,
            "processed": [],
        }

        if self.audit:
            self.audit.record(event)

            result["processed"].append("audit")

        if self.threat:
            analysis = self.threat.analyze(event)

            result["threat"] = analysis

            result["processed"].append("threat")

        if self.guardian:
            result["guardian"] = self.guardian.handle_threat(
                event,
                "HIGH",
            )

            result["processed"].append("guardian")

        return result
