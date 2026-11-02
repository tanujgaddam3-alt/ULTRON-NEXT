from abc import ABC, abstractmethod


class LanguageModel(ABC):
    @abstractmethod
    def generate(
        self,
        prompt: str,
    ) -> str:
        raise NotImplementedError
