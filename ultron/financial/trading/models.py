from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from math import isfinite
from uuid import uuid4


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    PENDING = "pending"
    CREATED = "created"
    VALIDATED = "validated"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Order:
    symbol: str
    side: OrderSide
    quantity: float
    price: float
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(
        default_factory=lambda: datetime.now(UTC)
    )

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("Symbol is required.")

        if not isfinite(self.quantity) or self.quantity <= 0:
            raise ValueError("Quantity must be finite and positive.")

        if not isfinite(self.price) or self.price <= 0:
            raise ValueError("Price must be finite and positive.")


@dataclass
class BrokerFill:
    fill_id: str = field(default_factory=lambda: str(uuid4()))
    order_id: str = ""
    symbol: str = ""
    side: OrderSide = OrderSide.BUY
    quantity: float = 0.0
    price: float = 0.0
    fee: float = 0.0
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(UTC)
    )


@dataclass
class BrokerOrderRecord:
    order_id: str
    symbol: str
    side: OrderSide
    quantity: float
    remaining_quantity: float
    filled_quantity: float = 0.0
    status: str = OrderStatus.CREATED.value
    price: float = 0.0
    created_at: datetime = field(
        default_factory=lambda: datetime.now(UTC)
    )
    updated_at: datetime = field(
        default_factory=lambda: datetime.now(UTC)
    )
    client_order_id: str | None = None
    cancelled: bool = False
    fills: list[BrokerFill] = field(default_factory=list)

    def apply_fill(
        self,
        quantity: float,
        price: float,
        *,
        fee: float = 0.0,
        fill_id: str | None = None,
    ) -> BrokerFill:
        fill_amount = float(quantity)
        fill_price = float(price)
        fill_fee = float(fee)
        if not isfinite(fill_amount) or fill_amount <= 0:
            raise ValueError("Fill quantity must be finite and positive.")
        if not isfinite(fill_price) or fill_price <= 0:
            raise ValueError("Fill price must be finite and positive.")
        if not isfinite(fill_fee) or fill_fee < 0:
            raise ValueError("Fill fee must be finite and non-negative.")

        remaining = max(0.0, self.quantity - self.filled_quantity)
        accepted_quantity = min(fill_amount, remaining)

        fill = BrokerFill(
            fill_id=fill_id or str(uuid4()),
            order_id=self.order_id,
            symbol=self.symbol,
            side=self.side,
            quantity=accepted_quantity,
            price=fill_price,
            fee=fill_fee,
            timestamp=datetime.now(UTC),
        )
        self.fills.append(fill)
        self.filled_quantity += accepted_quantity
        self.remaining_quantity = max(0.0, self.quantity - self.filled_quantity)
        self.updated_at = datetime.now(UTC)

        if self.filled_quantity >= self.quantity:
            self.status = OrderStatus.FILLED.value
        else:
            self.status = OrderStatus.PARTIALLY_FILLED.value

        return fill

    def cancel(self) -> None:
        self.cancelled = True
        self.updated_at = datetime.now(UTC)
        if self.status not in {
            OrderStatus.FILLED.value,
            OrderStatus.REJECTED.value,
        }:
            self.status = OrderStatus.CANCELLED.value

    @property
    def is_complete(self) -> bool:
        return self.status == OrderStatus.FILLED.value


@dataclass
class Position:
    symbol: str
    quantity: float = 0.0
    average_price: float = 0.0


@dataclass(frozen=True)
class Trade:
    order_id: str
    symbol: str
    side: OrderSide
    quantity: float
    price: float
    fees: float
    realized_pnl: float = 0.0
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(UTC)
    )

    @property
    def gross_value(self) -> float:
        return self.quantity * self.price


@dataclass
class Portfolio:
    initial_capital: float
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)

    @classmethod
    def create(cls, capital: float) -> "Portfolio":
        if not isfinite(capital) or capital <= 0:
            raise ValueError("Initial capital must be finite and positive.")

        return cls(
            initial_capital=capital,
            cash=capital,
        )

    @property
    def realized_pnl(self) -> float:
        return sum(
            trade.realized_pnl
            for trade in self.trades
        )

    def equity(self, prices: dict[str, float]) -> float:
        if not isfinite(self.cash):
            raise ValueError("Portfolio cash must be finite.")

        for symbol, price in prices.items():
            if not isfinite(price) or price <= 0:
                raise ValueError(
                    f"Price for {symbol} must be finite and positive."
                )

        value = self.cash

        for symbol, position in self.positions.items():
            price = prices.get(
                symbol,
                position.average_price,
            )
            if (
                not isfinite(position.quantity)
                or position.quantity <= 0
                or not isfinite(price)
                or price <= 0
            ):
                raise ValueError(
                    f"Position and mark price for {symbol} must be finite and positive."
                )

            value += position.quantity * price
            if not isfinite(value):
                raise ValueError("Portfolio equity must remain finite.")

        return value
