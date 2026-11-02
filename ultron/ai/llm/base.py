from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """
    Abstract interface for language model providers.

    Supports:
    - cloud models
    - local models
    - future providers
    """

    @abstractmethod
    def generate(
        self,
        prompt: str,
        context: dict | None = None,
    ) -> str:
        """
        Generate a response from the model.
        """

        raise NotImplementedError
