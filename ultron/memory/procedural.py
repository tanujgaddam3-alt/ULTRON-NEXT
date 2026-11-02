from ultron.interfaces.memory import MemoryStore


class ProceduralMemory(MemoryStore):
    """
    Stores learned procedures and workflows.
    """

    def __init__(self):
        self._procedures: dict[str, list[str]] = {}

    def add(
        self,
        item: dict[str, list[str]],
    ) -> None:

        if not isinstance(item, dict):
            raise TypeError("ProceduralMemory expects a dictionary.")

        self._procedures.update(item)

    def get(
        self,
    ) -> dict[str, list[str]]:
        return self._procedures.copy()

    def recall(
        self,
        query: str,
    ) -> dict[str, list[str]]:
        """
        Retrieve procedures relevant to a query.
        """

        query = query.lower()

        return {
            name: steps
            for name, steps in self._procedures.items()
            if query in name.lower() or any(query in step.lower() for step in steps)
        }

    def clear(
        self,
    ) -> None:
        self._procedures.clear()
