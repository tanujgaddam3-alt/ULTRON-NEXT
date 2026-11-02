class SecurityAPI:
    """
    Public interface for ULTRON security services.
    """

    def __init__(
        self,
        security_manager,
    ):

        self.manager = security_manager

    def authorize(
        self,
        action,
        context=None,
    ):

        return self.manager.authorize(
            action,
            context,
        )

    def report_threat(
        self,
        event,
        risk_level,
    ):

        return self.manager.respond(
            event,
            risk_level,
        )

    def security_status(
        self,
        diagnostics,
    ):

        return diagnostics.run()
