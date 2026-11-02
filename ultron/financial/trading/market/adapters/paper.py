from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ultron.financial.trading.models import (
    Order,
    Position,
)
from ultron.financial.trading.paper_broker import PaperBroker

from ..interface import MarketAdapter
from ..models import (
    INDIAN_INDEX_IDENTITIES,
    Instrument,
    InstrumentType,
    MarketType,
)


class PaperMarketAdapter(MarketAdapter):
    """
    Paper implementation of the common MarketAdapter interface.

    Uses ULTRON's existing PaperBroker and never connects
    to a real exchange or broker.
    """

    def __init__(
        self,
        broker: PaperBroker,
        prices: dict[str, float] | None = None,
    ) -> None:
        if broker is None:
            raise ValueError("PaperBroker is required.")

        self.broker = broker
        self.prices = prices or {}
        self._connected = False

    @property
    def market_type(self) -> MarketType:
        return MarketType.STOCK

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def get_instrument(
        self,
        symbol: str,
    ) -> Instrument:
        self._require_connection()

        symbol = symbol.strip().upper()

        if not symbol:
            raise ValueError("Symbol is required.")

        index = INDIAN_INDEX_IDENTITIES.get(symbol)
        if index:
            canonical, provider_symbol, exchange = index
            return Instrument(
                symbol=canonical,
                market=MarketType.STOCK,
                base_currency="INR",
                quote_currency="INR",
                quantity_step=None,
                tick_size=None,
                contract_size=None,
                min_quantity=None,
                min_notional=None,
                provider_symbol=provider_symbol,
                provider_instrument_id=provider_symbol,
                exchange=exchange,
                instrument_type=InstrumentType.INDEX,
                orderable=False,
            )

        return Instrument(
            symbol=symbol,
            market=MarketType.STOCK,
            base_currency="USD",
            quote_currency="USD",
        )

    def get_price(
        self,
        symbol: str,
    ) -> float:
        self._require_connection()

        symbol = symbol.strip().upper()

        if not symbol:
            raise ValueError("Symbol is required.")

        price = self.prices.get(symbol)

        if price is None:
            raise ValueError(
                f"No paper price available for {symbol}."
            )

        price = float(price)

        if price <= 0:
            raise ValueError(
                f"Invalid paper price for {symbol}."
            )

        return price

    def get_snapshot(self, symbol: str) -> dict[str, Any]:
        return {
            "symbol": symbol.strip().upper(),
            "price": self.get_price(symbol),
            "timestamp": datetime.now(UTC),
        }

    def is_market_open(
        self,
        symbol: str,
    ) -> bool:
        self._require_connection()

        symbol = symbol.strip().upper()

        if not symbol:
            raise ValueError("Symbol is required.")

        return True

    def get_capabilities(
        self,
        symbol: str,
    ) -> dict[str, Any]:
        self._require_connection()

        symbol = symbol.strip().upper()

        if not symbol:
            raise ValueError("Symbol is required.")

        return {
            "market_type": self.market_type.value,
            "paper": True,
            "supports_market_orders": True,
            "supports_limit_orders": False,
            "supports_fractional_quantity": False,
            "supports_short_selling": False,
        }

    def get_positions(self) -> dict[str, Position]:
        self._require_connection()

        return dict(
            self.broker.portfolio.positions
        )

    def place_order(
        self,
        order: Order,
    ) -> dict[str, Any]:
        self._require_connection()

        return self.broker.submit(order)

    def _require_connection(self) -> None:
        if not self._connected:
            raise RuntimeError(
                "Paper market adapter is not connected."
            )
