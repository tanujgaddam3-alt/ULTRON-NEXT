from typing import ClassVar

from ultron.domain.perception import Perception


class PerceptionAdapter:
    """
    Converts control commands into cognitive perceptions.
    """

    def convert(
        self,
        command,
    ) -> Perception:

        entities: ClassVar = {}

        if command.target:
            entities["target"] = command.target

        return Perception(
            source="command",
            raw_input=(
                command.action
                if not command.target
                else f"{command.action} {command.target}"
            ),
            intent=command.action,
            entities=entities,
            confidence=command.confidence,
        )
