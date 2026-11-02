from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ImprovementImpact:
    """
    Records the effect of an improvement.
    """

    target: str

    before: dict

    after: dict

    success: bool

    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class ImpactTracker:
    """
    Tracks whether improvements
    actually helped.
    """

    def compare(
        self,
        target: str,
        before: dict,
        after: dict,
    ) -> ImprovementImpact:

        success = after.get("score", 0) >= before.get("score", 0)

        return ImprovementImpact(
            target=target,
            before=before,
            after=after,
            success=success,
        )
