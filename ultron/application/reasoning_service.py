class ReasoningService:
    """
    Coordinates reasoning.
    """

    def __init__(self, reasoning_engine):
        self.reasoning_engine = reasoning_engine

    def think(self, context):
        return self.reasoning_engine.reason(context)
