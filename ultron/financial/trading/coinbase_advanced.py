from __future__ import annotations

import os
import secrets
import threading
import time
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any
from urllib.parse import quote

import requests
import jwt
from cryptography.hazmat.primitives import serialization

from .broker import BrokerAdapter
from .models import Order, OrderSide, OrderStatus
from ultron.security.permission.rules import require_simulation_broker


_API_ROOT = "/api/v3/brokerage"
_AUTHORIZATION_ACK = "I_ACKNOWLEDGE_REAL_ORDER_RISK"


class CoinbaseAPIError(RuntimeError):
    """A Coinbase API or transport failure without response secrets or bodies."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code


class CoinbaseAuthenticationError(CoinbaseAPIError):
    """The configured CDP key is missing, invalid, or lacks required scope."""


class CoinbaseRequestTimeout(CoinbaseAPIError):
    """A request timed out; order submission outcome may be unresolved."""


class CoinbaseEligibilityError(CoinbaseAPIError):
    """The authenticated account or product is not eligible for this operation."""


class CoinbaseAdvancedClient:
    """Small REST client for Coinbase Advanced Trade's documented v3 API."""

    def __init__(
        self,
        api_key_name: str | None = None,
        api_private_key: str | None = None,
        *,
        session: Any | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive.")

        self._api_key_name = (
            api_key_name or os.getenv("COINBASE_API_KEY_NAME")
        )
        private_key = (
            api_private_key or os.getenv("COINBASE_API_PRIVATE_KEY")
        )
        self._api_private_key = (
            private_key.replace("\\n", "\n") if private_key else None
        )
        self._session = session or requests.Session()
        self.timeout_seconds = float(timeout_seconds)

    @property
    def authenticated(self) -> bool:
        return bool(self._api_key_name and self._api_private_key)

    def _authorization(self, method: str, path: str) -> str:
        if not self.authenticated:
            raise CoinbaseAuthenticationError(
                "COINBASE_API_KEY_NAME and COINBASE_API_PRIVATE_KEY are required."
            )

        try:
            private_key = serialization.load_pem_private_key(
                self._api_private_key.encode("utf-8"),
                password=None,
            )
            now = int(time.time())
            token = jwt.encode(
                {
                    "sub": self._api_key_name,
                    "iss": "cdp",
                    "nbf": now,
                    "exp": now + 120,
                    "uri": f"{method.upper()} api.coinbase.com{path}",
                },
                private_key,
                algorithm="ES256",
                headers={
                    "kid": self._api_key_name,
                    "nonce": secrets.token_hex(16),
                },
            )
        except (TypeError, ValueError, jwt.PyJWTError) as exc:
            raise CoinbaseAuthenticationError(
                "Unable to sign Coinbase request with the configured ES256 key."
            ) from exc

        return f"Bearer {token}"

    def request(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = False,
        params: dict[str, Any] | list[tuple[str, Any]] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        method = method.upper()
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if authenticated:
            headers["Authorization"] = self._authorization(method, path)

        try:
            response = self._session.request(
                method,
                f"https://api.coinbase.com{path}",
                params=params,
                json=body,
                headers=headers,
                timeout=self.timeout_seconds,
            )
        except requests.Timeout as exc:
            raise CoinbaseRequestTimeout(
                f"Coinbase {method} {path} request timed out."
            ) from exc
        except requests.RequestException as exc:
            raise CoinbaseAPIError(
                f"Coinbase {method} {path} transport failed."
            ) from exc

        status_code = int(response.status_code)
        if status_code in {401, 403}:
            raise CoinbaseAuthenticationError(
                f"Coinbase rejected {method} {path} authentication or permissions.",
                status_code=status_code,
            )
        if status_code < 200 or status_code >= 300:
            raise CoinbaseAPIError(
                f"Coinbase {method} {path} returned HTTP {status_code}.",
                status_code=status_code,
            )

        try:
            result = response.json()
        except (TypeError, ValueError) as exc:
            raise CoinbaseAPIError(
                f"Coinbase {method} {path} returned invalid JSON.",
                status_code=status_code,
            ) from exc
        if not isinstance(result, dict):
            raise CoinbaseAPIError(
                f"Coinbase {method} {path} returned an unexpected response shape.",
                status_code=status_code,
            )
        return result

    def get_product_book(self, product_id: str) -> dict[str, Any]:
        return self.request(
            "GET",
            f"{_API_ROOT}/market/product_book",
            params={"product_id": product_id},
        )

    def get_public_candles(
        self,
        product_id: str,
        *,
        start: int,
        end: int,
        granularity: str = "ONE_DAY",
        limit: int = 350,
    ) -> dict[str, Any]:
        return self.request(
            "GET",
            f"{_API_ROOT}/market/products/{quote(product_id, safe='-')}/candles",
            params={
                "start": str(start),
                "end": str(end),
                "granularity": granularity,
                "limit": limit,
            },
        )

    def get_public_product(self, product_id: str) -> dict[str, Any]:
        return self.request(
            "GET",
            f"{_API_ROOT}/market/products/{quote(product_id, safe='-')}",
        )

    def get_product(self, product_id: str) -> dict[str, Any]:
        return self.request(
            "GET",
            f"{_API_ROOT}/products/{quote(product_id, safe='-')}",
            authenticated=True,
            params={"get_tradability_status": "true"},
        )

    def get_key_permissions(self) -> dict[str, Any]:
        return self.request(
            "GET",
            f"{_API_ROOT}/key_permissions",
            authenticated=True,
        )

    def get_transaction_summary(self) -> dict[str, Any]:
        return self.request(
            "GET",
            f"{_API_ROOT}/transaction_summary",
            authenticated=True,
            params={"product_type": "SPOT"},
        )

    def get_accounts(self) -> list[dict[str, Any]]:
        accounts: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            params: dict[str, Any] = {"limit": 250}
            if cursor:
                if cursor in seen_cursors:
                    raise CoinbaseAPIError(
                        "Coinbase account pagination returned a repeated cursor."
                    )
                seen_cursors.add(cursor)
                params["cursor"] = cursor
            page = self.request(
                "GET",
                f"{_API_ROOT}/accounts",
                authenticated=True,
                params=params,
            )
            page_accounts = page.get("accounts")
            if not isinstance(page_accounts, list):
                raise CoinbaseAPIError(
                    "Coinbase accounts response is missing its account list."
                )
            if any(not isinstance(account, dict) for account in page_accounts):
                raise CoinbaseAPIError(
                    "Coinbase accounts response contains an invalid account."
                )
            accounts.extend(page_accounts)
            if page.get("has_next") is not True:
                return accounts
            next_cursor = page.get("cursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                raise CoinbaseAPIError(
                    "Coinbase account pagination has no next cursor."
                )
            cursor = next_cursor

    def create_limit_ioc_order(
        self,
        *,
        client_order_id: str,
        product_id: str,
        side: str,
        base_size: str,
        limit_price: str,
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            f"{_API_ROOT}/orders",
            authenticated=True,
            body={
                "client_order_id": client_order_id,
                "product_id": product_id,
                "side": side,
                "order_configuration": {
                    "sor_limit_ioc": {
                        "base_size": base_size,
                        "limit_price": limit_price,
                    }
                },
            },
        )

    def get_order(self, order_id: str) -> dict[str, Any]:
        return self.request(
            "GET",
            f"{_API_ROOT}/orders/historical/{quote(order_id, safe='-')}",
            authenticated=True,
        )

    def get_orders(
        self,
        *,
        product_id: str,
        start_date: datetime,
        end_date: datetime,
    ) -> list[dict[str, Any]]:
        if (
            start_date.tzinfo is None
            or end_date.tzinfo is None
            or end_date <= start_date
        ):
            raise ValueError("Order search requires an increasing timezone-aware range.")
        orders: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        statuses = (
            "PENDING",
            "OPEN",
            "FILLED",
            "CANCELLED",
            "EXPIRED",
            "FAILED",
            "QUEUED",
            "CANCEL_QUEUED",
        )
        while True:
            params: list[tuple[str, Any]] = [
                ("product_ids", product_id),
                ("product_type", "SPOT"),
                ("limit", 250),
                ("start_date", start_date.astimezone(UTC).isoformat()),
                ("end_date", end_date.astimezone(UTC).isoformat()),
            ]
            params.extend(("order_status", status) for status in statuses)
            if cursor:
                if cursor in seen_cursors:
                    raise CoinbaseAPIError(
                        "Coinbase order pagination returned a repeated cursor."
                    )
                seen_cursors.add(cursor)
                params.append(("cursor", cursor))
            page = self.request(
                "GET",
                f"{_API_ROOT}/orders/historical/batch",
                authenticated=True,
                params=params,
            )
            page_orders = page.get("orders")
            if not isinstance(page_orders, list):
                raise CoinbaseAPIError(
                    "Coinbase orders response is missing its order list."
                )
            orders.extend(order for order in page_orders if isinstance(order, dict))
            if page.get("has_next") is not True:
                return orders
            next_cursor = page.get("cursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                raise CoinbaseAPIError(
                    "Coinbase order pagination omitted its next cursor."
                )
            cursor = next_cursor

    def get_order_fills(self, order_id: str) -> list[dict[str, Any]]:
        fills: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            params: list[tuple[str, Any]] = [
                ("order_ids", order_id),
                ("limit", 100),
            ]
            if cursor:
                if cursor in seen_cursors:
                    raise CoinbaseAPIError(
                        "Coinbase fill pagination returned a repeated cursor."
                    )
                seen_cursors.add(cursor)
                params.append(("cursor", cursor))
            page = self.request(
                "GET",
                f"{_API_ROOT}/orders/historical/fills",
                authenticated=True,
                params=params,
            )
            page_fills = page.get("fills")
            if not isinstance(page_fills, list):
                raise CoinbaseAPIError(
                    "Coinbase fills response is missing its fill list."
                )
            fills.extend(fill for fill in page_fills if isinstance(fill, dict))
            cursor = page.get("cursor")
            if not cursor:
                return fills

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        return self.request(
            "POST",
            f"{_API_ROOT}/orders/batch_cancel",
            authenticated=True,
            body={"order_ids": [order_id]},
        )


class CoinbaseSpotBroker(BrokerAdapter):
    """Coinbase Advanced spot execution with fail-closed live authorization."""

    def __init__(
        self,
        client: CoinbaseAdvancedClient | None = None,
        *,
        quote_currency: str | None = None,
        account_region: str | None = None,
        eligibility_account_id: str | None = None,
        region_eligibility_confirmed: bool | None = None,
        live_trading_authorized: bool | None = None,
        max_quote_age_seconds: float = 30.0,
        max_execution_price_deviation_pct: float = 0.5,
        regional_eligibility_verifier: (
            Callable[[str, str], dict[str, Any]] | None
        ) = None,
    ) -> None:
        super().__init__(mode="live")
        if not isfinite(max_quote_age_seconds) or max_quote_age_seconds <= 0:
            raise ValueError("max_quote_age_seconds must be finite and positive.")
        if (
            not isfinite(max_execution_price_deviation_pct)
            or max_execution_price_deviation_pct <= 0
        ):
            raise ValueError(
                "max_execution_price_deviation_pct must be finite and positive."
            )
        self.client = client or CoinbaseAdvancedClient()
        self.quote_currency = (
            quote_currency or os.getenv("COINBASE_QUOTE_CURRENCY", "INR")
        ).strip().upper()
        if not self.quote_currency.isalnum():
            raise ValueError("COINBASE_QUOTE_CURRENCY must be a currency code.")
        self.account_region = (
            account_region or os.getenv("COINBASE_ACCOUNT_REGION", "")
        ).strip().upper()
        self.eligibility_account_id = (
            eligibility_account_id
            if eligibility_account_id is not None
            else os.getenv("COINBASE_ELIGIBILITY_ACCOUNT_ID", "")
        ).strip()
        region_confirmed = (
            region_eligibility_confirmed
            if region_eligibility_confirmed is not None
            else os.getenv("COINBASE_REGION_ELIGIBILITY_CONFIRMED", "").lower()
            == "true"
        )
        authorization = (
            live_trading_authorized
            if live_trading_authorized is not None
            else (
                os.getenv("ULTRON_EXECUTION_BACKEND", "").lower()
                == "coinbase_live"
                and os.getenv("ULTRON_LIVE_TRADING_AUTHORIZATION")
                == _AUTHORIZATION_ACK
                and os.getenv("ULTRON_COINBASE_LIVE_ORDERS_ENABLED", "").lower()
                == "true"
            )
        )
        self._region_eligibility_asserted = bool(region_confirmed)
        self._regional_eligibility_verifier = regional_eligibility_verifier
        self._live_orders_enabled = bool(authorization)
        self.max_quote_age_seconds = float(max_quote_age_seconds)
        self.max_execution_price_deviation_pct = float(
            max_execution_price_deviation_pct
        )
        self._lock = threading.RLock()
        self._accepted_orders: dict[str, dict[str, Any]] = {}
        self._unresolved_orders: dict[str, dict[str, Any]] = {}

    @property
    def supports_live_orders(self) -> bool:
        return True

    @property
    def live_submission_enabled(self) -> bool:
        return self._live_orders_enabled

    @staticmethod
    def _product_id(symbol: str) -> str:
        if not isinstance(symbol, str):
            raise ValueError("Coinbase product symbol must be a string.")
        product_id = symbol.strip().upper()
        parts = product_id.split("-")
        if (
            len(parts) != 2
            or not all(part.isalnum() for part in parts)
        ):
            raise ValueError(
                "Coinbase spot product must use the BASE-QUOTE format."
            )
        return product_id

    @staticmethod
    def _decimal(value: Any, field_name: str) -> Decimal:
        try:
            result = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise CoinbaseAPIError(
                f"Coinbase returned an invalid {field_name}."
            ) from exc
        if not result.is_finite():
            raise CoinbaseAPIError(
                f"Coinbase returned a non-finite {field_name}."
            )
        return result

    @classmethod
    def _amount(cls, account: dict[str, Any], name: str) -> Decimal:
        value = account.get(name, {})
        if not isinstance(value, dict):
            raise CoinbaseAPIError(
                f"Coinbase account is missing {name}."
            )
        return cls._decimal(value.get("value"), name)

    def _book_quote(self, product_id: str) -> tuple[Decimal, Decimal]:
        response = self.client.get_product_book(product_id)
        book = response.get("pricebook")
        if not isinstance(book, dict) or book.get("product_id") != product_id:
            raise CoinbaseAPIError(
                f"Coinbase returned no order book for {product_id}."
            )
        bids = book.get("bids")
        asks = book.get("asks")
        if not isinstance(bids, list) or not bids or not isinstance(asks, list) or not asks:
            raise CoinbaseAPIError(
                f"Coinbase returned an empty order book for {product_id}."
            )
        bid = self._decimal(bids[0].get("price"), "best bid")
        ask = self._decimal(asks[0].get("price"), "best ask")
        if bid <= 0 or ask <= 0 or ask < bid:
            raise CoinbaseAPIError(
                f"Coinbase returned invalid bid/ask prices for {product_id}."
            )
        timestamp_value = book.get("time")
        if not isinstance(timestamp_value, str):
            raise CoinbaseAPIError(
                f"Coinbase order book for {product_id} has no source timestamp."
            )
        try:
            timestamp = datetime.fromisoformat(
                timestamp_value.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise CoinbaseAPIError(
                f"Coinbase order book for {product_id} has an invalid timestamp."
            ) from exc
        if timestamp.tzinfo is None:
            raise CoinbaseAPIError(
                f"Coinbase order book for {product_id} has a timezone-naive timestamp."
            )
        age = (datetime.now(UTC) - timestamp.astimezone(UTC)).total_seconds()
        if age < 0 or age > self.max_quote_age_seconds:
            raise CoinbaseAPIError(
                f"Coinbase order book for {product_id} is future-dated or stale."
            )
        return bid, ask

    def _market_price(self, product_id: str) -> float:
        bid, ask = self._book_quote(product_id)
        return float((bid + ask) / Decimal(2))

    def _account_balances(self) -> tuple[dict[str, dict[str, Decimal]], list[dict[str, Any]]]:
        accounts = self.client.get_accounts()
        balances: dict[str, dict[str, Decimal]] = {}
        for account in accounts:
            currency = str(account.get("currency", "")).strip().upper()
            if not currency:
                raise CoinbaseAPIError(
                    "Coinbase returned an account without a currency."
                )
            available = self._amount(account, "available_balance")
            hold = self._amount(account, "hold")
            if available < 0 or hold < 0:
                raise CoinbaseAPIError(
                    f"Coinbase returned a negative balance for {currency}."
                )
            balance = balances.setdefault(
                currency,
                {"available": Decimal(0), "hold": Decimal(0)},
            )
            balance["available"] += available
            balance["hold"] += hold
        return balances, accounts

    def _convert_to_quote(self, currency: str, amount: Decimal) -> Decimal:
        if currency == self.quote_currency or amount == 0:
            return amount

        direct = f"{currency}-{self.quote_currency}"
        try:
            return amount * self._decimal(
                self._market_price(direct),
                f"{direct} valuation price",
            )
        except CoinbaseAPIError as direct_error:
            inverse = f"{self.quote_currency}-{currency}"
            try:
                price = self._decimal(
                    self._market_price(inverse),
                    f"{inverse} valuation price",
                )
            except CoinbaseAPIError:
                raise CoinbaseAPIError(
                    f"Cannot value non-zero {currency} account balance in "
                    f"{self.quote_currency}; no fresh Coinbase spot market is available."
                ) from direct_error
            return amount / price

    def snapshot(self, prices: dict[str, float] | None = None) -> dict[str, Any]:
        balances, accounts = self._account_balances()
        available_quote = balances.get(
            self.quote_currency,
            {"available": Decimal(0), "hold": Decimal(0)},
        )["available"]
        total_equity = Decimal(0)
        normalized_positions: dict[str, dict[str, Any]] = {}
        normalized_balances: dict[str, dict[str, float]] = {}

        for currency, balance in balances.items():
            total = balance["available"] + balance["hold"]
            if total < 0:
                raise CoinbaseAPIError(
                    f"Coinbase returned a negative total balance for {currency}."
                )
            value = self._convert_to_quote(currency, total)
            total_equity += value
            normalized_balances[currency] = {
                "available": float(balance["available"]),
                "hold": float(balance["hold"]),
                "total": float(total),
                "valuation_currency": self.quote_currency,
                "valuation": float(value),
            }
            if currency == self.quote_currency or total <= 0:
                continue
            product_id = f"{currency}-{self.quote_currency}"
            price = (prices or {}).get(product_id)
            if price is None:
                price = self._market_price(product_id)
            price_decimal = self._decimal(price, f"{product_id} mark price")
            if price_decimal <= 0:
                raise CoinbaseAPIError(
                    f"Coinbase returned a non-positive mark price for {product_id}."
                )
            normalized_positions[product_id] = {
                "quantity": float(total),
                "available_quantity": float(balance["available"]),
                "held_quantity": float(balance["hold"]),
                "total_quantity": float(total),
                "average_price": None,
                "mark_price": float(price_decimal),
                "market_value": float(total * price_decimal),
                "base_currency": currency,
                "quote_currency": self.quote_currency,
                "source": "coinbase_account_balance",
            }

        if not total_equity.is_finite() or total_equity <= 0:
            raise CoinbaseAPIError(
                "Coinbase account balances do not produce a positive finite equity."
            )
        return {
            "cash": float(available_quote),
            "equity": float(total_equity),
            "positions": normalized_positions,
            "balances": normalized_balances,
            "buying_power": float(available_quote),
            "account_status": "active" if accounts else "empty",
            "broker": "coinbase_advanced",
            "mode": "live",
            "valuation_currency": self.quote_currency,
        }

    def get_account_snapshot(
        self,
        prices: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        return self.snapshot(prices)

    def verify_account(self, product_id: str) -> dict[str, Any]:
        product_id = self._product_id(product_id)
        if not self.account_region:
            raise CoinbaseEligibilityError(
                "Set COINBASE_ACCOUNT_REGION to the verified account country."
            )
        if self.account_region != "IN":
            raise CoinbaseEligibilityError(
                "This Coinbase adapter is configured for India accounts only."
            )

        permissions = self.client.get_key_permissions()
        if permissions.get("can_view") is not True:
            raise CoinbaseEligibilityError(
                "Coinbase API key does not have view permission."
            )
        if permissions.get("can_trade") is not True:
            raise CoinbaseEligibilityError(
                "Coinbase API key does not have trade permission."
            )

        accounts = self.client.get_accounts()
        quote_accounts = [
            account
            for account in accounts
            if str(account.get("currency", "")).upper() == self.quote_currency
            and account.get("active") is True
            and account.get("ready") is True
            and account.get("platform", "ACCOUNT_PLATFORM_CONSUMER")
            == "ACCOUNT_PLATFORM_CONSUMER"
        ]
        if not quote_accounts:
            raise CoinbaseEligibilityError(
                f"No active, ready Coinbase spot account is available for "
                f"{self.quote_currency}."
            )

        product = self.client.get_product(product_id)
        if product.get("product_id") != product_id:
            raise CoinbaseEligibilityError(
                "Coinbase product lookup did not return the requested product."
            )
        if (
            product.get("product_type", "SPOT") != "SPOT"
            or product.get("is_disabled") is True
            or product.get("trading_disabled") is True
            or product.get("view_only") is True
            or product.get("cancel_only") is True
            or product.get("post_only") is True
        ):
            raise CoinbaseEligibilityError(
                f"Coinbase spot product {product_id} is not tradable for this account."
            )
        if product_id.split("-", 1)[1] != self.quote_currency:
            raise CoinbaseEligibilityError(
                f"Coinbase product {product_id} does not use configured quote "
                f"currency {self.quote_currency}."
            )

        verifier = self._regional_eligibility_verifier
        if verifier is None:
            raise CoinbaseEligibilityError(
                "Coinbase Advanced Trade does not attest account residency. "
                "The regional environment setting is only an operator assertion; "
                "live orders require a trusted verifier backed by authoritative "
                "account-specific regional eligibility evidence."
            )
        regional_evidence = verifier(self.account_region, product_id)
        account_ids = {
            str(account.get("uuid", "")).strip()
            for account in quote_accounts
            if str(account.get("uuid", "")).strip()
        }
        if (
            not self.eligibility_account_id
            or self.eligibility_account_id not in account_ids
            or not isinstance(regional_evidence, dict)
            or regional_evidence.get("verified") is not True
            or regional_evidence.get("region") != self.account_region
            or regional_evidence.get("product_id") != product_id
            or regional_evidence.get("account_id") != self.eligibility_account_id
            or not str(regional_evidence.get("source", "")).strip()
            or not str(regional_evidence.get("evidence_id", "")).strip()
        ):
            raise CoinbaseEligibilityError(
                "Authoritative regional eligibility evidence is missing or invalid."
            )

        fee_summary = self.client.get_transaction_summary()
        fee_tier = fee_summary.get("fee_tier")
        if not isinstance(fee_tier, dict):
            raise CoinbaseEligibilityError(
                "Coinbase transaction summary has no authoritative spot fee tier."
            )
        taker_fee_rate = self._decimal(
            fee_tier.get("taker_fee_rate"), "taker fee rate"
        )
        if taker_fee_rate < 0 or taker_fee_rate >= 1:
            raise CoinbaseEligibilityError(
                "Coinbase returned an invalid taker fee rate."
            )
        tax_rate = Decimal(0)
        tax = fee_summary.get("goods_and_services_tax")
        if tax is not None:
            if not isinstance(tax, dict) or tax.get("type") not in {
                "INCLUSIVE",
                "EXCLUSIVE",
            }:
                raise CoinbaseEligibilityError(
                    "Coinbase returned an incomplete fee tax configuration."
                )
            tax_rate = self._decimal(tax.get("rate"), "goods and services tax rate")
            if tax_rate < 0 or tax_rate >= 1:
                raise CoinbaseEligibilityError(
                    "Coinbase returned an invalid goods and services tax rate."
                )
            if tax.get("type") == "INCLUSIVE":
                tax_rate = Decimal(0)
        effective_taker_fee_rate = taker_fee_rate * (Decimal(1) + tax_rate)

        def available_for(currency: str) -> Decimal:
            return sum(
                (
                    self._amount(account, "available_balance")
                    for account in accounts
                    if str(account.get("currency", "")).strip().upper()
                    == currency
                    and account.get("active") is True
                    and account.get("ready") is True
                    and account.get("platform", "ACCOUNT_PLATFORM_CONSUMER")
                    == "ACCOUNT_PLATFORM_CONSUMER"
                ),
                Decimal(0),
            )

        return {
            "eligible": True,
            "account_access_verified": True,
            "account_region_configured": self.account_region,
            "region_confirmed_by_operator": self._region_eligibility_asserted,
            "region_verified_by_api": False,
            "region_eligibility_verified": True,
            "product_id": product_id,
            "product_type": "SPOT",
            "trade_permission": True,
            "available_quote_balance": available_for(self.quote_currency),
            "available_base_balance": available_for(product_id.split("-", 1)[0]),
            "effective_taker_fee_rate": effective_taker_fee_rate,
        }

    @staticmethod
    def _quantity_string(quantity: float, product: dict[str, Any]) -> str:
        value = CoinbaseSpotBroker._decimal(quantity, "order quantity")
        increment = CoinbaseSpotBroker._decimal(
            product.get("base_increment"), "base increment"
        )
        minimum = CoinbaseSpotBroker._decimal(
            product.get("base_min_size"), "minimum base size"
        )
        maximum = CoinbaseSpotBroker._decimal(
            product.get("base_max_size"), "maximum base size"
        )
        if increment <= 0 or value < minimum or value > maximum:
            raise ValueError("Order quantity is outside Coinbase product limits.")
        if value % increment != 0:
            raise ValueError(
                "Order quantity is not aligned to Coinbase base_increment."
            )
        return format(value, "f")

    def submit(self, order: Order) -> dict[str, Any]:
        require_simulation_broker(self, "Coinbase order submission")
        if not isinstance(order, Order):
            return {
                "success": False,
                "status": OrderStatus.REJECTED.value,
                "reason": "Invalid ULTRON order.",
            }
        if not self._live_orders_enabled:
            return {
                "success": False,
                "status": OrderStatus.REJECTED.value,
                "stage": "live_authorization",
                "reason": (
                    "Coinbase live order submission is disabled. Set "
                    "ULTRON_EXECUTION_BACKEND=coinbase_live and the explicit "
                    "ULTRON_LIVE_TRADING_AUTHORIZATION acknowledgement to enable."
                ),
                "order_id": order.id,
            }

        product_id = self._product_id(order.symbol)
        if order.side not in {OrderSide.BUY, OrderSide.SELL}:
            return {
                "success": False,
                "status": OrderStatus.REJECTED.value,
                "reason": "Unsupported order side.",
                "order_id": order.id,
            }

        with self._lock:
            previous = self._accepted_orders.get(order.id)
            if previous is not None:
                return {**previous, "duplicate": True}
            unresolved = self._unresolved_orders.get(order.id)
            if unresolved is not None:
                return {
                    **unresolved,
                    "duplicate": True,
                    "reason": (
                        "This client order ID has an unresolved submission. "
                        "Reconcile it before retrying."
                    ),
                }

            try:
                eligibility = self.verify_account(product_id)
                product = self.client.get_product(product_id)
                quantity = self._quantity_string(order.quantity, product)
                bid, ask = self._book_quote(product_id)
                limit_price = ask if order.side is OrderSide.BUY else bid
                deviation_pct = (
                    abs(limit_price / self._decimal(order.price, "reference price") - 1)
                    * Decimal(100)
                )
                if deviation_pct > Decimal(
                    str(self.max_execution_price_deviation_pct)
                ):
                    raise ValueError(
                        "Fresh Coinbase execution price exceeds the configured "
                        "deviation limit from the risk-checked reference price."
                    )
                quote_increment = self._decimal(
                    product.get("quote_increment"), "quote increment"
                )
                if quote_increment <= 0 or limit_price % quote_increment != 0:
                    raise ValueError(
                        "Coinbase best price is not aligned to quote_increment."
                    )
                notional = limit_price * self._decimal(quantity, "order quantity")
                min_notional = self._decimal(
                    product.get("quote_min_size"), "minimum quote size"
                )
                max_notional = self._decimal(
                    product.get("quote_max_size"), "maximum quote size"
                )
                if notional < min_notional or notional > max_notional:
                    raise ValueError(
                        "Order notional is outside Coinbase product limits."
                    )
                available_balance = (
                    eligibility["available_quote_balance"]
                    if order.side is OrderSide.BUY
                    else eligibility["available_base_balance"]
                )
                required_balance = (
                    notional
                    * (
                        Decimal(1)
                        + eligibility["effective_taker_fee_rate"]
                    )
                    if order.side is OrderSide.BUY
                    else self._decimal(quantity, "order quantity")
                )
                if required_balance > available_balance:
                    raise ValueError(
                        "Order quantity exceeds authoritative available Coinbase "
                        "funds after account holds."
                    )
            except (CoinbaseAPIError, ValueError) as exc:
                return {
                    "success": False,
                    "status": OrderStatus.REJECTED.value,
                    "stage": "eligibility_or_validation",
                    "reason": str(exc),
                    "order_id": order.id,
                }

            self._unresolved_orders[order.id] = {
                "success": False,
                "status": OrderStatus.UNKNOWN.value,
                "client_order_id": order.id,
                "unresolved": True,
            }
            try:
                response = self.client.create_limit_ioc_order(
                    client_order_id=order.id,
                    product_id=product_id,
                    side=order.side.value.upper(),
                    base_size=quantity,
                    limit_price=format(limit_price, "f"),
                )
            except CoinbaseRequestTimeout:
                unresolved_result = {
                    "success": False,
                    "status": OrderStatus.UNKNOWN.value,
                    "stage": "broker_submission",
                    "reason": (
                        "Coinbase order submission timed out; outcome is unresolved. "
                        "Reconcile by client order ID before retrying."
                    ),
                    "client_order_id": order.id,
                    "unresolved": True,
                }
                self._unresolved_orders[order.id] = unresolved_result
                return dict(unresolved_result)
            except CoinbaseAPIError as exc:
                unresolved_result = {
                    "success": False,
                    "status": OrderStatus.UNKNOWN.value,
                    "stage": "broker_submission",
                    "reason": str(exc),
                    "client_order_id": order.id,
                    "unresolved": True,
                }
                self._unresolved_orders[order.id] = unresolved_result
                return dict(unresolved_result)

            if response.get("success") is not True:
                self._unresolved_orders.pop(order.id, None)
                return {
                    "success": False,
                    "status": OrderStatus.REJECTED.value,
                    "stage": "broker_submission",
                    "reason": "Coinbase rejected the order.",
                    "client_order_id": order.id,
                    "broker": "coinbase_advanced",
                    "mode": "live",
                }
            success = response.get("success_response")
            if not isinstance(success, dict) or not success.get("order_id"):
                unresolved_result = {
                    "success": False,
                    "status": OrderStatus.UNKNOWN.value,
                    "stage": "broker_submission",
                    "reason": (
                        "Coinbase accepted the request without returning an order ID; "
                        "reconciliation is required."
                    ),
                    "client_order_id": order.id,
                    "unresolved": True,
                }
                self._unresolved_orders[order.id] = unresolved_result
                return dict(unresolved_result)

            result = {
                "success": True,
                "accepted": True,
                "status": self._normalize_status(
                    success.get("status", "PENDING"), Decimal(0)
                ),
                "order_id": str(success["order_id"]),
                "client_order_id": order.id,
                "symbol": product_id,
                "side": order.side.value,
                "quantity": float(order.quantity),
                "reference_price": float(order.price),
                "filled_quantity": None,
                "remaining_quantity": None,
                "fills_reconciled": False,
                "broker": "coinbase_advanced",
                "mode": "live",
            }
            self._accepted_orders[order.id] = result
            self._unresolved_orders.pop(order.id, None)
            return dict(result)

    @staticmethod
    def _normalize_status(status: Any, filled: Decimal) -> str:
        value = str(status or "UNKNOWN").upper()
        if value == "FILLED":
            return OrderStatus.FILLED.value
        if value in {"CANCELLED", "CANCELED"}:
            return OrderStatus.CANCELLED.value
        if value == "EXPIRED":
            return OrderStatus.EXPIRED.value
        if value in {"FAILED", "REJECTED"}:
            return OrderStatus.REJECTED.value
        if value in {"CANCEL_QUEUED", "CANCEL_REQUESTED"}:
            return OrderStatus.CANCEL_REQUESTED.value
        if filled > 0:
            return OrderStatus.PARTIALLY_FILLED.value
        if value in {"OPEN", "QUEUED", "PENDING"}:
            return OrderStatus.ACKNOWLEDGED.value
        return OrderStatus.UNKNOWN.value

    def _order_payload(self, order_id: str) -> dict[str, Any]:
        response = self.client.get_order(order_id)
        order = response.get("order")
        if not isinstance(order, dict):
            raise CoinbaseAPIError(
                "Coinbase order response is missing the order."
            )
        if order.get("order_id") != order_id:
            raise CoinbaseAPIError(
                "Coinbase order response did not match the requested order."
            )
        return order

    def _status_from_order(
        self,
        order_id: str,
        order: dict[str, Any],
    ) -> dict[str, Any]:
        filled = self._decimal(order.get("filled_size", "0"), "filled size")
        requested_value: Any = order.get("base_size")
        configuration = order.get("order_configuration")
        if requested_value is None and isinstance(configuration, dict):
            for option in configuration.values():
                if isinstance(option, dict) and option.get("base_size") is not None:
                    requested_value = option["base_size"]
                    break
        requested = (
            self._decimal(requested_value, "requested size")
            if requested_value is not None
            else None
        )
        if filled < 0 or requested is not None and (requested <= 0 or filled > requested):
            raise CoinbaseAPIError(
                "Coinbase order quantities are inconsistent."
            )
        if (
            str(order.get("status", "")).upper() == "FILLED"
            and requested is not None
            and filled != requested
        ):
            raise CoinbaseAPIError(
                "Coinbase marked an order filled before its full quantity reconciled."
            )
        status = self._normalize_status(order.get("status"), filled)
        return {
            "order_id": str(order.get("order_id", order_id)),
            "client_order_id": order.get("client_order_id"),
            "symbol": order.get("product_id"),
            "state": status,
            "filled_quantity": float(filled),
            "remaining_quantity": (
                float(max(Decimal(0), requested - filled))
                if requested is not None
                else None
            ),
            "average_filled_price": (
                float(self._decimal(order["average_filled_price"], "average fill price"))
                if order.get("average_filled_price") not in (None, "")
                else None
            ),
            "fees": (
                float(self._decimal(order["total_fees"], "total fees"))
                if order.get("total_fees") not in (None, "")
                else None
            ),
            "valid": True,
        }

    def get_order_status(self, order_id: str) -> dict[str, Any]:
        return self._status_from_order(
            order_id,
            self._order_payload(order_id),
        )

    def reconcile_submission(
        self,
        client_order_id: str,
        product_id: str,
        *,
        created_at: datetime,
    ) -> dict[str, Any]:
        if not isinstance(client_order_id, str) or not client_order_id.strip():
            raise ValueError("A Coinbase client order ID is required.")
        product_id = self._product_id(product_id)
        if created_at.tzinfo is None:
            raise ValueError("Order creation time must be timezone-aware.")
        window_start = created_at.astimezone(UTC) - timedelta(minutes=5)
        window_end = datetime.now(UTC) + timedelta(minutes=1)
        candidates = self.client.get_orders(
            product_id=product_id,
            start_date=window_start,
            end_date=window_end,
        )
        matches = [
            candidate
            for candidate in candidates
            if candidate.get("client_order_id") == client_order_id
            and candidate.get("product_id") == product_id
        ]
        if len(matches) > 1:
            raise CoinbaseAPIError(
                "Coinbase returned multiple orders for one client order ID."
            )
        if not matches:
            return {
                "success": False,
                "reconciled": False,
                "unresolved": True,
                "client_order_id": client_order_id,
                "symbol": product_id,
                "state": OrderStatus.UNKNOWN.value,
            }

        order_id = matches[0].get("order_id")
        if not isinstance(order_id, str) or not order_id:
            raise CoinbaseAPIError(
                "Coinbase order search returned a matching order without an order ID."
            )
        result = self.reconcile_order(order_id)
        result["client_order_id"] = client_order_id
        with self._lock:
            self._unresolved_orders.pop(client_order_id, None)
            self._accepted_orders[client_order_id] = {
                "success": True,
                "accepted": True,
                "status": result["state"],
                "order_id": order_id,
                "client_order_id": client_order_id,
                "symbol": product_id,
                "fills_reconciled": True,
                "broker": "coinbase_advanced",
                "mode": "live",
            }
        return result

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        require_simulation_broker(self, "Coinbase order cancellation")
        response = self.client.cancel_order(order_id)
        results = response.get("results")
        if not isinstance(results, list) or not results:
            raise CoinbaseAPIError(
                "Coinbase returned no cancellation result."
            )
        result = next(
            (
                item
                for item in results
                if isinstance(item, dict) and item.get("order_id") == order_id
            ),
            None,
        )
        if result is None:
            raise CoinbaseAPIError(
                "Coinbase cancellation response did not match the requested order."
            )
        if result.get("success") is not True:
            return {
                "success": False,
                "state": OrderStatus.UNKNOWN.value,
                "order_id": order_id,
                "reason": "Coinbase did not accept the cancellation request.",
                "cancelled": False,
            }
        status = self.get_order_status(order_id)
        return {
            "success": True,
            "state": status["state"],
            "order_id": order_id,
            "cancel_requested": True,
            "cancelled": status["state"] == OrderStatus.CANCELLED.value,
        }

    def reconcile_order(self, order_id: str) -> dict[str, Any]:
        order = self._order_payload(order_id)
        status = self._status_from_order(order_id, order)
        fills = self.client.get_order_fills(order_id)
        normalized_fills: list[dict[str, Any]] = []
        seen_fill_ids: set[str] = set()
        fill_quantity = Decimal(0)
        fill_value = Decimal(0)
        fill_fees = Decimal(0)
        for fill in fills:
            fill_id = str(fill.get("entry_id") or fill.get("trade_id") or "")
            if not fill_id or fill_id in seen_fill_ids:
                if fill_id:
                    continue
                raise CoinbaseAPIError(
                    "Coinbase returned a fill without a stable fill ID."
                )
            seen_fill_ids.add(fill_id)
            if fill.get("order_id", order_id) != order_id:
                raise CoinbaseAPIError(
                    "Coinbase returned a fill for a different order."
                )
            if (
                fill.get("product_id") is not None
                and fill.get("product_id") != order.get("product_id")
            ):
                raise CoinbaseAPIError(
                    "Coinbase returned a fill for a different product."
                )
            if (
                fill.get("side") is not None
                and str(fill["side"]).upper() != str(order.get("side", "")).upper()
            ):
                raise CoinbaseAPIError(
                    "Coinbase returned a fill with a different order side."
                )
            quantity = self._decimal(fill.get("size"), "fill size")
            price = self._decimal(fill.get("price"), "fill price")
            fee_value = fill.get("commission", "0")
            fee = self._decimal(fee_value, "fill fee")
            if quantity <= 0 or price <= 0 or fee < 0:
                raise CoinbaseAPIError(
                    "Coinbase returned invalid fill values."
                )
            fill_quantity += quantity
            fill_value += quantity * price
            fill_fees += fee
            normalized_fills.append(
                {
                    "fill_id": fill_id,
                    "order_id": str(fill.get("order_id", order_id)),
                    "symbol": fill.get("product_id", order.get("product_id")),
                    "side": str(fill.get("side", order.get("side", ""))).lower(),
                    "quantity": float(quantity),
                    "price": float(price),
                    "fee": float(fee),
                    "fee_currency": fill.get("commission_asset"),
                    "timestamp": fill.get("trade_time"),
                }
            )

        expected_quantity = self._decimal(
            order.get("filled_size", "0"), "filled size"
        )
        total_fees = order.get("total_fees")
        if expected_quantity != fill_quantity:
            raise CoinbaseAPIError(
                "Coinbase order filled size does not reconcile with its authoritative fill list."
            )
        if (
            total_fees not in (None, "")
            and self._decimal(total_fees, "order fees") != fill_fees
        ):
            raise CoinbaseAPIError(
                "Coinbase order fees do not reconcile with its authoritative fill list."
            )

        average_price = (
            float(fill_value / fill_quantity)
            if fill_quantity > 0
            else None
        )
        return {
            **status,
            "reconciled": True,
            "fills": normalized_fills,
            "fill_value": float(fill_value),
            "fill_fees": float(fill_fees),
            "average_filled_price": average_price,
        }
