from __future__ import annotations

from ultron.cognition.schema.matcher import SchemaMatcher
from ultron.cognition.schema.models import ActivatedSchema


class SchemaEngine:
    """
    Coordinates schema matching and activation.
    """

    def __init__(
        self,
        matcher: SchemaMatcher,
    ):
        self._matcher = matcher

    def activate(
        self,
        query: str,
    ) -> list[ActivatedSchema]:
        """
        Activate schemas relevant to the current query.
        """

        return self._matcher.match(query)
