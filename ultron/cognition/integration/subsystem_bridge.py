from datetime import UTC, datetime


class SubsystemBridge:
    """
    Connects ULTRON subsystems with Cognition Core.
    """

    def __init__(
        self,
        cognition_agent=None,
    ):

        self.cognition = cognition_agent

        self.connections = {}

    def register(
        self,
        name,
        subsystem,
    ):

        self.connections[name] = subsystem

    def observe_event(
        self,
        system,
        event,
        score,
    ):

        if not self.cognition:
            return None

        return self.cognition.observe(
            system,
            event,
            score,
        )

    def status(
        self,
    ):

        return {
            "connected_systems": list(self.connections.keys()),
            "timestamp": datetime.now(UTC),
        }
