from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from ultron.financial.trading.coinbase_advanced import (
    CoinbaseAdvancedClient,
    CoinbaseAPIError,
    CoinbaseAuthenticationError,
    CoinbaseRequestTimeout,
    CoinbaseSpotBroker,
)
from ultron.financial.trading.market_data.coinbase_adapter import (
    CoinbaseMarketAdapter,
)
from ultron.financial.trading.models import Order, OrderSide, OrderStatus
from ultron.skills.market_data.assets import AssetClass
from ultron.skills.market_data.provider import MarketDataProvider


class Response:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload


class Session:
    def __init__(self, response: Response | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def request(self, method: str, url: str, **kwargs: object) -> Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def valid_private_key() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def test_client_signs_documented_es256_uri_claim_without_logging_key():
    key = valid_private_key()
    session = Session(Response(200, {"ok": True}))
    client = CoinbaseAdvancedClient(
        api_key_name="organizations/test/apiKeys/test-key",
        api_private_key=key,
        session=session,
    )

    assert client.request(
        "GET",
        "/api/v3/brokerage/accounts",
        authenticated=True,
    ) == {"ok": True}

    authorization = session.calls[0]["headers"]["Authorization"]
    claims = jwt.decode(
        authorization.removeprefix("Bearer "),
        options={"verify_signature": False},
    )
    assert claims["sub"] == "organizations/test/apiKeys/test-key"
    assert claims["uri"] == "GET api.coinbase.com/api/v3/brokerage/accounts"
    assert key not in authorization


def test_authenticated_request_rejects_missing_credentials():
    client = CoinbaseAdvancedClient(session=Session(Response(200, {})))

    with pytest.raises(CoinbaseAuthenticationError, match="COINBASE_API_KEY_NAME"):
        client.request("GET", "/api/v3/brokerage/accounts", authenticated=True)


def test_limit_ioc_order_uses_documented_coinbase_request_schema():
    session = Session(
        Response(
            200,
            {
                "success": True,
                "success_response": {"order_id": "exchange-order-1"},
            },
        )
    )
    client = CoinbaseAdvancedClient(
        api_key_name="test-key",
        api_private_key=valid_private_key(),
        session=session,
    )

    result = client.create_limit_ioc_order(
        client_order_id="client-order-1",
        product_id="BTC-INR",
        side="BUY",
        base_size="0.01",
        limit_price="5000001",
    )

    assert result["success"] is True
    assert session.calls[0]["method"] == "POST"
    assert session.calls[0]["url"].endswith("/api/v3/brokerage/orders")
    assert session.calls[0]["json"] == {
        "client_order_id": "client-order-1",
        "product_id": "BTC-INR",
        "side": "BUY",
        "order_configuration": {
            "sor_limit_ioc": {
                "base_size": "0.01",
                "limit_price": "5000001",
            }
        },
    }


def test_transaction_summary_reads_authenticated_spot_fee_tier():
    session = Session(
        Response(
            200,
            {
                "fee_tier": {
                    "taker_fee_rate": "0.001",
                    "maker_fee_rate": "0.0005",
                }
            },
        )
    )
    client = CoinbaseAdvancedClient(
        api_key_name="test-key",
        api_private_key=valid_private_key(),
        session=session,
    )

    assert client.get_transaction_summary()["fee_tier"]["taker_fee_rate"] == "0.001"
    assert session.calls[0]["method"] == "GET"
    assert session.calls[0]["url"].endswith(
        "/api/v3/brokerage/transaction_summary"
    )
    assert session.calls[0]["params"] == {"product_type": "SPOT"}
    assert session.calls[0]["headers"]["Authorization"].startswith("Bearer ")


@pytest.mark.parametrize("status_code", [401, 403])
def test_client_classifies_authentication_failures_without_exposing_response(
    status_code: int,
):
    client = CoinbaseAdvancedClient(
        api_key_name="test-key",
        api_private_key=valid_private_key(),
        session=Session(Response(status_code, {"message": "private error body"})),
    )

    with pytest.raises(CoinbaseAuthenticationError) as error:
        client.request("GET", "/api/v3/brokerage/accounts", authenticated=True)

    assert error.value.status_code == status_code
    assert "private error body" not in str(error.value)


def test_client_surfaces_api_errors_without_response_bodies():
    client = CoinbaseAdvancedClient(
        session=Session(Response(503, {"message": "internal detail"}))
    )

    with pytest.raises(CoinbaseAPIError) as error:
        client.get_product_book("BTC-INR")

    assert error.value.status_code == 503
    assert "internal detail" not in str(error.value)


def test_client_reports_transport_timeout():
    client = CoinbaseAdvancedClient(session=Session(requests.Timeout()))

    with pytest.raises(CoinbaseRequestTimeout):
        client.get_product_book("BTC-INR")


def test_client_searches_paginated_order_history_by_product_and_time_range():
    class PagedSession:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def request(self, method: str, url: str, **kwargs: object) -> Response:
            self.calls.append({"method": method, "url": url, **kwargs})
            if len(self.calls) == 1:
                return Response(200, {"orders": [], "has_next": True, "cursor": "next"})
            return Response(
                200,
                {
                    "orders": [{"client_order_id": "known-client-id"}],
                    "has_next": False,
                },
            )

    session = PagedSession()
    client = CoinbaseAdvancedClient(
        api_key_name="test-key",
        api_private_key=valid_private_key(),
        session=session,
    )

    orders = client.get_orders(
        product_id="BTC-INR",
        start_date=datetime.now(UTC) - timedelta(minutes=5),
        end_date=datetime.now(UTC),
    )

    assert orders == [{"client_order_id": "known-client-id"}]
    assert len(session.calls) == 2
    assert session.calls[0]["url"].endswith("/orders/historical/batch")
    assert ("product_ids", "BTC-INR") in session.calls[0]["params"]
    assert ("cursor", "next") in session.calls[1]["params"]


class BrokerClient:
    def __init__(
        self,
        *,
        timeout_on_submit: bool = False,
        available_quote: str = "60000",
    ) -> None:
        self.timeout_on_submit = timeout_on_submit
        self.available_quote = available_quote
        self.submit_calls: list[dict[str, str]] = []
        self.order = {
            "order_id": "exchange-order-1",
            "client_order_id": "client-order-1",
            "product_id": "BTC-INR",
            "side": "BUY",
            "status": "OPEN",
            "base_size": "0.01",
            "filled_size": "0",
        }
        self.fills: list[dict[str, str]] = []
        self.orders_for_search = [self.order]

    def get_key_permissions(self):
        return {"can_view": True, "can_trade": True}

    def get_transaction_summary(self):
        return {
            "fee_tier": {
                "taker_fee_rate": "0.001",
                "maker_fee_rate": "0",
            },
            "goods_and_services_tax": {"rate": "0.18", "type": "EXCLUSIVE"},
        }

    def get_accounts(self):
        return [
            {
                "uuid": "fixture-account",
                "currency": "INR",
                "active": True,
                "ready": True,
                "available_balance": {"value": self.available_quote},
                "hold": {"value": "100"},
            }
        ]

    def get_product(self, product_id: str):
        return {
            "product_id": product_id,
            "product_type": "SPOT",
            "is_disabled": False,
            "trading_disabled": False,
            "view_only": False,
            "base_increment": "0.00000001",
            "quote_increment": "1",
            "base_min_size": "0.00000001",
            "base_max_size": "1000",
            "quote_min_size": "1",
            "quote_max_size": "1000000001",
        }

    def create_limit_ioc_order(self, **kwargs: str):
        self.submit_calls.append(kwargs)
        if self.timeout_on_submit:
            raise CoinbaseRequestTimeout("request timed out")
        return {
            "success": True,
            "success_response": {
                "order_id": "exchange-order-1",
                "status": "PENDING",
            },
        }

    def get_order(self, order_id: str):
        return {"order": self.order}

    def get_orders(self, **kwargs: object):
        return self.orders_for_search

    def get_order_fills(self, order_id: str):
        return self.fills

    def cancel_order(self, order_id: str):
        return {"results": [{"order_id": order_id, "success": True}]}

    def get_product_book(self, product_id: str):
        return {
            "pricebook": {
                "product_id": product_id,
                "time": datetime.now(UTC).isoformat(),
                "bids": [{"price": "4999999"}],
                "asks": [{"price": "5000001"}],
            }
        }


def authorized_broker(client: BrokerClient) -> CoinbaseSpotBroker:
    return CoinbaseSpotBroker(
        client=client,
        quote_currency="INR",
        account_region="IN",
        eligibility_account_id="fixture-account",
        region_eligibility_confirmed=True,
        live_trading_authorized=True,
        regional_eligibility_verifier=lambda region, product: {
            "verified": True,
            "region": region,
            "product_id": product,
            "account_id": "fixture-account",
            "source": "test-authoritative-evidence",
            "evidence_id": f"{region}:{product}",
        },
    )


def order(order_id: str = "client-order-1") -> Order:
    return Order(
        id=order_id,
        symbol="BTC-INR",
        side=OrderSide.BUY,
        quantity=0.01,
        price=5_000_000.0,
    )


def test_broker_repeats_are_idempotent_for_accepted_client_order_id():
    client = BrokerClient()
    broker = authorized_broker(client)

    first = broker.submit(order())
    second = broker.submit(order())

    assert first["success"] is True
    assert first["accepted"] is True
    assert first["fills_reconciled"] is False
    assert second["duplicate"] is True
    assert len(client.submit_calls) == 1
    assert client.submit_calls[0] == {
        "client_order_id": "client-order-1",
        "product_id": "BTC-INR",
        "side": "BUY",
        "base_size": "0.01",
        "limit_price": "5000001",
    }


def test_broker_reserves_authoritative_taker_fee_and_exclusive_tax():
    client = BrokerClient(available_quote="50050")
    broker = authorized_broker(client)

    result = broker.submit(order())

    assert result["success"] is False
    assert result["stage"] == "eligibility_or_validation"
    assert "available Coinbase funds" in result["reason"]
    assert client.submit_calls == []


def test_live_submission_is_disabled_without_explicit_authorization():
    client = BrokerClient()
    broker = CoinbaseSpotBroker(
        client=client,
        quote_currency="INR",
        account_region="IN",
        region_eligibility_confirmed=True,
    )

    result = broker.submit(order())

    assert result["success"] is False
    assert result["stage"] == "live_authorization"
    assert not client.submit_calls


def test_broker_rejects_submission_without_regional_verifier():
    client = BrokerClient()
    broker = CoinbaseSpotBroker(
        client=client,
        quote_currency="INR",
        account_region="IN",
        region_eligibility_confirmed=False,
        live_trading_authorized=True,
    )

    result = broker.submit(order())

    assert result["success"] is False
    assert result["stage"] == "eligibility_or_validation"
    assert not client.submit_calls


def test_operator_region_assertion_does_not_verify_eligibility():
    client = BrokerClient()
    broker = CoinbaseSpotBroker(
        client=client,
        quote_currency="INR",
        account_region="IN",
        region_eligibility_confirmed=True,
        live_trading_authorized=True,
    )

    result = broker.submit(order())

    assert result["success"] is False
    assert result["stage"] == "eligibility_or_validation"
    assert "trusted verifier" in result["reason"]
    assert not client.submit_calls


def test_regional_verifier_must_bind_evidence_to_account_region_and_product():
    client = BrokerClient()
    broker = CoinbaseSpotBroker(
        client=client,
        quote_currency="INR",
        account_region="IN",
        eligibility_account_id="fixture-account",
        live_trading_authorized=True,
        regional_eligibility_verifier=lambda region, product: {
            "verified": True,
            "region": region,
            "product_id": "ETH-INR",
            "account_id": "fixture-account",
            "source": "test-authoritative-evidence",
            "evidence_id": "fixture-evidence",
        },
    )

    result = broker.submit(order())

    assert result["success"] is False
    assert result["stage"] == "eligibility_or_validation"
    assert "evidence is missing or invalid" in result["reason"]
    assert not client.submit_calls


def test_regional_verifier_must_bind_evidence_to_authenticated_quote_account():
    client = BrokerClient()
    broker = CoinbaseSpotBroker(
        client=client,
        quote_currency="INR",
        account_region="IN",
        eligibility_account_id="fixture-account",
        live_trading_authorized=True,
        regional_eligibility_verifier=lambda region, product: {
            "verified": True,
            "region": region,
            "product_id": product,
            "account_id": "unrelated-account",
            "source": "test-authoritative-evidence",
            "evidence_id": "fixture-evidence",
        },
    )

    result = broker.submit(order())

    assert result["success"] is False
    assert result["stage"] == "eligibility_or_validation"
    assert "evidence is missing or invalid" in result["reason"]
    assert not client.submit_calls


def test_regional_verifier_requires_configured_account_uuid_from_provider():
    client = BrokerClient()
    broker = CoinbaseSpotBroker(
        client=client,
        quote_currency="INR",
        account_region="IN",
        live_trading_authorized=True,
        regional_eligibility_verifier=lambda region, product: {
            "verified": True,
            "region": region,
            "product_id": product,
            "account_id": "fixture-account",
            "source": "test-authoritative-evidence",
            "evidence_id": "fixture-evidence",
        },
    )

    result = broker.submit(order())

    assert result["success"] is False
    assert result["stage"] == "eligibility_or_validation"
    assert "evidence is missing or invalid" in result["reason"]
    assert not client.submit_calls


def test_cancel_request_is_not_misreported_as_confirmed_cancellation():
    client = BrokerClient()
    broker = authorized_broker(client)

    result = broker.cancel_order("exchange-order-1")

    assert result["success"] is True
    assert result["cancel_requested"] is True
    assert result["cancelled"] is False


def test_broker_does_not_retry_unresolved_timeout_for_same_order_id():
    client = BrokerClient(timeout_on_submit=True)
    broker = authorized_broker(client)

    first = broker.submit(order())
    second = broker.submit(order())

    assert first["status"] == OrderStatus.UNKNOWN.value
    assert first["unresolved"] is True
    assert second["duplicate"] is True
    assert len(client.submit_calls) == 1


def test_timed_out_submission_can_be_resolved_by_authoritative_order_search():
    client = BrokerClient(timeout_on_submit=True)
    broker = authorized_broker(client)

    assert broker.submit(order())["unresolved"] is True
    result = broker.reconcile_submission(
        "client-order-1",
        "BTC-INR",
        created_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    assert result["reconciled"] is True
    assert result["order_id"] == "exchange-order-1"
    assert result["client_order_id"] == "client-order-1"
    assert broker.submit(order())["duplicate"] is True
    assert len(client.submit_calls) == 1


def test_unresolved_submission_remains_unresolved_when_search_has_no_match():
    client = BrokerClient(timeout_on_submit=True)
    client.orders_for_search = []
    broker = authorized_broker(client)

    result = broker.reconcile_submission(
        "client-order-1",
        "BTC-INR",
        created_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    assert result["unresolved"] is True
    assert result["reconciled"] is False


def test_reconciliation_tracks_partial_fills_and_fees_authoritatively():
    client = BrokerClient()
    client.order.update(
        status="OPEN",
        filled_size="0.004",
        total_fees="10",
        average_filled_price="5000000",
    )
    client.fills = [
        {
            "entry_id": "fill-1",
            "order_id": "exchange-order-1",
            "product_id": "BTC-INR",
            "side": "BUY",
            "size": "0.004",
            "price": "5000000",
            "commission": "10",
            "commission_asset": "INR",
            "trade_time": datetime.now(UTC).isoformat(),
        }
    ]
    broker = authorized_broker(client)

    status = broker.get_order_status("exchange-order-1")
    reconciled = broker.reconcile_order("exchange-order-1")

    assert status["state"] == OrderStatus.PARTIALLY_FILLED.value
    assert status["filled_quantity"] == pytest.approx(0.004)
    assert status["remaining_quantity"] == pytest.approx(0.006)
    assert reconciled["reconciled"] is True
    assert reconciled["fill_fees"] == pytest.approx(10)
    assert reconciled["fills"][0]["fill_id"] == "fill-1"


def test_reconciliation_rejects_fill_mismatch():
    client = BrokerClient()
    client.order.update(filled_size="0.004", total_fees="1")
    client.fills = [
        {
            "entry_id": "fill-1",
            "size": "0.003",
            "price": "5000000",
            "commission": "1",
        }
    ]

    with pytest.raises(CoinbaseAPIError, match="filled size does not reconcile"):
        authorized_broker(client).reconcile_order("exchange-order-1")


def test_account_snapshot_reconciles_available_and_held_balances():
    client = BrokerClient()
    client.get_accounts = lambda: [
        {
            "currency": "INR",
            "available_balance": {"value": "50000"},
            "hold": {"value": "100"},
        },
        {
            "currency": "BTC",
            "available_balance": {"value": "0.1"},
            "hold": {"value": "0.01"},
        },
    ]

    snapshot = authorized_broker(client).snapshot()

    assert snapshot["cash"] == pytest.approx(50000)
    assert snapshot["equity"] == pytest.approx(600100)
    assert snapshot["positions"]["BTC-INR"]["quantity"] == pytest.approx(0.11)
    assert snapshot["positions"]["BTC-INR"]["available_quantity"] == pytest.approx(0.1)
    assert snapshot["positions"]["BTC-INR"]["held_quantity"] == pytest.approx(0.01)
    assert snapshot["positions"]["BTC-INR"]["total_quantity"] == pytest.approx(0.11)
    assert snapshot["positions"]["BTC-INR"]["market_value"] == pytest.approx(550000)
    assert snapshot["positions"]["BTC-INR"]["average_price"] is None


def test_public_candle_adapter_returns_validated_complete_history():
    now = datetime.now(UTC)
    candles = [
        {
            "start": str(int((now - timedelta(days=3)).timestamp())),
            "open": "10",
            "high": "12",
            "low": "9",
            "close": "11",
            "volume": "100",
        },
        {
            "start": str(int((now - timedelta(days=1)).timestamp())),
            "open": "11",
            "high": "13",
            "low": "10",
            "close": "12",
            "volume": "120",
        },
    ]

    class CandleClient:
        def get_public_candles(self, product_id: str, **kwargs: object):
            assert product_id == "BTC-INR"
            assert kwargs["limit"] == 350
            return {"candles": candles}

    history = CoinbaseMarketAdapter(client=CandleClient()).get_history(
        "BTC-INR", "5d"
    )

    assert [record["close"] for record in history] == [11.0, 12.0]
    assert history[0]["timestamp"] < history[1]["timestamp"]


def test_market_data_provider_routes_coinbase_crypto_without_fallback():
    class DataAdapter:
        def _product_id(self, symbol: str) -> str:
            assert symbol == "BTC-INR"
            return symbol

        def get_snapshot(self, symbol: str):
            return {
                "price": 5_000_000.0,
                "timestamp": datetime.now(UTC),
                "age_seconds": 0.1,
            }

        def get_history(self, symbol: str, period: str):
            return [
                {"timestamp": datetime.now(UTC), "open": 1, "high": 2,
                 "low": 1, "close": close, "volume": 3}
                for close in (4.0, 5.0)
            ]

    provider = MarketDataProvider(provider="coinbase", coinbase_adapter=DataAdapter())
    point = provider.get_price("BTC/INR")
    snapshot = provider.get_snapshot("BTC/INR")
    history = provider.get_history("BTC/INR", "5d")

    assert point.asset_class is AssetClass.CRYPTO
    assert point.source == "coinbase_advanced"
    assert snapshot.currency == "INR"
    assert snapshot.change_percent == pytest.approx(25.0)
    assert list(history["Close"]) == [4.0, 5.0]
