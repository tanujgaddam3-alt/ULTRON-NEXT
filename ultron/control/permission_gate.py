from ultron.control.command_models import (
    CommandLevel,
)


class PermissionDecision:
    ALLOW = "allow"

    DENY = "deny"

    CONFIRM = "confirm"


class PermissionGate:
    """
    Controls command execution authority.
    """

    def __init__(
        self,
    ):

        self.blocked_actions = [
            "disable_security",
            "delete_core",
        ]

    def check(
        self,
        command,
    ):

        if command.action in self.blocked_actions:
            return PermissionDecision.DENY

        if command.level == CommandLevel.CRITICAL:
            return PermissionDecision.CONFIRM

        return PermissionDecision.ALLOW
