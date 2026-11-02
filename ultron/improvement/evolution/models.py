from dataclasses import dataclass


@dataclass
class CapabilityStatus:

    name: str

    exists: bool

    score: float

    weaknesses: list[str]


@dataclass
class EvolutionDecision:

    capability: str

    action: str

    reason: str

    priority: float
