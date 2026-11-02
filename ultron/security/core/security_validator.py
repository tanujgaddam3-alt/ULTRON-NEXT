from typing import ClassVar


class SecurityValidator:
    """
    Validates security architecture.
    """

    def __init__(
        self,
        registry=None,
        config=None,
    ):

        self.registry = registry

        self.config = config

    def validate(
        self,
    ):

        checks: ClassVar = {}

        if self.registry:
            components = self.registry.list_components()

            checks["components"] = len(components) > 0

        if self.config:
            checks["encryption"] = self.config.encryption_enabled

            checks["logging"] = self.config.audit_logging

            checks["integrity"] = self.config.integrity_checking

        checks["security_ready"] = all(checks.values())

        return checks
