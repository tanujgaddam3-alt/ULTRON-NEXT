from __future__ import annotations

from typing import Mapping, Sequence

from .regime import MarketRegime, MarketRegimeEngine


class MultiTimeframeAnalyzer:
    """
    Evaluates market regimes across multiple timeframes.

    This is an analytical component only.
    It does not generate or execute orders.
    """

    DEFAULT_WEIGHTS: dict[str, float] = {
        "1m": 0.05,
        "5m": 0.10,
        "15m": 0.15,
        "1h": 0.25,
        "4h": 0.25,
        "1d": 0.20,
    }

    def __init__(
        self,
        regime_engine: MarketRegimeEngine | None = None,
        weights: Mapping[str, float] | None = None,
    ) -> None:

        self.regime_engine = (
            regime_engine or MarketRegimeEngine()
        )

        raw_weights = dict(
            weights or self.DEFAULT_WEIGHTS
        )

        if not raw_weights:
            raise ValueError(
                "At least one timeframe weight is required."
            )

        normalized: dict[str, float] = {}

        for timeframe, weight in raw_weights.items():
            name = self._normalize_timeframe(timeframe)

            if weight <= 0:
                raise ValueError(
                    "Timeframe weights must be positive."
                )

            normalized[name] = float(weight)

        total = sum(normalized.values())

        if total <= 0:
            raise ValueError(
                "Timeframe weights must have a positive total."
            )

        self.weights = {
            timeframe: weight / total
            for timeframe, weight in normalized.items()
        }

    def analyze(
        self,
        candles_by_timeframe: Mapping[
            str,
            Sequence[float],
        ],
    ) -> dict[str, object]:

        if not candles_by_timeframe:
            raise ValueError(
                "At least one timeframe is required."
            )

        regimes: dict[str, dict[str, object]] = {}

        for timeframe, closes in candles_by_timeframe.items():
            normalized = self._normalize_timeframe(
                timeframe
            )

            result = self.regime_engine.analyze(
                closes
            )

            regimes[normalized] = result

        weighted_direction = 0.0
        weighted_confidence = 0.0

        direction_map = {
            MarketRegime.TRENDING_UP.value: 1.0,
            MarketRegime.TRENDING_DOWN.value: -1.0,
            MarketRegime.RANGE_BOUND.value: 0.0,
            MarketRegime.HIGH_VOLATILITY.value: 0.0,
            MarketRegime.LOW_VOLATILITY.value: 0.0,
            MarketRegime.TRANSITION.value: 0.0,
        }

        total_weight = 0.0

        for timeframe, result in regimes.items():
            weight = self.weights.get(
                timeframe,
                1.0,
            )

            regime = str(result["regime"])
            confidence = float(
                result["confidence"]
            )

            direction = direction_map.get(
                regime,
                0.0,
            )

            weighted_direction += (
                direction * weight * confidence
            )

            weighted_confidence += (
                confidence * weight
            )

            total_weight += weight

        if total_weight <= 0:
            raise ValueError(
                "No valid timeframe weights available."
            )

        weighted_direction /= total_weight
        weighted_confidence /= total_weight

        if weighted_direction > 0.15:
            overall_direction = "bullish"

        elif weighted_direction < -0.15:
            overall_direction = "bearish"

        else:
            overall_direction = "neutral"

        directional_regimes = [
            result["regime"]
            for result in regimes.values()
            if result["regime"]
            in (
                MarketRegime.TRENDING_UP.value,
                MarketRegime.TRENDING_DOWN.value,
            )
        ]

        if directional_regimes:
            bullish = sum(
                regime
                == MarketRegime.TRENDING_UP.value
                for regime in directional_regimes
            )

            bearish = sum(
                regime
                == MarketRegime.TRENDING_DOWN.value
                for regime in directional_regimes
            )

            alignment = (
                max(bullish, bearish)
                / len(directional_regimes)
            )

        else:
            alignment = 0.0

        conflicting = (
            MarketRegime.TRENDING_UP.value
            in directional_regimes
            and MarketRegime.TRENDING_DOWN.value
            in directional_regimes
        )

        if conflicting:
            assessment = "conflicting"

        elif alignment >= 0.75:
            assessment = "strong_alignment"

        elif alignment >= 0.50:
            assessment = "moderate_alignment"

        else:
            assessment = "weak_alignment"

        return {
            "overall_direction": overall_direction,
            "alignment": alignment,
            "weighted_direction": weighted_direction,
            "confidence": weighted_confidence,
            "assessment": assessment,
            "conflicting": conflicting,
            "regimes": regimes,
        }

    @staticmethod
    def _normalize_timeframe(
        timeframe: str,
    ) -> str:

        value = str(timeframe).strip().lower()

        aliases = {
            "1min": "1m",
            "5min": "5m",
            "15min": "15m",
            "30min": "30m",
            "60min": "1h",
            "60m": "1h",
            "1hour": "1h",
            "4hour": "4h",
            "4hr": "4h",
            "1day": "1d",
            "daily": "1d",
        }

        return aliases.get(value, value)
