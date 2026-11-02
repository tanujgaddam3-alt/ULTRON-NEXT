from __future__ import annotations

import json
import math
import os
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from ultron.financial.trading.market.interface import MarketAdapter
from ultron.financial.trading.market.models import Instrument, MarketType


class OandaAPIError(RuntimeError):
    """Sanitized OANDA request or response failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        failure_type: str = "api",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.failure_type = failure_type


class OandaConfigurationError(ValueError):
    """Missing or invalid local OANDA configuration."""


class OandaV20Client:
    """Read-only OANDA REST v20 client. This client has no order methods."""

    _BASE_URLS = {
        "practice": "https://api-fxpractice.oanda.com",
        "live": "https://api-fxtrade.oanda.com",
    }

    def __init__(
        self,
        token: str | None = None,
        account_id: str | None = None,
        *,
        environment: str | None = None,
        timeout_seconds: float = 5.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.token = (
            token if token is not None else os.getenv("OANDA_API_TOKEN", "")
        ).strip()
        self.account_id = (
            account_id if account_id is not None else os.getenv("OANDA_ACCOUNT_ID", "")
        ).strip()
        self.environment = (
            environment or os.getenv("OANDA_ENVIRONMENT", "practice")
        ).strip().lower()
        if not self.token:
            raise OandaConfigurationError(
                "OANDA_API_TOKEN is required for authenticated OANDA access."
            )
        if not self.account_id:
            raise OandaConfigurationError(
                "OANDA_ACCOUNT_ID is required for authenticated OANDA access."
            )
        if self.environment not in self._BASE_URLS:
            raise OandaConfigurationError(
                "OANDA_ENVIRONMENT must be 'practice' or 'live'."
            )
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise OandaConfigurationError(
                "OANDA request timeout must be finite and positive."
            )
        self.base_url = self._BASE_URLS[self.environment]
        self.timeout_seconds = float(timeout_seconds)
        self._opener = opener or urlopen
        self._account_path = f"/v3/accounts/{quote(self.account_id, safe='')}"
        self._request_lock = threading.Lock()
        self._last_request_started: float | None = None

    def _request(
        self,
        path: str,
        params: dict[str, str | int | bool] | None = None,
    ) -> dict[str, Any]:
        query = urlencode(params or {})
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"
        request = Request(
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "Accept-Datetime-Format": "RFC3339",
            },
            method="GET",
        )
        try:
            with self._request_lock:
                now = time.monotonic()
                if self._last_request_started is not None:
                    delay = 0.5 - (now - self._last_request_started)
                    if delay > 0:
                        time.sleep(delay)
                self._last_request_started = time.monotonic()
                response = self._opener(request, timeout=self.timeout_seconds)
            try:
                body = response.read()
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
        except HTTPError as exc:
            if exc.code == 401:
                failure_type = "authentication"
            elif exc.code == 403:
                failure_type = "permission"
            elif exc.code == 404:
                failure_type = "not_found"
            else:
                failure_type = "http"
            raise OandaAPIError(
                f"OANDA rejected a read-only request (HTTP {exc.code}).",
                status_code=exc.code,
                failure_type=failure_type,
            ) from None
        except TimeoutError:
            raise OandaAPIError(
                "OANDA read-only request timed out.",
                failure_type="timeout",
            ) from None
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise OandaAPIError(
                    "OANDA read-only request timed out.",
                    failure_type="timeout",
                ) from None
            raise OandaAPIError(
                "OANDA read-only request could not reach the provider.",
                failure_type="network",
            ) from None
        except OSError:
            raise OandaAPIError(
                "OANDA read-only response could not be read.",
                failure_type="network",
            ) from None

        try:
            decoded = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            raise OandaAPIError(
                "OANDA returned a non-JSON response.",
                failure_type="invalid_response",
            ) from None
        if not isinstance(decoded, dict):
            raise OandaAPIError(
                "OANDA returned an invalid response shape.",
                failure_type="invalid_response",
            )
        return decoded

    def get_account(self) -> dict[str, Any]:
        return self._request(self._account_path)

    def get_account_summary(self) -> dict[str, Any]:
        return self._request(f"{self._account_path}/summary")

    def get_instruments(self, instrument: str | None = None) -> dict[str, Any]:
        params = {"instruments": instrument} if instrument else None
        return self._request(f"{self._account_path}/instruments", params)

    def get_price(self, instrument: str) -> dict[str, Any]:
        return self._request(
            f"{self._account_path}/pricing",
            {"instruments": instrument},
        )

    def get_candles(
        self,
        instrument: str,
        *,
        granularity: str,
        count: int,
    ) -> dict[str, Any]:
        path = (
            f"{self._account_path}/instruments/"
            f"{quote(instrument, safe='')}/candles"
        )
        return self._request(
            path,
            {
                "price": "M",
                "granularity": granularity,
                "count": count,
                "smooth": "false",
            },
        )


class OandaExecutionClient(OandaV20Client):
    """OANDA v20 order client, kept separate from read-only market access."""

    def _request_order(
        self,
        path: str,
        *,
        method: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if method not in {"POST", "PUT"}:
            raise ValueError("OANDA execution requests must use POST or PUT.")
        url = f"{self.base_url}{path}"
        payload = (
            json.dumps(body, separators=(",", ":")).encode("utf-8")
            if body is not None
            else None
        )
        request = Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "Accept-Datetime-Format": "RFC3339",
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            with self._request_lock:
                now = time.monotonic()
                if self._last_request_started is not None:
                    delay = 0.5 - (now - self._last_request_started)
                    if delay > 0:
                        time.sleep(delay)
                self._last_request_started = time.monotonic()
                response = self._opener(request, timeout=self.timeout_seconds)
            try:
                raw = response.read()
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
        except HTTPError as exc:
            failure_type = {
                401: "authentication",
                403: "permission",
                404: "not_found",
                429: "rate_limit",
            }.get(exc.code, "http")
            raise OandaAPIError(
                f"OANDA rejected an execution request (HTTP {exc.code}).",
                status_code=exc.code,
                failure_type=failure_type,
            ) from None
        except TimeoutError:
            raise OandaAPIError(
                "OANDA execution request timed out; its outcome may be uncertain.",
                failure_type="timeout",
            ) from None
        except URLError as exc:
            failure_type = (
                "timeout" if isinstance(exc.reason, TimeoutError) else "network"
            )
            raise OandaAPIError(
                "OANDA execution request could not be confirmed.",
                failure_type=failure_type,
            ) from None
        except OSError:
            raise OandaAPIError(
                "OANDA execution response could not be read.",
                failure_type="network",
            ) from None
        try:
            decoded = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            raise OandaAPIError(
                "OANDA returned a non-JSON execution response.",
                failure_type="invalid_response",
            ) from None
        if not isinstance(decoded, dict):
            raise OandaAPIError(
                "OANDA returned an invalid execution response shape.",
                failure_type="invalid_response",
            )
        return decoded

    def create_order(self, order: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(order, dict) or not order:
            raise ValueError("OANDA order request must be a non-empty object.")
        return self._request_order(
            f"{self._account_path}/orders",
            method="POST",
            body={"order": order},
        )

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        if not order_id.strip():
            raise ValueError("OANDA order ID is required for cancellation.")
        return self._request_order(
            f"{self._account_path}/orders/{quote(order_id, safe='')}/cancel",
            method="PUT",
            body={},
        )

    def get_order(self, order_id: str) -> dict[str, Any]:
        return self._request(
            f"{self._account_path}/orders/{quote(order_id, safe='')}"
        )

    def get_transactions_since(self, transaction_id: str) -> dict[str, Any]:
        if not transaction_id.isdigit():
            raise ValueError("OANDA transaction checkpoint must be numeric.")
        return self._request(
            f"{self._account_path}/transactions/sinceid",
            {"id": transaction_id},
        )

    def get_transaction(self, transaction_id: str) -> dict[str, Any]:
        if not transaction_id.isdigit():
            raise ValueError("OANDA transaction ID must be numeric.")
        return self._request(
            f"{self._account_path}/transactions/{quote(transaction_id, safe='')}"
        )

    def get_open_trades(self) -> dict[str, Any]:
        return self._request(f"{self._account_path}/openTrades")

    def get_trades(
        self,
        *,
        instrument: str | None = None,
        state: str = "ALL",
        count: int = 500,
    ) -> dict[str, Any]:
        if state not in {"OPEN", "CLOSED", "ALL"}:
            raise ValueError("OANDA trade state must be OPEN, CLOSED, or ALL.")
        if isinstance(count, bool) or not 1 <= count <= 500:
            raise ValueError("OANDA trade count must be between 1 and 500.")
        params: dict[str, str | int] = {"state": state, "count": count}
        if instrument:
            params["instrument"] = instrument
        return self._request(f"{self._account_path}/trades", params)


class OandaMarketAdapter(MarketAdapter):
    """Account-authenticated, read-only OANDA forex market-data adapter."""

    _GRANULARITIES = {
        "S5", "S10", "S15", "S30",
        "M1", "M2", "M4", "M5", "M10", "M15", "M30",
        "H1", "H2", "H3", "H4", "H6", "H8", "H12",
        "D", "W", "M",
    }

    def __init__(
        self,
        client: OandaV20Client | None = None,
        *,
        max_quote_age_seconds: float = 15.0,
    ) -> None:
        if not math.isfinite(max_quote_age_seconds) or max_quote_age_seconds <= 0:
            raise ValueError("max_quote_age_seconds must be finite and positive.")
        self.client = client or OandaV20Client()
        self.max_quote_age_seconds = float(max_quote_age_seconds)
        self._cache_lock = threading.Lock()
        self._cached_price: tuple[float, str, dict[str, Any]] | None = None

    @property
    def market_type(self) -> MarketType:
        return MarketType.FOREX

    @staticmethod
    def _instrument_name(symbol: str) -> str:
        if not isinstance(symbol, str):
            raise ValueError("OANDA instrument must be a currency pair.")
        value = symbol.strip().upper().replace("/", "_")
        parts = value.split("_")
        if (
            len(parts) != 2
            or any(len(part) != 3 or not part.isascii() or not part.isalpha() for part in parts)
        ):
            raise ValueError("OANDA forex instrument must use BASE_QUOTE format.")
        return value

    @staticmethod
    def _decimal(value: Any, field: str) -> Decimal:
        try:
            result = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise OandaAPIError(
                f"OANDA returned an invalid {field}.",
                failure_type="invalid_response",
            ) from None
        if not result.is_finite():
            raise OandaAPIError(
                f"OANDA returned a non-finite {field}.",
                failure_type="invalid_response",
            )
        return result

    @staticmethod
    def _timestamp(value: Any) -> datetime:
        if not isinstance(value, str):
            raise OandaAPIError(
                "OANDA response omitted a source timestamp.",
                failure_type="invalid_response",
            )
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise OandaAPIError(
                "OANDA returned an invalid source timestamp.",
                failure_type="invalid_response",
            ) from None
        if timestamp.tzinfo is None:
            raise OandaAPIError(
                "OANDA returned a timezone-naive source timestamp.",
                failure_type="invalid_response",
            )
        return timestamp.astimezone(UTC)

    def _fetch_price(self, instrument: str) -> dict[str, Any]:
        response = self.client.get_price(instrument)
        prices = response.get("prices")
        if not isinstance(prices, list):
            raise OandaAPIError(
                "OANDA pricing response omitted its prices list.",
                failure_type="invalid_response",
            )
        matches = [
            item
            for item in prices
            if isinstance(item, dict) and item.get("instrument") == instrument
        ]
        if len(matches) != 1:
            raise OandaAPIError(
                "OANDA pricing response did not uniquely match the requested instrument.",
                failure_type="invalid_response",
            )
        with self._cache_lock:
            self._cached_price = (time.monotonic(), instrument, matches[0])
        return matches[0]

    def _price_response(self, instrument: str) -> dict[str, Any]:
        with self._cache_lock:
            cached = self._cached_price
            if (
                cached is not None
                and cached[1] == instrument
                and time.monotonic() - cached[0] <= 1.0
            ):
                self._cached_price = None
                return cached[2]
        return self._fetch_price(instrument)

    def is_market_open(self, symbol: str) -> bool:
        instrument = self._instrument_name(symbol)
        price = self._fetch_price(instrument)
        return price.get("status") == "tradeable"

    def get_snapshot(self, symbol: str) -> dict[str, Any]:
        instrument = self._instrument_name(symbol)
        response = self._price_response(instrument)
        if response.get("status") != "tradeable":
            raise OandaAPIError(
                "OANDA reports the requested instrument is not tradeable now.",
                failure_type="market_closed",
            )
        bids = response.get("bids")
        asks = response.get("asks")
        if (
            not isinstance(bids, list)
            or not bids
            or not isinstance(bids[0], dict)
            or not isinstance(asks, list)
            or not asks
            or not isinstance(asks[0], dict)
        ):
            raise OandaAPIError(
                "OANDA returned an incomplete bid/ask book.",
                failure_type="invalid_response",
            )
        bid = self._decimal(bids[0].get("price"), "best bid")
        ask = self._decimal(asks[0].get("price"), "best ask")
        if bid <= 0 or ask <= 0 or ask < bid:
            raise OandaAPIError(
                "OANDA returned an invalid bid/ask spread.",
                failure_type="invalid_response",
            )
        timestamp = self._timestamp(response.get("time"))
        now = datetime.now(UTC)
        age = (now - timestamp).total_seconds()
        if age < 0 or age > self.max_quote_age_seconds:
            raise OandaAPIError(
                "OANDA price is future-dated or stale.",
                failure_type="stale_data",
            )
        midpoint = (bid + ask) / Decimal(2)
        result: dict[str, Any] = {
            "symbol": instrument,
            "price": float(midpoint),
            "bid": float(bid),
            "ask": float(ask),
            "midpoint": float(midpoint),
            "spread": float(ask - bid),
            "spread_bps": float((ask - bid) / midpoint * Decimal(10_000)),
            "timestamp": timestamp,
            "retrieved_at": now,
            "age_seconds": age,
            "provider": "oanda_v20",
            "feed": "account_authenticated_rest_polling",
            "market_type": MarketType.FOREX.value,
            "market_status": "tradeable",
            "quote_currency": instrument.split("_")[1],
        }
        for field, level in (("bid_liquidity", bids[0]), ("ask_liquidity", asks[0])):
            if "liquidity" in level:
                liquidity = self._decimal(level["liquidity"], field)
                if liquidity < 0:
                    raise OandaAPIError(
                        f"OANDA returned invalid {field}.",
                        failure_type="invalid_response",
                    )
                result[field] = float(liquidity)
        return result

    def get_price(self, symbol: str) -> float:
        return float(self.get_snapshot(symbol)["price"])

    def get_history(
        self,
        symbol: str,
        *,
        granularity: str = "D",
        count: int = 500,
    ) -> list[dict[str, Any]]:
        instrument = self._instrument_name(symbol)
        if granularity not in self._GRANULARITIES:
            raise ValueError("Unsupported OANDA candle granularity.")
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 5000:
            raise ValueError("OANDA candle count must be an integer from 1 to 5000.")
        response = self.client.get_candles(
            instrument,
            granularity=granularity,
            count=count,
        )
        if (
            response.get("instrument") != instrument
            or response.get("granularity") != granularity
        ):
            raise OandaAPIError(
                "OANDA candle response did not match the requested instrument and granularity.",
                failure_type="invalid_response",
            )
        candles = response.get("candles")
        if not isinstance(candles, list):
            raise OandaAPIError(
                "OANDA candle response omitted its candles list.",
                failure_type="invalid_response",
            )
        records: list[dict[str, Any]] = []
        previous_timestamp: datetime | None = None
        for candle in candles:
            if not isinstance(candle, dict) or not isinstance(candle.get("mid"), dict):
                raise OandaAPIError(
                    "OANDA returned an incomplete mid-price candle.",
                    failure_type="invalid_response",
                )
            timestamp = self._timestamp(candle.get("time"))
            if previous_timestamp is not None and timestamp <= previous_timestamp:
                raise OandaAPIError(
                    "OANDA returned duplicate or out-of-order candles.",
                    failure_type="invalid_response",
                )
            previous_timestamp = timestamp
            complete = candle.get("complete")
            if not isinstance(complete, bool):
                raise OandaAPIError(
                    "OANDA candle omitted its completeness flag.",
                    failure_type="invalid_response",
                )
            mid = candle["mid"]
            values = {
                key: self._decimal(mid.get(source_key), key)
                for key, source_key in (
                    ("open", "o"),
                    ("high", "h"),
                    ("low", "l"),
                    ("close", "c"),
                )
            }
            volume = self._decimal(candle.get("volume"), "candle volume")
            if (
                min(values.values()) <= 0
                or volume < 0
                or volume != volume.to_integral_value()
                or values["high"] < max(values["open"], values["close"])
                or values["low"] > min(values["open"], values["close"])
            ):
                raise OandaAPIError(
                    "OANDA returned invalid candle values.",
                    failure_type="invalid_response",
                )
            records.append(
                {
                    "symbol": instrument,
                    "timestamp": timestamp,
                    **{key: float(value) for key, value in values.items()},
                    "volume": int(volume),
                    "is_complete": complete,
                    "provider": "oanda_v20",
                    "granularity": granularity,
                }
            )
        return records

    def get_instrument(self, symbol: str) -> Instrument:
        instrument = self._instrument_name(symbol)
        response = self.client.get_instruments(instrument)
        instruments = response.get("instruments")
        if not isinstance(instruments, list):
            raise OandaAPIError(
                "OANDA instrument response omitted its instruments list.",
                failure_type="invalid_response",
            )
        matches = [
            item
            for item in instruments
            if isinstance(item, dict) and item.get("name") == instrument
        ]
        if len(matches) != 1:
            raise OandaAPIError(
                "OANDA does not report the requested instrument as available to this account.",
                failure_type="not_found",
            )
        metadata = matches[0]
        if metadata.get("type") != "CURRENCY":
            raise OandaAPIError(
                "OANDA account instrument is not a spot currency pair.",
                failure_type="not_found",
            )
        base, quote_currency = instrument.split("_")
        try:
            precision = int(metadata["tradeUnitsPrecision"])
            display_precision = int(metadata["displayPrecision"])
            minimum = float(self._decimal(metadata["minimumTradeSize"], "minimum trade size"))
            maximum = float(self._decimal(metadata["maximumOrderUnits"], "maximum order units"))
            margin_rate = float(self._decimal(metadata["marginRate"], "margin rate"))
        except (KeyError, TypeError, ValueError):
            raise OandaAPIError(
                "OANDA returned incomplete instrument constraints.",
                failure_type="invalid_response",
            ) from None
        if (
            precision < 0
            or display_precision < 0
            or minimum <= 0
            or maximum < minimum
            or margin_rate <= 0
        ):
            raise OandaAPIError(
                "OANDA returned invalid instrument constraints.",
                failure_type="invalid_response",
            )
        return Instrument(
            symbol=instrument,
            market=MarketType.FOREX,
            base_currency=base,
            quote_currency=quote_currency,
            quantity_step=10.0 ** -precision,
            tick_size=10.0 ** -display_precision,
            min_quantity=minimum,
            provider_symbol=instrument,
            asset_class="forex",
            trading_status="active",
            market_session="provider_reported",
            provider_capabilities={
                "account_instrument_available": True,
                "read_only_market_data": True,
                "live_orders": False,
            },
            is_shortable=True,
            is_margin_eligible=True,
        )

    def get_account_snapshot(
        self,
        prices: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        del prices
        response = self.client.get_account()
        account = response.get("account")
        if not isinstance(account, dict):
            raise OandaAPIError(
                "OANDA account response omitted account details.",
                failure_type="invalid_response",
            )
        if account.get("id") != self.client.account_id:
            raise OandaAPIError(
                "OANDA account response did not match the configured account.",
                failure_type="invalid_response",
            )
        raw_positions = account.get("positions")
        if not isinstance(raw_positions, list):
            raise OandaAPIError(
                "OANDA account response omitted authoritative positions.",
                failure_type="invalid_response",
            )
        positions: dict[str, dict[str, float]] = {}
        for position in raw_positions:
            if not isinstance(position, dict) or not isinstance(position.get("instrument"), str):
                raise OandaAPIError(
                    "OANDA returned an invalid position.",
                    failure_type="invalid_response",
                )
            long_data = position.get("long", {})
            short_data = position.get("short", {})
            if not isinstance(long_data, dict) or not isinstance(short_data, dict):
                raise OandaAPIError(
                    "OANDA returned invalid long/short position details.",
                    failure_type="invalid_response",
                )
            long_units = self._decimal(long_data.get("units", "0"), "long units")
            short_units = abs(self._decimal(short_data.get("units", "0"), "short units"))
            net_units = long_units - short_units
            if net_units != 0:
                positions[position["instrument"]] = {
                    "quantity": float(net_units),
                    "long_quantity": float(long_units),
                    "short_quantity": float(-short_units),
                    "long_average_price": float(
                        self._decimal(long_data.get("averagePrice", "0"), "long average price")
                    ),
                    "short_average_price": float(
                        self._decimal(short_data.get("averagePrice", "0"), "short average price")
                    ),
                }

        def amount(field: str) -> float:
            value = self._decimal(account.get(field), field)
            if value < 0:
                raise OandaAPIError(
                    f"OANDA returned a negative {field}.",
                    failure_type="invalid_response",
                )
            return float(value)

        currency = account.get("currency")
        last_transaction_id = account.get("lastTransactionID")
        if (
            not isinstance(currency, str)
            or len(currency) != 3
            or not isinstance(last_transaction_id, str)
            or not last_transaction_id.isdigit()
        ):
            raise OandaAPIError(
                "OANDA account response omitted currency or transaction provenance.",
                failure_type="invalid_response",
            )
        try:
            pending_order_count = int(account["pendingOrderCount"])
            open_trade_count = int(account["openTradeCount"])
        except (KeyError, TypeError, ValueError):
            raise OandaAPIError(
                "OANDA account response omitted valid order and trade counts.",
                failure_type="invalid_response",
            ) from None
        if (
            isinstance(account.get("pendingOrderCount"), bool)
            or isinstance(account.get("openTradeCount"), bool)
            or pending_order_count < 0
            or open_trade_count < 0
        ):
            raise OandaAPIError(
                "OANDA account response returned invalid order or trade counts.",
                failure_type="invalid_response",
            )

        return {
            "cash": amount("balance"),
            "equity": amount("NAV"),
            "buying_power": amount("marginAvailable"),
            "margin_used": amount("marginUsed"),
            "unrealized_pl": float(self._decimal(account.get("unrealizedPL"), "unrealized P/L")),
            "currency": currency.upper(),
            "account_id": self.client.account_id,
            "last_transaction_id": last_transaction_id,
            "positions": positions,
            "pending_order_count": pending_order_count,
            "open_trade_count": open_trade_count,
            "account_status": str(account.get("status", "unknown")),
            "account_access_verified": True,
            "provider": "oanda_v20",
            "mode": self.client.environment,
        }

    def get_capabilities(self, symbol: str) -> dict[str, Any]:
        instrument = self.get_instrument(symbol)
        return {
            "provider": "oanda_v20",
            "market_type": instrument.market.value,
            "instrument": instrument.symbol,
            "market_hours": "provider-reported FX tradeability",
            "supports_bid_ask": True,
            "supports_top_of_book_liquidity": True,
            "supports_historical_candles": True,
            "supports_account_snapshot": True,
            "supports_live_orders": False,
            "feed_mode": "authenticated_rest_polling",
            "trading_status": instrument.trading_status,
        }
