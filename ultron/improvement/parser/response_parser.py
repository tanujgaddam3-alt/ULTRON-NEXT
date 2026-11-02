class ImprovementResponseParser:
    """
    Converts LLM recommendations into
    structured improvement plans.
    """

    def parse(
        self,
        response: str,
        component: str,
    ) -> dict:

        return {
            "target": component,
            "strategy": self.extract_strategy(response),
            "problem": response,
            "risk": "MEDIUM",
        }

    def extract_strategy(
        self,
        response: str,
    ):

        keywords = [
            "Dynamic Hypothesis Generation",
            "Adaptive Learning",
            "Context Awareness",
            "Feedback Loop",
        ]

        for keyword in keywords:
            if keyword.lower() in response.lower():
                return keyword

        return "General architectural improvement"
