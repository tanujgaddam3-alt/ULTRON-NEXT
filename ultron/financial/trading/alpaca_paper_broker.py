from __future__ import annotations

import os
from typing import Any
from uuid import UUID

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide as AlpacaOrderSide
from alpaca.trading.enums import TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from .broker import BrokerAdapter
from .models import Order, OrderSide, OrderStatus
from ultron.security.permission.rules import require_simulation_broker


class AlpacaPaperBroker(BrokerAdapter):
    """
    Alpaca paper-trading execution backend.

    This class is intentionally hard-locked to Alpaca paper trading.
    It cannot be switched to a live account through configuration.
    """

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
    ) -> None:
        super().__init__(mode="paper")
        api_key = api_key or os.getenv("ALPACA_API_KEY")
        secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY")

        if not api_key:
            raise RuntimeError(
                "ALPACA_API_KEY is required for Alpaca paper trading."
            )

        if not secret_key:
            raise RuntimeError(
                "ALPACA_SECRET_KEY is required for Alpaca paper trading."
            )

        self.client = TradingClient(
            api_key,
            secret_key,
            paper=True,
        )

        account = self.client.get_account()

        status = str(account.status)

        if "ACTIVE" not in status.upper():
            raise RuntimeError(
                f"Alpaca paper account is not active: {status}"
            )

        if bool(getattr(account, "trading_blocked", False)):
            raise RuntimeError(
                "Alpaca paper account trading is blocked."
            )

    def get_account_snapshot(
        self,
        prices: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        return self.snapshot(prices)

    def get_order_status(self, order_id: str) -> dict[str, Any]:
        try:
            order = self._get_order(order_id)
        except Exception:
            return {
                "order_id": order_id,
                "state": OrderStatus.UNKNOWN.value,
                "filled_quantity": 0.0,
                "remaining_quantity": 0.0,
                "cancelled": False,
                "valid": False,
            }

        filled_quantity = float(getattr(order, "filled_qty", 0.0) or 0.0)
        quantity = float(getattr(order, "qty", 0.0) or 0.0)
        state = str(
            getattr(order, "status", OrderStatus.UNKNOWN.value)
        ).lower()
        if state == "canceled":
            state = OrderStatus.CANCELLED.value

        return {
            "order_id": order_id,
            "state": state,
            "filled_quantity": filled_quantity,
            "remaining_quantity": max(0.0, quantity - filled_quantity),
            "cancelled": state == OrderStatus.CANCELLED.value,
            "valid": True,
        }

    def _get_order(self, order_id: str):
        """Resolve either Alpaca's broker ID or ULTRON's client ID."""
        lookup_id: Any = order_id
        try:
            lookup_id = UUID(str(order_id))
        except (TypeError, ValueError):
            pass
        try:
            return self.client.get_order_by_id(lookup_id)
        except Exception:
            return self.client.get_order_by_client_order_id(order_id)

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        require_simulation_broker(self, "Alpaca paper order cancellation")
        try:
            order = self._get_order(order_id)
            broker_id = getattr(order, "id", order_id)
            self.client.cancel_order_by_id(broker_id)
            return {
                "success": True,
                "state": OrderStatus.CANCELLED.value,
                "order_id": order_id,
                "cancelled": True,
            }
        except Exception as exc:
            return {
                "success": False,
                "state": OrderStatus.UNKNOWN.value,
                "reason": str(exc),
                "order_id": order_id,
                "cancelled": False,
            }

    def reconcile_order(self, order_id: str) -> dict[str, Any]:
        """Return a normalized, read-only lifecycle snapshot for an order."""
        status = self.get_order_status(order_id)
        if not status.get("valid", False):
            return {
                **status,
                "reconciled": False,
                "fills": [],
            }

        return {
            **status,
            "reconciled": True,
            "fills": [],
        }

    def snapshot(
        self,
        prices: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """
        Return ULTRON-compatible portfolio state from Alpaca.
        """

        account = self.client.get_account()
        positions = self.client.get_all_positions()

        normalized_positions: dict[str, dict[str, float]] = {}

        for position in positions:
            symbol = str(position.symbol).upper()

            normalized_positions[symbol] = {
                "quantity": float(position.qty),
                "average_price": float(position.avg_entry_price),
            }

        return {
            "cash": float(account.cash),
            "equity": float(account.equity),
            "positions": normalized_positions,
            "trade_count": 0,
            "buying_power": float(account.buying_power),
            "account_status": str(account.status),
            "broker": "alpaca",
            "mode": "paper",
        }

    def submit(
        self,
        order: Order,
    ) -> dict[str, Any]:
        """
        Submit a ULTRON order to Alpaca Paper Trading.

        Market orders are used intentionally here. The price in ULTRON's
        Order object is the analysis/reference price, not a guaranteed
        execution price.
        """
        require_simulation_broker(self, "Alpaca paper order submission")

        if not isinstance(order, Order):
            return {
                "success": False,
                "status": "rejected",
                "reason": "Invalid ULTRON order.",
            }

        symbol = order.symbol.strip().upper()

        if not symbol:
            return {
                "success": False,
                "status": "rejected",
                "reason": "Order symbol is required.",
                "order_id": order.id,
            }

        if order.quantity <= 0:
            return {
                "success": False,
                "status": "rejected",
                "reason": "Order quantity must be positive.",
                "order_id": order.id,
            }

        if order.side == OrderSide.BUY:
            side = AlpacaOrderSide.BUY
        elif order.side == OrderSide.SELL:
            side = AlpacaOrderSide.SELL
        else:
            return {
                "success": False,
                "status": "rejected",
                "reason": "Unsupported order side.",
                "order_id": order.id,
            }

        try:
            request = MarketOrderRequest(
                symbol=symbol,
                qty=float(order.quantity),
                side=side,
                time_in_force=TimeInForce.DAY,
                client_order_id=order.id,
            )

            submitted = self.client.submit_order(
                order_data=request,
            )

            status = str(submitted.status).lower()

            return {
                "success": True,
                "status": status,
                "order_id": str(submitted.id),
                "client_order_id": str(
                    getattr(submitted, "client_order_id", order.id)
                ),
                "symbol": symbol,
                "side": order.side.value,
                "quantity": float(order.quantity),
                "reference_price": float(order.price),
                "submitted_at": (
                    submitted.submitted_at.isoformat()
                    if submitted.submitted_at
                    else None
                ),
                "broker": "alpaca",
                "mode": "paper",
            }

        except Exception as exc:
            return {
                "success": False,
                "status": "rejected",
                "reason": str(exc),
                "order_id": order.id,
                "symbol": symbol,
                "side": order.side.value,
                "quantity": float(order.quantity),
                "broker": "alpaca",
                "mode": "paper",
            }
