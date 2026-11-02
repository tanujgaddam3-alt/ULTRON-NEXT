class FailureAnalyzer:
    """
    Converts test failures into improvement requests.
    """

    def analyze(
        self,
        test_result,
    ):

        if test_result.get("success"):
            return None

        output = test_result.get("output", "") + test_result.get("errors", "")

        return {
            "component": self._detect_component(output),
            "failure": output,
        }

    def _detect_component(
        self,
        output,
    ):

        mapping = {
            "knowledge": "KnowledgeSystem",
            "memory": "MemorySystem",
            "runtime": "RuntimeSystem",
            "reasoning": "ReasoningEngine",
            "execution": "ExecutionEngine",
        }

        lower = output.lower()

        for key, value in mapping.items():
            if key in lower:
                return value

        return "UnknownComponent"
