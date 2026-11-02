class SecurityInitializer:
    """
    Initializes ULTRON security subsystem.
    """

    def __init__(
        self,
        config=None,
        registry=None,
        diagnostics=None,
    ):

        self.config = config

        self.registry = registry

        self.diagnostics = diagnostics

    def initialize(
        self,
    ):

        result = {
            "security": "starting",
            "components": [],
        }

        if self.config:
            result["configuration"] = "loaded"

        if self.registry:
            components = self.registry.list_components()

            result["components"] = components

        if self.diagnostics:
            result["health"] = self.diagnostics.run()

        result["security"] = "ready"

        return result
