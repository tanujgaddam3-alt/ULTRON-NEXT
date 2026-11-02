class KnowledgeCognitionBridge:
    """
    Connects Knowledge Intelligence
    with Cognitive Smith Chart.
    """

    def __init__(
        self,
        knowledge_analyzer=None,
        cognition_agent=None,
    ):

        self.analyzer = knowledge_analyzer

        self.cognition = cognition_agent

    def evaluate(
        self,
        graph,
    ):

        if not self.analyzer:
            return None

        analysis = self.analyzer.analyze(graph)

        if self.cognition:
            self.cognition.observe(
                "knowledge",
                "knowledge_health",
                analysis["knowledge_health"],
            )

        return analysis
