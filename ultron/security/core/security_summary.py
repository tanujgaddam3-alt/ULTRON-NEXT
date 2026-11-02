from datetime import UTC, datetime
from typing import ClassVar


class SecuritySummary:
    """
    Generates ULTRON security overview.
    """

    def __init__(
        self,
        registry=None,
        health=None,
    ):

        self.registry = registry

        self.health = health

    def generate(
        self,
    ):

        components: ClassVar = []

        if self.registry:
            components = self.registry.list_components()

        health = None

        if self.health:
            health = self.health.check()

        return {
            "system": "ULTRON SECURITY GUARDIAN",
            "timestamp": (datetime.now(UTC)),
            "components": components,
            "health": health,
            "status": "OPERATIONAL",
        }
