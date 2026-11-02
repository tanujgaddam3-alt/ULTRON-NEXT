from typing import ClassVar

from ultron.domain.perception import Perception


class InputParser:
    """
    Converts raw input into an initial Perception object.
    """

    def parse(self, text: str) -> Perception:
        return Perception(
            source="terminal",
            raw_input=text,
            intent="unknown",
            entities={},
            confidence=0.0,
        )
