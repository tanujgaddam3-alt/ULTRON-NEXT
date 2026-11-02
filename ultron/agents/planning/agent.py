from typing import ClassVar

from ultron.agents.base import BaseAgent
from ultron.agents.models import (
    AgentResult,
    AgentTask,
)


class PlanningAgent(BaseAgent):
    """
    Agent specialized in strategic planning.
    """

    name = "planning"

    capabilities: ClassVar[list[str]] = []

    def execute(
        self,
        task: AgentTask,
    ) -> AgentResult:

        return AgentResult(
            success=False,
            agent=self.name,
            output="Planning is unavailable: no planning implementation is configured.",
            metadata={
                "status": "blocked",
                "reason": "planning_implementation_unavailable",
            },
        )
