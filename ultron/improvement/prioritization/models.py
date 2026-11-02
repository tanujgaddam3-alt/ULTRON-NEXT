from dataclasses import dataclass


@dataclass
class ImprovementPriority:

    capability: str

    impact: float

    urgency: float

    dependency: float

    risk: float

    score: float

    reason: str
