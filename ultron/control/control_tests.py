class ControlIntegrationTest:
    """
    Tests ULTRON control subsystem.
    """

    def __init__(
        self,
        parser=None,
        permission=None,
        router=None,
        executor=None,
        agent=None,
    ):

        self.parser = parser

        self.permission = permission

        self.router = router

        self.executor = executor

        self.agent = agent

    def run(
        self,
    ):

        results = {
            "parser": self.parser is not None,
            "permission": self.permission is not None,
            "router": self.router is not None,
            "executor": self.executor is not None,
            "agent": self.agent is not None,
        }

        results["passed"] = all(results.values())

        return results
