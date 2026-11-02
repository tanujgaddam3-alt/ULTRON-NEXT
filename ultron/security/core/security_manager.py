from ultron.security.core.guardian_core import (
    SecurityGuardianCore,
)


class SecurityManager:
    """
    High-level security interface.

    All ULTRON components should
    communicate through this layer.
    """

    def __init__(
        self,
        guardian=None,
    ):

        self.guardian = guardian or SecurityGuardianCore()

    def authorize(
        self,
        action,
        context=None,
    ):

        return self.guardian.evaluate(
            action,
            context,
        )

    def respond(
        self,
        event,
        risk_level,
    ):

        return self.guardian.protect(
            event,
            risk_level,
        )

    def health_check(self):

        return {"security_layer": True}
