from datetime import UTC, datetime
from typing import ClassVar


class SecurityHealthMonitor:
    """
    Monitors security subsystem health.
    """

    def __init__(
        self,
        components=None,
    ):

        self.components = components or {}

    def check(
        self,
    ):

        health: ClassVar = {}

        for name, component in self.components.items():
            health[name] = {
                "available": (component is not None),
                "checked_at": (datetime.now(UTC)),
            }

        return health

    def is_healthy(
        self,
    ):

        result = self.check()

        return all(item["available"] for item in result.values())
