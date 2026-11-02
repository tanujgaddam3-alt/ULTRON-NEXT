from ultron.security.permission.models import (
    PermissionLevel,
)


class ImprovementValidator:
    """
    Validates self-improvement requests.
    """

    HIGH_RISK_COMPONENTS = {
        "security",
        "identity",
        "permission",
        "guardian",
        "core",
    }

    def validate(
        self,
        request,
    ):

        component = request.component.lower()

        if component in self.HIGH_RISK_COMPONENTS:
            request.risk_level = "HIGH"

            return {
                "decision": PermissionLevel.CONFIRM,
                "reason": ("Critical component modification requires approval."),
            }

        request.risk_level = "LOW"

        return {
            "decision": PermissionLevel.ALLOW,
            "reason": ("Low-risk improvement."),
        }
