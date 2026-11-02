from typing import ClassVar

from ultron.agents.base import BaseAgent
from ultron.agents.models import (
    AgentResult,
    AgentTask,
)


class ResearchAgent(BaseAgent):
    """
    Agent specialized in research tasks.
    """

    name = "research"

    capabilities: ClassVar[list[str]] = []

    def execute(
        self,
        task: AgentTask,
    ) -> AgentResult:

        return AgentResult(
            success=False,
            agent=self.name,
            output="Research is unavailable: no research source is configured.",
            metadata={
                "status": "blocked",
                "reason": "research_source_unavailable",
            },
        )
