from typing import ClassVar

from ultron.agents.selection.scoring import (
    AgentScorer,
)


class AgentSelector:
    """
    Selects the best agent for a task.
    """

    def __init__(
        self,
        registry,
    ):

        self.registry = registry
        self.scorer = AgentScorer()

    def select(
        self,
        capabilities: list[str],
    ):

        agents = self.registry._agents.values()

        ranked: ClassVar = []

        for agent in agents:
            score = self.scorer.score(
                agent,
                capabilities,
            )

            ranked.append(
                (
                    agent,
                    score,
                )
            )

        ranked.sort(
            key=lambda x: x[1],
            reverse=True,
        )

        return ranked[0][0] if ranked else None
