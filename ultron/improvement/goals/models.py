from dataclasses import dataclass, field


@dataclass
class CapabilityRequirement:

    capability: str

    reason: str

    importance: int = 5



@dataclass
class GoalCapabilityMap:

    goal: str

    required: list[CapabilityRequirement] = field(
        default_factory=list
    )

    missing: list[str] = field(
        default_factory=list
    )

    available: list[str] = field(
        default_factory=list
    )
