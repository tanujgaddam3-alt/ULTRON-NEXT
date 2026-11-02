from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass
class PositionSizer:
    risk_per_trade_percent: float = 1.0

    def __post_init__(self) -> None:
        if (
            not isfinite(self.risk_per_trade_percent)
            or not 0 < self.risk_per_trade_percent <= 10
        ):
            raise ValueError(
                "risk_per_trade_percent must be between 0 and 10."
            )

    def calculate(
        self,
        equity: float,
        entry_price: float,
        stop_loss: float,
    ) -> dict[str, float | bool | str]:

        if not isfinite(equity) or equity <= 0:
            return {
                "approved": False,
                "quantity": 0.0,
                "reason": "Equity must be positive.",
            }

        if not isfinite(entry_price) or entry_price <= 0:
            return {
                "approved": False,
                "quantity": 0.0,
                "reason": "Entry price must be positive.",
            }

        if not isfinite(stop_loss) or stop_loss <= 0:
            return {
                "approved": False,
                "quantity": 0.0,
                "reason": "Stop loss must be positive.",
            }

        if stop_loss >= entry_price:
            return {
                "approved": False,
                "quantity": 0.0,
                "reason": (
                    "Stop loss must be below entry price "
                    "for a long position."
                ),
            }

        risk_capital = (
            equity
            * self.risk_per_trade_percent
            / 100.0
        )

        risk_per_unit = (
            entry_price - stop_loss
        )

        quantity = (
            risk_capital / risk_per_unit
        )

        if not all(
            isfinite(value)
            for value in (risk_capital, risk_per_unit, quantity)
        ):
            return {
                "approved": False,
                "quantity": 0.0,
                "reason": "Position sizing produced a non-finite result.",
            }

        return {
            "approved": True,
            "quantity": quantity,
            "risk_capital": risk_capital,
            "risk_per_unit": risk_per_unit,
            "risk_percent": self.risk_per_trade_percent,
            "reason": (
                "Position size calculated from "
                "risk-per-trade and stop-loss distance."
            ),
        }
