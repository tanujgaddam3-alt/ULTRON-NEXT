from __future__ import annotations

import os
from datetime import datetime, timezone
from math import isfinite
from typing import Any

import pandas as pd
import yfinance as yf

from .assets import AssetClass, classify_asset, normalize_symbol
from .models import MarketSnapshot, PricePoint


class MarketDataProvider:
    """
    Explicitly selected market-data provider.

    ULTRON_FINANCIAL_MARKET_PROVIDER=yahoo (default), india_yahoo, alpaca,
    coinbase, or groww.
    Alpaca mode is US-equities-only; Coinbase mode is crypto-only. Neither
    provider silently falls back to Yahoo.
    Company fundamentals remain the responsibility of CompanyDataProvider.
    """

    def __init__(
        self,
        provider: str | None = None,
        alpaca_adapter=None,
        coinbase_adapter=None,
        groww_adapter=None,
    ) -> None:
        selected = (provider or os.getenv("ULTRON_FINANCIAL_MARKET_PROVIDER", "yahoo")).strip().lower()
        if selected not in {"yahoo", "india_yahoo", "alpaca", "coinbase", "groww"}:
            raise ValueError(f"Unsupported market-data provider: {selected!r}")
        self.source = selected
        self._alpaca = alpaca_adapter
        self._coinbase = coinbase_adapter
        self._groww = groww_adapter
        if selected == "alpaca" and self._alpaca is None:
            # Perform symbol validation without forcing provider setup, to keep
            # fail-closed behavior for missing credentials while still rejecting
            # unsupported symbols before any live data access is attempted.
            from ultron.financial.trading.market_data.alpaca_adapter import AlpacaMarketAdapter
            self._alpaca = None
            self._alpaca_cls = AlpacaMarketAdapter
        else:
            self._alpaca_cls = None
        if selected == "coinbase" and self._coinbase is None:
            from ultron.financial.trading.market_data.coinbase_adapter import CoinbaseMarketAdapter
            self._coinbase = CoinbaseMarketAdapter()
        if selected == "groww" and self._groww is None:
            from ultron.financial.trading.market_data.groww_adapter import GrowwMarketAdapter
            self._groww = GrowwMarketAdapter()

    def _require_alpaca_adapter(self):
        if self.source != "alpaca":
            return None
        if self._alpaca is None:
            from ultron.financial.trading.market_data.alpaca_adapter import AlpacaMarketAdapter
            self._alpaca = AlpacaMarketAdapter()
        return self._alpaca

    def classify(self, symbol: str) -> AssetClass:
        if self.source == "coinbase":
            self._validate_symbol(symbol)
            return AssetClass.CRYPTO
        if self.source == "groww":
            self._validate_symbol(symbol)
            return AssetClass.EQUITY
        return classify_asset(symbol)

    def normalize(self, symbol: str) -> str:
        if self.source == "coinbase":
            return self._validate_symbol(symbol)
        if self.source == "groww":
            return self._groww.normalize_symbol(symbol)
        return normalize_symbol(symbol)

    def _validate_symbol(self, symbol: str) -> str:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("A market symbol is required.")
        value = symbol.strip().upper()
        if self.source == "alpaca":
            from ultron.financial.trading.market_data.alpaca_adapter import AlpacaMarketAdapter
            AlpacaMarketAdapter._validate_symbol(value)
        elif self.source == "coinbase":
            if "/" in value:
                parts = value.split("/")
                if len(parts) != 2 or not all(parts):
                    raise ValueError("Coinbase symbols must use BASE-QUOTE format.")
                value = "-".join(parts)
            self._coinbase._product_id(value)
        elif self.source == "groww":
            value = self._groww.normalize_symbol(value)
        return value

    def get_price(self, symbol: str) -> PricePoint:
        original = self._validate_symbol(symbol)
        if self.source == "alpaca":
            adapter = self._require_alpaca_adapter()
            snapshot = adapter.get_snapshot(original)
            return PricePoint(
                symbol=original, price=float(snapshot["price"]),
                timestamp=snapshot["timestamp"], asset_class=AssetClass.EQUITY,
                source="alpaca",
            )
        if self.source == "coinbase":
            snapshot = self._coinbase.get_snapshot(original)
            return PricePoint(
                symbol=original,
                price=float(snapshot["price"]),
                timestamp=snapshot["timestamp"],
                asset_class=AssetClass.CRYPTO,
                source="coinbase_advanced",
            )
        if self.source == "groww":
            snapshot = self._groww.get_snapshot(original)
            return PricePoint(
                symbol=original,
                price=float(snapshot["price"]),
                timestamp=snapshot["timestamp"],
                asset_class=AssetClass.EQUITY,
                source="groww_trade_api",
            )

        asset_class = self.classify(original)
        ticker = yf.Ticker(normalize_symbol(original, asset_class))
        data = ticker.history(period="1d", auto_adjust=False)
        if data.empty or "Close" not in data.columns:
            raise ValueError(f"No market data found for {original}.")
        close = data["Close"].dropna()
        if close.empty:
            raise ValueError(f"No valid closing price found for {original}.")
        price = float(close.iloc[-1])
        if self.source == "india_yahoo" and (
            not isfinite(price) or price <= 0
        ):
            raise ValueError("Indian market provider returned a non-finite or non-positive price.")
        return PricePoint(
            symbol=original, price=price,
            timestamp=self._timestamp_from_index(
                close.index[-1],
                require_timezone=self.source == "india_yahoo",
            ),
            asset_class=asset_class, source="yfinance",
        )

    def get_snapshot(self, symbol: str) -> MarketSnapshot:
        original = self._validate_symbol(symbol)
        if self.source == "alpaca":
            adapter = self._require_alpaca_adapter()
            snap = adapter.get_snapshot(original)
            return MarketSnapshot(
                symbol=original, current_price=float(snap["price"]),
                change_percent=0.0, timestamp=snap["timestamp"],
                asset_class=AssetClass.EQUITY, source="alpaca",
                freshness="current", data_age_seconds=float(snap["age_seconds"]),
                currency="USD",
            )
        if self.source == "coinbase":
            snap = self._coinbase.get_snapshot(original)
            history = self._coinbase.get_history(original, period="5d")
            closes = [float(candle["close"]) for candle in history]
            change = (
                (closes[-1] / closes[-2] - 1.0) * 100.0
                if len(closes) >= 2 and closes[-2] > 0
                else 0.0
            )
            age = float(snap["age_seconds"])
            return MarketSnapshot(
                symbol=original,
                current_price=float(snap["price"]),
                change_percent=change,
                timestamp=snap["timestamp"],
                asset_class=AssetClass.CRYPTO,
                source="coinbase_advanced",
                freshness=self._freshness(age),
                data_age_seconds=age,
                currency=original.split("-", 1)[1],
                volume=float(history[-1]["volume"]) if history else None,
            )
        if self.source == "groww":
            snap = self._groww.get_snapshot(original)
            age = float(snap["age_seconds"])
            return MarketSnapshot(
                symbol=original,
                current_price=float(snap["price"]),
                change_percent=float(snap["day_change_percent"]),
                timestamp=snap["timestamp"],
                asset_class=AssetClass.EQUITY,
                source="groww_trade_api",
                freshness=self._freshness(age),
                data_age_seconds=age,
                currency="INR",
                volume=snap["volume"],
                instrument_type="index",
                realtime_status="provider_documented_realtime",
            )

        asset_class = self.classify(original)
        ticker = yf.Ticker(normalize_symbol(original, asset_class))
        data = ticker.history(period="5d", auto_adjust=False)
        if data.empty or "Close" not in data.columns:
            raise ValueError(f"No market data found for {original}.")
        close = data["Close"].dropna()
        if close.empty:
            raise ValueError(f"No valid closing price found for {original}.")
        price = float(close.iloc[-1])
        if self.source == "india_yahoo" and (
            not isfinite(price) or price <= 0
        ):
            raise ValueError("Indian market provider returned a non-finite or non-positive price.")
        timestamp = self._timestamp_from_index(
            close.index[-1],
            require_timezone=self.source == "india_yahoo",
        )
        change = 0.0
        if len(close) >= 2 and float(close.iloc[-2]):
            change = (price / float(close.iloc[-2]) - 1.0) * 100.0
        volume = float(data["Volume"].dropna().iloc[-1]) if "Volume" in data.columns and not data["Volume"].dropna().empty else None
        age = (
            datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)
        ).total_seconds()
        if self.source == "india_yahoo" and age < 0:
            raise ValueError("Indian market provider returned a future-dated timestamp.")
        age = max(0.0, age)
        return MarketSnapshot(
            symbol=original, current_price=price, change_percent=change,
            timestamp=timestamp, asset_class=asset_class, source="yfinance",
            freshness=self._freshness(age), data_age_seconds=age,
            currency=self._currency(asset_class, original), volume=volume,
            instrument_type=self._instrument_type(original),
            realtime_status="unknown_or_delayed",
        )

    def get_history(self, symbol: str, period: str = "1y") -> Any:
        original = self._validate_symbol(symbol)
        if self.source == "alpaca":
            result = self._alpaca.get_history(original, period=period, timeframe="1Day")
            bars = result.get("bars", [])
            frame = pd.DataFrame(bars)
            if frame.empty:
                return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
            frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="raise")
            frame = frame.set_index("timestamp")
            frame.index.name = "Datetime"
            frame = frame.rename(columns={
                "open": "Open", "high": "High", "low": "Low",
                "close": "Close", "volume": "Volume",
            })
            # Do not conceal missing/malformed OHLCV values.
            required = ["Open", "High", "Low", "Close", "Volume"]
            missing = [column for column in required if column not in frame.columns]
            if missing:
                raise ValueError(f"Alpaca history missing required columns: {missing}")
            return frame.sort_index()[required]
        if self.source == "groww":
            candles = self._groww.get_history(original, period=period)
            if any(not candle.is_complete for candle in candles):
                raise ValueError(
                    "Groww historical data contains an incomplete daily candle."
                )
            records = [
                {
                    "timestamp": candle.timestamp,
                    "Open": candle.open,
                    "High": candle.high,
                    "Low": candle.low,
                    "Close": candle.close,
                    "Volume": candle.volume,
                }
                for candle in candles
            ]
            frame = pd.DataFrame(records)
            if frame.empty:
                return pd.DataFrame(
                    columns=["Open", "High", "Low", "Close", "Volume"]
                )
            frame = frame.set_index("timestamp")
            frame.index.name = "Datetime"
            return frame[["Open", "High", "Low", "Close", "Volume"]].sort_index()
        if self.source == "coinbase":
            candles = self._coinbase.get_history(original, period=period)
            frame = pd.DataFrame(candles)
            if frame.empty:
                return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
            frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="raise")
            frame = frame.set_index("timestamp")
            frame.index.name = "Datetime"
            frame = frame.rename(
                columns={
                    "open": "Open",
                    "high": "High",
                    "low": "Low",
                    "close": "Close",
                    "volume": "Volume",
                }
            )
            return frame[["Open", "High", "Low", "Close", "Volume"]].sort_index()
        asset_class = self.classify(original)
        ticker = yf.Ticker(normalize_symbol(original, asset_class))
        return ticker.history(period=period, auto_adjust=False)

    @staticmethod
    def _timestamp_from_index(
        value: Any,
        *,
        require_timezone: bool = False,
    ) -> datetime:
        if hasattr(value, "to_pydatetime"):
            value = value.to_pydatetime()
        if not isinstance(value, datetime):
            raise ValueError("Market provider returned an invalid timestamp.")
        if value.tzinfo is None:
            if require_timezone:
                raise ValueError(
                    "Indian market provider returned a timezone-naive timestamp."
                )
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _freshness(age_seconds: float) -> str:
        if age_seconds <= 15 * 60:
            return "current"
        if age_seconds <= 24 * 60 * 60:
            return "delayed"
        return "stale"

    @staticmethod
    def _currency(asset_class: AssetClass, symbol: str) -> str | None:
        normalized = symbol.strip().upper().replace(" ", "")
        if (
            normalized in {
                "NIFTY", "NIFTY50", "^NSEI", "SENSEX", "BSESENSEX", "^BSESN"
            }
            or normalized.endswith((".NS", ".BO"))
        ):
            return "INR"
        if asset_class is AssetClass.FOREX:
            value = symbol.replace("=X", "")
            if "/" in value:
                return value.split("/", 1)[1]
            if len(value) >= 6:
                return value[3:6]
        if asset_class is AssetClass.CRYPTO:
            if "/" in symbol:
                return symbol.split("/", 1)[1]
            if "-" in symbol:
                return symbol.split("-", 1)[1]
        return "USD"

    @staticmethod
    def _instrument_type(symbol: str) -> str:
        normalized = symbol.strip().upper().replace(" ", "")
        if normalized in {
            "NIFTY", "NIFTY50", "^NSEI", "SENSEX", "BSESENSEX", "^BSESN"
        }:
            return "index"
        return "equity"
