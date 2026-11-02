from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any


@dataclass(frozen=True)
class DynamicExitConfig:
    """
    Configuration for volatility-aware trade exits.

    ATR is used as the primary distance measurement.
    """

    atr_multiplier: float = 2.0
    reward_risk_ratio: float = 2.0
    min_stop_percent: float = 0.5
    max_stop_percent: float = 10.0

    def __post_init__(self) -> None:
        if not isfinite(self.atr_multiplier) or self.atr_multiplier <= 0:
            raise ValueError("atr_multiplier must be finite and positive.")

        if not isfinite(self.reward_risk_ratio) or self.reward_risk_ratio <= 0:
            raise ValueError(
                "reward_risk_ratio must be finite and positive."
            )

        if (
            not isfinite(self.min_stop_percent)
            or not 0 < self.min_stop_percent <= 100
        ):
            raise ValueError(
                "min_stop_percent must be between 0 and 100."
            )

        if (
            not isfinite(self.max_stop_percent)
            or not 0 < self.max_stop_percent <= 100
        ):
            raise ValueError(
                "max_stop_percent must be between 0 and 100."
            )

        if self.min_stop_percent > self.max_stop_percent:
            raise ValueError(
                "min_stop_percent cannot exceed max_stop_percent."
            )


class DynamicExitEngine:
    """
    Calculates market-aware stop-loss and take-profit levels.

    Long:
        stop = entry - ATR distance
        target = entry + risk * reward/risk

    Short:
        stop = entry + ATR distance
        target = entry - risk * reward/risk
    """

    def __init__(
        self,
        config: DynamicExitConfig | None = None,
    ) -> None:
        self.config = config or DynamicExitConfig()

    def calculate(
        self,
        entry_price: float,
        atr: float,
        direction: str,
    ) -> dict[str, Any]:

        if not isfinite(entry_price) or entry_price <= 0:
            return self._reject(
                "Entry price must be positive."
            )

        if not isfinite(atr) or atr <= 0:
            return self._reject(
                "ATR must be positive."
            )

        direction = direction.strip().lower()

        if direction not in {"long", "short"}:
            return self._reject(
                "Direction must be long or short."
            )

        raw_distance = atr * self.config.atr_multiplier

        min_distance = (
            entry_price
            * self.config.min_stop_percent
            / 100.0
        )

        max_distance = (
            entry_price
            * self.config.max_stop_percent
            / 100.0
        )

        stop_distance = max(
            min_distance,
            min(raw_distance, max_distance),
        )

        risk_per_unit = stop_distance

        if direction == "long":
            stop_loss = entry_price - stop_distance
            take_profit = (
                entry_price
                + risk_per_unit
                * self.config.reward_risk_ratio
            )

        else:
            stop_loss = entry_price + stop_distance
            take_profit = (
                entry_price
                - risk_per_unit
                * self.config.reward_risk_ratio
            )

        if not all(
            isfinite(value)
            for value in (
                stop_distance,
                stop_loss,
                take_profit,
            )
        ):
            return self._reject("Exit calculation produced a non-finite result.")

        return {
            "approved": True,
            "direction": direction,
            "entry_price": entry_price,
            "atr": atr,
            "atr_multiplier": self.config.atr_multiplier,
            "stop_distance": stop_distance,
            "risk_per_unit": risk_per_unit,
            "stop_percent": (
                stop_distance / entry_price
            ) * 100.0,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "reward_risk_ratio": (
                self.config.reward_risk_ratio
            ),
        }

    @staticmethod
    def _reject(reason: str) -> dict[str, Any]:
        return {
            "approved": False,
            "reason": reason,
        }
