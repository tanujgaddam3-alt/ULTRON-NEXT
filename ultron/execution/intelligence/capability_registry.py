from datetime import UTC, datetime


class CapabilityRegistry:
    """
    Stores ULTRON available capabilities.
    """

    def __init__(
        self,
    ):

        self.capabilities = {}

    def register(
        self,
        name: str,
        description: str,
        risk: float = 0.0,
        permission: str = "basic",
    ):

        self.capabilities[name] = {
            "description": description,
            "risk": risk,
            "permission": permission,
            "registered_at": datetime.now(UTC),
        }

    def get(
        self,
        name: str,
    ):

        return self.capabilities.get(name)

    def available(
        self,
    ):

        return list(self.capabilities.keys())

    def exists(
        self,
        name: str,
    ):

        return name in self.capabilities
