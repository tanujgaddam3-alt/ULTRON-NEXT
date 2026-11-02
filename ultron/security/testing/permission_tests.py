from ultron.security.testing.models import (
    SecurityTestResult,
)


class PermissionSecurityTest:
    """
    Tests permission firewall behavior.
    """

    def __init__(
        self,
        permission_engine,
    ):

        self.permission = permission_engine

    def run(
        self,
    ):

        checks = [
            self.test_safe_action(),
            self.test_sensitive_action(),
            self.test_blocked_action(),
        ]

        passed = all(checks)

        return SecurityTestResult(
            test_name="Permission Firewall Test",
            passed=passed,
            message=(
                "Permission controls verified."
                if passed
                else "Permission vulnerability detected."
            ),
        )

    def test_safe_action(
        self,
    ):

        result = self.permission.evaluate(
            "analyze",
            "owner",
            100,
        )

        return result.permission.value == "allow"

    def test_sensitive_action(
        self,
    ):

        result = self.permission.evaluate(
            "install_software",
            "owner",
            100,
        )

        return result.permission.value == "confirm"

    def test_blocked_action(
        self,
    ):

        result = self.permission.evaluate(
            "disable_security",
            "owner",
            100,
        )

        return result.permission.value == "deny"
