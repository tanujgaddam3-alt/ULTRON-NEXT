class ImprovementGuardianAdapter:
    """
    Adapter between ULTRON security guardian
    and improvement approval system.
    """

    def __init__(
        self,
        guardian,
    ):
        self.guardian = guardian

    def approve(
        self,
        request,
    ):

        result = self.guardian.evaluate(
            action="improvement",
            context={
                "component": request.component,
                "description": request.description,
                "risk_level": request.risk_level,
            },
        )

        # Default policy:
        # deny only explicit threats

        if result.get("threat"):
            return False

        return True
