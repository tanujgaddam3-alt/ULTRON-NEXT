class SecurityGuardianSystem:
    """
    Unified ULTRON security controller.
    """

    def __init__(
        self,
        guardian,
        coordinator=None,
        learning=None,
    ):

        self.guardian = guardian

        self.coordinator = coordinator

        self.learning = learning

    def process(
        self,
        event,
        risk_level,
    ):

        response = self.guardian.handle_threat(
            event,
            risk_level,
        )

        if self.learning:
            self.learning.learn(event)

        return {
            "event": event,
            "response": response,
        }
