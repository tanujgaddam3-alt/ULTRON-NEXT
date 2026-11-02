from collections import deque

from ultron.domain.perception import Perception
from ultron.interfaces.memory import MemoryStore


class SensoryMemory(MemoryStore):
    """
    Very short-lived memory.

    Holds the latest perceptions before they are processed.
    """

    def __init__(self, capacity: int = 20):
        self._memory = deque(maxlen=capacity)

    def add(self, item: Perception) -> None:
        self._memory.append(item)

    def get(self):
        return list(self._memory)

    def clear(self) -> None:
        self._memory.clear()
