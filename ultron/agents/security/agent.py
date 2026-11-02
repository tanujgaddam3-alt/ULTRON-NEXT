from typing import ClassVar

from ultron.agents.base import BaseAgent
from ultron.agents.models import (
    AgentResult,
    AgentTask,
)


class SecurityAgent(BaseAgent):
    """
    Agent specialized in security analysis.
    """

    name = "security"

    capabilities: ClassVar[list[str]] = []

    def execute(
        self,
        task: AgentTask,
    ) -> AgentResult:

        return AgentResult(
            success=False,
            agent=self.name,
            output="Security analysis is unavailable: no security analyzer is configured.",
            metadata={
                "status": "blocked",
                "reason": "security_analyzer_unavailable",
            },
        )
