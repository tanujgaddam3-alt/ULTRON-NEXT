class AdaptiveAgentSelector:
    """
    Selects agents using capability and history.
    """

    def __init__(
        self,
        performance_memory=None,
    ):

        self.memory = performance_memory

    def score(
        self,
        agent,
        task,
    ):

        score = 0.0

        if agent.supports(task):
            score += 0.6

        if self.memory:
            reliability = self.memory.success_rate(agent.name)

            score += reliability * 0.4

        return {
            "agent": agent.name,
            "score": score,
        }

    def select(
        self,
        agents,
        task,
    ):

        ranked = [
            self.score(
                agent,
                task,
            )
            for agent in agents
        ]

        return max(
            ranked,
            key=lambda item: item["score"],
        )
