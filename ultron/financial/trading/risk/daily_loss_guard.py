from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class DailyLossGuard:
    """
    Prevents new trading when the portfolio has exceeded
    its configured daily loss limit.

    This guard does not execute or close positions.
    It only decides whether new trading is permitted.
    """

    max_daily_loss_percent: float = 2.0

    def __post_init__(self) -> None:
        if (
            not isfinite(self.max_daily_loss_percent)
            or self.max_daily_loss_percent <= 0
        ):
            raise ValueError(
                "max_daily_loss_percent must be finite and positive."
            )

    def validate(
        self,
        starting_equity: float,
        current_equity: float,
    ) -> dict[str, float | bool | str]:

        if not isfinite(starting_equity) or starting_equity <= 0:
            return {
                "valid": False,
                "reason": "Starting equity must be finite and positive.",
            }

        if not isfinite(current_equity) or current_equity < 0:
            return {
                "valid": False,
                "reason": "Current equity must be finite and non-negative.",
            }

        loss = starting_equity - current_equity
        loss_percent = (
            loss / starting_equity
        ) * 100.0

        if not isfinite(loss) or not isfinite(loss_percent):
            return {
                "valid": False,
                "reason": "Daily loss calculation is non-finite.",
            }

        if loss_percent >= self.max_daily_loss_percent:
            return {
                "valid": False,
                "halted": True,
                "reason": "Daily loss limit exceeded.",
                "starting_equity": starting_equity,
                "current_equity": current_equity,
                "loss": loss,
                "loss_percent": loss_percent,
                "max_daily_loss_percent":
                    self.max_daily_loss_percent,
            }

        return {
            "valid": True,
            "halted": False,
            "reason": "Daily loss limit not exceeded.",
            "starting_equity": starting_equity,
            "current_equity": current_equity,
            "loss": loss,
            "loss_percent": loss_percent,
            "max_daily_loss_percent":
                self.max_daily_loss_percent,
        }
