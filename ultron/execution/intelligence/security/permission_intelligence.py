class PermissionIntelligence:
    """
    Evaluates action permission levels.
    """

    def __init__(
        self,
    ):

        self.history = []

    def evaluate(
        self,
        action: str,
        risk: float = 0.0,
    ):

        if not 0 <= risk <= 1:
            raise ValueError("Risk must be between 0 and 1")

        if risk >= 0.8:
            decision = "restricted"

        elif risk >= 0.5:
            decision = "review"

        else:
            decision = "allowed"

        result = {
            "action": action,
            "risk": risk,
            "decision": decision,
        }

        self.history.append(result)

        return result
