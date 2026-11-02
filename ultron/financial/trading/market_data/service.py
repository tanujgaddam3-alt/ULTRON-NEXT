from __future__ import annotations

from datetime import UTC, datetime
from math import isfinite
from typing import Any

from ..market.interface import MarketAdapter
from .models import Candle
from .series import MarketSeries
from .validator import MarketDataValidator


class MarketDataService:
    """
    Validates and exposes market data through a market adapter.

    The service keeps market-specific mechanics out of the
    intelligence and trading layers.
    """

    def __init__(
        self,
        adapter: MarketAdapter,
        validator: MarketDataValidator | None = None,
    ) -> None:
        if adapter is None:
            raise ValueError("Market adapter is required.")

        self.adapter = adapter
        self.validator = (
            validator
            or MarketDataValidator()
        )

    def get_current_price(
        self,
        symbol: str,
    ) -> dict[str, Any]:
        symbol = symbol.strip().upper()
        if not symbol:
            return {"valid": False, "reason": "Symbol is required."}
        if not self.adapter.is_market_open(symbol):
            return {"valid": False, "reason": "Market is closed.", "symbol": symbol}

        get_snapshot = getattr(self.adapter, "get_snapshot", None)
        if callable(get_snapshot):
            snapshot = get_snapshot(symbol)
            timestamp = snapshot.get("timestamp")
            try:
                price = float(snapshot.get("price", 0.0))
            except (TypeError, ValueError):
                price = 0.0
            if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
                return {"valid": False, "symbol": symbol, "reason": "Missing or timezone-naive source timestamp."}
            age = (datetime.now(UTC) - timestamp.astimezone(UTC)).total_seconds()
            max_age = float(getattr(self.adapter, "max_quote_age_seconds", 30.0))
            if not isfinite(max_age) or max_age <= 0:
                return {"valid": False, "symbol": symbol, "reason": "Market quote age limit must be finite and positive."}
            if age < 0 or age > max_age:
                return {"valid": False, "symbol": symbol, "reason": "Market quote is future-dated or stale.",
                        "age_seconds": age, "max_age_seconds": max_age}
            if not isfinite(price) or price <= 0:
                return {"valid": False, "symbol": symbol, "reason": "Provider returned a non-finite or non-positive price."}
            result = {
                **snapshot,
                "valid": True,
                "symbol": symbol,
                "price": price,
                "timestamp": timestamp.astimezone(UTC),
                "age_seconds": age,
            }
            has_bid = "bid" in snapshot
            has_ask = "ask" in snapshot
            if has_bid != has_ask:
                return {
                    "valid": False,
                    "symbol": symbol,
                    "reason": "Provider returned an incomplete bid/ask quote.",
                }
            if has_bid:
                try:
                    bid = float(snapshot["bid"])
                    ask = float(snapshot["ask"])
                except (TypeError, ValueError):
                    return {
                        "valid": False,
                        "symbol": symbol,
                        "reason": "Provider returned invalid bid/ask prices.",
                    }
                if (
                    not isfinite(bid)
                    or not isfinite(ask)
                    or bid <= 0
                    or ask <= 0
                    or ask < bid
                ):
                    return {
                        "valid": False,
                        "symbol": symbol,
                        "reason": "Provider returned an invalid bid/ask spread.",
                    }
                midpoint = (bid + ask) / 2.0
                result.update(
                    {
                        "bid": bid,
                        "ask": ask,
                        "midpoint": midpoint,
                        "spread": ask - bid,
                        "spread_bps": (ask - bid) / midpoint * 10_000,
                    }
                )
            return result

        return {
            "valid": False,
            "symbol": symbol,
            "reason": "A timestamped market snapshot is required.",
        }

    def validate_series(
        self,
        candles: list[Candle],
    ) -> dict[str, Any]:
        result = self.validator.validate_series(candles)

        if not result.get("valid", False):
            return result

        series = MarketSeries(candles)

        return {
            **result,
            "series": series,
            "latest_price": series.latest.close,
        }
