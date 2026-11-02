from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

import yfinance as yf

from ..interface import MarketAdapter
from ..models import (
    INDIAN_INDEX_IDENTITIES,
    Instrument,
    InstrumentType,
    MarketType,
)
from ...market_data.models import Candle


class YahooMarketAdapter(MarketAdapter):
    """
    Read-only market-data adapter backed by Yahoo Finance.

    Supports:
    - Equities: AAPL, MSFT, RELIANCE.NS
    - Crypto: BTC/USD, ETH/USD
    - Forex: USD/INR, EUR/USD
    - Indian indices: NIFTY50 / ^NSEI, SENSEX / ^BSESN

    Yahoo Finance does not provide this adapter with verified exchange
    licensing or a guaranteed real-time feed. Timestamps are provider bar
    timestamps; latency is reported as unknown and never asserted real-time.
    """

    INDIA_TZ = ZoneInfo("Asia/Kolkata")
    _INDIAN_INDICES = INDIAN_INDEX_IDENTITIES
    _INTERVALS = {
        "1m": timedelta(minutes=1),
        "2m": timedelta(minutes=2),
        "5m": timedelta(minutes=5),
        "15m": timedelta(minutes=15),
        "30m": timedelta(minutes=30),
        "60m": timedelta(hours=1),
        "90m": timedelta(minutes=90),
        "1h": timedelta(hours=1),
        "1d": timedelta(days=1),
        "5d": timedelta(days=5),
        "1wk": timedelta(days=7),
        "1mo": timedelta(days=31),
        "3mo": timedelta(days=92),
    }

    _FOREX_CURRENCIES = {
        "USD", "EUR", "GBP", "JPY", "INR", "AUD",
        "CAD", "CHF", "NZD", "CNY", "HKD", "SGD",
    }

    def __init__(self) -> None:
        self._connected = False

    @property
    def market_type(self) -> MarketType:
        # The adapter is multi-asset, so this property is only a
        # compatibility value. get_instrument() determines the actual type.
        return MarketType.STOCK

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def get_instrument(self, symbol: str) -> Instrument:
        normalized = self._normalize_input(symbol)
        index = self._INDIAN_INDICES.get(normalized)
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
                metadata_verified=False,
                orderable=False,
            )

        market = self._classify(normalized)

        if market is MarketType.FOREX:
            base, quote = normalized.split("/")
            return Instrument(
                symbol=normalized,
                market=market,
                base_currency=base,
                quote_currency=quote,
                quantity_step=0.01,
                tick_size=0.0001,
            )

        if market is MarketType.CRYPTO:
            base, quote = normalized.split("/")
            return Instrument(
                symbol=normalized,
                market=market,
                base_currency=base,
                quote_currency=quote,
                quantity_step=0.00000001,
                tick_size=0.01,
            )

        india_exchange = (
            "NSE" if normalized.endswith(".NS")
            else "BSE" if normalized.endswith(".BO")
            else None
        )
        return Instrument(
            symbol=normalized,
            market=MarketType.STOCK,
            base_currency="INR" if india_exchange else "USD",
            quote_currency="INR" if india_exchange else "USD",
            quantity_step=None if india_exchange else 1.0,
            tick_size=None if india_exchange else 0.01,
            contract_size=None if india_exchange else 1.0,
            min_quantity=None if india_exchange else 1.0,
            min_notional=None if india_exchange else 0.0,
            exchange=india_exchange,
            provider_symbol=normalized,
            provider_instrument_id=normalized,
            metadata_verified=False,
            orderable=india_exchange is None,
        )

    def get_price(self, symbol: str) -> float:
        normalized = self._normalize_input(symbol)
        provider_symbol = self._provider_symbol(normalized)

        ticker = yf.Ticker(provider_symbol)

        data = ticker.history(
            period="1d",
            interval="1m",
        )

        if data.empty or "Close" not in data:
            raise ValueError(
                f"No market data available for {normalized}."
            )

        close = data["Close"].dropna()

        if close.empty:
            raise ValueError(
                f"No valid price available for {normalized}."
            )

        price = float(close.iloc[-1])

        if not isfinite(price) or price <= 0:
            raise ValueError(
                f"Invalid market price for {normalized}."
            )

        return price

    def get_snapshot(self, symbol: str) -> dict[str, Any]:
        normalized = self._normalize_input(symbol)
        instrument = self.get_instrument(normalized)
        provider_symbol = self._provider_symbol(normalized)
        data = yf.Ticker(provider_symbol).history(period="1d", interval="1m")
        if data.empty or "Close" not in data:
            raise ValueError(f"No market data available for {normalized}.")

        closes = data["Close"].dropna()
        if closes.empty:
            raise ValueError(f"No valid price available for {normalized}.")
        price = float(closes.iloc[-1])
        if not isfinite(price) or price <= 0:
            raise ValueError(f"Invalid market price for {normalized}.")

        timestamp = closes.index[-1]
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
            raise ValueError(
                f"Yahoo Finance returned no timezone-aware source timestamp for {normalized}."
            )
        timestamp = timestamp.to_pydatetime().astimezone(
            self.INDIA_TZ if instrument.exchange in {"NSE", "BSE"} else UTC
        )
        session = self.get_market_session(normalized)
        return {
            "symbol": instrument.symbol,
            "price": price,
            "timestamp": timestamp,
            "provider": "yahoo_finance",
            "provider_symbol": provider_symbol,
            "source": "Yahoo Finance via yfinance",
            "real_time": False,
            "latency_status": "unknown_or_delayed",
            "currency": instrument.quote_currency,
            "instrument_type": instrument.instrument_type.value,
            "orderable": instrument.orderable,
            "market_session": session,
        }

    def get_history(
        self,
        symbol: str,
        *,
        period: str = "1y",
        interval: str = "1d",
        now: datetime | None = None,
    ) -> list[Candle]:
        normalized = self._normalize_input(symbol)
        if interval not in self._INTERVALS:
            raise ValueError(
                f"Unsupported Yahoo Finance interval: {interval}."
            )
        instrument = self.get_instrument(normalized)
        data = yf.Ticker(self._provider_symbol(normalized)).history(
            period=period,
            interval=interval,
        )
        if data.empty:
            raise ValueError(f"No historical market data available for {normalized}.")
        required = {"Open", "High", "Low", "Close", "Volume"}
        if not required.issubset(data.columns):
            raise ValueError(
                f"Historical data for {normalized} is missing OHLCV columns."
            )

        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("Current time must be timezone-aware.")
        candles: list[Candle] = []
        for timestamp, row in data.iterrows():
            if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
                raise ValueError(
                    f"Yahoo Finance returned a timezone-naive candle for {normalized}."
                )
            local_timestamp = timestamp.to_pydatetime().astimezone(
                self.INDIA_TZ if instrument.exchange in {"NSE", "BSE"} else UTC
            )
            if interval == "1d" and instrument.exchange in {"NSE", "BSE"}:
                session_close = datetime.combine(
                    local_timestamp.date(), time(15, 30), tzinfo=self.INDIA_TZ
                )
                complete = current.astimezone(self.INDIA_TZ) >= session_close
            else:
                complete = (
                    local_timestamp + self._INTERVALS[interval]
                    <= current.astimezone(local_timestamp.tzinfo)
                )
            candles.append(
                Candle(
                    symbol=instrument.symbol,
                    timestamp=local_timestamp,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row["Volume"]),
                    is_complete=complete,
                    source="Yahoo Finance via yfinance",
                    realtime_status="unknown_or_delayed",
                )
            )
        return candles

    def get_market_session(
        self,
        symbol: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        normalized = self._normalize_input(symbol)
        instrument = self.get_instrument(normalized)
        if instrument.exchange not in {"NSE", "BSE"}:
            return {
                "exchange": instrument.exchange,
                "timezone": "Asia/Kolkata" if instrument.exchange else None,
                "calendar_verified": False,
                "status": "exchange_calendar_not_available",
            }

        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("Current time must be timezone-aware.")
        local = current.astimezone(self.INDIA_TZ)
        scheduled = (
            local.weekday() < 5
            and time(9, 15) <= local.time().replace(tzinfo=None) < time(15, 30)
        )
        return {
            "exchange": instrument.exchange,
            "timezone": "Asia/Kolkata",
            "local_time": local.isoformat(),
            "scheduled_open": scheduled,
            "calendar_verified": False,
            "calendar_source": "weekday session-hours estimate; holidays unverified",
            "status": "scheduled_regular_session" if scheduled else "outside_scheduled_session",
        }

    def is_market_open(self, symbol: str) -> bool:
        normalized = self._normalize_input(symbol)
        instrument = self.get_instrument(normalized)
        if instrument.exchange in {"NSE", "BSE"}:
            return bool(self.get_market_session(normalized)["scheduled_open"])
        market = self._classify(normalized)

        if market is MarketType.CRYPTO:
            return True

        if market is MarketType.FOREX:
            now = datetime.now(UTC)

            # Forex is generally closed from Friday evening through
            # Sunday evening UTC. Exact venue hours vary.
            if now.weekday() == 5:
                return False

            if now.weekday() == 6:
                return False

            return True

        # Yahoo's historical data is sufficient for price retrieval,
        # but exchange-specific trading calendars are not guaranteed
        # here. Keep the adapter conservative.
        return True

    def get_capabilities(
        self,
        symbol: str,
    ) -> dict[str, Any]:
        normalized = self._normalize_input(symbol)
        market = self._classify(normalized)

        capabilities: dict[str, Any] = {
            "market_type": market.value,
            "provider": "yahoo_finance",
            "real_time": False,
            "latency_status": "unknown_or_delayed",
            "authorized_realtime_feed_verified": False,
            "paper": False,
            "supports_market_orders": False,
            "supports_limit_orders": False,
            "supports_fractional_quantity": False,
            "supports_short_selling": False,
        }

        if market is MarketType.CRYPTO:
            capabilities.update(
                {
                    "supports_fractional_quantity": True,
                    "market_hours": "24/7",
                }
            )
        elif market is MarketType.FOREX:
            capabilities.update(
                {
                    "market_hours": "24/5",
                }
            )
        else:
            capabilities["market_hours"] = "exchange_dependent"

        instrument = self.get_instrument(normalized)
        capabilities.update(
            {
                "instrument_type": instrument.instrument_type.value,
                "exchange": instrument.exchange,
                "orderable": instrument.orderable,
                "metadata_verified": instrument.metadata_verified,
            }
        )
        if instrument.exchange in {"NSE", "BSE"}:
            capabilities.update(
                {
                    "currency": "INR",
                    "market_hours": "09:15-15:30 Asia/Kolkata, weekdays",
                    "holiday_calendar_verified": False,
                    "market_data_access_authorized": False,
                }
            )
        return capabilities

    @classmethod
    def _normalize_input(cls, symbol: str) -> str:
        if not isinstance(symbol, str):
            raise ValueError("Symbol must be a string.")

        symbol = symbol.strip().upper()

        if not symbol:
            raise ValueError("Symbol is required.")

        if symbol in cls._INDIAN_INDICES:
            return cls._INDIAN_INDICES[symbol][0]

        if "/" in symbol:
            parts = [part.strip() for part in symbol.split("/")]

            if len(parts) != 2 or not all(parts):
                raise ValueError(
                    f"Invalid pair symbol: {symbol}"
                )

            return f"{parts[0]}/{parts[1]}"

        return symbol

    @classmethod
    def _classify(cls, symbol: str) -> MarketType:
        if "/" not in symbol:
            return MarketType.STOCK

        base, quote = symbol.split("/", 1)

        if (
            base in cls._FOREX_CURRENCIES
            and quote in cls._FOREX_CURRENCIES
        ):
            return MarketType.FOREX

        return MarketType.CRYPTO

    @classmethod
    def _provider_symbol(cls, symbol: str) -> str:
        index = cls._INDIAN_INDICES.get(symbol)
        if index:
            return index[1]

        market = cls._classify(symbol)

        if market is MarketType.FOREX:
            base, quote = symbol.split("/")
            return f"{base}{quote}=X"

        if market is MarketType.CRYPTO:
            base, quote = symbol.split("/")
            return f"{base}-{quote}"

        return symbol
