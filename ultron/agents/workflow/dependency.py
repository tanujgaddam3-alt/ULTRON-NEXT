class DependencyResolver:
    """
    Resolves execution order between agents.
    """

    DEFAULT_ORDER = {
        "planning": 1,
        "research": 2,
        "coding": 3,
        "security": 4,
        "testing": 5,
        "deployment": 6,
    }

    def resolve(
        self,
        agents: list[str],
    ) -> list[str]:
        """
        Sort agents based on dependency priority.
        """

        return sorted(
            agents,
            key=lambda agent: self.DEFAULT_ORDER.get(
                agent,
                99,
            ),
        )
