from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from io import BytesIO
from math import isfinite
import re
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from ultron.financial.trading.market.interface import MarketAdapter
from ultron.financial.trading.market.models import (
    INDIAN_INDEX_IDENTITIES,
    Instrument,
    InstrumentType,
    MarketType,
)
from ultron.financial.trading.market_data.models import Candle

from .groww_credentials import (
    GROWW_CREDENTIAL_KEYS,
    GrowwConfigurationError,
    load_groww_credentials,
)


class GrowwMarketDataError(RuntimeError):
    """Sanitized Groww read-only API failure."""

    def __init__(
        self,
        message: str,
        *,
        failure_type: str = "api",
        http_status: int | None = None,
        provider_code: str | None = None,
        diagnostic_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_type = failure_type
        self.http_status = http_status
        self.provider_code = provider_code
        self.diagnostic_message = diagnostic_message or message


class GrowwMarketAdapter(MarketAdapter):
    """Authenticated, read-only Groww market-data adapter for Indian indices."""

    INDIA_TZ = ZoneInfo("Asia/Kolkata")
    _INDEX_IDENTITIES = {
        "NIFTY50": {
            "exchange": "NSE",
            "trading_symbols": {"NIFTY"},
            "names": {"NIFTY50"},
        },
        "SENSEX": {
            "exchange": "BSE",
            "trading_symbols": {"SENSEX"},
            "names": {"SENSEX", "BSESENSEX"},
        },
    }
    _PERIOD_DAYS = {
        "1d": 1,
        "5d": 5,
        "1mo": 30,
        "3mo": 90,
        "6mo": 180,
        "1y": 365,
        "2y": 730,
        "1080d": 1080,
    }

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        *,
        client: Any | None = None,
        sdk_factory: Any | None = None,
        credentials_file: str | None = None,
        max_quote_age_seconds: float = 30.0,
        request_timeout_seconds: int = 5,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isfinite(max_quote_age_seconds) or max_quote_age_seconds <= 0:
            raise ValueError("max_quote_age_seconds must be finite and positive.")
        if (
            isinstance(request_timeout_seconds, bool)
            or not isinstance(request_timeout_seconds, int)
            or request_timeout_seconds <= 0
        ):
            raise ValueError("request_timeout_seconds must be a positive integer.")
        self.max_quote_age_seconds = float(max_quote_age_seconds)
        self.request_timeout_seconds = request_timeout_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._injected_client = client is not None
        if client is not None:
            self._client = client
            return

        credentials = (
            {}
            if api_key is not None and api_secret is not None
            else load_groww_credentials(credentials_file)
        )
        key = (api_key if api_key is not None else credentials[GROWW_CREDENTIAL_KEYS[0]]).strip()
        secret = (
            api_secret
            if api_secret is not None
            else credentials[GROWW_CREDENTIAL_KEYS[1]]
        ).strip()
        missing = [
            name
            for name, value in zip(GROWW_CREDENTIAL_KEYS, (key, secret), strict=True)
            if not value
        ]
        if missing:
            raise GrowwConfigurationError(
                "Groww authenticated market data requires GROWW_API_KEY and GROWW_API_SECRET."
            )

        if sdk_factory is None:
            try:
                from growwapi import GrowwAPI
            except ImportError:
                raise GrowwConfigurationError(
                    "The official Groww Python SDK is unavailable; install growwapi."
                ) from None
            sdk_factory = GrowwAPI
            uses_official_sdk = True
        else:
            uses_official_sdk = False
        try:
            access_token = sdk_factory.get_access_token(
                api_key=key,
                secret=secret,
            )
            if not isinstance(access_token, str) or not access_token.strip():
                raise GrowwMarketDataError(
                    "Groww authentication returned no access token.",
                    failure_type="authentication",
                )
            self._client = sdk_factory(access_token)
            if uses_official_sdk:
                self._install_accept_header(self._client)
        except (GrowwConfigurationError, GrowwMarketDataError):
            raise
        except Exception as exc:
            raise self._provider_error("authentication", exc) from None

    @staticmethod
    def _install_accept_header(client: Any) -> None:
        request_get = getattr(client, "_request_get", None)
        if not callable(request_get):
            raise GrowwConfigurationError(
                "The installed Groww SDK does not expose its expected GET request method."
            )

        def request_get_with_accept(
            url: str,
            params: dict[str, Any] | None = None,
            headers: dict[str, Any] | None = None,
            timeout: int | None = None,
            **kwargs: Any,
        ) -> Any:
            request_headers = dict(headers or {})
            request_headers.setdefault("Accept", "application/json")
            return request_get(
                url=url,
                params=params,
                headers=request_headers,
                timeout=timeout,
                **kwargs,
            )

        client._request_get = request_get_with_accept

    @property
    def market_type(self) -> MarketType:
        return MarketType.STOCK

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("A Groww index symbol is required.")
        normalized = symbol.strip().upper()
        identity = INDIAN_INDEX_IDENTITIES.get(normalized)
        if identity is None:
            raise ValueError(
                "Groww market data supports only NIFTY 50 and BSE Sensex."
            )
        return identity[0]

    @staticmethod
    def _normalized_name(value: Any) -> str:
        if value is None:
            return ""
        text = str(value).strip().upper()
        if text.lower() == "nan":
            return ""
        return "".join(character for character in text if character.isalnum())

    @staticmethod
    def _text(value: Any) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        return "" if text.lower() == "nan" else text

    @staticmethod
    def _records(response: Any) -> list[dict[str, Any]]:
        if hasattr(response, "to_dict"):
            try:
                records = response.to_dict(orient="records")
            except (TypeError, ValueError):
                raise GrowwMarketDataError(
                    "Groww returned an invalid instrument directory.",
                    failure_type="invalid_response",
                ) from None
        elif isinstance(response, list):
            records = response
        else:
            raise GrowwMarketDataError(
                "Groww returned an invalid instrument directory.",
                failure_type="invalid_response",
            )
        if not isinstance(records, list) or any(
            not isinstance(record, dict) for record in records
        ):
            raise GrowwMarketDataError(
                "Groww returned an invalid instrument directory.",
                failure_type="invalid_response",
            )
        return records

    @staticmethod
    def _provider_error(operation: str, error: Exception) -> GrowwMarketDataError:
        name = type(error).__name__.lower()
        raw_provider_code = getattr(error, "code", None)
        raw_status = getattr(getattr(error, "response", None), "status_code", None)
        if raw_status is None:
            raw_status = raw_provider_code
        status_code = None
        if raw_status is not None:
            try:
                candidate = int(raw_status)
            except (TypeError, ValueError):
                candidate = None
            if candidate is not None and 100 <= candidate <= 599:
                status_code = candidate
        provider_code = (
            raw_provider_code.strip()
            if isinstance(raw_provider_code, str)
            and re.fullmatch(r"[A-Z]{2,4}\d{3,6}", raw_provider_code.strip())
            else None
        )
        if (
            isinstance(error, TimeoutError)
            or "timeout" in name
            or status_code in {408, 504}
        ):
            failure_type = "timeout"
        elif "ratelimit" in name or "rate_limit" in name or status_code == 429:
            failure_type = "rate_limit"
        elif (
            "authorisation" in name
            or "authorization" in name
            or status_code == 403
        ):
            failure_type = "authorization"
        elif (
            "authentication" in name
            or status_code == 401
        ):
            failure_type = "authentication"
        elif "badrequest" in name or "bad_request" in name:
            failure_type = "bad_request"
        elif status_code == 400:
            failure_type = "bad_request"
        elif status_code == 404:
            failure_type = "not_found"
        elif "connection" in name or "network" in name or "unavailable" in name:
            failure_type = "unavailable"
        elif isinstance(status_code, int) and status_code >= 500:
            failure_type = "unavailable"
        else:
            failure_type = "api"
        safe_messages = {
            "authentication": "Groww rejected the access token; it may be invalid or expired.",
            "authorization": (
                "Groww denied access; token permissions, subscription, or account "
                "eligibility may be insufficient."
            ),
            "bad_request": (
                "Groww rejected the request; authentication approval or request "
                "parameters may need attention."
            ),
            "rate_limit": "Groww rate limit was reached.",
            "timeout": "The Groww request timed out.",
            "not_found": "Groww did not find the requested resource.",
            "unavailable": "Groww service or network is unavailable.",
            "api": "Groww returned an API error.",
        }
        return GrowwMarketDataError(
            f"Groww {operation} request failed ({failure_type}).",
            failure_type=failure_type,
            http_status=status_code,
            provider_code=provider_code,
            diagnostic_message=safe_messages[failure_type],
        )

    def _api_call(self, operation: str, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except GrowwMarketDataError:
            raise
        except Exception as exc:
            raise self._provider_error(operation, exc) from None

    def _instrument_records(self) -> list[dict[str, Any]]:
        directory_url = getattr(type(self._client), "INSTRUMENT_CSV_URL", None)
        if directory_url:
            response = self._api_call(
                "instrument directory",
                requests.get,
                directory_url,
                timeout=self.request_timeout_seconds,
            )
            self._api_call("instrument directory", response.raise_for_status)
            try:
                directory = pd.read_csv(
                    BytesIO(response.content),
                    dtype="str",
                )
            except Exception as exc:
                raise self._provider_error("instrument directory", exc) from None
            return self._records(directory)
        if not self._injected_client:
            raise GrowwMarketDataError(
                "The installed Groww SDK does not expose a safe instrument-directory source.",
                failure_type="sdk_incompatible",
            )
        return self._records(
            self._api_call(
                "instrument lookup",
                self._client.get_all_instruments,
            )
        )

    def get_instrument(self, symbol: str) -> Instrument:
        canonical = self.normalize_symbol(symbol)
        expected = self._INDEX_IDENTITIES[canonical]
        records = self._instrument_records()
        candidates = []
        for record in records:
            if (
                self._text(record.get("exchange")).upper()
                != expected["exchange"]
                or self._text(record.get("segment")).upper() != "CASH"
            ):
                continue
            trading_symbol = self._normalized_name(record.get("trading_symbol"))
            name = self._normalized_name(record.get("name"))
            if (
                trading_symbol in expected["trading_symbols"]
                or name in expected["names"]
            ):
                candidates.append(record)

        if not candidates:
            raise GrowwMarketDataError(
                f"Groww has no current cash-market instrument mapping for {canonical}.",
                failure_type="instrument_unavailable",
            )
        if len(candidates) != 1:
            raise GrowwMarketDataError(
                f"Groww returned ambiguous instrument mappings for {canonical}.",
                failure_type="invalid_response",
            )

        record = candidates[0]
        trading_symbol = self._text(record.get("trading_symbol"))
        if not trading_symbol:
            raise GrowwMarketDataError(
                f"Groww returned an incomplete instrument mapping for {canonical}.",
                failure_type="invalid_response",
            )
        provider_id = self._text(record.get("groww_symbol"))
        if not provider_id:
            raise GrowwMarketDataError(
                f"Groww returned no provider identifier for {canonical}.",
                failure_type="invalid_response",
            )
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
            provider_symbol=trading_symbol,
            provider_instrument_id=provider_id,
            exchange=expected["exchange"],
            instrument_type=InstrumentType.INDEX,
            metadata_verified=False,
            orderable=False,
        )

    def get_snapshot(self, symbol: str) -> dict[str, Any]:
        instrument = self.get_instrument(symbol)
        response = self._api_call(
            "quote",
            self._client.get_quote,
            exchange=instrument.exchange,
            segment="CASH",
            trading_symbol=instrument.provider_symbol,
            timeout=self.request_timeout_seconds,
        )
        if not isinstance(response, dict):
            raise GrowwMarketDataError(
                "Groww returned an invalid quote response.",
                failure_type="invalid_response",
            )
        try:
            if isinstance(response["last_price"], bool):
                raise ValueError
            price = float(response["last_price"])
            raw_timestamp = response["last_trade_time"]
            timestamp_millis = int(raw_timestamp)
            if isinstance(raw_timestamp, bool) or float(raw_timestamp) != timestamp_millis:
                raise ValueError
            timestamp = datetime.fromtimestamp(
                timestamp_millis / 1000.0,
                tz=UTC,
            ).astimezone(self.INDIA_TZ)
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            raise GrowwMarketDataError(
                "Groww returned a malformed quote price or source timestamp.",
                failure_type="invalid_response",
            ) from None
        if not isfinite(price) or price <= 0:
            raise GrowwMarketDataError(
                "Groww returned a non-finite or non-positive quote.",
                failure_type="invalid_response",
            )
        now = self._now()
        age = (now - timestamp.astimezone(UTC)).total_seconds()
        if age < 0:
            raise GrowwMarketDataError(
                "Groww returned a future-dated quote.",
                failure_type="invalid_response",
            )
        if age > self.max_quote_age_seconds:
            raise GrowwMarketDataError(
                "Groww returned a stale quote.",
                failure_type="stale_data",
            )
        try:
            if isinstance(response["day_change_perc"], bool):
                raise ValueError
            day_change = float(response["day_change_perc"])
        except (TypeError, ValueError):
            raise GrowwMarketDataError(
                "Groww returned a malformed daily change value.",
                failure_type="invalid_response",
            ) from None
        if not isfinite(day_change):
            raise GrowwMarketDataError(
                "Groww returned a non-finite daily change value.",
                failure_type="invalid_response",
            )
        return {
            "symbol": instrument.symbol,
            "price": price,
            "timestamp": timestamp,
            "retrieved_at": now,
            "age_seconds": age,
            "provider": "groww",
            "source": "Groww Trade API",
            "provider_symbol": instrument.provider_symbol,
            "provider_instrument_id": instrument.provider_instrument_id,
            "instrument_type": instrument.instrument_type.value,
            "exchange": instrument.exchange,
            "currency": instrument.quote_currency,
            "day_change_percent": day_change,
            "volume": self._optional_nonnegative(response.get("volume"), "volume"),
            "real_time": True,
            "realtime_status": "provider_documented_realtime",
            "orderable": False,
            "market_session": self.get_market_session(instrument.symbol, now),
        }

    @staticmethod
    def _optional_nonnegative(value: Any, field: str) -> float | None:
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise GrowwMarketDataError(
                f"Groww returned an invalid {field} value.",
                failure_type="invalid_response",
            ) from None
        if not isfinite(number) or number < 0:
            raise GrowwMarketDataError(
                f"Groww returned an invalid {field} value.",
                failure_type="invalid_response",
            )
        return number

    def get_price(self, symbol: str) -> float:
        return float(self.get_snapshot(symbol)["price"])

    def get_history(
        self,
        symbol: str,
        period: str = "1y",
        interval: str = "1d",
        *,
        now: datetime | None = None,
    ) -> list[Candle]:
        if interval != "1d":
            raise ValueError(
                "Groww history currently supports daily candles only."
            )
        if period not in self._PERIOD_DAYS:
            raise ValueError(
                f"Unsupported Groww history period {period!r}; choose "
                f"{', '.join(self._PERIOD_DAYS)}."
            )
        current = now or self._now()
        if current.tzinfo is None:
            raise ValueError("History end time must be timezone-aware.")
        current_india = current.astimezone(self.INDIA_TZ)
        start_date = current_india.date() - timedelta(
            days=self._PERIOD_DAYS[period] - 1
        )
        instrument = self.get_instrument(symbol)
        candles: list[Candle] = []
        cursor = start_date
        while cursor <= current_india.date():
            chunk_end_date = min(cursor + timedelta(days=179), current_india.date())
            chunk_start = datetime.combine(cursor, time.min, tzinfo=self.INDIA_TZ)
            chunk_end = datetime.combine(
                chunk_end_date,
                time.max if chunk_end_date < current_india.date() else current_india.time(),
                tzinfo=self.INDIA_TZ,
            )
            response = self._api_call(
                "historical candles",
                self._client.get_historical_candles,
                exchange=instrument.exchange,
                segment="CASH",
                groww_symbol=instrument.provider_instrument_id,
                start_time=chunk_start.strftime("%Y-%m-%d %H:%M:%S"),
                end_time=chunk_end.strftime("%Y-%m-%d %H:%M:%S"),
                candle_interval="1day",
                timeout=self.request_timeout_seconds,
            )
            if not isinstance(response, dict) or not isinstance(
                response.get("candles"), list
            ):
                raise GrowwMarketDataError(
                    "Groww returned an invalid historical-candle response.",
                    failure_type="invalid_response",
                )
            if not response["candles"]:
                raise GrowwMarketDataError(
                    "Groww returned no candles for a requested historical interval.",
                    failure_type="no_data",
                )
            for values in response["candles"]:
                candles.append(
                    self._parse_daily_candle(
                        instrument.symbol,
                        values,
                        current_india,
                        chunk_start,
                        chunk_end,
                    )
                )
            cursor = chunk_end_date + timedelta(days=1)

        if not candles:
            raise GrowwMarketDataError(
                f"Groww returned no historical candles for {instrument.symbol}.",
                failure_type="no_data",
            )
        timestamps = [candle.timestamp for candle in candles]
        if timestamps != sorted(timestamps) or len(set(timestamps)) != len(timestamps):
            raise GrowwMarketDataError(
                "Groww returned out-of-order or duplicate historical candles.",
                failure_type="invalid_response",
            )
        return candles

    @classmethod
    def _parse_daily_candle(
        cls,
        symbol: str,
        values: Any,
        current_india: datetime,
        requested_start: datetime,
        requested_end: datetime,
    ) -> Candle:
        if (
            not isinstance(values, (list, tuple))
            or len(values) != 7
            or values[6] is not None
        ):
            raise GrowwMarketDataError(
                "Groww returned a malformed historical candle.",
                failure_type="invalid_response",
            )
        try:
            raw_timestamp = values[0]
            if (
                not isinstance(raw_timestamp, str)
                or ("T" not in raw_timestamp and " " not in raw_timestamp)
                or any(isinstance(value, bool) for value in values[1:6])
            ):
                raise ValueError
            timestamp = datetime.fromisoformat(raw_timestamp)
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=cls.INDIA_TZ)
            else:
                timestamp = timestamp.astimezone(cls.INDIA_TZ)
            open_price, high, low, close = (
                float(value) for value in values[1:5]
            )
            volume = None if values[5] is None else float(values[5])
        except (TypeError, ValueError, OverflowError):
            raise GrowwMarketDataError(
                "Groww returned a malformed historical candle.",
                failure_type="invalid_response",
            ) from None
        if timestamp > current_india:
            raise GrowwMarketDataError(
                "Groww returned a future-dated historical candle.",
                failure_type="invalid_response",
            )
        if timestamp < requested_start or timestamp > requested_end:
            raise GrowwMarketDataError(
                "Groww returned a candle outside the requested historical interval.",
                failure_type="invalid_response",
            )
        complete = timestamp.date() < current_india.date() or (
            timestamp.date() == current_india.date()
            and current_india.time().replace(tzinfo=None) >= time(15, 30)
        )
        try:
            return Candle(
                symbol=symbol,
                timestamp=timestamp,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
                is_complete=complete,
                source="Groww Trade API historical candles (Asia/Kolkata assumed)",
                realtime_status="historical",
            )
        except ValueError:
            raise GrowwMarketDataError(
                "Groww returned invalid OHLCV candle values.",
                failure_type="invalid_response",
            ) from None

    def get_market_session(
        self,
        symbol: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        canonical = self.normalize_symbol(symbol)
        instrument = self._INDEX_IDENTITIES[canonical]
        current = now or self._now()
        if current.tzinfo is None:
            raise ValueError("Market-session time must be timezone-aware.")
        local = current.astimezone(self.INDIA_TZ)
        open_now = local.weekday() < 5 and (
            time(9, 15) <= local.time().replace(tzinfo=None) < time(15, 30)
        )
        return {
            "exchange": instrument["exchange"],
            "timezone": "Asia/Kolkata",
            "local_time": local.isoformat(),
            "scheduled_open": open_now,
            "calendar_verified": False,
            "calendar_source": "Groww API documentation does not provide an exchange holiday calendar.",
            "status": (
                "scheduled_regular_session"
                if open_now
                else "outside_scheduled_session"
            ),
        }

    def is_market_open(self, symbol: str) -> bool:
        return bool(self.get_market_session(symbol)["scheduled_open"])

    def get_capabilities(self, symbol: str) -> dict[str, Any]:
        instrument = self.get_instrument(symbol)
        return {
            "market_type": MarketType.STOCK.value,
            "provider": "groww",
            "supports_latest_quotes": True,
            "supports_daily_historical_candles": True,
            "supports_order_submission": False,
            "supports_order_modification": False,
            "supports_order_cancellation": False,
            "real_time": True,
            "orderable": False,
            "instrument_type": instrument.instrument_type.value,
            "exchange": instrument.exchange,
            "currency": instrument.quote_currency,
            "market_hours": "09:15-15:30 Asia/Kolkata, weekdays (estimated)",
            "holiday_calendar_verified": False,
        }

    def _now(self) -> datetime:
        current = self._clock()
        if not isinstance(current, datetime) or current.tzinfo is None:
            raise ValueError("Groww adapter clock must return a timezone-aware datetime.")
        return current
