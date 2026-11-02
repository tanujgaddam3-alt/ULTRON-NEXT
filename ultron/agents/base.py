from abc import ABC, abstractmethod
from typing import ClassVar

from ultron.agents.models import (
    AgentResult,
    AgentTask,
)


class BaseAgent(ABC):
    """
    Base contract for ULTRON agents.
    """

    name = "base"

    capabilities: ClassVar[list[str]] = []

    def info(self):

        return {
            "name": self.name,
            "capabilities": self.capabilities,
        }

    def supports(
        self,
        task: AgentTask,
    ) -> bool:
        if not task.required_capabilities:
            return bool(self.capabilities)

        return all(
            capability in self.capabilities
            for capability in task.required_capabilities
        )

    @abstractmethod
    def execute(
        self,
        task: AgentTask,
    ) -> AgentResult:

        raise NotImplementedError
