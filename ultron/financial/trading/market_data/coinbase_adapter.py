from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Any

from ultron.financial.trading.coinbase_advanced import (
    CoinbaseAdvancedClient,
    CoinbaseAPIError,
)
from ultron.financial.trading.market.interface import MarketAdapter
from ultron.financial.trading.market.models import Instrument, MarketType


class CoinbaseMarketDataError(CoinbaseAPIError):
    """Invalid, stale, or unavailable Coinbase spot market data."""


class CoinbaseMarketAdapter(MarketAdapter):
    """Timestamped Coinbase Advanced Trade spot market data."""

    def __init__(
        self,
        client: CoinbaseAdvancedClient | None = None,
        *,
        max_quote_age_seconds: float = 30.0,
    ) -> None:
        if not isfinite(max_quote_age_seconds) or max_quote_age_seconds <= 0:
            raise ValueError("max_quote_age_seconds must be finite and positive.")
        self.client = client or CoinbaseAdvancedClient()
        self.max_quote_age_seconds = float(max_quote_age_seconds)

    @property
    def market_type(self) -> MarketType:
        return MarketType.CRYPTO

    @staticmethod
    def _product_id(symbol: str) -> str:
        if not isinstance(symbol, str):
            raise ValueError("Coinbase product symbol must be a string.")
        product_id = symbol.strip().upper()
        parts = product_id.split("-")
        if (
            len(parts) != 2
            or not all(part.isascii() and part.isalnum() for part in parts)
        ):
            raise ValueError("Coinbase spot product must use BASE-QUOTE format.")
        return product_id

    def get_snapshot(self, symbol: str) -> dict[str, Any]:
        product_id = self._product_id(symbol)
        response = self.client.get_product_book(product_id)
        book = response.get("pricebook")
        if not isinstance(book, dict) or book.get("product_id") != product_id:
            raise CoinbaseMarketDataError(
                f"Coinbase returned no order book for {product_id}."
            )
        bids = book.get("bids")
        asks = book.get("asks")
        if (
            not isinstance(bids, list)
            or not bids
            or not isinstance(bids[0], dict)
            or not isinstance(asks, list)
            or not asks
            or not isinstance(asks[0], dict)
        ):
            raise CoinbaseMarketDataError(
                f"Coinbase returned an incomplete order book for {product_id}."
            )
        try:
            bid = Decimal(str(bids[0]["price"]))
            ask = Decimal(str(asks[0]["price"]))
        except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
            raise CoinbaseMarketDataError(
                f"Coinbase returned invalid best prices for {product_id}."
            ) from exc
        if (
            not bid.is_finite()
            or not ask.is_finite()
            or bid <= 0
            or ask <= 0
            or ask < bid
        ):
            raise CoinbaseMarketDataError(
                f"Coinbase returned an invalid bid/ask spread for {product_id}."
            )

        source_timestamp = book.get("time")
        if not isinstance(source_timestamp, str):
            raise CoinbaseMarketDataError(
                f"Coinbase order book for {product_id} has no source timestamp."
            )
        try:
            timestamp = datetime.fromisoformat(
                source_timestamp.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise CoinbaseMarketDataError(
                f"Coinbase order book for {product_id} has an invalid source timestamp."
            ) from exc
        if timestamp.tzinfo is None:
            raise CoinbaseMarketDataError(
                f"Coinbase order book for {product_id} has a timezone-naive timestamp."
            )
        timestamp = timestamp.astimezone(UTC)
        age = (datetime.now(UTC) - timestamp).total_seconds()
        if age < 0 or age > self.max_quote_age_seconds:
            raise CoinbaseMarketDataError(
                f"Coinbase order book for {product_id} is future-dated or stale."
            )

        return {
            "symbol": product_id,
            "price": float((bid + ask) / Decimal(2)),
            "bid": float(bid),
            "ask": float(ask),
            "timestamp": timestamp,
            "age_seconds": age,
            "provider": "coinbase_advanced",
            "feed": "public_rest_product_book",
            "market_type": MarketType.CRYPTO.value,
        }

    def get_price(self, symbol: str) -> float:
        return float(self.get_snapshot(symbol)["price"])

    def get_history(
        self,
        symbol: str,
        period: str = "1y",
        *,
        granularity: str = "ONE_DAY",
    ) -> list[dict[str, Any]]:
        product_id = self._product_id(symbol)
        periods = {
            "1d": timedelta(days=1),
            "5d": timedelta(days=5),
            "1mo": timedelta(days=30),
            "3mo": timedelta(days=90),
            "6mo": timedelta(days=180),
            "1y": timedelta(days=365),
        }
        if period not in periods:
            raise ValueError(
                f"Unsupported Coinbase history period {period!r}; "
                f"choose one of {', '.join(periods)}."
            )
        granularities = {
            "ONE_MINUTE": 60,
            "FIVE_MINUTE": 300,
            "FIFTEEN_MINUTE": 900,
            "THIRTY_MINUTE": 1800,
            "ONE_HOUR": 3600,
            "TWO_HOUR": 7200,
            "FOUR_HOUR": 14400,
            "SIX_HOUR": 21600,
            "ONE_DAY": 86400,
        }
        if granularity not in granularities:
            raise ValueError(
                f"Unsupported Coinbase candle granularity {granularity!r}."
            )
        interval_seconds = granularities[granularity]
        end = datetime.now(UTC)
        start = max(
            end - periods[period],
            end - timedelta(seconds=interval_seconds * 350),
        )
        response = self.client.get_public_candles(
            product_id,
            start=int(start.timestamp()),
            end=int(end.timestamp()),
            granularity=granularity,
            limit=350,
        )
        raw_candles = response.get("candles")
        if not isinstance(raw_candles, list):
            raise CoinbaseMarketDataError(
                f"Coinbase returned no historical candles for {product_id}."
            )
        records: list[dict[str, Any]] = []
        seen_timestamps: set[int] = set()
        for candle in raw_candles:
            if not isinstance(candle, dict):
                raise CoinbaseMarketDataError(
                    f"Coinbase returned an invalid candle for {product_id}."
                )
            try:
                timestamp_seconds = int(candle["start"])
                values = {
                    name: float(candle[name])
                    for name in ("open", "high", "low", "close", "volume")
                }
            except (KeyError, TypeError, ValueError) as exc:
                raise CoinbaseMarketDataError(
                    f"Coinbase returned incomplete candle data for {product_id}."
                ) from exc
            if timestamp_seconds in seen_timestamps:
                raise CoinbaseMarketDataError(
                    f"Coinbase returned duplicate candles for {product_id}."
                )
            seen_timestamps.add(timestamp_seconds)
            timestamp = datetime.fromtimestamp(timestamp_seconds, UTC)
            if timestamp < start or timestamp >= end:
                continue
            if timestamp + timedelta(seconds=interval_seconds) > end:
                continue
            if (
                not all(isfinite(value) for value in values.values())
                or min(values["open"], values["high"], values["low"], values["close"]) <= 0
                or values["volume"] < 0
                or values["high"] < max(values["open"], values["close"])
                or values["low"] > min(values["open"], values["close"])
            ):
                raise CoinbaseMarketDataError(
                    f"Coinbase returned invalid candle values for {product_id}."
                )
            records.append(
                {"timestamp": timestamp, **values}
            )
        records.sort(key=lambda record: record["timestamp"])
        if not records:
            raise CoinbaseMarketDataError(
                f"Coinbase returned no complete candles for {product_id}."
            )
        return records

    def get_instrument(self, symbol: str) -> Instrument:
        product_id = self._product_id(symbol)
        product = self.client.get_public_product(product_id)
        if product.get("product_id") != product_id:
            raise CoinbaseMarketDataError(
                f"Coinbase product metadata did not match {product_id}."
            )
        base = str(product.get("base_currency_id") or product_id.split("-")[0])
        quote = str(product.get("quote_currency_id") or product_id.split("-")[1])
        try:
            base_increment = float(product["base_increment"])
            quote_increment = float(product["quote_increment"])
            min_quantity = float(product["base_min_size"])
            max_quantity = float(product["base_max_size"])
            min_notional = float(product["quote_min_size"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CoinbaseMarketDataError(
                f"Coinbase product metadata is incomplete for {product_id}."
            ) from exc
        if (
            not all(
                isfinite(value) and value > 0
                for value in (
                    base_increment,
                    quote_increment,
                    min_quantity,
                    max_quantity,
                )
            )
            or not isfinite(min_notional)
            or min_notional < 0
        ):
            raise CoinbaseMarketDataError(
                f"Coinbase product metadata has invalid size rules for {product_id}."
            )
        enabled = (
            product.get("is_disabled") is not True
            and product.get("trading_disabled") is not True
            and product.get("view_only") is not True
            and product.get("cancel_only") is not True
        )
        market_orders_enabled = enabled and product.get("limit_only") is not True
        return Instrument(
            symbol=product_id,
            market=MarketType.CRYPTO,
            base_currency=base,
            quote_currency=quote,
            quantity_step=base_increment,
            tick_size=quote_increment,
            min_quantity=min_quantity,
            min_notional=min_notional,
            provider_symbol=product_id,
            asset_class="crypto_spot",
            trading_status="active" if enabled else "disabled",
            market_session="24/7",
            provider_capabilities={
                "spot": True,
                "market_orders": market_orders_enabled,
                "marketable_limit_ioc": enabled and product.get("post_only") is not True,
                "fractional_quantity": base_increment < 1,
                "short_selling": False,
            },
            is_shortable=False,
            is_margin_eligible=False,
        )

    def is_market_open(self, symbol: str) -> bool:
        self._product_id(symbol)
        return True

    def get_capabilities(self, symbol: str) -> dict[str, Any]:
        instrument = self.get_instrument(symbol)
        return {
            "market_type": instrument.market.value,
            "provider": "coinbase_advanced",
            "product_id": instrument.symbol,
            "market_hours": "24/7",
            "supports_spot": True,
            "supports_market_orders": instrument.provider_capabilities["market_orders"],
            "supports_marketable_limit_ioc": instrument.provider_capabilities[
                "marketable_limit_ioc"
            ],
            "supports_fractional_quantity": (
                instrument.provider_capabilities["fractional_quantity"]
            ),
            "supports_short_selling": False,
            "trading_status": instrument.trading_status,
        }
