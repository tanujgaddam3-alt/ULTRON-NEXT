from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SignalAction(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass(frozen=True)
class TradingSignal:
    symbol: str
    action: SignalAction
    price: float
    stop_loss: float | None = None
    take_profit: float | None = None
    confidence: float = 0.0
    strategy: str | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("Symbol is required.")

        if self.price <= 0:
            raise ValueError("Price must be positive.")

        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                "Confidence must be between 0 and 1."
            )

        if self.stop_loss is not None:
            if self.stop_loss <= 0:
                raise ValueError(
                    "Stop loss must be positive."
                )

        if self.take_profit is not None:
            if self.take_profit <= 0:
                raise ValueError(
                    "Take profit must be positive."
                )

        if self.action == SignalAction.BUY:
            if (
                self.stop_loss is not None
                and self.stop_loss >= self.price
            ):
                raise ValueError(
                    "BUY stop loss must be below entry price."
                )

            if (
                self.take_profit is not None
                and self.take_profit <= self.price
            ):
                raise ValueError(
                    "BUY take profit must be above entry price."
                )

        if self.action == SignalAction.SELL:
            if (
                self.stop_loss is not None
                and self.stop_loss <= self.price
            ):
                raise ValueError(
                    "SELL stop loss must be above entry price."
                )

            if (
                self.take_profit is not None
                and self.take_profit >= self.price
            ):
                raise ValueError(
                    "SELL take profit must be below entry price."
                )
