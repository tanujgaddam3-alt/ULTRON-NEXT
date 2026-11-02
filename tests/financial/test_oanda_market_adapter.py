from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from ultron.financial.trading.market.models import MarketType
from ultron.financial.trading.market_data.service import MarketDataService
from ultron.financial.trading.market_data.oanda_adapter import (
    OandaAPIError,
    OandaConfigurationError,
    OandaMarketAdapter,
    OandaV20Client,
)


def quote(instrument: str = "EUR_USD", *, status: str = "tradeable", time=None):
    return {
        "instrument": instrument,
        "status": status,
        "time": (time or datetime.now(UTC)).isoformat().replace("+00:00", "Z"),
        "bids": [{"price": "1.08210", "liquidity": "1000000"}],
        "asks": [{"price": "1.08230", "liquidity": "900000"}],
    }


class FixtureClient:
    account_id = "account-fixture"
    environment = "practice"

    def __init__(self, prices=None):
        self.prices = prices or {"EUR_USD": quote()}
        self.price_calls: list[str] = []

    def get_price(self, instrument):
        self.price_calls.append(instrument)
        return {"prices": [self.prices[instrument]]}

    def get_instruments(self, instrument=None):
        return {
            "instruments": [
                {
                    "name": instrument or "EUR_USD",
                    "type": "CURRENCY",
                    "displayName": "EUR/USD",
                    "displayPrecision": 5,
                    "tradeUnitsPrecision": 0,
                    "minimumTradeSize": "1",
                    "maximumOrderUnits": "100000000",
                    "marginRate": "0.05",
                }
            ]
        }

    def get_candles(self, instrument, *, granularity, count):
        return {
            "instrument": instrument,
            "granularity": granularity,
            "candles": [
                {
                    "time": "2026-10-14T00:00:00Z",
                    "complete": True,
                    "volume": 42,
                    "mid": {"o": "1.08", "h": "1.09", "l": "1.07", "c": "1.085"},
                },
                {
                    "time": "2026-10-15T00:00:00Z",
                    "complete": False,
                    "volume": 12,
                    "mid": {"o": "1.085", "h": "1.10", "l": "1.08", "c": "1.09"},
                },
            ]
        }

    def get_account(self):
        return {
            "account": {
                "id": self.account_id,
                "currency": "USD",
                "balance": "10000.00",
                "NAV": "10100.00",
                "marginAvailable": "9500.00",
                "marginUsed": "500.00",
                "unrealizedPL": "100.00",
                "lastTransactionID": "42",
                "pendingOrderCount": 1,
                "openTradeCount": 2,
                "positions": [
                    {
                        "instrument": "EUR_USD",
                        "long": {"units": "1000", "averagePrice": "1.07"},
                        "short": {"units": "250", "averagePrice": "1.09"},
                    },
                    {
                        "instrument": "GBP_USD",
                        "long": {"units": "0", "averagePrice": "0"},
                        "short": {"units": "0", "averagePrice": "0"},
                    },
                ],
            }
        }


class Response:
    def __init__(self, value):
        self.body = json.dumps(value).encode()

    def read(self):
        return self.body

    def close(self):
        pass


def test_client_uses_documented_practice_endpoint_and_bearer_auth():
    seen = []

    def opener(request, *, timeout):
        seen.append((request, timeout))
        return Response({"account": {}})

    client = OandaV20Client(
        token="secret-token",
        account_id="account/fixture",
        opener=opener,
    )

    client.get_account_summary()

    request, timeout = seen[0]
    assert request.method == "GET"
    assert request.full_url == (
        "https://api-fxpractice.oanda.com/v3/accounts/"
        "account%2Ffixture/summary"
    )
    assert request.get_header("Authorization") == "Bearer secret-token"
    assert timeout == 5.0
    assert "secret-token" not in request.full_url


def test_client_can_select_live_read_only_endpoint_explicitly():
    client = OandaV20Client(
        token="secret-token",
        account_id="account-fixture",
        environment="live",
        opener=lambda request, **kwargs: Response({"account": {}}),
    )

    assert client.base_url == "https://api-fxtrade.oanda.com"
    assert not hasattr(client, "create_order")


