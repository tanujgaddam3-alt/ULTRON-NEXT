from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4


class FinancialStepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class FinancialPlanStep:
    order: int
    action: str
    parameters: dict[str, object] = field(default_factory=dict)
    depends_on: list[int] = field(default_factory=list)
    status: FinancialStepStatus = FinancialStepStatus.PENDING
    result: object | None = None
    error: str | None = None


@dataclass
class FinancialPlan:
    objective: str
    symbol: str
    asset_class: str | None = None
    steps: list[FinancialPlanStep] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(
        default_factory=lambda: datetime.now(UTC)
    )

    @property
    def completed(self) -> bool:
        return bool(self.steps) and all(
            step.status in {
                FinancialStepStatus.COMPLETED,
                FinancialStepStatus.SKIPPED,
            }
            for step in self.steps
        )
