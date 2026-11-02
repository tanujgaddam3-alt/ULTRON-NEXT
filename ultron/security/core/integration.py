class SecurityIntegration:
    """
    Connects ULTRON systems
    with security protection.
    """

    def __init__(
        self,
        security_manager,
    ):

        self.security = security_manager

    def check_action(
        self,
        action,
        context=None,
    ):

        return self.security.authorize(
            action,
            context,
        )

    def report_event(
        self,
        event,
        risk_level,
    ):

        return self.security.respond(
            event,
            risk_level,
        )
