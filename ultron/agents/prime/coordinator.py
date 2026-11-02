from ultron.agents.models import AgentTask
from ultron.agents.prime.intent import UserIntent
from ultron.agents.prime.workflow import Workflow
from ultron.agents.registry import AgentRegistry


class PrimeCoordinator:
    """
    Coordinates ULTRON Prime decisions.
    """

    def __init__(
        self,
        registry: AgentRegistry | None = None,
    ):

        self.registry = registry if registry is not None else AgentRegistry()

    def create_intent(
        self,
        command: str,
    ) -> UserIntent:
        """
        Convert command into structured intent.
        """

        return UserIntent(
            command=command,
            goal=command,
        )

    def build_workflow(
        self,
        intent: UserIntent,
    ) -> Workflow:
        """
        Select agents and create workflow.
        """

        task = AgentTask(
            goal=intent.goal,
            required_capabilities=(intent.required_capabilities),
        )

        agents = self.registry.find(task)

        return Workflow(
            goal=intent.goal,
            agents=[agent.name for agent in agents],
            steps=[
                "Analyze goal",
                "Execute agent tasks",
                "Evaluate results",
            ],
        )
