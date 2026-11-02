from ultron.security.permission.engine import (
    PermissionEngine,
)


class SecurityPolicy:
    """
    Central security gate before execution.
    """

    def __init__(
        self,
        permission_engine=None,
    ):

        self.permission = permission_engine if permission_engine else PermissionEngine()

    def authorize(
        self,
        action: str,
        requester: str,
        trust_score: float = 100,
    ):

        decision = self.permission.evaluate(
            action,
            requester,
            trust_score,
        )

        if decision.permission.value == "allow":
            return {
                "allowed": True,
                "decision": decision,
            }

        if decision.permission.value == "confirm":
            return {
                "allowed": False,
                "requires_confirmation": True,
                "decision": decision,
            }

        return {
            "allowed": False,
            "blocked": True,
            "decision": decision,
        }
