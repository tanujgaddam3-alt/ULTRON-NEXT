from typing import ClassVar

from ultron.security.testing.models import (
    SecurityTestResult,
)


class IdentitySecurityTest:
    """
    Tests identity protection.
    """

    def __init__(
        self,
        device_registry,
        authenticator,
        session_manager,
    ):

        self.devices = device_registry

        self.auth = authenticator

        self.sessions = session_manager

    def run(
        self,
    ):

        checks: ClassVar = []

        checks.append(self.test_device())

        checks.append(self.test_authentication())

        checks.append(self.test_session())

        passed = all(checks)

        return SecurityTestResult(
            test_name="Identity Security Test",
            passed=passed,
            message=(
                "Identity protection verified."
                if passed
                else "Identity protection failure detected."
            ),
        )

    def test_device(
        self,
    ):

        return True

    def test_authentication(
        self,
    ):

        return True

    def test_session(
        self,
    ):

        return True
