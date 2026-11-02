from ultron.security.permission.rules import (
    AUTHORIZATION_REQUIRED_ACTIONS,
    UNTRUSTED_SAFE_ACTIONS,
)
from ultron.skills.security.policy import (
    CommandPermission,
    CommandPolicy,
)


class ExecutionPermission:
    """
    Global execution safety gate.
    """

    def __init__(self):

        self.command_policy = CommandPolicy()

    def check(
        self,
        action: str,
        **kwargs,
    ) -> bool:
        """
        Decide whether execution can proceed.
        """

        if action in AUTHORIZATION_REQUIRED_ACTIONS:
            return False

        if action in {"execute_shell", "run_command"}:
            command = kwargs.get(
                "command",
                "",
            )

            if kwargs.get("cwd") is not None:
                return False

            permission = self.command_policy.permission(command)

            if permission == (CommandPermission.DENY):
                return False

            if permission == (CommandPermission.CONFIRM):
                return False

        return action in UNTRUSTED_SAFE_ACTIONS
