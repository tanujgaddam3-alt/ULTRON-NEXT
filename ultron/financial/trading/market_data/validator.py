from __future__ import annotations

from datetime import UTC, datetime
from math import isfinite
from typing import Iterable

from .models import Candle


class MarketDataValidator:
    """
    Validates market data before it reaches indicators
    or trading intelligence.
    """

    def __init__(
        self,
        max_age_seconds: float = 60.0,
    ) -> None:
        if not isfinite(max_age_seconds) or max_age_seconds <= 0:
            raise ValueError(
                "max_age_seconds must be finite and positive."
            )

        self.max_age_seconds = max_age_seconds

    def validate_candle(
        self,
        candle: Candle,
        now: datetime | None = None,
    ) -> dict[str, object]:

        if not isinstance(candle, Candle):
            return {
                "valid": False,
                "reason": "Invalid candle.",
            }

        if not candle.is_complete:
            return {
                "valid": False,
                "reason": "Market candle is incomplete.",
            }

        current_time = now or datetime.now(UTC)

        age = (
            current_time - candle.timestamp
        ).total_seconds()

        if age < 0:
            return {
                "valid": False,
                "reason": "Candle timestamp is in the future.",
                "age_seconds": age,
            }

        if age > self.max_age_seconds:
            return {
                "valid": False,
                "reason": "Market candle is stale.",
                "age_seconds": age,
                "max_age_seconds": self.max_age_seconds,
            }

        return {
            "valid": True,
            "reason": "Market candle is valid.",
            "age_seconds": age,
        }

    def validate_series(
        self,
        candles: Iterable[Candle],
    ) -> dict[str, object]:

        candles = list(candles)

        if not candles:
            return {
                "valid": False,
                "reason": "Market data series is empty.",
            }

        if any(not candle.is_complete for candle in candles):
            return {
                "valid": False,
                "reason": "Market data series contains an incomplete candle.",
            }

        symbols = {
            candle.symbol
            for candle in candles
        }

        if len(symbols) != 1:
            return {
                "valid": False,
                "reason": "Market data contains multiple symbols.",
            }

        timestamps = [
            candle.timestamp
            for candle in candles
        ]

        if timestamps != sorted(timestamps):
            return {
                "valid": False,
                "reason": "Market data is not chronologically ordered.",
            }

        if len(set(timestamps)) != len(timestamps):
            return {
                "valid": False,
                "reason": "Duplicate candle timestamps detected.",
            }

        return {
            "valid": True,
            "reason": "Market data series is valid.",
            "symbol": candles[0].symbol,
            "count": len(candles),
            "start": timestamps[0],
            "end": timestamps[-1],
        }
