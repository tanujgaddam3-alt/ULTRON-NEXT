from typing import ClassVar


class CapabilityMap:
    """
    Maps goals to required capabilities.
    """

    RULES = {
        "software": [
            "code_generation",
            "architecture_design",
            "testing",
        ],
        "security": [
            "vulnerability_scan",
            "threat_analysis",
        ],
        "research": [
            "web_research",
            "information_analysis",
        ],
        "planning": [
            "task_decomposition",
            "strategy_generation",
        ],
    }

    def find(
        self,
        goal: str,
    ) -> list[str]:

        goal = goal.lower()

        capabilities: ClassVar = []

        for key, values in self.RULES.items():
            if key in goal:
                capabilities.extend(values)

        return capabilities
