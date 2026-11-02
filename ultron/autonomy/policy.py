from enum import Enum


class ActionPermission(str, Enum):
    """
    Autonomy action permission levels.
    """

    ALLOW = "allow"

    CONFIRM = "confirm"

    DENY = "deny"


class AutonomyPolicy:
    """
    Controls autonomous actions.
    """

    SAFE_ACTIONS = {
        "analyze",
        "plan",
        "organize",
        "summarize",
        "create_task",
    }

    CONFIRM_ACTIONS = {
        "install_software",
        "modify_configuration",
        "send_message",
        "execute_external_action",
    }

    DENIED_ACTIONS = {
        "delete_system_files",
        "disable_security",
        "unsafe_self_modify",
    }

    def check(
        self,
        action: str,
    ) -> ActionPermission:

        if not action:
            return ActionPermission.DENY

        if action in self.DENIED_ACTIONS:
            return ActionPermission.DENY

        if action in self.CONFIRM_ACTIONS:
            return ActionPermission.CONFIRM

        if action in self.SAFE_ACTIONS:
            return ActionPermission.ALLOW

        return ActionPermission.CONFIRM
