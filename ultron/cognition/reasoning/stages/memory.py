from ultron.cognition.reasoning.state import ReasoningState
from ultron.memory.manager import MemoryManager

from .base import ReasoningStage


class MemoryRecallStage(ReasoningStage):
    """
    Retrieves memories relevant to the current reasoning process.
    """

    def __init__(self):
        self.memory = MemoryManager()

    def run(
        self,
        state: ReasoningState,
    ) -> ReasoningState:

        query = ""

        if state.goals:
            query = state.goals[0]

        elif state.intent:
            query = state.intent

        memories = self.memory.recall(query)

        state.memories.append(memories)

        state.trace.append(f"Retrieved memories for '{query}'.")

        return state
