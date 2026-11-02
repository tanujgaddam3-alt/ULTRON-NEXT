from ultron.domain.perception import Perception
from ultron.domain.understanding import (
    Understanding,
    UnderstandingType,
)


class UnderstandingEngine:
    def understand(
        self,
        perception: Perception,
    ) -> Understanding:

        mapping = {
            "open_application": UnderstandingType.COMMAND,
            "build_project": UnderstandingType.COMMAND,
            "question": UnderstandingType.QUESTION,
            "search": UnderstandingType.SEARCH,
            "memory_store": UnderstandingType.MEMORY,
            "conversation": UnderstandingType.CONVERSATION,
        }

        understanding_type = mapping.get(
            perception.intent,
            UnderstandingType.UNKNOWN,
        )

        return Understanding(
            type=understanding_type,
            summary=perception.raw_input,
            goal=None,
            requires_reasoning=understanding_type
            in {
                UnderstandingType.QUESTION,
                UnderstandingType.SEARCH,
            },
            requires_execution=understanding_type is UnderstandingType.COMMAND,
            requires_planning=False,
            context={
                **perception.entities,
                "intent": perception.intent,
            },
        )
