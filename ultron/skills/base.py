from abc import ABC, abstractmethod


class Skill(ABC):
    """
    Base class for every ULTRON skill.
    """

    name: str = "base"

    description: str = ""

    version: str = "1.0"

    actions: list[str] = []

    def supports(
        self,
        action: str,
    ) -> bool:
        """
        Check whether this skill supports an action.
        """

        return action in self.actions

    def info(self) -> dict:
        """
        Return skill metadata.
        """

        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "actions": self.actions,
        }

    def validate(
        self,
        action: str,
        **kwargs,
    ) -> bool:
        """
        Validate before execution.
        """

        return self.supports(action)

    @abstractmethod
    def execute(
        self,
        action: str,
        **kwargs,
    ):
        """
        Execute an action.
        """

        raise NotImplementedError
