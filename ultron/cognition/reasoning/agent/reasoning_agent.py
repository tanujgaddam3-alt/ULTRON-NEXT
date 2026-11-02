class ReasoningAgent:
    """
    Main coordinator for ULTRON reasoning.
    """

    name = "reasoning"

    def __init__(
        self,
        engine=None,
        bridge=None,
        metrics=None,
    ):

        self.engine = engine

        self.bridge = bridge

        self.metrics = metrics

    def reason(
        self,
        context,
        query=None,
    ):

        if self.bridge and query:
            context = self.bridge.enrich_context(
                context,
                query,
            )

        if not self.engine:
            return None

        result = self.engine.reason(context)

        return result

    def status(
        self,
    ):

        return {
            "agent": self.name,
            "online": True,
        }
