from datetime import UTC, datetime


class SecurityDiagnostics:
    """
    Performs security subsystem diagnostics.
    """

    def __init__(
        self,
        health_monitor=None,
        status_manager=None,
    ):

        self.health = health_monitor

        self.status = status_manager

    def run(
        self,
    ):

        report = {
            "timestamp": (datetime.now(UTC)),
            "health": None,
            "status": None,
        }

        if self.health:
            report["health"] = self.health.check()

        if self.status:
            report["status"] = self.status.get_status()

        return report
