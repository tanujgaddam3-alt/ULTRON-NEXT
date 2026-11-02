from __future__ import annotations

from ultron.cognition.context.models import CognitiveContext


class CognitiveContextBuilder:
    """
    Builder for CognitiveContext.
    """

    def __init__(self):
        self._values = {}

    def begin(
        self,
        query: str,
        understanding,
    ):
        self._values["query"] = query
        self._values["understanding"] = understanding
        return self

    def goals(self, goals):
        self._values["goals"] = goals
        return self

    def memories(self, memories):
        self._values["memories"] = memories
        return self

    def schemas(self, schemas):
        self._values["schemas"] = schemas
        return self

    def world(self, world):
        self._values["world"] = world
        return self

    def skills(
        self,
        skills,
    ):
        self._values["skills"] = skills
        return self

    def knowledge(
        self,
        knowledge,
    ):
        self._values["knowledge"] = knowledge
        return self

    def attention(self, attention):
        self._values["attention"] = attention
        return self

    def metadata(self, metadata):
        self._values["metadata"] = metadata
        return self

    def build(self):
        return CognitiveContext(**self._values)
