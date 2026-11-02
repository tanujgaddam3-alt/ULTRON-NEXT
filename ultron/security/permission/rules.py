from ultron.security.permission.models import (
    PermissionLevel,
)

AUTHORIZATION_REQUIRED_ACTIONS = frozenset(
    {
        "build_project",
        "close_application",
        "create_file",
        "create_project",
        "delete_artifact",
        "delete_file",
        "delete_project",
        "get_project",
        "improve_self",
        "list_artifacts",
        "list_directory",
        "list_projects",
        "open_application",
        "paper_trade",
        "read_artifact",
        "read_file",
        "read_project",
        "register_artifact",
        "write_file",
    }
)

UNTRUSTED_SAFE_ACTIONS = frozenset(
    {
        "analyze",
        "backtest",
        "company",
        "get_disk_usage",
        "get_system_info",
        "history",
        "price",
        "run_command",
        "search",
        "self_analyze",
        "summarize",
    }
)


def deny_when_authorization_unavailable(action: str) -> None:
    if action in AUTHORIZATION_REQUIRED_ACTIONS:
        raise PermissionError(
            "Sensitive or consequential action dispatch is disabled "
            "until a trusted authorization boundary is configured."
        )


def require_simulation_broker(broker: object, operation: str) -> None:
    if (
        getattr(broker, "mode", None) != "simulation"
        or bool(getattr(broker, "supports_live_orders", False))
    ):
        raise PermissionError(
            f"{operation} is disabled unless the broker is local simulation."
        )


def require_simulation_execution_backend(backend: str | None) -> None:
    if backend != "simulation":
        raise RuntimeError(
            "Provider-backed financial execution is unavailable until "
            "the shared authorization contract is integrated. "
            "ULTRON_EXECUTION_BACKEND must be explicitly set to simulation."
        )


class PermissionRules:
    """
    Defines allowed, confirmation,
    and denied actions.
    """

    SAFE_ACTIONS = {
        "analyze",
        "search",
        "summarize",
        "create_task",
        "organize",
    }

    CONFIRM_ACTIONS = {
        "install_software",
        "modify_configuration",
        "send_message",
        "execute_external_action",
        "access_sensitive_data",
    }

    DENIED_ACTIONS = {
        "disable_security",
        "delete_audit_logs",
        "modify_guardian_core",
        "remove_identity_protection",
    }

    def check(
        self,
        action: str,
    ) -> PermissionLevel:

        if action in self.DENIED_ACTIONS:
            return PermissionLevel.DENY

        if action in self.CONFIRM_ACTIONS:
            return PermissionLevel.CONFIRM

        if action in self.SAFE_ACTIONS:
            return PermissionLevel.ALLOW

        return PermissionLevel.CONFIRM
