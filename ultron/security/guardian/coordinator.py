class GuardianCoordinator:
    """
    Coordinates ULTRON security systems.
    """

    def __init__(
        self,
        identity=None,
        permission=None,
        threat=None,
        audit=None,
    ):

        self.identity = identity

        self.permission = permission

        self.threat = threat

        self.audit = audit

    def evaluate(
        self,
        action,
        identity_context,
    ):

        result = {
            "action": action,
            "checks": {},
        }

        if self.identity:
            result["checks"]["identity"] = self.identity

        if self.permission:
            result["checks"]["permission"] = self.permission

        if self.threat:
            result["checks"]["threat"] = self.threat

        return result
