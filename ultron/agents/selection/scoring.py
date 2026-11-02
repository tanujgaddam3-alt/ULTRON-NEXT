class AgentScorer:
    """
    Scores agents based on capability matching
    and performance.
    """

    def score(
        self,
        agent,
        required_capabilities: list[str],
    ) -> float:

        if not required_capabilities:
            return 0.0

        matched = 0

        for capability in required_capabilities:
            if capability in agent.capabilities:
                matched += 1

        capability_score = matched / len(required_capabilities)

        return capability_score
