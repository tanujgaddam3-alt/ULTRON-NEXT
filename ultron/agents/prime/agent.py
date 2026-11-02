from ultron.agents.prime.coordinator import (
    PrimeCoordinator,
)


class PrimeAgent:
    """
    Main command intelligence agent.

    Converts user commands into
    coordinated agent workflows.
    """

    name = "prime"

    def __init__(
        self,
        coordinator: PrimeCoordinator | None = None,
        orchestrator=None,
    ):

        self.coordinator = (
            coordinator if coordinator is not None else PrimeCoordinator()
        )

        self.orchestrator = orchestrator

    def process(
        self,
        command: str,
    ):

        intent = self.coordinator.create_intent(command)

        workflow = self.coordinator.build_workflow(intent)

        return {
            "intent": intent,
            "workflow": workflow,
        }
