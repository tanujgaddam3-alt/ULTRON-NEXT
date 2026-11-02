from __future__ import annotations

import threading
from math import isfinite
from typing import Any

from .broker import BrokerAdapter
from .models import (
    BrokerOrderRecord,
    Order,
    OrderSide,
    OrderStatus,
    Portfolio,
    Trade,
)
from .market.models import is_indian_index_symbol


class PaperBroker(BrokerAdapter):
    """
    Simulation-only broker.

    Executes orders against supplied prices and updates
    a paper portfolio. It never connects to a real broker.
    """

    def __init__(
        self,
        initial_capital: float = 100_000.0,
        commission_rate: float = 0.001,
    ) -> None:
        super().__init__(mode="simulation")
        if not isfinite(initial_capital) or initial_capital <= 0:
            raise ValueError(
                "Initial capital must be finite and positive."
            )

        if not isfinite(commission_rate) or commission_rate < 0:
            raise ValueError(
                "Commission rate must be finite and non-negative."
            )

        self.commission_rate = commission_rate
        self.portfolio = Portfolio.create(
            initial_capital
        )
        self._orders: dict[str, BrokerOrderRecord] = {}
        self._lock = threading.RLock()

    def _record_order(self, order: Order) -> BrokerOrderRecord:
        record = BrokerOrderRecord(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            remaining_quantity=order.quantity,
            filled_quantity=0.0,
            price=order.price,
            status=OrderStatus.SUBMITTED.value,
            client_order_id=order.id,
        )
        self._orders[order.id] = record
        return record

    @staticmethod
    def _status_payload(status: dict[str, Any]) -> dict[str, Any]:
        """
        Operational identifiers are not part of the financial semantics
        of a broker execution result. They are kept in the canonical
        top-level order response, but stripped from nested status payloads
        to preserve deterministic backtest comparisons.
        """
        payload = dict(status)
        payload.pop("order_id", None)
        return payload

    def get_order_status(self, order_id: str) -> dict[str, Any]:
        with self._lock:
            order = self._orders.get(order_id)
            if order is None:
                return {
                    "order_id": order_id,
                    "state": OrderStatus.UNKNOWN.value,
                    "filled_quantity": 0.0,
                    "remaining_quantity": 0.0,
                    "cancelled": False,
                    "valid": False,
                }

            return {
                "order_id": order_id,
                "symbol": order.symbol,
                "state": order.status,
                "filled_quantity": float(order.filled_quantity),
                "remaining_quantity": float(order.remaining_quantity),
                "cancelled": bool(order.cancelled),
                "valid": True,
            }

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        with self._lock:
            order = self._orders.get(order_id)
            if order is None:
                return {
                    "success": False,
                    "state": OrderStatus.UNKNOWN.value,
                    "reason": "Order is not tracked by this broker.",
                    "order_id": order_id,
                }
            if order.status == OrderStatus.FILLED.value:
                return {
                    "success": False,
                    "state": OrderStatus.FILLED.value,
                    "reason": "A filled order cannot be cancelled.",
                    "order_id": order_id,
                }

            order.cancel()
            return {
                "success": True,
                "state": order.status,
                "order_id": order_id,
                "symbol": order.symbol,
                "cancelled": True,
            }

    def reconcile_order(self, order_id: str) -> dict[str, Any]:
        with self._lock:
            order = self._orders.get(order_id)
            if order is None:
                return {
                    "order_id": order_id,
                    "state": OrderStatus.UNKNOWN.value,
                    "filled_quantity": 0.0,
                    "remaining_quantity": 0.0,
                    "cancelled": False,
                    "reconciled": False,
                }

            status = order.status
            if order.filled_quantity > 0 and order.remaining_quantity > 0:
                status = OrderStatus.PARTIALLY_FILLED.value
            elif order.filled_quantity >= order.quantity:
                status = OrderStatus.FILLED.value

            return {
                "order_id": order_id,
                "symbol": order.symbol,
                "state": status,
                "filled_quantity": float(order.filled_quantity),
                "remaining_quantity": float(order.remaining_quantity),
                "cancelled": bool(order.cancelled),
                "reconciled": True,
                "fills": [
                    {
                        "fill_id": fill.fill_id,
                        "order_id": fill.order_id,
                        "symbol": fill.symbol,
                        "side": fill.side.value,
                        "quantity": float(fill.quantity),
                        "price": float(fill.price),
                        "fee": float(fill.fee),
                        "timestamp": fill.timestamp.isoformat(),
                    }
                    for fill in order.fills
                ],
            }

    def get_account_snapshot(self, prices: dict[str, float] | None = None) -> dict[str, Any]:
        return self.snapshot(prices or {})

    def submit(
        self,
        order: Order,
    ) -> dict[str, Any]:
        """
        Execute a paper order immediately at its supplied price.
        """
        with self._lock:
            if is_indian_index_symbol(order.symbol):
                return {
                    "success": False,
                    "status": OrderStatus.REJECTED.value,
                    "reason": "Market indices are not directly orderable.",
                    "order_id": order.id,
                }
            value = order.quantity * order.price
            fees = value * self.commission_rate
            if not isfinite(value) or not isfinite(fees):
                return {
                    "success": False,
                    "status": OrderStatus.REJECTED.value,
                    "reason": "Order value and fees must be finite.",
                    "order_id": order.id,
                }
            realized_pnl = 0.0

            if order.side == OrderSide.BUY:
                total_cost = value + fees

                if total_cost > self.portfolio.cash:
                    return {
                        "success": False,
                        "status": OrderStatus.REJECTED.value,
                        "reason": "Insufficient paper cash.",
                        "order_id": order.id,
                    }

                self.portfolio.cash -= total_cost

                position = self.portfolio.positions.get(
                    order.symbol
                )

                if position is None:
                    position = self.portfolio.positions[
                        order.symbol
                    ] = self._new_position(order)
                else:
                    old_value = (
                        position.quantity
                        * position.average_price
                    )
                    position.quantity += order.quantity
                    position.average_price = (
                        old_value + value
                    ) / position.quantity

            elif order.side == OrderSide.SELL:
                position = self.portfolio.positions.get(
                    order.symbol
                )

                if (
                    position is None
                    or position.quantity < order.quantity
                ):
                    return {
                        "success": False,
                        "status": OrderStatus.REJECTED.value,
                        "reason": "Insufficient paper position.",
                        "order_id": order.id,
                    }

                realized_pnl = (
                    order.price - position.average_price
                ) * order.quantity - fees

                self.portfolio.cash += value - fees

                position.quantity -= order.quantity

                if position.quantity == 0:
                    del self.portfolio.positions[
                        order.symbol
                    ]

            else:
                return {
                    "success": False,
                    "status": OrderStatus.REJECTED.value,
                    "reason": "Unsupported order side.",
                    "order_id": order.id,
                }

            trade = Trade(
                order_id=order.id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                price=order.price,
                fees=fees,
                realized_pnl=realized_pnl,
            )

            self.portfolio.trades.append(trade)

            record = self._record_order(order)
            record.apply_fill(order.quantity, order.price, fee=fees, fill_id=order.id)
            record.status = OrderStatus.FILLED.value
            record.remaining_quantity = 0.0

            return {
                "success": True,
                "status": OrderStatus.FILLED.value,
                "order_id": order.id,
                "symbol": order.symbol,
                "side": order.side.value,
                "quantity": order.quantity,
                "price": order.price,
                "fees": fees,
                "cash": self.portfolio.cash,
                "broker_status": self._status_payload(
                    self.get_order_status(order.id)
                ),
            }

    @staticmethod
    def _new_position(order: Order):
        from .models import Position

        return Position(
            symbol=order.symbol,
            quantity=order.quantity,
            average_price=order.price,
        )

    def snapshot(
        self,
        prices: dict[str, float],
    ) -> dict[str, Any]:
        equity = self.portfolio.equity(prices)

        return {
            "cash": self.portfolio.cash,
            "equity": equity,
            "positions": {
                symbol: {
                    "quantity": position.quantity,
                    "average_price": position.average_price,
                }
                for symbol, position
                in self.portfolio.positions.items()
            },
            "trade_count": len(
                self.portfolio.trades
            ),
        }
