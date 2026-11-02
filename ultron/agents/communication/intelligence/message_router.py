from ultron.agents.communication.message import AgentMessage


class MessageRouter:
    """
    Routes messages between agents.
    """

    def __init__(
        self,
        bus=None,
    ):

        self.bus = bus

    def send(
        self,
        sender: str,
        receiver: str,
        content: str,
        metadata=None,
    ):

        message = AgentMessage(
            sender=sender,
            receiver=receiver,
            content=content,
            metadata=metadata or {},
        )

        if self.bus:
            self.bus.send(message)

        return message

    def receive(
        self,
        agent_name: str,
    ):

        if not self.bus:
            return []

        return [
            message for message in self.bus.history() if message.receiver == agent_name
        ]
