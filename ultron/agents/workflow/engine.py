from typing import ClassVar

from ultron.agents.workflow.dependency import (
    DependencyResolver,
)
from ultron.agents.workflow.model import (
    AgentWorkflow,
    AgentWorkflowStep,
)


class AgentWorkflowEngine:
    """
    Creates executable workflows
    for multi-agent operations.
    """

    def __init__(
        self,
        resolver=None,
    ):

        self.resolver = resolver if resolver is not None else DependencyResolver()

    def create(
        self,
        goal: str,
        agents: list[str],
    ) -> AgentWorkflow:

        ordered_agents = self.resolver.resolve(agents)

        steps: ClassVar = []

        for index, agent in enumerate(
            ordered_agents,
            start=1,
        ):
            dependencies: ClassVar = []

            if index > 1:
                dependencies.append(index - 1)

            steps.append(
                AgentWorkflowStep(
                    order=index,
                    agent=agent,
                    objective=(f"Complete assigned {agent} task"),
                    depends_on=dependencies,
                )
            )

        return AgentWorkflow(
            goal=goal,
            steps=steps,
            status="ready",
        )
