class CapabilityMetrics:
    """
    Measures capability performance.
    """


    def __init__(
        self,
        capability_registry=None,
    ):

        self.capability_registry = (
            capability_registry
        )


    def measure(
        self,
        capability,
    ):

        score = 0


        # Exists in registry

        if (
            self.capability_registry
            and
            self.capability_registry.exists(
                capability
            )
        ):

            score += 30



        # Skill can load

        try:

            from ultron.infrastructure.container import container


            result = (
                container.skill_loader.load(
                    capability
                )
            )


            if result.get(
                "success"
            ):

                score += 30


        except Exception:

            pass



        # Has implementation files

        import os


        paths = [

            f"ultron/skills/{capability}",

            f"ultron/perception/{capability}",

        ]


        for path in paths:

            if os.path.exists(path):

                score += 20



        # Has tests

        if os.path.exists(
            f"tests/test_{capability}.py"
        ):

            score += 20



        return score