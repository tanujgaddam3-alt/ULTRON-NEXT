class SecurityGuardianCore:
    """
    Central security intelligence layer.

    Coordinates:
    - Identity protection
    - Permission control
    - Threat detection
    - Immune system
    - Response handling
    """

    def __init__(
        self,
        identity=None,
        permission=None,
        threat=None,
        immune=None,
        response=None,
        audit=None,
    ):

        self.identity = identity

        self.permission = permission

        self.threat = threat

        self.immune = immune

        self.response = response

        self.audit = audit

    def evaluate(
        self,
        action,
        context=None,
    ):

        security_context = {
            "action": action,
            "context": context or {},
            "identity": None,
            "permission": None,
            "threat": None,
        }

        if self.identity:
            security_context["identity"] = self.identity.verify(context)

        if self.permission:
            security_context["permission"] = self.permission.evaluate(
                action,
                context.get(
                    "user",
                    "unknown",
                ),
                context.get(
                    "trust_score",
                    0,
                ),
            )

        if self.threat:
            security_context["threat"] = self.threat.analyze(action)

        return security_context

    def protect(
        self,
        event,
        risk_level,
    ):

        if self.response:
            result = self.response.handle(
                event,
                risk_level,
            )

        else:
            result = {"status": "monitored"}

        if self.audit:
            self.audit.record(event)

        return result
