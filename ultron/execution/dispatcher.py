from ultron.security.identity.authorization import AuthorizationContext
from ultron.skills.manager import SkillManager


class Dispatcher:
    """
    Dispatches validated actions to skills.
    """

    def __init__(
        self,
        skills: SkillManager | None = None,
    ):
        self.skills = skills if skills is not None else SkillManager()

    def dispatch(
        self,
        action: str,
        *,
        authorization_context: AuthorizationContext | None = None,
        **kwargs,
    ):
        """
        Send an action to the appropriate skill.
        """

        if not action:
            raise ValueError("Action cannot be empty.")

        if not isinstance(
            action,
            str,
        ):
            raise TypeError("Action must be a string.")

        return self.skills.execute(
            action,
            authorization_context=authorization_context,
            **kwargs,
        )
