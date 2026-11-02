from ultron.security.permission.models import (
    PermissionDecision,
)
from ultron.security.permission.rules import (
    PermissionRules,
)


class PermissionEngine:
    """
    Final authorization decision system.
    """

    def __init__(
        self,
        rules=None,
    ):

        self.rules = rules if rules is not None else PermissionRules()

    def evaluate(
        self,
        action: str,
        requester: str,
        trust_score: float = 100,
    ) -> PermissionDecision:

        permission = self.rules.check(action)

        if trust_score < 30:
            permission = "deny"

            reason = "Low trust score."

        elif trust_score < 70:
            reason = "Additional verification required."

        else:
            reason = "Trusted request."

        return PermissionDecision(
            action=action,
            permission=permission,
            reason=reason,
        )
