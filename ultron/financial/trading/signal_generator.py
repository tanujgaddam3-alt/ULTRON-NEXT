from __future__ import annotations

from typing import Any

from .signal import SignalAction, TradingSignal


class FinancialSignalGenerator:
    """
    Converts financial intelligence and technical indicators
    into paper-trading signals.

    This component does not execute trades.
    """

    def generate(
        self,
        analysis: dict[str, Any],
        price: float,
        indicators: dict[str, Any] | None = None,
    ) -> TradingSignal:

        if not isinstance(analysis, dict):
            raise ValueError(
                "Financial analysis result must be a dictionary."
            )

        if price <= 0:
            raise ValueError(
                "Current price must be positive."
            )

        symbol = str(
            analysis.get("symbol", "")
        ).strip().upper()

        if not symbol:
            raise ValueError(
                "Financial analysis must contain a symbol."
            )

        decision = analysis.get("decision", {})
        reasoning = analysis.get("reasoning", {})
        risk = analysis.get("risk", {})

        if not isinstance(decision, dict):
            decision = {}

        if not isinstance(reasoning, dict):
            reasoning = {}

        if not isinstance(risk, dict):
            risk = {}

        decision_name = str(
            decision.get("decision", "avoid")
        ).lower()

        confidence = float(
            decision.get(
                "confidence",
                reasoning.get("confidence", 0.0),
            )
            or 0.0
        )

        risk_data = risk.get("risk", risk)

        if not isinstance(risk_data, dict):
            risk_data = {}

        risk_level = str(
            risk_data.get("risk_level", "high")
        ).lower()

        strategies = analysis.get("strategies", [])

        if not isinstance(strategies, list):
            strategies = []

        strategy = (
            str(strategies[0])
            if strategies
            else None
        )

        if (
            decision_name != "consider"
            or confidence < 0.75
            or risk_level == "high"
        ):
            return TradingSignal(
                symbol=symbol,
                action=SignalAction.HOLD,
                price=price,
                confidence=confidence,
                strategy=strategy,
                reason=(
                    f"Trading blocked: decision={decision_name}, "
                    f"confidence={confidence:.2f}, "
                    f"risk={risk_level}."
                ),
            )

        atr_value = None

        if isinstance(indicators, dict):
            raw_atr = indicators.get("atr_14")

            if raw_atr is not None:
                try:
                    atr_value = float(raw_atr)
                except (TypeError, ValueError):
                    atr_value = None

        if atr_value is not None and atr_value > 0:
            stop_loss = round(
                price - (2.0 * atr_value),
                4,
            )

            take_profit = round(
                price + (3.0 * atr_value),
                4,
            )

            if stop_loss <= 0:
                stop_loss = round(price * 0.96, 4)

            reason = (
                "Financial decision and technical indicators "
                "passed the trading gate: "
                f"decision={decision_name}, "
                f"confidence={confidence:.2f}, "
                f"risk={risk_level}, "
                f"ATR={atr_value:.4f}."
            )

        else:
            stop_loss = round(price * 0.96, 4)
            take_profit = round(price * 1.08, 4)

            reason = (
                "Financial decision passed the trading gate: "
                f"decision={decision_name}, "
                f"confidence={confidence:.2f}, "
                f"risk={risk_level}."
            )

        return TradingSignal(
            symbol=symbol,
            action=SignalAction.BUY,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            strategy=strategy,
            reason=reason,
        )
