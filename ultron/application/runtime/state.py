from dataclasses import dataclass

from .cycle import CognitiveCycle


@dataclass
class RuntimeState:
    running: bool = False

    cycle_count: int = 0

    last_cycle: CognitiveCycle | None = None
