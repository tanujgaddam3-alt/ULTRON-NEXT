from ultron.ai.llm.provider import ProviderManager
from ultron.context.manager import ContextManager


class ReasoningEngine:
    """
    Advanced reasoning layer.

    Combines:
    - context
    - language model
    - reasoning strategy
    """

    def __init__(
        self,
        context_manager: ContextManager | None = None,
        llm: ProviderManager | None = None,
    ):

        self.context = context_manager or ContextManager()

        self.llm = llm

    def reason(
        self,
        request: str,
    ) -> dict:

        context = self.context.update(request)

        payload = {
            "request": request,
            "context": context.model_dump(),
        }

        if self.llm:
            response = self.llm.generate(
                request,
                payload,
            )

        else:
            response = "No language model configured."

        return {
            "request": request,
            "analysis": response,
            "context": payload,
        }
