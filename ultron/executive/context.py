from dataclasses import dataclass

from ultron.domain.perception import Perception
from ultron.domain.understanding import Understanding


@dataclass
class ExecutiveContext:
    perception: Perception | None = None
    understanding: Understanding | None = None
