from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite


@dataclass(frozen=True)
class Candle:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    is_complete: bool = True
    source: str = "unspecified"
    realtime_status: str = "unknown"

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()

        if not symbol:
            raise ValueError("Symbol is required.")

        if self.timestamp.tzinfo is None:
            raise ValueError("Timestamp must be timezone-aware.")

        if not isfinite(self.open) or self.open <= 0:
            raise ValueError("Open price must be finite and positive.")

        if not isfinite(self.high) or self.high <= 0:
            raise ValueError("High price must be finite and positive.")

        if not isfinite(self.low) or self.low <= 0:
            raise ValueError("Low price must be finite and positive.")

        if not isfinite(self.close) or self.close <= 0:
            raise ValueError("Close price must be finite and positive.")

        if self.volume is not None and (
            not isfinite(self.volume) or self.volume < 0
        ):
            raise ValueError("Volume must be finite and non-negative.")

        if not isinstance(self.is_complete, bool):
            raise ValueError("Candle completion state must be boolean.")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("Candle data source is required.")
        if not isinstance(self.realtime_status, str) or not self.realtime_status.strip():
            raise ValueError("Candle real-time status is required.")

        if self.high < max(self.open, self.close):
            raise ValueError(
                "High price must be >= open and close."
            )

        if self.low > min(self.open, self.close):
            raise ValueError(
                "Low price must be <= open and close."
            )

        object.__setattr__(self, "symbol", symbol)

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def typical_price(self) -> float:
        return (
            self.high + self.low + self.close
        ) / 3.0


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    price: float
    timestamp: datetime

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()

        if not symbol:
            raise ValueError("Symbol is required.")

        if not isfinite(self.price) or self.price <= 0:
            raise ValueError("Price must be finite and positive.")

        if self.timestamp.tzinfo is None:
            raise ValueError(
                "Timestamp must be timezone-aware."
            )

        object.__setattr__(self, "symbol", symbol)

    @classmethod
    def now(
        cls,
        symbol: str,
        price: float,
    ) -> "MarketSnapshot":
        return cls(
            symbol=symbol,
            price=price,
            timestamp=datetime.now(UTC),
        )
