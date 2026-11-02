from abc import ABC, abstractmethod

from ultron.cognition.reasoning.state import ReasoningState


class ReasoningStage(ABC):
    """
    Base class for all reasoning stages.
    """

    @abstractmethod
    def run(
        self,
        state: ReasoningState,
    ) -> ReasoningState:
        """
        Process and return the updated reasoning state.
        """
        raise NotImplementedError
