class DigitalImmuneSystem:
    """
    Unified adaptive security layer.
    """

    def __init__(
        self,
        memory,
        rules,
        adaptive_engine,
        hardening,
    ):

        self.memory = memory

        self.rules = rules

        self.adaptive = adaptive_engine

        self.hardening = hardening

    def process_threat(
        self,
        pattern: str,
        defense: str,
    ):

        analysis = self.adaptive.analyze(pattern)

        if analysis["status"] == "unknown_threat":
            self.adaptive.learn(
                pattern,
                defense,
            )

            self.hardening.create(
                target=pattern,
                improvement=defense,
            )

            return {
                "status": "learned",
                "pattern": pattern,
            }

        return {
            "status": "protected",
            "action": analysis["action"],
        }
