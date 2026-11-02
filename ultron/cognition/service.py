from __future__ import annotations

from ultron.cognition.context.engine import ContextEngine
from ultron.cognition.reasoning.engine import ReasoningEngine


class CognitiveService:
    """
    Coordinates the cognitive process.
    """

    def __init__(
        self,
        context_engine: ContextEngine,
        reasoning_engine: ReasoningEngine,
    ):
        self._context_engine = context_engine
        self._reasoning_engine = reasoning_engine

    def process(
        self,
        query: str,
    ):

        context = self._context_engine.build(
            query=query,
        )

        return self._reasoning_engine.reason(context)
