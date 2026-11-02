from typing import ClassVar

from ultron.agents.prime.agent import PrimeAgent


class PrimeExecutor:
    """
    Executes ULTRON Prime workflows.
    """

    def __init__(
        self,
        prime: PrimeAgent,
        orchestrator,
    ):

        self.prime = prime
        self.orchestrator = orchestrator

    def run(
        self,
        command: str,
    ):

        result = self.prime.process(command)

        workflow = result["workflow"]

        outputs: ClassVar = []

        for agent_name in workflow.agents:
            outputs.append(
                {
                    "agent": agent_name,
                    "status": "assigned",
                }
            )

        return {
            "goal": workflow.goal,
            "agents": outputs,
            "status": "completed",
        }
