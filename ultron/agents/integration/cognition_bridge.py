class AgentCognitionBridge:
    """
    Connects agent performance
    with cognitive monitoring.
    """

    def __init__(
        self,
        performance_memory=None,
        cognition_agent=None,
    ):

        self.memory = performance_memory

        self.cognition = cognition_agent

    def observe_agent(
        self,
        agent_name: str,
    ):

        if not self.memory:
            return None

        performance = self.memory.success_rate(agent_name)

        if self.cognition:
            self.cognition.observe(
                "agent",
                agent_name,
                performance,
            )

        return {
            "agent": agent_name,
            "performance": performance,
        }
