from __future__ import annotations

from typing import Any, Callable
from uuid import uuid4

from .broker import BrokerAdapter
from .market.models import Instrument, is_indian_index_symbol
from .models import Order, OrderSide
from .risk.position_sizer import PositionSizer
from .signal import TradingSignal, SignalAction
from ultron.security.permission.rules import require_simulation_broker


class PaperTradingEngine:
    """
    Applies the shared position-sizing and exposure controls before broker execution.

    Paper mode remains available through PaperBroker. Live broker use requires
    a caller-provided durable callback before the broker submission is attempted.
    """

    def __init__(
        self,
        broker: BrokerAdapter,
        max_position_percent: float = 25.0,
        risk_per_trade_percent: float = 1.0,
    ):
        if broker is None or not isinstance(broker, BrokerAdapter):
            raise ValueError("A BrokerAdapter is required.")

        if not 0 < max_position_percent <= 100:
            raise ValueError(
                "max_position_percent must be between 0 and 100."
            )

        self.broker = broker
        self.max_position_percent = max_position_percent
        self.position_sizer = PositionSizer(
            risk_per_trade_percent=risk_per_trade_percent
        )

    def execute(
        self,
        signal: TradingSignal,
        market_prices: dict[str, float] | None = None,
        *,
        order_id: str | None = None,
        before_submit: Callable[[Order], None] | None = None,
        instrument: Instrument | None = None,
    ) -> dict[str, Any]:

        require_simulation_broker(self.broker, "Financial order execution")

        if not isinstance(signal, TradingSignal):
            return self._reject("Invalid trading signal.")

        symbol = signal.symbol.strip().upper()
        price = float(signal.price)

        if is_indian_index_symbol(symbol):
            return self._reject(
                "Market indices are not directly orderable.",
                symbol,
            )
        if price <= 0:
            return self._reject("Signal price must be positive.")
        if instrument is not None:
            if instrument.symbol != symbol:
                return self._reject(
                    "Instrument metadata does not match the signal symbol.",
                    symbol,
                )
            try:
                instrument.validate_price(price)
            except (TypeError, ValueError) as exc:
                return self._reject(str(exc), symbol)
            if not instrument.orderable:
                return self._reject(
                    "Instrument is not orderable.",
                    symbol,
                )

        prices = market_prices or {symbol: price}

        if signal.action == SignalAction.HOLD:
            return {
                "success": True,
                "status": "held",
                "symbol": symbol,
                "action": "hold",
                "reason": signal.reason,
                "portfolio": self.broker.snapshot(prices),
            }

        snapshot = self.broker.snapshot(prices)
        equity = float(snapshot["equity"])

        if equity <= 0:
            return self._reject(
                "Portfolio equity must be positive.",
                symbol,
            )

        if signal.action == SignalAction.BUY:
            if signal.stop_loss is None:
                return self._reject(
                    "BUY signal requires a stop-loss for risk-based sizing.",
                    symbol,
                )

            sizing = self.position_sizer.calculate(
                equity=equity,
                entry_price=price,
                stop_loss=float(signal.stop_loss),
            )

            if not sizing.get("approved", False):
                return {
                    "success": False,
                    "status": "rejected",
                    "symbol": symbol,
                    "reason": sizing.get(
                        "reason",
                        "Position sizing rejected.",
                    ),
                    "risk": sizing,
                    "portfolio": snapshot,
                }

            risk_quantity = float(
                sizing["quantity"]
            )

            exposure_quantity = self._buy_quantity(
                snapshot,
                symbol,
                price,
            )

            quantity = min(
                risk_quantity,
                exposure_quantity,
            )

            if quantity <= 0:
                return {
                    "success": False,
                    "status": "rejected",
                    "symbol": symbol,
                    "reason": (
                        "Portfolio exposure limit "
                        "prevents this trade."
                    ),
                    "risk": sizing,
                    "portfolio": snapshot,
                }

            side = OrderSide.BUY

        elif signal.action == SignalAction.SELL:
            position = snapshot["positions"].get(symbol)

            if not position:
                return self._reject(
                    "No position available to sell.",
                    symbol,
                )

            quantity = float(
                position.get("available_quantity", position["quantity"])
            )

            if quantity <= 0:
                return self._reject(
                    "Position quantity is zero.",
                    symbol,
                )

            side = OrderSide.SELL

        else:
            return self._reject(
                "Unsupported signal action.",
                symbol,
            )

        if instrument is not None and not instrument.eligible_for_order(
            quantity,
            quantity * price,
        ):
            return self._reject(
                "Instrument quantity, notional, or metadata validation rejected the order.",
                symbol,
            )

        order = Order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            id=order_id or str(uuid4()),
        )

        if before_submit is not None:
            before_submit(order)
        result = self.broker.submit(order)

        response = {
            "success": bool(result.get("success", False)),
            "status": result.get("status", "unknown"),
            "symbol": symbol,
            "action": signal.action.value,
            "quantity": quantity,
            "price": price,
            "signal": signal,
            "order": result,
            "portfolio": self.broker.snapshot(prices),
        }

        if signal.action == SignalAction.BUY:
            response["risk"] = sizing
            response["risk"]["requested_quantity"] = (
                float(sizing["quantity"])
            )
            response["risk"]["final_quantity"] = quantity

        return response

    def monitor_exit(
        self,
        signal: TradingSignal,
        market_price: float,
    ) -> dict[str, Any]:

        if not isinstance(signal, TradingSignal):
            return self._reject("Invalid trading signal.")

        price = float(market_price)

        if price <= 0:
            return self._reject("Market price must be positive.")

        symbol = signal.symbol.strip().upper()
        position = self.broker.snapshot(
            {symbol: price}
        )["positions"].get(symbol)

        if not position:
            return {
                "success": True,
                "status": "no_position",
                "symbol": symbol,
                "action": "none",
            }

        if (
            signal.stop_loss is not None
            and price <= signal.stop_loss
        ):
            exit_signal = self._exit_signal(
                signal,
                price,
                "stop_loss",
            )
            result = self.execute(
                exit_signal,
                {symbol: price},
            )
            result["exit_reason"] = "stop_loss"
            return result

        if (
            signal.take_profit is not None
            and price >= signal.take_profit
        ):
            exit_signal = self._exit_signal(
                signal,
                price,
                "take_profit",
            )
            result = self.execute(
                exit_signal,
                {symbol: price},
            )
            result["exit_reason"] = "take_profit"
            return result

        return {
            "success": True,
            "status": "holding",
            "symbol": symbol,
            "action": "hold",
            "price": price,
        }

    def _buy_quantity(
        self,
        snapshot: dict[str, Any],
        symbol: str,
        price: float,
    ) -> float:

        equity = float(snapshot["equity"])

        max_value = equity * (
            self.max_position_percent / 100.0
        )

        position = snapshot["positions"].get(
            symbol,
            {},
        )

        existing_quantity = float(
            position.get("quantity", 0)
        )

        existing_value = (
            existing_quantity * price
        )

        remaining_value = max_value - existing_value

        if remaining_value <= 0:
            return 0.0

        cash = float(snapshot["cash"])

        # Leave enough cash for the order itself.
        remaining_value = min(
            remaining_value,
            cash,
        )

        return max(
            0.0,
            remaining_value / price,
        )

    @staticmethod
    def _exit_signal(
        signal: TradingSignal,
        price: float,
        reason: str,
    ) -> TradingSignal:

        return TradingSignal(
            symbol=signal.symbol,
            action=SignalAction.SELL,
            price=price,
            stop_loss=None,
            take_profit=None,
            confidence=signal.confidence,
            strategy=signal.strategy,
            reason=f"Automatic {reason} exit.",
        )

    @staticmethod
    def _reject(
        reason: str,
        symbol: str | None = None,
    ) -> dict[str, Any]:

        result = {
            "success": False,
            "status": "rejected",
            "reason": reason,
        }

        if symbol:
            result["symbol"] = symbol

        return result
