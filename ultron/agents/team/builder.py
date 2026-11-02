from typing import ClassVar

from ultron.agents.team.model import AgentTeam


class TeamBuilder:
    """
    Builds optimal agent teams
    from required capabilities.
    """

    def __init__(
        self,
        selector,
    ):

        self.selector = selector

    def build(
        self,
        objective: str,
        capabilities: list[str],
    ) -> AgentTeam:

        selected_agents: ClassVar = []

        for capability in capabilities:
            agent = self.selector.select([capability])

            if agent:
                if agent.name not in selected_agents:
                    selected_agents.append(agent.name)

        return AgentTeam(
            name="adaptive_team",
            objective=objective,
            agents=selected_agents,
            status="ready",
        )
