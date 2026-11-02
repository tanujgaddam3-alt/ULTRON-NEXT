from dataclasses import dataclass, field
from typing import Any

from ultron.cognition.context.models import CognitiveContext


@dataclass(slots=True)
class CognitiveCycle:
    event: Any = None

    perception: Any = None

    understanding: Any = None

    context: CognitiveContext | None = None

    reasoning: Any = None

    hypotheses: list[Any] = field(default_factory=list)

    decision: Any = None

    plan: Any = None

    execution: Any = None

    reflection: Any = None

    learning: Any = None

    metadata: dict[str, Any] = field(default_factory=dict)
