from __future__ import annotations

from typing import Any

from ultron.financial.trading.signal import (
    SignalAction,
    TradingSignal,
)


class DecisionSignalAdapter:
    """Converts an approved trading decision into an execution signal."""

    @staticmethod
    def convert(
        decision: dict[str, Any],
    ) -> TradingSignal:

        if not isinstance(decision, dict):
            raise ValueError("Decision must be a dictionary.")

        if not decision.get("approved", False):
            raise ValueError(
                "Only approved decisions can become trading signals."
            )

        action = str(
            decision.get("action", "hold")
        ).strip().lower()

        try:
            signal_action = SignalAction(action)
        except ValueError as exc:
            raise ValueError(
                f"Unsupported decision action: {action}"
            ) from exc

        symbol = str(
            decision.get("symbol", "")
        ).strip().upper()

        price = float(
            decision.get("entry_price", 0.0)
        )

        confidence = float(
            decision.get("confidence", 0.0)
        )

        stop_loss = decision.get("stop_loss")
        take_profit = decision.get("take_profit")

        if stop_loss is None and decision.get("exits"):
            stop_loss = decision["exits"].get("stop_loss")

        if take_profit is None and decision.get("exits"):
            take_profit = decision["exits"].get("take_profit")

        reasons = decision.get("reasons", [])

        reason = (
            " ".join(str(item) for item in reasons)
            if reasons
            else str(decision.get("reason", ""))
        )

        return TradingSignal(
            symbol=symbol,
            action=signal_action,
            price=price,
            stop_loss=(
                float(stop_loss)
                if stop_loss is not None
                else None
            ),
            take_profit=(
                float(take_profit)
                if take_profit is not None
                else None
            ),
            confidence=confidence,
            strategy="ULTRON_DECISION_ENGINE",
            reason=reason,
        )