def test_client_rejects_missing_credentials_and_invalid_environment():
    with pytest.raises(OandaConfigurationError, match="OANDA_API_TOKEN"):
        OandaV20Client(token="", account_id="account-fixture")
    with pytest.raises(OandaConfigurationError, match="OANDA_ACCOUNT_ID"):
        OandaV20Client(token="token", account_id="")
    with pytest.raises(OandaConfigurationError, match="practice.*live"):
        OandaV20Client(
            token="token",
            account_id="account-fixture",
            environment="unknown",
        )


@pytest.mark.parametrize(
    ("status", "failure_type"),
    [(401, "authentication"), (403, "permission"), (404, "not_found")],
)
def test_client_classifies_http_errors_without_echoing_response(status, failure_type):
    def opener(request, *, timeout):
        raise HTTPError(
            request.full_url,
            status,
            "sensitive account details",
            hdrs=None,
            fp=None,
        )

    client = OandaV20Client(
        token="secret-token",
        account_id="account-fixture",
        opener=opener,
    )
    with pytest.raises(OandaAPIError) as error:
        client.get_account()

    assert error.value.failure_type == failure_type
    assert error.value.status_code == status
    assert "sensitive account details" not in str(error.value)
    assert "secret-token" not in str(error.value)


def test_client_reports_timeouts_without_echoing_sensitive_details():
    def opener(request, *, timeout):
        raise TimeoutError("secret-token")

    client = OandaV20Client(
        token="secret-token",
        account_id="account-fixture",
        opener=opener,
    )
    with pytest.raises(OandaAPIError, match="timed out") as error:
        client.get_account()

    assert error.value.failure_type == "timeout"
    assert "secret-token" not in str(error.value)


def test_client_classifies_wrapped_socket_timeout():
    from urllib.error import URLError

    client = OandaV20Client(
        token="secret-token",
        account_id="account-fixture",
        opener=lambda request, **kwargs: (_ for _ in ()).throw(
            URLError(TimeoutError("secret-token"))
        ),
    )

    with pytest.raises(OandaAPIError, match="timed out") as error:
        client.get_account()

    assert error.value.failure_type == "timeout"
    assert "secret-token" not in str(error.value)


def test_quote_contains_timestamp_bid_ask_spread_and_polling_provenance():
    adapter = OandaMarketAdapter(client=FixtureClient())

    snapshot = adapter.get_snapshot("eur/usd")

    assert snapshot["symbol"] == "EUR_USD"
    assert snapshot["market_type"] == MarketType.FOREX.value
    assert snapshot["provider"] == "oanda_v20"
    assert snapshot["feed"] == "account_authenticated_rest_polling"
    assert snapshot["bid"] == pytest.approx(1.0821)
    assert snapshot["ask"] == pytest.approx(1.0823)
    assert snapshot["spread"] == pytest.approx(0.0002)
    assert snapshot["spread_bps"] > 0
    assert snapshot["timestamp"].tzinfo is not None
    assert snapshot["bid_liquidity"] == 1_000_000
    assert snapshot["ask_liquidity"] == 900_000


def test_oanda_quote_flows_through_shared_service_with_spread_validation():
    client = FixtureClient()
    service = MarketDataService(OandaMarketAdapter(client=client))

    result = service.get_current_price("EUR/USD")

    assert result["valid"] is True
    assert result["provider"] == "oanda_v20"
    assert result["spread"] == pytest.approx(0.0002)
    assert result["spread_bps"] > 0
    assert client.price_calls == ["EUR_USD"]


@pytest.mark.parametrize(
    ("price", "failure_type"),
    [
        (quote(time=datetime.now(UTC) - timedelta(minutes=1)), "stale_data"),
        (quote(time=datetime.now(UTC) + timedelta(minutes=1)), "stale_data"),
        (
            {
                **quote(),
                "bids": [{"price": "1.08300"}],
            },
            "invalid_response",
        ),
    ],
)
def test_quote_rejects_stale_future_or_crossed_prices(price, failure_type):
    adapter = OandaMarketAdapter(
        client=FixtureClient(prices={"EUR_USD": price}),
        max_quote_age_seconds=15,
    )

    with pytest.raises(OandaAPIError) as error:
        adapter.get_snapshot("EUR_USD")

    assert error.value.failure_type == failure_type


def test_market_status_is_provider_reported_and_cache_is_symbol_scoped():
    client = FixtureClient(
        prices={
            "EUR_USD": quote("EUR_USD", status="tradeable"),
            "USD_JPY": quote("USD_JPY", status="non-tradeable"),
        }
    )
    adapter = OandaMarketAdapter(client=client)

    assert adapter.is_market_open("EUR_USD") is True
    assert adapter.get_snapshot("EUR_USD")["symbol"] == "EUR_USD"
    assert adapter.is_market_open("USD_JPY") is False
    assert client.price_calls == ["EUR_USD", "USD_JPY"]


