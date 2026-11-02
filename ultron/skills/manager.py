from typing import Any

from ultron.application.runtime.operation_store import SQLiteOperationStore
from ultron.security.identity.authorization import (
    AuthorizationContext,
    AuthorizationRejectedError,
)
from ultron.security.identity.authenticator import WebSocketAuthenticator
from ultron.skills.registry import SkillRegistry
from ultron.security.permission.rules import (
    AUTHORIZATION_REQUIRED_ACTIONS,
    deny_when_authorization_unavailable,
    UNTRUSTED_SAFE_ACTIONS,
)
from ultron.skills.system.skill import SystemSkill
from ultron.skills.terminal.skill import TerminalSkill


class SkillManager:
    """
    Manages ULTRON skills.

    Responsibilities:
    - register skills
    - discover capabilities
    - validate actions
    - execute skills
    """

    def __init__(
        self,
        registry: SkillRegistry | None = None,
    ):

        self.registry = registry if registry is not None else SkillRegistry()
        self._authorization_authority: WebSocketAuthenticator | None = None
        self._operation_store: SQLiteOperationStore | None = None

        if registry is None:
            self.register(SystemSkill())
            self.register(TerminalSkill())

    def configure_trusted_authorization(
        self,
        authority: WebSocketAuthenticator,
        operation_store: SQLiteOperationStore,
    ) -> None:
        if type(authority) is not WebSocketAuthenticator:
            raise TypeError("Skill dispatch requires WebSocketAuthenticator.")
        if not isinstance(operation_store, SQLiteOperationStore):
            raise TypeError("Skill dispatch requires the SQLite operation store.")
        self._authorization_authority = authority
        self._operation_store = operation_store

    def register(
        self,
        skill,
    ) -> None:

        if skill is None:
            raise ValueError("Cannot register empty skill.")

        self.registry.register(skill)

    def get_skill(
        self,
        action: str,
    ):

        return self.registry.get(action)

    def available_skills(
        self,
    ) -> list[dict]:

        return [skill.info() for skill in self.registry.skills]

    def execute(
        self,
        action: str,
        *,
        authorization_context: AuthorizationContext | None = None,
        **kwargs,
    ):

        if not action:
            raise ValueError("Action cannot be empty.")

        skill = self.get_skill(action)

        if skill is None:
            raise ValueError(f"No skill registered for '{action}'")

        if not skill.validate(
            action,
            **kwargs,
        ):
            raise ValueError(f"Skill validation failed for '{action}'")

        if authorization_context is not None:
            if (
                type(authorization_context) is not AuthorizationContext
                or authorization_context.purpose != "execute"
                or action != "run_command"
                or self._authorization_authority is None
                or self._operation_store is None
                or not self._authorization_authority.validate_and_claim_dispatch(
                    authorization_context,
                    self._operation_store,
                    action=action,
                    parameters=kwargs,
                    operation_id=authorization_context.operation_id,
                    correlation_id=authorization_context.operation_correlation_id,
                )
            ):
                raise AuthorizationRejectedError(
                    "Trusted authorization grant is unclaimed, expired, or replayed."
                )
        elif action in AUTHORIZATION_REQUIRED_ACTIONS:
            deny_when_authorization_unavailable(action)
        elif action not in UNTRUSTED_SAFE_ACTIONS:
            raise PermissionError(
                "Skill action is disabled until it has an explicit safe "
                "classification or trusted authorization boundary."
            )

        return skill.execute(
            action,
            **kwargs,
        )

    def health_check(self):

        return {"registered_skills": len(self.registry.skills)}
