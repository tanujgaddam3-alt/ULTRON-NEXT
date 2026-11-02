from datetime import UTC, datetime
from uuid import uuid4


class VoiceMemory:
    """
    Stores voice conversations.
    """

    def __init__(
        self,
    ):

        self.history = []

    def store(
        self,
        user_text,
        response,
    ):

        memory = {
            "id": str(uuid4()),
            "input": user_text,
            "response": response,
            "timestamp": (datetime.now(UTC)),
        }

        self.history.append(memory)

        return memory

    def recall(
        self,
    ):

        return self.history
