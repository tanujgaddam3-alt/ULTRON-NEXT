from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..controller import FinancialTradingController
from ..intelligence.decision import TradingDecisionEngine
from ..intelligence.decision_adapter import DecisionSignalAdapter


class PaperTradingOrchestrator:
    """
    Coordinates market intelligence and paper execution.

    Market data -> decision -> signal -> controller -> paper broker.
    """

    def __init__(
        self,
        controller: FinancialTradingController,
        decision_engine: TradingDecisionEngine | None = None,
    ) -> None:
        if controller is None:
            raise ValueError("Trading controller is required.")

        self.controller = controller
        self.decision_engine = (
            decision_engine or TradingDecisionEngine()
        )

    def snapshot(
        self,
        prices: dict[str, float],
    ) -> dict[str, Any]:
        if not isinstance(prices, dict):
            raise ValueError("Prices must be a dictionary.")

        normalized = {
            str(symbol).strip().upper(): float(price)
            for symbol, price in prices.items()
        }

        if any(price <= 0 for price in normalized.values()):
            raise ValueError("Prices must be positive.")

        return self.controller.trading_engine.broker.snapshot(
            normalized
        )

    def run(
        self,
        equity: float,
        symbol: str,
        candles_by_timeframe: Mapping[
            str,
            Sequence[float],
        ],
    ) -> dict[str, Any]:

        symbol = symbol.strip().upper()

        if not symbol:
            return {
                "success": False,
                "stage": "validation",
                "reason": "Symbol is required.",
            }

        if equity <= 0:
            return {
                "success": False,
                "stage": "validation",
                "reason": "Equity must be positive.",
            }

        if not candles_by_timeframe:
            return {
                "success": False,
                "stage": "market_data",
                "reason": "At least one timeframe is required.",
            }

        market_result = self.controller.market_data.get_current_price(
            symbol
        ) if self.controller.market_data else None

        if not market_result or not market_result.get("valid"):
            return {
                "success": False,
                "stage": "market_data",
                "reason": (
                    market_result.get("reason")
                    if market_result
                    else "Market data service is not configured."
                ),
            }

        entry_price = float(market_result["price"])

        try:
            decision = self.decision_engine.evaluate(
                equity=equity,
                symbol=symbol,
                candles_by_timeframe=candles_by_timeframe,
                entry_price=entry_price,
            )
        except (TypeError, ValueError) as exc:
            return {
                "success": False,
                "stage": "decision",
                "reason": str(exc),
            }

        if not decision.get("approved", False):
            return {
                "success": True,
                "status": "held",
                "stage": "decision",
                "decision": decision,
                "market_data": market_result,
            }

        try:
            signal = DecisionSignalAdapter.convert(
                decision
            )
        except (TypeError, ValueError) as exc:
            return {
                "success": False,
                "stage": "signal",
                "reason": str(exc),
                "decision": decision,
                "market_data": market_result,
            }

        analysis = {
            "symbol": signal.symbol,
            "decision": {
                "decision": (
                    "consider"
                    if signal.action.value != "hold"
                    else "avoid"
                ),
                "confidence": signal.confidence,
            },
            "reasoning": {
                "confidence": signal.confidence,
            },
            "risk": {
                "risk": {
                    "risk_level": "low",
                }
            },
            "strategies": (
                [signal.strategy]
                if signal.strategy
                else []
            ),
        }

        execution = self.controller.execute(
            analysis=analysis,
            price=entry_price,
            price_timestamp=market_result["timestamp"],
        )

        return {
            "success": bool(execution.get("success", False)),
            "status": "executed",
            "market_data": market_result,
            "decision": decision,
            "signal": signal,
            "execution": execution,
        }
