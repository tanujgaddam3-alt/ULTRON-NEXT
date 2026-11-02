from ultron.agents.communication.message import (
    AgentMessage,
)


class AgentCommunicationBus:
    """
    Handles communication between agents.
    """

    def __init__(self):

        self.messages = []

    def send(
        self,
        message: AgentMessage,
    ):

        self.messages.append(message)

        return message

    def history(
        self,
    ) -> list[AgentMessage]:

        return self.messages
