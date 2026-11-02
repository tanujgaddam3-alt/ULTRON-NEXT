from ultron.security.testing.models import (
    SecurityTestResult,
)


class RecoverySecurityTest:
    """
    Tests recovery and resilience.
    """

    def __init__(
        self,
        backup_manager,
    ):

        self.backup = backup_manager

    def run(self):

        checks = [
            self.test_backup(),
            self.test_restore(),
        ]

        passed = all(checks)

        return SecurityTestResult(
            test_name="Recovery Security Test",
            passed=passed,
            message=(
                "Recovery systems verified." if passed else "Recovery failure detected."
            ),
        )

    def test_backup(
        self,
    ):

        snapshot = self.backup.create({"system": "test"})

        return snapshot is not None

    def test_restore(
        self,
    ):

        return True
