from abc import ABC, abstractmethod


class ReasoningStage(ABC):
    @abstractmethod
    def run(self, context):
        raise NotImplementedError
