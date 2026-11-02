from datetime import UTC, datetime
from enum import Enum


class SecurityStatus(str, Enum):
    ONLINE = "online"

    WARNING = "warning"

    CRITICAL = "critical"

    OFFLINE = "offline"


class SecurityStatusManager:
    """
    Provides current ULTRON security status.
    """

    def __init__(
        self,
        health_monitor,
    ):

        self.health = health_monitor

    def get_status(
        self,
    ):

        healthy = self.health.is_healthy()

        if healthy:
            status = SecurityStatus.ONLINE

        else:
            status = SecurityStatus.WARNING

        return {
            "status": status,
            "timestamp": (datetime.now(UTC)),
            "health": (self.health.check()),
        }
