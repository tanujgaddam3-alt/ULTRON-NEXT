from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

from ultron.financial.trading.market.models import Instrument, MarketType


class AlpacaMarketDataError(RuntimeError):
    def __init__(self, message: str, *, category: str = "provider_error") -> None:
        super().__init__(message)
        self.category = category


class AlpacaMarketAdapter:
    """Timestamp-aware, data-only Alpaca adapter for US equities.

    This adapter never submits orders. Latest trades are freshness-checked;
    historical bars retain their provider timestamps and are not treated as
    current prices.
    """

    def __init__(
        self,
        api_key=None,
        secret_key=None,
        *,
        feed=None,
        max_quote_age_seconds=30.0,
    ):
        self.api_key = api_key or os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
        self.secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
        if not self.api_key or not self.secret_key:
            raise AlpacaMarketDataError(
                "Alpaca credentials are missing. Set ALPACA_API_KEY and ALPACA_SECRET_KEY."
            )
        if max_quote_age_seconds <= 0:
            raise ValueError("max_quote_age_seconds must be positive")
        self.max_quote_age_seconds = float(max_quote_age_seconds)
        self.feed = feed or os.getenv("ALPACA_DATA_FEED")
        try:
            from alpaca.data.enums import DataFeed
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.requests import StockLatestTradeRequest, StockBarsRequest
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
            from alpaca.trading.client import TradingClient
        except ImportError as exc:
            raise AlpacaMarketDataError("alpaca-py is required for Alpaca market data") from exc
        self._trade_request = StockLatestTradeRequest
        self._bars_request = StockBarsRequest
        self._time_frame = TimeFrame
        self._time_frame_unit = TimeFrameUnit
        self._data_feed_enum = DataFeed
        from alpaca.trading.requests import GetCalendarRequest

        self._calendar_request = GetCalendarRequest
        self._data_client = StockHistoricalDataClient(self.api_key, self.secret_key)
        # Used only for the market clock/account verification, never order submission.
        self._trading_client = TradingClient(self.api_key, self.secret_key, paper=True)

    @property
    def market_type(self):
        return MarketType.STOCK

    @staticmethod
    def _validate_symbol(symbol):
        value = str(symbol).strip().upper()
        if not value:
            raise ValueError("Symbol is required")
        if value.endswith((".NS", ".BO")) or "/" in value or value.endswith("=X"):
            raise AlpacaMarketDataError(f"{value} is not a supported Alpaca US-equity symbol")
        if not value.replace(".", "").replace("-", "").isalnum():
            raise ValueError(f"Invalid equity symbol: {value}")
        return value

    def _feed_value(self):
        if not self.feed:
            return None
        try:
            return self._data_feed_enum(self.feed.lower())
        except (ValueError, AttributeError) as exc:
            raise AlpacaMarketDataError(f"Unsupported ALPACA_DATA_FEED={self.feed!r}") from exc

    def _request_kwargs(self):
        kwargs = {}
        feed = self._feed_value()
        if feed is not None:
            kwargs["feed"] = feed
        return kwargs

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _error_category(error: BaseException) -> str:
        error_type = type(error).__name__.lower()
        status = getattr(error, "status_code", None)
        message = str(error).lower()
        if status == 429 or "rate limit" in message or "too many requests" in message:
            return "rate_limit"
        if status == 401:
            return "authentication"
        if status == 403 or isinstance(error, PermissionError):
            return "permission_or_subscription"
        if isinstance(error, (TimeoutError, ConnectionError)) or "timeout" in error_type:
            return "network"
        if any(marker in error_type for marker in ("connection", "network", "timeout")):
            return "network"
        return "provider_error"

    @classmethod
    def _safe_provider_error(cls, operation: str, error: BaseException) -> AlpacaMarketDataError:
        category = cls._error_category(error)
        return AlpacaMarketDataError(
            f"Alpaca {operation} failed ({category}; {type(error).__name__})",
            category=category,
        )

    def _calendar_for(self, start: date, end: date):
        return self._trading_client.get_calendar(
            self._calendar_request(start=start, end=end)
        )

    @staticmethod
    def _calendar_boundary_utc(value: Any) -> datetime | None:
        if not isinstance(value, datetime):
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=ZoneInfo("America/New_York"))
        return value.astimezone(UTC)

    def get_market_session_status(self) -> dict[str, Any]:
        """Return a calendar-verified regular-session status from Alpaca."""
        try:
            clock = self._trading_client.get_clock()
        except Exception as exc:
            error = self._safe_provider_error("market clock request", exc)
            return {
                "status": "unknown", "calendar_verified": False,
                "error_category": error.category, "error": str(error),
            }

        clock_timestamp = getattr(clock, "timestamp", None)
        if not isinstance(clock_timestamp, datetime) or clock_timestamp.tzinfo is None:
            return {
                "status": "unknown", "calendar_verified": False,
                "error_category": "invalid_clock", "error": "Alpaca clock timestamp is missing or timezone-naive.",
            }
        clock_timestamp = clock_timestamp.astimezone(UTC)
        local_date = clock_timestamp.astimezone(ZoneInfo("America/New_York")).date()
        try:
            sessions = self._calendar_for(local_date, local_date)
        except Exception as exc:
            error = self._safe_provider_error("market calendar request", exc)
            return {
                "status": "unknown", "calendar_verified": False,
                "clock_timestamp": clock_timestamp,
                "error_category": error.category, "error": str(error),
            }

        if not isinstance(sessions, (list, tuple)):
            return {
                "status": "unknown", "calendar_verified": False,
                "clock_timestamp": clock_timestamp,
                "error_category": "invalid_calendar",
                "error": "Alpaca calendar returned an invalid response.",
            }
        if any(not isinstance(getattr(item, "date", None), date) for item in sessions):
            return {
                "status": "unknown", "calendar_verified": False,
                "clock_timestamp": clock_timestamp,
                "error_category": "invalid_calendar",
                "error": "Alpaca calendar returned an invalid session date.",
            }

        session = next((item for item in sessions if item.date == local_date), None)
        provider_open = getattr(clock, "is_open", None)
        if not isinstance(provider_open, bool):
            return {
                "status": "unknown", "calendar_verified": True,
                "clock_timestamp": clock_timestamp,
                "error_category": "invalid_clock", "error": "Alpaca clock returned an invalid open-state value.",
            }
        if session is None:
            if provider_open:
                return {
                    "status": "unknown", "calendar_verified": True,
                    "clock_timestamp": clock_timestamp,
                    "error_category": "session_inconsistent",
                    "error": "Alpaca clock reports open without a scheduled regular session.",
                }
            return {
                "status": "closed", "calendar_verified": True,
                "clock_timestamp": clock_timestamp, "session_date": None,
                "reason": "no_regular_session",
            }

        session_open = self._calendar_boundary_utc(getattr(session, "open", None))
        session_close = self._calendar_boundary_utc(getattr(session, "close", None))
        if session_open is None or session_close is None:
            return {
                "status": "unknown", "calendar_verified": False,
                "clock_timestamp": clock_timestamp,
                "error_category": "invalid_calendar",
                "error": "Alpaca calendar returned invalid session boundaries.",
            }
        if session_close <= session_open:
            return {
                "status": "unknown", "calendar_verified": False,
                "clock_timestamp": clock_timestamp,
                "error_category": "invalid_calendar",
                "error": "Alpaca calendar returned invalid session boundaries.",
            }
        within_session = session_open <= clock_timestamp < session_close
        if within_session != provider_open:
            return {
                "status": "unknown", "calendar_verified": True,
                "clock_timestamp": clock_timestamp,
                "session_date": local_date,
                "session_open": session_open, "session_close": session_close,
                "error_category": "session_inconsistent",
                "error": "Alpaca clock state conflicts with calendar session boundaries.",
            }
        return {
            "status": "open" if within_session else "closed",
            "calendar_verified": True, "clock_timestamp": clock_timestamp,
            "session_date": local_date,
            "session_open": session_open, "session_close": session_close,
            "reason": "within_regular_session" if within_session else "outside_regular_session",
        }

    def _fetch_latest_trade(self, symbol: str) -> dict[str, Any] | None:
        try:
            response = self._data_client.get_stock_latest_trade(
                self._trade_request(symbol_or_symbols=symbol, **self._request_kwargs())
            )
        except Exception as exc:
            raise self._safe_provider_error("latest-trade request", exc) from None
        if not isinstance(response, dict):
            raise AlpacaMarketDataError(
                "Alpaca returned an invalid latest-trade response.",
                category="invalid_response",
            )
        trade = response.get(symbol)
        if trade is None:
            return None
        timestamp = getattr(trade, "timestamp", None)
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
            raise AlpacaMarketDataError("Alpaca returned a missing or timezone-naive trade timestamp.", category="invalid_trade")
        timestamp = timestamp.astimezone(UTC)
        now = self._now()
        if now.tzinfo is None:
            raise AlpacaMarketDataError("Retrieval clock returned a timezone-naive timestamp.", category="invalid_clock")
        now = now.astimezone(UTC)
        age = (now - timestamp).total_seconds()
        if age < 0:
            raise AlpacaMarketDataError("Alpaca returned a future-dated trade.", category="invalid_trade")
        try:
            price = float(trade.price)
        except (TypeError, ValueError, OverflowError):
            raise AlpacaMarketDataError("Alpaca returned an invalid trade price.", category="invalid_trade") from None
        if not isfinite(price) or price <= 0:
            raise AlpacaMarketDataError("Alpaca returned a non-finite or non-positive trade price.", category="invalid_trade")
        return {
            "symbol": symbol, "price": price, "timestamp": timestamp,
            "retrieved_at": now, "age_seconds": age,
            "provider": "alpaca", "feed": self._feed_name(),
            "currency": "USD",
        }

    def _feed_name(self) -> str:
        return str(self.feed).strip().upper() if self.feed else "account-default"

    def _last_known_session_status(
        self, trade_timestamp: datetime, session_status: dict[str, Any]
    ) -> str:
        if session_status.get("status") != "closed":
            return "stale_unclassified"
        clock_timestamp = session_status.get("clock_timestamp")
        if not isinstance(clock_timestamp, datetime):
            return "stale_unclassified"
        current_date = clock_timestamp.astimezone(ZoneInfo("America/New_York")).date()
        trade_date = trade_timestamp.astimezone(ZoneInfo("America/New_York")).date()
        if (current_date - trade_date).days > 31 or trade_date > current_date:
            return "historical_session_unverified"
        try:
            sessions = self._calendar_for(trade_date, current_date)
        except Exception:
            return "historical_session_unverified"
        if not isinstance(sessions, (list, tuple)) or any(
            not isinstance(getattr(item, "date", None), date) for item in sessions
        ):
            return "historical_session_unverified"
        regular_sessions = sorted(
            (item for item in sessions if trade_date <= item.date <= current_date),
            key=lambda item: item.date,
        )
        source_session = next((item for item in regular_sessions if item.date == trade_date), None)
        if source_session is None:
            return "non_session_day"
        source_open = self._calendar_boundary_utc(source_session.open)
        source_close = self._calendar_boundary_utc(source_session.close)
        if source_open is None or source_close is None:
            return "historical_session_unverified"
        source_time = trade_timestamp.astimezone(UTC)
        if not source_open <= source_time < source_close:
            return "outside_regular_session"
        current_session = next(
            (item for item in regular_sessions if item.date == current_date), None
        )
        clock_time = clock_timestamp.astimezone(UTC)
        current_open = (
            self._calendar_boundary_utc(getattr(current_session, "open", None))
            if current_session is not None else None
        )
        before_current_open = (
            current_session is not None
            and current_open is not None
            and clock_time < current_open
        )
        completed_sessions = [
            item for item in regular_sessions
            if item.date < current_date or (item.date == current_date and not before_current_open)
        ]
        if not completed_sessions:
            return "historical_session_trade"
        latest_completed_session = completed_sessions[-1]
        if source_session.date == latest_completed_session.date and trade_date < current_date:
            return "previous_session"
        return "historical_session_trade"

    def get_latest_trade_report(self, symbol: str) -> dict[str, Any]:
        """Report trade freshness and session context without promoting stale data."""
        value = self._validate_symbol(symbol)
        session_status = self.get_market_session_status()
        trade = self._fetch_latest_trade(value)
        report: dict[str, Any] = {
            "provider_request_status": "succeeded",
            "provider": "alpaca", "symbol": value, "feed": self._feed_name(),
            "currency": "USD", "market_status": session_status,
            "trade_available": trade is not None,
            "trade_status": "unavailable" if trade is None else "invalid",
            "current_quote_permitted": False,
            "freshness_threshold_seconds": self.max_quote_age_seconds,
            "trade_timestamp": None, "retrieved_at": self._now(),
            "age_seconds": None, "last_known_trade": None, "price": None,
        }
        if trade is None:
            report["error_category"] = "trade_unavailable"
            report["error"] = f"Alpaca returned no latest trade for {value}."
            return report

        age = trade["age_seconds"]
        fresh = age <= self.max_quote_age_seconds
        session_state = session_status.get("status")
        permitted = fresh and session_state == "open"
        report.update({
            "trade_status": "fresh" if fresh else "stale",
            "trade_timestamp": trade["timestamp"],
            "retrieved_at": trade["retrieved_at"],
            "age_seconds": age,
            "current_quote_permitted": permitted,
            "error_category": None if permitted else (
                "stale_trade" if not fresh else
                "market_closed" if session_state == "closed" else "market_status_unknown"
            ),
        })
        if permitted:
            report["price"] = trade["price"]
        else:
            report["last_known_trade"] = {
                "status": "stale" if not fresh else "not_current_session",
                "session_classification": self._last_known_session_status(
                    trade["timestamp"], session_status
                ) if not fresh else "current_trade_not_current_quote",
                "price": trade["price"], "timestamp": trade["timestamp"],
                "age_seconds": age, "provider": trade["provider"],
                "feed": trade["feed"], "currency": trade["currency"],
            }
        return report

    def get_instrument(self, symbol):
        value = self._validate_symbol(symbol)
        return Instrument(symbol=value, market=MarketType.STOCK, base_currency="USD", quote_currency="USD")

    def get_price(self, symbol):
        return float(self.get_snapshot(symbol)["price"])

    def get_snapshot(self, symbol) -> dict[str, Any]:
        value = self._validate_symbol(symbol)
        trade = self._fetch_latest_trade(value)
        if trade is None:
            raise AlpacaMarketDataError(f"Alpaca returned no latest trade for {value}", category="trade_unavailable")
        if trade["age_seconds"] > self.max_quote_age_seconds:
            raise AlpacaMarketDataError(
                f"Alpaca trade is stale ({trade['age_seconds']:.1f}s; threshold {self.max_quote_age_seconds:.1f}s)",
                category="stale_trade",
            )
        return {**trade, "trade_status": "fresh", "freshness": "fresh"}

    @staticmethod
    def _period_start(period: str, now: datetime) -> datetime:
        """Convert a conservative set of familiar period strings to UTC start."""
        value = str(period).strip().lower()
        durations = {
            "1d": timedelta(days=1), "5d": timedelta(days=5),
            "1mo": timedelta(days=30), "3mo": timedelta(days=90),
            "6mo": timedelta(days=182), "1y": timedelta(days=365),
            "2y": timedelta(days=730), "5y": timedelta(days=1825),
        }
        if value not in durations:
            raise ValueError(
                f"Unsupported period {period!r}; choose {', '.join(durations)}."
            )
        return now - durations[value]

    def get_history(
        self,
        symbol: str,
        period: str = "1y",
        timeframe: str = "1Day",
        limit: int = 10000,
    ) -> dict[str, Any]:
        """Fetch historical OHLCV bars with source timestamps.

        Returns serializable dictionaries; does not fabricate missing bars or
        label historical data as a current quote.
        """
        value = self._validate_symbol(symbol)
        if not isinstance(limit, int) or limit < 1 or limit > 10000:
            raise ValueError("limit must be an integer from 1 to 10000")
        frames = {
            "1Min": self._time_frame.Minute,
            "5Min": self._time_frame(5, self._time_frame_unit.Minute),
            "15Min": self._time_frame(15, self._time_frame_unit.Minute),
            "1Hour": self._time_frame.Hour,
            "1Day": self._time_frame.Day,
        }
        if timeframe not in frames:
            raise ValueError(f"Unsupported timeframe {timeframe!r}; choose {', '.join(frames)}")
        now = datetime.now(UTC)
        start = self._period_start(period, now)
        try:
            from alpaca.data.enums import Adjustment
            kwargs = {
                "symbol_or_symbols": value,
                "timeframe": frames[timeframe],
                "start": start,
                "end": now,
                "limit": limit,
                "adjustment": Adjustment.ALL,
                **self._request_kwargs(),
            }
            response = self._data_client.get_stock_bars(self._bars_request(**kwargs))
            bars = response.data.get(value, [])
            timestamps = [bar.timestamp for bar in bars]
            if timestamps != sorted(timestamps):
                raise AlpacaMarketDataError(
                    "Alpaca returned historical bars out of order"
                )
            if len(set(timestamps)) != len(timestamps):
                raise AlpacaMarketDataError(
                    "Alpaca returned duplicate historical bar timestamps"
                )
            records = []
            for bar in bars:
                timestamp = bar.timestamp
                if timestamp.tzinfo is None:
                    raise AlpacaMarketDataError("Alpaca returned a timezone-naive historical timestamp")
                records.append({
                    "symbol": value,
                    "timestamp": timestamp.astimezone(UTC).isoformat(),
                    "open": float(bar.open), "high": float(bar.high),
                    "low": float(bar.low), "close": float(bar.close),
                    "volume": float(bar.volume), "trade_count": getattr(bar, "trade_count", None),
                    "vwap": float(bar.vwap) if getattr(bar, "vwap", None) is not None else None,
                    "provider": "alpaca", "feed": self.feed or "account-default",
                    "timeframe": timeframe, "adjustment": "all",
                })
            return {
                "success": True, "symbol": value, "provider": "alpaca",
                "feed": self.feed or "account-default", "timeframe": timeframe,
                "period": period, "start": start.isoformat(), "end": now.isoformat(),
                "limit": limit, "adjustment": "all",
                "count": len(records), "bars": records,
            }
        except (ValueError, AlpacaMarketDataError):
            raise
        except Exception as exc:
            raise self._safe_provider_error("historical-bars request", exc) from None

    def is_market_open(self, symbol):
        self._validate_symbol(symbol)
        status = self.get_market_session_status()
        if status["status"] == "unknown":
            raise AlpacaMarketDataError(
                status["error"], category=status.get("error_category", "unknown_session")
            )
        return status["status"] == "open"

    def get_capabilities(self, symbol):
        value = self._validate_symbol(symbol)
        return {
            "symbol": value, "provider": "alpaca", "latest_trade": True,
            "historical_bars": True, "streaming": False, "market_clock": True,
            "market": "US_equities_only", "order_submission": False,
            "supported_timeframes": ["1Min", "5Min", "15Min", "1Hour", "1Day"],
        }
