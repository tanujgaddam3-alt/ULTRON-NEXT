from typing import Any

from ultron.interfaces.memory import MemoryStore


class WorkingMemory(MemoryStore):
    """
    Stores the current context and active reasoning state.
    """

    def __init__(self):
        self._state: dict[str, Any] = {}

    def add(
        self,
        item: dict[str, Any],
    ) -> None:

        if not isinstance(item, dict):
            raise TypeError("WorkingMemory expects a dictionary.")

        self._state.update(item)

    def get(
        self,
    ) -> dict[str, Any]:
        return self._state.copy()

    def recall(
        self,
        query: str,
    ) -> dict[str, Any]:
        """
        Retrieve working-memory entries relevant to a query.
        """

        query = query.lower()

        return {
            key: value
            for key, value in self._state.items()
            if query in key.lower() or query in str(value).lower()
        }

    def clear(
        self,
    ) -> None:
        self._state.clear()

    def update_state(
        self,
        **kwargs,
    ) -> None:
        """
        Update cognitive state fields.
        """

        self._state.update(kwargs)


def get_state(
    self,
    key: str,
    default=None,
):
    """
    Retrieve a specific cognitive state item.
    """

    return self._state.get(
        key,
        default,
    )


def snapshot(self):
    """
    Return current cognitive snapshot.
    """

    return self._state.copy()


def reset_cycle(self):
    """
    Clear temporary cycle information.
    """

    self._state.pop(
        "execution",
        None,
    )

    self._state.pop(
        "reflection",
        None,
    )
