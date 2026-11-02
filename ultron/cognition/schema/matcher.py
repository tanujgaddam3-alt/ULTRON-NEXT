from __future__ import annotations

from abc import ABC, abstractmethod

from ultron.cognition.schema.models import ActivatedSchema


class SchemaMatcher(ABC):
    """
    Finds schemas relevant to the current cognitive context.
    """

    @abstractmethod
    def match(
        self,
        query: str,
    ) -> list[ActivatedSchema]:
        """
        Return activated schemas ranked by relevance.
        """
        raise NotImplementedError
