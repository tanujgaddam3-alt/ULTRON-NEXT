from datetime import UTC, datetime
from uuid import uuid4


class VisualMemory:
    """
    Stores visual experiences.
    """

    def __init__(
        self,
    ):

        self.memories = []

    def remember(
        self,
        observation,
    ):

        memory = {
            "id": str(uuid4()),
            "observation": observation,
            "timestamp": (datetime.now(UTC)),
        }

        self.memories.append(memory)

        return memory

    def recall(
        self,
    ):

        return self.memories
