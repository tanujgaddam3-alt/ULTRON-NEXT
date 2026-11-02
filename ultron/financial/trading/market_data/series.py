from __future__ import annotations

from typing import Sequence

from .models import Candle


class MarketSeries:
    """
    Read-only utilities for candle collections.
    """

    def __init__(
        self,
        candles: Sequence[Candle],
    ) -> None:

        self.candles = tuple(candles)

        if not self.candles:
            raise ValueError(
                "At least one candle is required."
            )

    @property
    def symbol(self) -> str:
        return self.candles[0].symbol

    @property
    def closes(self) -> list[float]:
        return [
            candle.close
            for candle in self.candles
        ]

    @property
    def highs(self) -> list[float]:
        return [
            candle.high
            for candle in self.candles
        ]

    @property
    def lows(self) -> list[float]:
        return [
            candle.low
            for candle in self.candles
        ]

    @property
    def volumes(self) -> list[float | None]:
        return [
            candle.volume
            for candle in self.candles
        ]

    @property
    def latest(self) -> Candle:
        return self.candles[-1]

    def returns(self) -> list[float]:
        closes = self.closes

        return [
            (closes[i] / closes[i - 1]) - 1.0
            for i in range(1, len(closes))
        ]

    def percentage_change(self) -> float:
        first = self.candles[0].close
        last = self.candles[-1].close

        return (
            (last - first) / first
        ) * 100.0
