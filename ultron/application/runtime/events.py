from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class RuntimeEvent:
    source: str
    payload: Any
