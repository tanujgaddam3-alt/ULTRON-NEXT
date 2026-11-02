class AdaptiveSecurityEngine:
    """
    Learns from threats and adapts defenses.
    """

    def __init__(
        self,
        memory,
        rules,
    ):

        self.memory = memory

        self.rules = rules

    def analyze(
        self,
        pattern: str,
    ):

        existing_rules = self.rules.match(pattern)

        if existing_rules:
            return {
                "status": "known_threat",
                "action": existing_rules[0].action,
            }

        return {
            "status": "unknown_threat",
            "action": "analyze_and_learn",
        }

    def learn(
        self,
        pattern: str,
        defense: str,
    ):

        self.memory.remember(
            pattern,
            defense,
        )

        self.rules.create_rule(
            pattern,
            defense,
        )

        return {
            "status": "adapted",
            "pattern": pattern,
            "defense": defense,
        }
