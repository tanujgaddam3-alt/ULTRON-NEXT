class AgentCapabilityScorer:
    """
    Scores agents based on task compatibility.
    """

    def __init__(
        self,
    ):

        self.history = []

    def score(
        self,
        agent,
        task,
    ):

        score = 0.0

        if agent.supports(task):
            score += 0.7

        if hasattr(agent, "performance"):
            score += agent.performance * 0.3

        result = {
            "agent": agent.name,
            "score": score,
        }

        self.history.append(result)

        return result

    def rank(
        self,
        agents,
        task,
    ):

        results = [
            self.score(
                agent,
                task,
            )
            for agent in agents
        ]

        return sorted(
            results,
            key=lambda x: x["score"],
            reverse=True,
        )
