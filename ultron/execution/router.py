from ultron.skills.manager import SkillManager


class ActionRouter:
    """
    Resolves actions to execution capabilities.
    """

    def __init__(
        self,
        skills: SkillManager | None = None,
    ):

        self.skills = skills if skills is not None else SkillManager()

    def resolve(
        self,
        action: str,
    ):

        if not action:
            raise ValueError("Action cannot be empty.")

        skill = self.skills.get_skill(action)

        if skill is None:
            raise ValueError(f"No capability found for '{action}'")

        return skill

    def capabilities(
        self,
    ) -> list[dict]:

        return self.skills.available_skills()
