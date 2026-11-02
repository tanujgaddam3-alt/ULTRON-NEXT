from ultron.domain.experience import Experience
from ultron.interfaces.memory import MemoryStore


class EpisodicMemory(MemoryStore):
    """
    Stores experiences.
    """

    def __init__(self):
        self._episodes: list[Experience] = []

    def add(
        self,
        item: Experience,
    ):
        self._episodes.append(item)

    def get(
        self,
    ) -> list[Experience]:
        return self._episodes.copy()

    def recall(
        self,
        query: str,
    ) -> list[Experience]:
        """
        Retrieve experiences relevant to a query.
        """

        query = query.lower()

        matches: list[Experience] = []

        for episode in self._episodes:
            text = " ".join(
                [
                    episode.task_id,
                    episode.outcome,
                    episode.lesson,
                ]
            ).lower()

            if query in text:
                matches.append(episode)

        return matches

    def clear(self):
        self._episodes.clear()
