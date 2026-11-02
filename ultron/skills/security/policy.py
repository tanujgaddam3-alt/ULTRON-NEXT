from enum import Enum
import os
import shlex

from ultron.security.permission.rules import (
    AUTHORIZATION_REQUIRED_ACTIONS,
    deny_when_authorization_unavailable,
)


class CommandPermission(str, Enum):
    """
    Permission level for terminal commands.
    """

    ALLOW = "allow"

    CONFIRM = "confirm"

    DENY = "deny"


class CommandPolicy:
    """
    Determines whether terminal commands
    can execute automatically.
    """

    SAFE_COMMANDS = {"ls", "pwd", "whoami"}

    CONFIRM_COMMANDS = {
        "shutdown",
        "reboot",
        "poweroff",
        "systemctl",
        "apt",
        "apt-get",
    }

    DENY_COMMANDS = {
        "mkfs",
        "fdisk",
        "dd",
    }

    def permission(
        self,
        command: str,
    ) -> CommandPermission:

        if not isinstance(command, str) or not command:
            return CommandPermission.DENY

        try:
            arguments = shlex.split(command, posix=os.name != "nt")
        except (ValueError, IndexError):
            return CommandPermission.CONFIRM
        if not arguments:
            return CommandPermission.DENY

        executable = arguments[0]
        executable_name = executable.replace("\\", "/").rsplit("/", 1)[-1]

        if executable_name in self.DENY_COMMANDS:
            return CommandPermission.DENY

        if executable_name in self.CONFIRM_COMMANDS:
            return CommandPermission.CONFIRM

        if executable in self.SAFE_COMMANDS and len(arguments) == 1:
            return CommandPermission.ALLOW

        return CommandPermission.CONFIRM