def test_instrument_metadata_is_account_specific_and_not_an_execution_capability():
    adapter = OandaMarketAdapter(client=FixtureClient())

    instrument = adapter.get_instrument("EUR/USD")

    assert instrument.market is MarketType.FOREX
    assert instrument.base_currency == "EUR"
    assert instrument.quote_currency == "USD"
    assert instrument.quantity_step == 1
    assert instrument.tick_size == pytest.approx(0.00001)
    assert instrument.min_quantity == 1
    assert instrument.provider_capabilities["account_instrument_available"] is True
    assert instrument.provider_capabilities["live_orders"] is False
    assert adapter.get_capabilities("EUR_USD")["supports_live_orders"] is False


def test_candle_history_preserves_provider_completeness_and_validates_order():
    adapter = OandaMarketAdapter(client=FixtureClient())

    candles = adapter.get_history("EUR_USD", granularity="D", count=2)

    assert len(candles) == 2
    assert candles[0]["is_complete"] is True
    assert candles[1]["is_complete"] is False
    assert candles[0]["provider"] == "oanda_v20"
    assert candles[0]["close"] == pytest.approx(1.085)


@pytest.mark.parametrize(
    ("granularity", "count"),
    [("bad", 10), ("D", 5001), ("D", True)],
)
def test_candle_history_rejects_invalid_request(granularity, count):
    adapter = OandaMarketAdapter(client=FixtureClient())
    with pytest.raises(ValueError):
        adapter.get_history("EUR_USD", granularity=granularity, count=count)


def test_account_snapshot_reconciles_signed_long_and_short_units():
    adapter = OandaMarketAdapter(client=FixtureClient())

    account = adapter.get_account_snapshot()

    assert account["account_access_verified"] is True
    assert account["cash"] == 10_000
    assert account["equity"] == 10_100
    assert account["buying_power"] == 9_500
    assert account["margin_used"] == 500
    assert account["unrealized_pl"] == 100
    assert account["currency"] == "USD"
    assert account["account_id"] == "account-fixture"
    assert account["last_transaction_id"] == "42"
    assert account["positions"]["EUR_USD"]["quantity"] == 750
    assert account["positions"]["EUR_USD"]["short_quantity"] == -250
    assert account["pending_order_count"] == 1


def test_account_snapshot_rejects_unmatched_account_identity():
    client = FixtureClient()
    client.get_account = lambda: {
        "account": {
            "id": "different-account",
            "positions": [],
            "balance": "1",
            "NAV": "1",
            "marginAvailable": "1",
            "marginUsed": "0",
            "unrealizedPL": "0",
            "lastTransactionID": "1",
            "pendingOrderCount": 0,
            "openTradeCount": 0,
        }
    }

    with pytest.raises(OandaAPIError, match="did not match"):
        OandaMarketAdapter(client=client).get_account_snapshot()


def test_instrument_normalization_rejects_non_currency_pair():
    with pytest.raises(ValueError, match="BASE_QUOTE"):
        OandaMarketAdapter._instrument_name("BTC-INR")


def test_account_instrument_list_must_identify_currency_pair():
    client = FixtureClient()
    client.get_instruments = lambda instrument=None: {
        "instruments": [
            {
                "name": instrument,
                "type": "CFD",
            }
        ]
    }

    with pytest.raises(OandaAPIError, match="not a spot currency pair"):
        OandaMarketAdapter(client=client).get_instrument("EUR_USD")


def test_fixture_query_contract_for_readonly_candles():
    seen = []

    def opener(request, *, timeout):
        seen.append(request)
        return Response({"candles": []})

    client = OandaV20Client(
        token="secret-token",
        account_id="account-fixture",
        opener=opener,
    )
    client.get_candles("EUR_USD", granularity="M5", count=100)

    request = seen[0]
    assert urlsplit(request.full_url).path.endswith(
        "/instruments/EUR_USD/candles"
    )
    assert parse_qs(urlsplit(request.full_url).query) == {
        "price": ["M"],
        "granularity": ["M5"],
        "count": ["100"],
        "smooth": ["false"],
    }
