from dataclasses import dataclass


@dataclass
class SecurityConfig:
    """
    Central security configuration.
    """

    max_login_attempts: int = 5

    threat_monitoring: bool = True

    auto_response: bool = True

    encryption_enabled: bool = True

    audit_logging: bool = True

    integrity_checking: bool = True

    emergency_lockdown: bool = True

    def enable_high_security(
        self,
    ):

        self.max_login_attempts = 3

        self.threat_monitoring = True

        self.auto_response = True

        self.integrity_checking = True
