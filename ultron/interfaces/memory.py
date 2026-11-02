from abc import ABC, abstractmethod
from typing import Any


class MemoryStore(ABC):
    """Base interface for every memory store."""

    @abstractmethod
    def add(self, item: Any) -> None:
        """Store an item."""
        raise NotImplementedError

    @abstractmethod
    def get(self, *args: Any, **kwargs: Any) -> Any:
        """Retrieve item(s)."""
        raise NotImplementedError

    @abstractmethod
    def clear(self) -> None:
        """Remove all items."""
        raise NotImplementedError
