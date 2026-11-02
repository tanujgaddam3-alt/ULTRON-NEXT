from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence


class MarketRegime(str, Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGE_BOUND = "range_bound"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    TRANSITION = "transition"


@dataclass(frozen=True)
class RegimeResult:
    regime: MarketRegime
    confidence: float
    trend_strength: float
    volatility: float
    momentum: float
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "regime": self.regime.value,
            "confidence": self.confidence,
            "trend_strength": self.trend_strength,
            "volatility": self.volatility,
            "momentum": self.momentum,
            "reason": self.reason,
        }


class MarketRegimeEngine:
    """
    Classifies the current market regime from price behaviour.

    This is a deterministic analytical layer.
    It does not execute trades and does not invent market data.
    """

    def __init__(
        self,
        trend_threshold: float = 0.02,
        high_volatility_threshold: float = 0.04,
        low_volatility_threshold: float = 0.01,
    ) -> None:

        if trend_threshold <= 0:
            raise ValueError(
                "trend_threshold must be positive."
            )

        if high_volatility_threshold <= 0:
            raise ValueError(
                "high_volatility_threshold must be positive."
            )

        if low_volatility_threshold < 0:
            raise ValueError(
                "low_volatility_threshold cannot be negative."
            )

        if (
            low_volatility_threshold
            >= high_volatility_threshold
        ):
            raise ValueError(
                "Low volatility threshold must be below "
                "high volatility threshold."
            )

        self.trend_threshold = trend_threshold
        self.high_volatility_threshold = (
            high_volatility_threshold
        )
        self.low_volatility_threshold = (
            low_volatility_threshold
        )

    def analyze(
        self,
        closes: Sequence[float],
    ) -> dict[str, object]:

        prices = [float(value) for value in closes]

        if len(prices) < 3:
            raise ValueError(
                "At least 3 closing prices are required."
            )

        if any(price <= 0 for price in prices):
            raise ValueError(
                "Closing prices must be positive."
            )

        returns = [
            (prices[i] / prices[i - 1]) - 1.0
            for i in range(1, len(prices))
        ]

        total_return = (
            prices[-1] / prices[0]
        ) - 1.0

        average_return = sum(returns) / len(returns)

        variance = sum(
            (value - average_return) ** 2
            for value in returns
        ) / len(returns)

        volatility = variance ** 0.5

        trend_strength = abs(total_return)

        positive = sum(
            1 for value in returns
            if value > 0
        )

        negative = sum(
            1 for value in returns
            if value < 0
        )

        momentum = average_return

        if (
            trend_strength >= self.trend_threshold
            and positive > negative
        ):
            regime = MarketRegime.TRENDING_UP
            reason = (
                "Price movement shows positive directional "
                "trend strength."
            )

        elif (
            trend_strength >= self.trend_threshold
            and negative > positive
        ):
            regime = MarketRegime.TRENDING_DOWN
            reason = (
                "Price movement shows negative directional "
                "trend strength."
            )

        elif volatility >= self.high_volatility_threshold:
            regime = MarketRegime.HIGH_VOLATILITY
            reason = (
                "Price volatility is above the "
                "high-volatility threshold."
            )

        elif volatility <= self.low_volatility_threshold:
            regime = MarketRegime.LOW_VOLATILITY
            reason = (
                "Price volatility is below the "
                "low-volatility threshold."
            )
        elif positive > 0 and negative > 0:
            regime = MarketRegime.RANGE_BOUND
            reason = (
                "Price movement lacks sufficient directional "
                "trend strength."
            )

        else:
            regime = MarketRegime.TRANSITION
            reason = (
                "Market conditions do not clearly match "
                "another regime."
            )

        confidence = self._confidence(
            trend_strength=trend_strength,
            volatility=volatility,
            positive=positive,
            negative=negative,
        )

        result = RegimeResult(
            regime=regime,
            confidence=confidence,
            trend_strength=trend_strength,
            volatility=volatility,
            momentum=momentum,
            reason=reason,
        )

        return result.as_dict()

    @staticmethod
    def _confidence(
        trend_strength: float,
        volatility: float,
        positive: int,
        negative: int,
    ) -> float:

        total = positive + negative

        directional_consistency = (
            abs(positive - negative) / total
            if total
            else 0.0
        )

        raw = (
            min(trend_strength / 0.10, 1.0) * 0.5
            + directional_consistency * 0.3
            + min(volatility / 0.05, 1.0) * 0.2
        )

        return max(
            0.0,
            min(1.0, raw),
        )
