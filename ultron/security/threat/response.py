from ultron.security.guardian import (
    GuardianController,
)
from ultron.security.response import (
    SecurityResponseEngine,
)


class ThreatResponseManager:
    """
    Coordinates threat detection responses.
    """

    def __init__(
        self,
        response_engine=None,
        guardian=None,
    ):

        self.response_engine = response_engine or SecurityResponseEngine()

        self.guardian = guardian or GuardianController()

    def handle(
        self,
        event,
        risk_level: str,
    ):

        response = self.response_engine.decide(risk_level)

        if response.value == "guardian_mode":
            return self.guardian.activate(event)

        return {
            "status": response.value,
            "event": event.id,
        }
