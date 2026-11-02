from datetime import UTC, datetime


class ComplianceTracker:
    """
    Tracks security compliance status.
    """

    def __init__(self):

        self.requirements = {}

    def register(
        self,
        name: str,
        status: bool = False,
    ):

        self.requirements[name] = {
            "status": status,
            "updated_at": (datetime.now(UTC)),
        }

        return self.requirements[name]

    def update(
        self,
        name: str,
        status: bool,
    ):

        if name in self.requirements:
            self.requirements[name]["status"] = status

            self.requirements[name]["updated_at"] = datetime.now(UTC)

    def report(
        self,
    ):

        return self.requirements
