from __future__ import annotations

from typing import Any, Mapping, Sequence

from .multi_timeframe import MultiTimeframeAnalyzer
from .strategy_score import StrategyScoreEngine
from ..indicators import TechnicalIndicatorEngine
from ..risk.dynamic_exits import DynamicExitEngine
from ..risk.position_sizer import PositionSizer


class TradingDecisionEngine:
    """
    Combines market intelligence into one explainable trading decision.

    This engine does not execute orders.

    Pipeline:
        market data
            -> technical indicators
            -> multi-timeframe analysis
            -> strategy score
            -> dynamic exits
            -> position sizing
            -> final decision
    """

    def __init__(
        self,
        indicator_engine: TechnicalIndicatorEngine | None = None,
        timeframe_analyzer: MultiTimeframeAnalyzer | None = None,
        strategy_engine: StrategyScoreEngine | None = None,
        exit_engine: DynamicExitEngine | None = None,
        position_sizer: PositionSizer | None = None,
    ) -> None:
        self.indicator_engine = (
            indicator_engine or TechnicalIndicatorEngine()
        )
        self.timeframe_analyzer = (
            timeframe_analyzer or MultiTimeframeAnalyzer()
        )
        self.strategy_engine = (
            strategy_engine or StrategyScoreEngine()
        )
        self.exit_engine = (
            exit_engine or DynamicExitEngine()
        )
        self.position_sizer = (
            position_sizer or PositionSizer()
        )

    def evaluate(
        self,
        equity: float,
        symbol: str,
        candles_by_timeframe: Mapping[str, Sequence[float]],
        entry_price: float,
    ) -> dict[str, Any]:

        if equity <= 0:
            return self._reject("Equity must be positive.")

        symbol = symbol.strip().upper()

        if not symbol:
            return self._reject("Symbol cannot be empty.")

        if entry_price <= 0:
            return self._reject("Entry price must be positive.")

        if not candles_by_timeframe:
            return self._reject(
                "At least one timeframe is required."
            )

        # ---------------------------------------------------------
        # Multi-timeframe intelligence
        # ---------------------------------------------------------

        timeframe = self.timeframe_analyzer.analyze(
            candles_by_timeframe
        )

        direction = str(
            timeframe["overall_direction"]
        )

        alignment = float(
            timeframe["alignment"]
        )

        confidence = float(
            timeframe["confidence"]
        )

        # ---------------------------------------------------------
        # Technical indicators
        # ---------------------------------------------------------

        primary_timeframe = self._select_primary_timeframe(
            candles_by_timeframe
        )

        closes = list(
            candles_by_timeframe[primary_timeframe]
        )

        indicators = self.indicator_engine.snapshot(
            closes
        )

        momentum = self._extract_momentum(
            indicators,
            closes,
        )

        # ---------------------------------------------------------
        # Determine regime
        # ---------------------------------------------------------

        primary_regime = str(
            timeframe["regimes"][primary_timeframe]["regime"]
        )

        # ---------------------------------------------------------
        # Strategy quality
        # ---------------------------------------------------------

        strategy = self.strategy_engine.evaluate(
            direction=direction,
            confidence=confidence,
            alignment=alignment,
            momentum=momentum,
            regime=primary_regime,
        )

        if not strategy.get("approved", False):
            return {
                "approved": False,
                "action": "hold",
                "symbol": symbol,
                "direction": direction,
                "entry_price": entry_price,
                "strategy": strategy,
                "timeframe": timeframe,
                "indicators": indicators,
                "reason": strategy.get(
                    "reason",
                    "Strategy conditions were not approved.",
                ),
            }

        # ---------------------------------------------------------
        # Dynamic exits
        # ---------------------------------------------------------

        trade_direction = (
            "long"
            if direction == "bullish"
            else "short"
        )

        atr = float(
            indicators.get("atr_14", 0.0)
        )

        exits = self.exit_engine.calculate(
            entry_price=entry_price,
            atr=atr,
            direction=trade_direction,
        )

        if not exits.get("approved", False):
            return {
                "approved": False,
                "action": "hold",
                "symbol": symbol,
                "direction": direction,
                "entry_price": entry_price,
                "strategy": strategy,
                "timeframe": timeframe,
                "indicators": indicators,
                "exits": exits,
                "reason": exits.get(
                    "reason",
                    "Dynamic exit calculation failed.",
                ),
            }

        # ---------------------------------------------------------
        # Position sizing
        # ---------------------------------------------------------

        stop_loss = float(exits["stop_loss"])

        if trade_direction == "long":
            sizing_stop = stop_loss
        else:
            # PositionSizer currently expects a long-style stop.
            # Convert the short risk distance into an equivalent
            # long-style calculation without changing the actual
            # short stop stored in the decision.
            sizing_stop = (
                entry_price
                - (stop_loss - entry_price)
            )

        sizing = self.position_sizer.calculate(
            equity=equity,
            entry_price=entry_price,
            stop_loss=sizing_stop,
        )

        if not sizing.get("approved", False):
            return {
                "approved": False,
                "action": "hold",
                "symbol": symbol,
                "direction": direction,
                "entry_price": entry_price,
                "strategy": strategy,
                "timeframe": timeframe,
                "indicators": indicators,
                "exits": exits,
                "sizing": sizing,
                "reason": sizing.get(
                    "reason",
                    "Position sizing failed.",
                ),
            }

        # ---------------------------------------------------------
        # Final decision
        # ---------------------------------------------------------

        action = (
            "buy"
            if direction == "bullish"
            else "sell"
        )

        return {
            "approved": True,
            "action": action,
            "symbol": symbol,
            "direction": direction,
            "confidence": confidence,
            "alignment": alignment,
            "entry_price": entry_price,
            "strategy_score": strategy["score"],
            "strategy_strength": strategy["strength"],
            "regime": primary_regime,
            "momentum": momentum,
            "indicators": indicators,
            "timeframe": timeframe,
            "exits": exits,
            "sizing": sizing,
            "quantity": sizing["quantity"],
            "stop_loss": exits["stop_loss"],
            "take_profit": exits["take_profit"],
            "reasons": strategy.get("reasons", []),
        }

    @staticmethod
    def _select_primary_timeframe(
        candles_by_timeframe: Mapping[
            str,
            Sequence[float],
        ],
    ) -> str:

        priority = (
            "1h",
            "15m",
            "5m",
            "4h",
            "1d",
            "1m",
        )

        normalized = {
            str(key).strip().lower(): key
            for key in candles_by_timeframe
        }

        for timeframe in priority:
            if timeframe in normalized:
                return normalized[timeframe]

        return next(iter(candles_by_timeframe))

    @staticmethod
    def _extract_momentum(
        indicators: Mapping[str, Any],
        closes: Sequence[float],
    ) -> float:

        if len(closes) >= 2:
            previous = float(closes[-2])
            current = float(closes[-1])

            if previous > 0:
                return (
                    current - previous
                ) / previous

        return 0.0

    @staticmethod
    def _reject(reason: str) -> dict[str, Any]:
        return {
            "approved": False,
            "action": "hold",
            "reason": reason,
        }
