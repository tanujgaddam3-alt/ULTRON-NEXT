from typing import ClassVar

from ultron.agents.base import BaseAgent
from ultron.agents.models import AgentTask


class AgentRegistry:
    """
    Stores and discovers available agents.
    """

    def __init__(self):

        self._agents: dict[str, BaseAgent] = {}

    def register(
        self,
        agent: BaseAgent,
    ) -> None:

        if agent is None:
            raise ValueError("Cannot register empty agent.")

        self._agents[agent.name] = agent

    def unregister(
        self,
        name: str,
    ) -> None:

        self._agents.pop(
            name,
            None,
        )

    def get(
        self,
        name: str,
    ) -> BaseAgent | None:

        return self._agents.get(name)

    def find(
        self,
        task: AgentTask,
    ):

        matches: ClassVar = []

        for agent in self._agents.values():
            if agent.supports(task):
                matches.append(agent)

        return matches

    def available(
        self,
    ) -> list[dict]:

        return [agent.info() for agent in self._agents.values()]

    @property
    def agents(self):

        return tuple(self._agents.values())
