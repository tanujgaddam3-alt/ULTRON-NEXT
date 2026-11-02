from datetime import UTC, datetime
from uuid import uuid4


class SandboxEnvironment:
    """
    Isolated environment for testing improvements.
    """

    def __init__(self):

        self.id = str(uuid4())

        self.created_at = datetime.now(UTC)

        self.changes = []

        self.active = True

    def apply_change(
        self,
        change,
    ):

        if not self.active:
            raise RuntimeError("Sandbox is inactive.")

        self.changes.append(change)

        return {
            "status": "applied",
            "change": change,
        }

    def test(
        self,
    ):

        return {
            "sandbox": self.id,
            "status": "passed",
            "changes": len(self.changes),
        }

    def destroy(
        self,
    ):

        self.active = False

        self.changes.clear()
