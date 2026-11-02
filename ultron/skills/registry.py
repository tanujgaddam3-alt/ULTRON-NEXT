from typing import ClassVar

from ultron.skills.base import Skill


class SkillRegistry:
    """
    Stores and resolves available skills.
    """

    def __init__(self):

        self._skills: dict[str, Skill] = {}

    def register(
        self,
        skill: Skill,
        replace: bool = False,
    ) -> None:
        """
        Register a skill.
        """

        if skill is None:
            raise ValueError("Cannot register empty skill.")

        for action in skill.actions:
            if action in self._skills and not replace:
                raise ValueError(f"Skill already registered for '{action}'.")

            self._skills[action] = skill

    def unregister(
        self,
        action: str,
    ) -> None:
        """
        Remove a skill action mapping.
        """

        self._skills.pop(
            action,
            None,
        )

    def get(
        self,
        action: str,
    ) -> Skill | None:

        if not action:
            return None

        return self._skills.get(action)

    def find_by_name(
        self,
        name: str,
    ) -> Skill | None:
        """
        Find a skill by its identity.
        """

        for skill in self.skills:
            if skill.name == name:
                return skill

        return None

    def capabilities(
        self,
    ) -> list[dict]:
        """
        Return available capabilities.
        """

        return [skill.info() for skill in self.skills]

    @property
    def skills(
        self,
    ) -> tuple[Skill, ...]:
        """
        Return unique registered skills.
        """

        unique: ClassVar = []

        seen = set()

        for skill in self._skills.values():
            identifier = id(skill)

            if identifier not in seen:
                seen.add(identifier)

                unique.append(skill)

        return tuple(unique)
