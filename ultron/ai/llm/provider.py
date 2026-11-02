from ultron.ai.llm.base import LLMProvider


class ProviderManager:
    """
    Manages the active LLM provider.
    """

    def __init__(
        self,
        provider: LLMProvider | None = None,
    ):

        self.provider = provider

    def set_provider(
        self,
        provider: LLMProvider,
    ):

        self.provider = provider

    def generate(
        self,
        prompt: str,
        context: dict | None = None,
    ) -> str:

        if self.provider is None:
            raise RuntimeError("No LLM provider configured.")

        return self.provider.generate(
            prompt,
            context,
        )
