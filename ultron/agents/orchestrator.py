from ultron.agents.intelligence.capability_scorer import (
    AgentCapabilityScorer,
)
from ultron.agents.models import (
    AgentResult,
    AgentTask,
)
from ultron.agents.registry import (
    AgentRegistry,
)


class AgentOrchestrator:
    """
    Coordinates intelligent task assignment.
    """

    def __init__(
        self,
        registry: AgentRegistry | None = None,
    ):

        self.registry = registry if registry is not None else AgentRegistry()

        self.scorer = AgentCapabilityScorer()

    def register(
        self,
        agent,
    ):

        self.registry.register(agent)

    def dispatch(
        self,
        task: AgentTask,
    ) -> AgentResult:

        agents = self.registry.find(task)

        if not agents:
            return AgentResult(
                success=False,
                agent="orchestrator",
                output=("No suitable agent found."),
            )

        ranked = self.scorer.rank(
            agents,
            task,
        )

        selected = self.registry.get(ranked[0]["agent"])
        if selected is None:
            return AgentResult(
                success=False,
                agent="orchestrator",
                output="Selected agent is no longer available.",
                metadata={
                    "status": "unavailable",
                    "reason": "selected_agent_unavailable",
                },
            )

        return AgentResult(
            success=False,
            agent=selected.name,
            output=(
                "Agent dispatch blocked: no trusted operation-grant "
                "integration is available."
            ),
            metadata={
                "status": "blocked",
                "reason": "trusted_operation_grant_unavailable",
            },
        )
