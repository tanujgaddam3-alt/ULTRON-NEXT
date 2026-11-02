from ultron.cognition.agent.cognition_agent import (
    CognitionAgent,
)


class CognitionManager:
    """
    Main entry point for ULTRON cognitive layer.
    """

    def __init__(
        self,
        agent=None,
    ):

        self.agent = agent or CognitionAgent()

    def status(
        self,
    ):

        return self.agent.status()
