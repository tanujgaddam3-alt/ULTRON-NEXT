from typing import ClassVar

from ultron.agents.selection.scoring import (
    AgentScorer,
)


class AdaptiveAgentSelector:
    """
    Selects agents using capability
    and historical performance.
    """

    def __init__(
        self,
        registry,
        performance_store,
    ):

        self.registry = registry
        self.performance = performance_store
        self.scorer = AgentScorer()

    def select(
        self,
        capabilities: list[str],
    ):

        ranked: ClassVar = []

        for agent in self.registry.agents:
            capability_score = self.scorer.score(
                agent,
                capabilities,
            )

            performance_score = self.performance.average_score(agent.name)

            final_score = capability_score * 0.5 + performance_score * 0.5

            ranked.append(
                (
                    agent,
                    final_score,
                )
            )

        ranked.sort(
            key=lambda x: x[1],
            reverse=True,
        )

        return ranked[0][0] if ranked else None
