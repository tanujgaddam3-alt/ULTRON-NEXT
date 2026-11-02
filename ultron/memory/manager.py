from typing import Any

from ultron.memory.episodic import EpisodicMemory
from ultron.memory.procedural import ProceduralMemory
from ultron.memory.semantic import SemanticMemory
from ultron.memory.sensory import SensoryMemory
from ultron.memory.working import WorkingMemory


class MemoryManager:
    """
    Central access point for all memory systems.
    """

    def __init__(self):
        self.sensory = SensoryMemory()
        self.working = WorkingMemory()
        self.episodic = EpisodicMemory()
        self.semantic = SemanticMemory()
        self.procedural = ProceduralMemory()

    def recall(
        self,
        query: str,
    ) -> dict[str, Any]:

         query = self._normalize_query(query)

         return {
            "working": self.working.recall(query),
            "episodic": self.episodic.recall(query),
            "semantic": self.semantic.recall(query),
            "procedural": self.procedural.recall(query),
        }
    def _normalize_query(
        self,
        query: str,
    ) -> str:

        query = query.lower()

        keywords = [
            "name",
            "age",
            "location",
            "city",
            "country",
        ]

        for word in keywords:

            if word in query:
                return word

        return query

    def remember(
        self,
        category: str,
        data: dict,
    ):
         """
         Store new information in memory.
        """

         if category == "semantic":

            self.semantic.add(
                data
            )

         elif category == "episodic":

            self.episodic.add(
                data
            )

         elif category == "procedural":

            self.procedural.add(
                data
            )

         elif category == "working":

            self.working.add(
                data
            )
    def clear_all(self):
        self.sensory.clear()
        self.working.clear()
        self.episodic.clear()
        self.semantic.clear()
        self.procedural.clear()

    def health_check(self):

        return {
            "episodic": self.episodic is not None,
            "semantic": self.semantic is not None,
        }
