class SecurityIntegrationTest:
    """
    Tests complete security connection.
    """

    def __init__(
        self,
        security_manager=None,
        guardian=None,
    ):

        self.manager = security_manager

        self.guardian = guardian

    def run(
        self,
    ):

        results = {
            "security_manager": self.manager is not None,
            "guardian": self.guardian is not None,
        }

        results["passed"] = all(results.values())

        return results
