from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StrategyScoreConfig:
    minimum_score: float = 60.0

    def __post_init__(self) -> None:
        if not 0 <= self.minimum_score <= 100:
            raise ValueError(
                "minimum_score must be between 0 and 100."
            )


class StrategyScoreEngine:
    """
    Produces an explainable 0-100 strategy quality score.

    The score combines:
        - directional signal
        - timeframe alignment
        - confidence
        - momentum
        - market regime
    """

    def __init__(
        self,
        config: StrategyScoreConfig | None = None,
    ) -> None:
        self.config = config or StrategyScoreConfig()

    def evaluate(
        self,
        direction: str,
        confidence: float,
        alignment: float,
        momentum: float,
        regime: str,
    ) -> dict[str, Any]:

        direction = direction.strip().lower()
        regime = regime.strip().lower()

        if direction not in {"bullish", "bearish", "neutral"}:
            return self._reject(
                "Direction must be bullish, bearish, or neutral."
            )

        if not 0 <= confidence <= 1:
            return self._reject(
                "Confidence must be between 0 and 1."
            )

        if not 0 <= alignment <= 1:
            return self._reject(
                "Alignment must be between 0 and 1."
            )

        # Normalize momentum into a bounded strength value.
        momentum_strength = min(
            abs(float(momentum)) * 100.0,
            100.0,
        )

        confidence_score = confidence * 100.0
        alignment_score = alignment * 100.0

        score = (
            confidence_score * 0.35
            + alignment_score * 0.30
            + momentum_strength * 0.20
        )

        regime_bonus = 0.0

        if regime in {"trending_up", "trending_down"}:
            regime_bonus = 10.0

        elif regime == "range_bound":
            regime_bonus = 2.0

        elif regime == "high_volatility":
            regime_bonus = -5.0

        score += regime_bonus
        score = max(0.0, min(score, 100.0))

        if direction == "neutral":
            score *= 0.5

        if score >= 80:
            strength = "strong"
        elif score >= self.config.minimum_score:
            strength = "moderate"
        else:
            strength = "weak"

        approved = (
            score >= self.config.minimum_score
            and direction != "neutral"
        )

        reasons: list[str] = []

        if confidence >= 0.75:
            reasons.append("High model confidence.")
        elif confidence < 0.50:
            reasons.append("Low model confidence.")

        if alignment >= 0.70:
            reasons.append("Strong timeframe alignment.")
        elif alignment < 0.50:
            reasons.append("Weak timeframe alignment.")

        if abs(momentum) >= 0.01:
            reasons.append("Meaningful directional momentum.")
        else:
            reasons.append("Weak directional momentum.")

        if regime in {"trending_up", "trending_down"}:
            reasons.append("Market is trending.")

        elif regime == "range_bound":
            reasons.append("Market is range-bound.")

        elif regime == "high_volatility":
            reasons.append(
                "High volatility increases execution risk."
            )

        return {
            "approved": approved,
            "score": score,
            "strength": strength,
            "direction": direction,
            "confidence": confidence,
            "alignment": alignment,
            "momentum": momentum,
            "regime": regime,
            "regime_bonus": regime_bonus,
            "reasons": reasons,
        }

    @staticmethod
    def _reject(reason: str) -> dict[str, Any]:
        return {
            "approved": False,
            "score": 0.0,
            "strength": "invalid",
            "reason": reason,
        }
