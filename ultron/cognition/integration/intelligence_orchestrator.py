class IntelligenceOrchestrator:
    """
    Coordinates ULTRON intelligence systems.
    """

    def __init__(
        self,
        knowledge_agent=None,
        reasoning_agent=None,
        cognition_agent=None,
    ):

        self.knowledge = knowledge_agent

        self.reasoning = reasoning_agent

        self.cognition = cognition_agent

    def think(
        self,
        context,
        query=None,
    ):

        result = None

        if self.reasoning:
            result = self.reasoning.reason(
                context,
                query,
            )

        if self.cognition:
            self.cognition.observe(
                "reasoning",
                "decision",
                1.0,
            )

        return result

    def status(
        self,
    ):

        return {
            "knowledge": self.knowledge is not None,
            "reasoning": self.reasoning is not None,
            "cognition": self.cognition is not None,
        }
