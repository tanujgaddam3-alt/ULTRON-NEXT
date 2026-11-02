from dataclasses import dataclass, field
from typing import Any


@dataclass
class SelfReport:
    """
    Represents ULTRON's internal self assessment.
    """

    skills: list[str] = field(default_factory=list)

    schema_count: int = 0

    schemas: list[dict[str, Any]] = field(
        default_factory=list
    )

    experience_cycles: int = 0

    success_rate: float = 0.0

    weaknesses: list[str] = field(
        default_factory=list
    )

    recommendations: list[str] = field(
        default_factory=list
    )
