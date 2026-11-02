from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from ultron.financial.bootstrap import build_financial_runtime
from ultron.financial.trading import groww_diagnostic
from ultron.financial.trading.market.models import InstrumentType
from ultron.financial.trading.market_data import groww_adapter
from ultron.financial.trading.market_data.groww_adapter import (
    GrowwMarketAdapter,
    GrowwMarketDataError,
)
from ultron.financial.trading.market_data.service import MarketDataService
from ultron.financial.trading.market_data.groww_credentials import (
    GrowwConfigurationError,
)
from ultron.skills.market_data.provider import MarketDataProvider


IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 3, 5, 10, 0, tzinfo=IST)


def timestamp_millis(value: datetime) -> int:
    return int(value.astimezone(UTC).timestamp() * 1000)


def timestamp_text(value: datetime) -> str:
    return value.astimezone(IST).strftime("%Y-%m-%dT%H:%M:%S")


def instrument_rows():
    return pd.DataFrame(
        [
            {
                "exchange": "NSE",
                "exchange_token": 1,
                "trading_symbol": "NIFTY",
                "groww_symbol": "NSE-NIFTY",
                "name": "NIFTY 50",
                "segment": "CASH",
                "instrument_type": "INDEX",
            },
            {
                "exchange": "BSE",
                "exchange_token": 2,
                "trading_symbol": "SENSEX",
                "groww_symbol": "BSE-SENSEX",
                "name": "BSE Sensex",
                "segment": "CASH",
                "instrument_type": "INDEX",
            },
        ]
    )


class FakeGrowwClient:
    def __init__(self, *, instruments=None, quote=None, candles=None):
        self.instruments = instrument_rows() if instruments is None else instruments
        self.quote = quote or {
            "last_price": 25_000.0,
            "last_trade_time": timestamp_millis(NOW - timedelta(seconds=2)),
            "day_change_perc": 0.5,
            "volume": 10_000,
        }
        self.candles = candles if candles is not None else [
            [
                timestamp_text(NOW - timedelta(days=1)),
                24_900,
                25_100,
                24_800,
                25_000,
                0,
                None,
            ],
            [
                timestamp_text(NOW.replace(hour=9, minute=15)),
                25_000,
                25_050,
                24_990,
                25_020,
                None,
                None,
            ],
        ]
        self.fill_empty_chunks = False
        self.calls = []

    def get_all_instruments(self):
        self.calls.append("get_all_instruments")
        return self.instruments

    def get_quote(self, **kwargs):
        self.calls.append(("get_quote", kwargs))
        return self.quote

    def get_historical_candles(self, **kwargs):
        self.calls.append(("get_historical_candles", kwargs))
        start = datetime.fromisoformat(kwargs["start_time"]).replace(tzinfo=IST)
        end = datetime.fromisoformat(kwargs["end_time"]).replace(tzinfo=IST)
        candles = [
            row
            for row in self.candles
            if start
            <= datetime.fromisoformat(row[0]).replace(tzinfo=IST)
            <= end
        ]
        if not candles and self.fill_empty_chunks:
            timestamp = start + timedelta(days=1)
            candles = [
                [timestamp_text(timestamp), 100, 101, 99, 100, None, None]
            ]
        return {"candles": candles}


def make_adapter(client=None, *, now=NOW, max_age=30.0):
    return GrowwMarketAdapter(
        client=client or FakeGrowwClient(),
        clock=lambda: now,
        max_quote_age_seconds=max_age,
    )


@pytest.mark.parametrize(
    ("symbol", "canonical", "exchange", "provider_symbol"),
    [
        ("NIFTY 50", "NIFTY50", "NSE", "NIFTY"),
        ("^NSEI", "NIFTY50", "NSE", "NIFTY"),
        ("BSE Sensex", "SENSEX", "BSE", "SENSEX"),
        ("^BSESN", "SENSEX", "BSE", "SENSEX"),
    ],
)
def test_groww_indexes_resolve_authoritative_cash_instrument_records(
    symbol, canonical, exchange, provider_symbol
):
    instrument = make_adapter().get_instrument(symbol)

    assert instrument.symbol == canonical
    assert instrument.exchange == exchange
    assert instrument.provider_symbol == provider_symbol
    assert instrument.provider_instrument_id in {"NSE-NIFTY", "BSE-SENSEX"}
    assert instrument.instrument_type is InstrumentType.INDEX
    assert instrument.orderable is False
    assert instrument.eligible_for_order(1, 1) is False


def test_groww_index_mapping_uses_live_directory_and_rejects_unlisted_symbols():
    client = FakeGrowwClient(instruments=instrument_rows().iloc[:1])
    adapter = make_adapter(client)

    with pytest.raises(GrowwMarketDataError, match="no current cash-market"):
        adapter.get_instrument("SENSEX")
    with pytest.raises(ValueError, match="only NIFTY 50 and BSE Sensex"):
        adapter.get_instrument("RELIANCE")


def test_groww_rejects_ambiguous_instrument_directory():
    rows = pd.concat([instrument_rows().iloc[[0]], instrument_rows().iloc[[0]]])

    with pytest.raises(GrowwMarketDataError, match="ambiguous"):
        make_adapter(FakeGrowwClient(instruments=rows)).get_instrument("NIFTY50")


def test_groww_quote_retains_source_timestamp_and_provenance():
    client = FakeGrowwClient()
    snapshot = make_adapter(client).get_snapshot("NIFTY50")

    assert snapshot["symbol"] == "NIFTY50"
    assert snapshot["price"] == 25_000
    assert snapshot["timestamp"] == NOW - timedelta(seconds=2)
    assert snapshot["timestamp"].utcoffset() == timedelta(hours=5, minutes=30)
    assert snapshot["provider"] == "groww"
    assert snapshot["real_time"] is True
    assert snapshot["realtime_status"] == "provider_documented_realtime"
    assert snapshot["orderable"] is False
    assert snapshot["currency"] == "INR"
    quote_request = next(
        call[1]
        for call in client.calls
        if isinstance(call, tuple) and call[0] == "get_quote"
    )
    assert quote_request["timeout"] == 5


def test_groww_sdk_instrument_directory_fetch_is_bounded_and_does_not_use_sdk_file_writer(
    monkeypatch,
):
    class SDKClient:
        INSTRUMENT_CSV_URL = "https://growwapi-assets.groww.in/instruments/instrument.csv"

        def get_all_instruments(self):
            raise AssertionError("SDK loader writes a CSV file to disk")

    class Response:
        content = (
            b"exchange,exchange_token,trading_symbol,groww_symbol,name,segment\n"
            b"NSE,1,NIFTY,NSE-NIFTY,NIFTY 50,CASH\n"
        )

        def raise_for_status(self):
            return None

    def get(url, *, timeout):
        assert url == SDKClient.INSTRUMENT_CSV_URL
        assert timeout == 5
        return Response()

    monkeypatch.setattr(groww_adapter.requests, "get", get)
    instrument = make_adapter(SDKClient()).get_instrument("NIFTY50")

    assert instrument.provider_instrument_id == "NSE-NIFTY"


def test_groww_sdk_without_safe_instrument_source_fails_closed():
    class SDKClient:
        def __init__(self, token):
            self.token = token

        @staticmethod
        def get_access_token(*, api_key, secret):
            return "access-token"

        def get_all_instruments(self):
            raise AssertionError("Unsafe SDK file-writing loader was called")

    adapter = GrowwMarketAdapter(
        api_key="private-key",
        api_secret="private-secret",
        sdk_factory=SDKClient,
    )

    with pytest.raises(GrowwMarketDataError) as raised:
        adapter.get_instrument("NIFTY50")

    assert raised.value.failure_type == "sdk_incompatible"


@pytest.mark.parametrize(
    ("age", "failure_type"),
    [
        (timedelta(seconds=40), "stale_data"),
        (timedelta(seconds=-1), "invalid_response"),
    ],
)
def test_groww_quote_rejects_stale_or_future_timestamps(age, failure_type):
    client = FakeGrowwClient(
        quote={
            "last_price": 25_000,
            "last_trade_time": timestamp_millis(NOW - age),
        }
    )

    with pytest.raises(GrowwMarketDataError) as raised:
        make_adapter(client).get_snapshot("NIFTY50")

    assert raised.value.failure_type == failure_type


@pytest.mark.parametrize(
    "quote",
    [
        {"last_price": float("nan"), "last_trade_time": timestamp_millis(NOW)},
        {"last_price": 0, "last_trade_time": timestamp_millis(NOW)},
        {"last_price": 100, "last_trade_time": "not-a-time"},
        {"last_price": 100},
    ],
)
def test_groww_quote_rejects_invalid_price_and_timestamp(quote):
    with pytest.raises(GrowwMarketDataError):
        make_adapter(FakeGrowwClient(quote=quote)).get_snapshot("NIFTY50")


def test_groww_daily_candles_use_exchange_local_ist_and_completion_state():
    client = FakeGrowwClient()
    adapter = make_adapter(client)
    candles = adapter.get_history("NIFTY 50", period="5d", now=NOW)

    assert len(candles) == 2
    assert candles[0].timestamp.tzinfo == IST
    assert candles[0].is_complete is True
    assert "Groww Trade API historical candles" in candles[0].source
    assert candles[0].realtime_status == "historical"
    assert candles[0].volume == 0
    assert candles[1].volume is None
    assert candles[1].is_complete is False
    request = next(
        call[1]
        for call in client.calls
        if isinstance(call, tuple) and call[0] == "get_historical_candles"
    )
    assert request["exchange"] == "NSE"
    assert request["segment"] == "CASH"
    assert request["groww_symbol"] == "NSE-NIFTY"
    assert request["candle_interval"] == "1day"
    assert request["timeout"] == 5
    assert request["end_time"] > request["start_time"]


def test_groww_market_session_is_timezone_aware_and_calendar_is_unverified():
    session = make_adapter().get_market_session("SENSEX", NOW)

    assert session["exchange"] == "BSE"
    assert session["timezone"] == "Asia/Kolkata"
    assert session["scheduled_open"] is True
    assert session["calendar_verified"] is False

    weekend = make_adapter().get_market_session(
        "SENSEX",
        datetime(2026, 3, 7, 10, 0, tzinfo=IST),
    )
    assert weekend["scheduled_open"] is False


def test_shared_market_data_service_rejects_incomplete_groww_candles():
    adapter = make_adapter()
    candles = adapter.get_history("NIFTY50", period="5d", now=NOW)

    result = MarketDataService(adapter).validate_series(candles)

    assert result["valid"] is False
    assert "incomplete" in result["reason"]


def test_groww_year_of_daily_history_is_chunked_to_documented_request_limits():
    client = FakeGrowwClient()
    client.fill_empty_chunks = True

    candles = make_adapter(client).get_history("NIFTY50", period="1y", now=NOW)
    requests = [
        call[1]
        for call in client.calls
        if isinstance(call, tuple) and call[0] == "get_historical_candles"
    ]

    assert len(candles) == 4
    assert len(requests) == 3
    for request in requests:
        start = datetime.fromisoformat(request["start_time"])
        end = datetime.fromisoformat(request["end_time"])
        assert (end.date() - start.date()).days < 180


@pytest.mark.parametrize(
    "candles",
    [
        [],
        [[timestamp_text(NOW), 1, 1, 1, 1, 1]],
        [[timestamp_text(NOW), 1, 1, 1, 1, float("nan"), None]],
        [[timestamp_text(NOW), 1, 1, 2, 1, 0, None]],
    ],
)
def test_groww_history_rejects_missing_or_malformed_candles(candles):
    with pytest.raises(GrowwMarketDataError):
        make_adapter(FakeGrowwClient(candles=candles)).get_history(
            "NIFTY50",
            period="5d",
            now=NOW,
        )


def test_groww_history_rejects_duplicate_or_out_of_order_timestamps():
    row = [
        timestamp_text(NOW - timedelta(days=1)),
        10,
        11,
        9,
        10,
        0,
        None,
    ]
    with pytest.raises(GrowwMarketDataError, match="out-of-order or duplicate"):
        make_adapter(FakeGrowwClient(candles=[row, row])).get_history(
            "NIFTY50",
            period="5d",
            now=NOW,
        )


def test_groww_daily_history_rejects_unsupported_ranges_and_intraday_intervals():
    adapter = make_adapter()
    with pytest.raises(ValueError, match="daily candles only"):
        adapter.get_history("NIFTY50", interval="5m")
    with pytest.raises(ValueError, match="Unsupported Groww history period"):
        adapter.get_history("NIFTY50", period="5y")


def test_groww_authentication_requires_credentials_and_secure_local_file(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("GROWW_API_KEY", raising=False)
    monkeypatch.delenv("GROWW_API_SECRET", raising=False)
    path = tmp_path / ".env.financial"
    path.write_text("GROWW_API_KEY=hidden-key\nGROWW_API_SECRET=hidden-secret\n")
    path.chmod(0o644)

    with pytest.raises(GrowwConfigurationError, match="accessible only"):
        GrowwMarketAdapter(credentials_file=str(path))
    with pytest.raises(GrowwConfigurationError, match="requires GROWW_API_KEY"):
        GrowwMarketAdapter(credentials_file=str(tmp_path / "missing.env"))


def test_groww_credentials_coexist_with_export_style_alpaca_entries(
    monkeypatch, tmp_path
):
    from ultron.financial.trading.market_data.groww_credentials import (
        load_groww_credentials,
    )

    monkeypatch.delenv("GROWW_API_KEY", raising=False)
    monkeypatch.delenv("GROWW_API_SECRET", raising=False)
    path = tmp_path / ".env.financial"
    path.write_text(
        "export ALPACA_API_KEY=dummy-alpaca-key\n"
        "export ALPACA_SECRET_KEY=dummy-alpaca-secret\n"
        "GROWW_API_KEY=dummy-groww-key\n"
        "GROWW_API_SECRET=dummy-groww-secret\n"
    )
    path.chmod(0o600)

    credentials = load_groww_credentials(path)

    assert credentials == {
        "GROWW_API_KEY": "dummy-groww-key",
        "GROWW_API_SECRET": "dummy-groww-secret",
    }


def test_groww_authentication_failure_is_classified_without_leaking_secrets():
    class FakeSDK:
        @staticmethod
        def get_access_token(*, api_key, secret):
            assert api_key == "private-key"
            assert secret == "private-secret"
            raise GrowwAPIAuthenticationException("private-secret")

    class GrowwAPIAuthenticationException(Exception):
        pass

    with pytest.raises(GrowwMarketDataError) as raised:
        GrowwMarketAdapter(
            api_key="private-key",
            api_secret="private-secret",
            sdk_factory=FakeSDK,
        )

    assert raised.value.failure_type == "authentication"
    assert "private-secret" not in str(raised.value)
    assert "private-key" not in str(raised.value)


def test_official_groww_sdk_token_request_uses_approval_checksum_and_seconds(
    monkeypatch,
):
    from growwapi import GrowwAPI
    import growwapi.groww.client as sdk_client

    timestamp = 1_800_000_000
    monkeypatch.setattr(sdk_client, "time", SimpleNamespace(time=lambda: timestamp))
    requests = []

    class Response:
        status_code = 200
        ok = True

        @staticmethod
        def json():
            return {"token": "dummy-access-token"}

    def post(url, **kwargs):
        kwargs["url"] = url
        requests.append(kwargs)
        return Response()

    monkeypatch.setattr(sdk_client.requests, "post", post)
    token = GrowwAPI.get_access_token(
        api_key="dummy-api-key",
        secret="dummy-api-secret",
    )

    assert token == "dummy-access-token"
    assert len(requests) == 1
    request = requests[0]
    assert request["url"] == "https://api.groww.in/v1/token/api/access"
    assert request["headers"]["Authorization"] == "Bearer dummy-api-key"
    assert request["headers"]["Content-Type"] == "application/json"
    assert request["json"] == {
        "key_type": "approval",
        "checksum": hashlib.sha256(
            f"dummy-api-secret{timestamp}".encode("utf-8")
        ).hexdigest(),
        "timestamp": timestamp,
    }
    assert request["timeout"] == 15


def test_adapter_passes_generated_access_token_to_groww_sdk_client():
    class FakeSDK:
        @staticmethod
        def get_access_token(*, api_key, secret):
            assert api_key == "dummy-api-key"
            assert secret == "dummy-api-secret"
            return "dummy-access-token"

        def __init__(self, token):
            self.token = token

    adapter = GrowwMarketAdapter(
        api_key="dummy-api-key",
        api_secret="dummy-api-secret",
        sdk_factory=FakeSDK,
    )

    assert adapter._client.token == "dummy-access-token"


def test_official_sdk_read_requests_include_required_accept_header(monkeypatch):
    from growwapi import GrowwAPI
    import growwapi.groww.client as sdk_client

    client = object.__new__(GrowwAPI)
    GrowwMarketAdapter._install_accept_header(client)
    captured = {}

    def get(url, *, params, headers, timeout, **kwargs):
        captured.update(headers)
        return object()

    monkeypatch.setattr(sdk_client.requests, "get", get)
    original_headers = {
        "Authorization": "Bearer dummy-access-token",
        "X-API-VERSION": "1.0",
    }
    client._request_get(
        url="https://api.groww.in/v1/live-data/quote",
        headers=original_headers,
        timeout=5,
    )

    assert captured["Accept"] == "application/json"
    assert captured["X-API-VERSION"] == "1.0"
    assert captured["Authorization"] == "Bearer dummy-access-token"
    assert "Accept" not in original_headers


def test_groww_approval_rejection_is_sanitized_and_not_retried(monkeypatch):
    from growwapi import GrowwAPI
    import growwapi.groww.client as sdk_client

    calls = []

    class Response:
        status_code = 400
        ok = False

        @staticmethod
        def json():
            return {"error": {"displayMessage": "Daily approval required"}}

    def post(url, **kwargs):
        kwargs["url"] = url
        calls.append(kwargs)
        return Response()

    monkeypatch.setattr(sdk_client.requests, "post", post)

    class SDKFactory:
        @staticmethod
        def get_access_token(*, api_key, secret):
            return GrowwAPI.get_access_token(api_key=api_key, secret=secret)

        def __init__(self, token):
            self.token = token

    with pytest.raises(GrowwMarketDataError) as raised:
        GrowwMarketAdapter(
            api_key="dummy-api-key",
            api_secret="dummy-api-secret",
            sdk_factory=SDKFactory,
        )

    assert raised.value.failure_type == "bad_request"
    assert "Daily approval required" not in str(raised.value)
    assert len(calls) == 1


def test_groww_malformed_token_response_fails_closed_without_leaking(
    monkeypatch,
):
    from growwapi import GrowwAPI
    import growwapi.groww.client as sdk_client

    class Response:
        status_code = 200
        ok = True

        @staticmethod
        def json():
            return {"unexpected": "dummy-secret"}

    monkeypatch.setattr(sdk_client.requests, "post", lambda **kwargs: Response())

    class SDKFactory:
        @staticmethod
        def get_access_token(*, api_key, secret):
            return GrowwAPI.get_access_token(api_key=api_key, secret=secret)

        def __init__(self, token):
            self.token = token

    with pytest.raises(GrowwMarketDataError) as raised:
        GrowwMarketAdapter(
            api_key="dummy-api-key",
            api_secret="dummy-api-secret",
            sdk_factory=SDKFactory,
        )

    assert raised.value.failure_type == "api"
    assert "dummy-secret" not in str(raised.value)


@pytest.mark.parametrize(
    ("exception_name", "code", "failure_type"),
    [
        ("GrowwAPIAuthenticationException", "401", "authentication"),
        ("GrowwAPIAuthorisationException", "403", "authorization"),
    ],
)
def test_groww_expired_token_and_permission_failures_are_distinguished(
    exception_name, code, failure_type
):
    error_type = type(exception_name, (Exception,), {})

    class Client(FakeGrowwClient):
        quote_calls = 0

        def get_quote(self, **kwargs):
            self.quote_calls += 1
            error = error_type("dummy-sensitive-provider-message")
            error.code = code
            raise error

    client = Client()
    with pytest.raises(GrowwMarketDataError) as raised:
        make_adapter(client).get_snapshot("NIFTY50")

    assert raised.value.failure_type == failure_type
    assert "dummy-sensitive-provider-message" not in str(raised.value)
    assert client.quote_calls == 1


@pytest.mark.parametrize(
    ("exception_type", "failure_type"),
    [
        ("GrowwAPITimeoutException", "timeout"),
        ("GrowwAPIRateLimitException", "rate_limit"),
    ],
)
def test_groww_timeout_and_rate_limit_errors_are_sanitized(
    exception_type, failure_type
):
    error_type = type(exception_type, (Exception,), {})

    class Client(FakeGrowwClient):
        def get_all_instruments(self):
            raise error_type("do not expose this detail")

    with pytest.raises(GrowwMarketDataError) as raised:
        make_adapter(Client()).get_instrument("NIFTY50")

    assert raised.value.failure_type == failure_type
    assert "do not expose" not in str(raised.value)


@pytest.mark.parametrize(
    ("code", "failure_type"),
    [(401, "authentication"), (429, "rate_limit"), (503, "unavailable")],
)
def test_groww_sdk_http_error_codes_are_classified_without_details(code, failure_type):
    class Client(FakeGrowwClient):
        def get_all_instruments(self):
            error = RuntimeError("private provider response")
            error.code = str(code)
            raise error

    with pytest.raises(GrowwMarketDataError) as raised:
        make_adapter(Client()).get_instrument("NIFTY50")

    assert raised.value.failure_type == failure_type
    assert "private provider response" not in str(raised.value)


def test_groww_adapter_and_shared_provider_have_no_order_or_yahoo_fallback():
    client = FakeGrowwClient(
        candles=[
            [
                timestamp_text(NOW - timedelta(days=1)),
                24_900,
                25_100,
                24_800,
                25_000,
                None,
                None,
            ]
        ]
    )
    adapter = make_adapter(client)
    provider = MarketDataProvider(provider="groww", groww_adapter=adapter)

    snapshot = provider.get_snapshot("NIFTY 50")
    history = provider.get_history("NIFTY50", period="5d")

    assert snapshot.source == "groww_trade_api"
    assert snapshot.instrument_type == "index"
    assert snapshot.currency == "INR"
    assert snapshot.realtime_status == "provider_documented_realtime"
    assert list(history.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert not hasattr(adapter, "place_order")
    assert not hasattr(adapter, "submit_order")
    assert all(
        call == "get_all_instruments"
        or call[0] in {"get_quote", "get_historical_candles"}
        for call in client.calls
    )


def test_groww_provider_does_not_fallback_when_the_selected_adapter_fails():
    class FailingAdapter:
        normalize_symbol = staticmethod(GrowwMarketAdapter.normalize_symbol)

        def get_snapshot(self, symbol):
            assert symbol == "NIFTY50"
            raise GrowwMarketDataError(
                "Groww quote request failed (unavailable).",
                failure_type="unavailable",
            )

    provider = MarketDataProvider(
        provider="groww",
        groww_adapter=FailingAdapter(),
    )

    with pytest.raises(GrowwMarketDataError, match="Groww quote request failed"):
        provider.get_snapshot("NIFTY50")


def test_bootstrap_groww_selection_remains_paper_only(monkeypatch):
    class FakeAdapter:
        pass

    monkeypatch.setenv("ULTRON_FINANCIAL_MARKET_PROVIDER", "groww")
    monkeypatch.setattr(groww_adapter, "GrowwMarketAdapter", FakeAdapter)

    runtime = build_financial_runtime(memory_db=":memory:")
    try:
        assert isinstance(runtime.trading_controller.market_data.adapter, FakeAdapter)
        assert runtime.paper_broker.supports_live_orders is False
    finally:
        runtime.close()


def test_groww_diagnostic_reports_missing_credentials_without_requests(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("GROWW_API_KEY", raising=False)
    monkeypatch.delenv("GROWW_API_SECRET", raising=False)
    result = groww_diagnostic.run_read_only_diagnostic(
        credentials_file=tmp_path / "missing.env"
    )

    assert result["authenticated"] is False
    assert result["read_only"] is True
    assert set(result["missing_configuration"]) == {
        "GROWW_API_KEY",
        "GROWW_API_SECRET",
    }
    assert "No authenticated" in result["reason"]


def test_groww_diagnostic_withholds_prices_and_credentials():
    result = groww_diagnostic.run_read_only_diagnostic(
        adapter=make_adapter(),
    )

    assert result["authenticated"] is True
    assert result["success"] is True
    assert result["quote_available"] is True
    assert result["history_available"] is True
    assert result["orderable"] is False
    assert result["regional_eligibility"] == "not_verified"
    rendered = str(result)
    assert "25000" not in rendered
    assert "private-key" not in rendered


def test_groww_diagnostic_reports_status_and_safe_provider_messages():
    class FailingAdapter:
        def get_instrument(self, symbol):
            return make_adapter().get_instrument(symbol)

        def get_snapshot(self, symbol):
            error = RuntimeError(
                "Bearer dummy-access-token checksum=dummy-checksum "
                "key=dummy-api-key secret=dummy-api-secret"
            )
            error.code = "403"
            raise GrowwMarketAdapter._provider_error("quote", error)

        def get_history(self, symbol, *, period, interval):
            error = RuntimeError(
                "authorization header contains dummy-access-token"
            )
            error.response = SimpleNamespace(status_code=403)
            raise GrowwMarketAdapter._provider_error("historical candles", error)

    result = groww_diagnostic.run_read_only_diagnostic(adapter=FailingAdapter())

    assert result["authenticated"] is False
    assert result["quote_error"] == "authorization"
    assert result["quote_error_status"] == 403
    assert result["quote_provider_error_code"] is None
    assert result["history_error"] == "authorization"
    assert result["history_error_status"] == 403
    assert result["history_provider_error_code"] is None
    assert result["quote_error_message"] == result["history_error_message"]
    serialized = str(result)
    for sensitive in (
        "dummy-access-token",
        "dummy-checksum",
        "dummy-api-key",
        "dummy-api-secret",
        "Bearer",
        "authorization header",
    ):
        assert sensitive not in serialized


def test_groww_diagnostic_reports_authentication_failure_status_safely(
    monkeypatch, tmp_path
):
    class FailingSDK:
        @staticmethod
        def get_access_token(*, api_key, secret):
            error = RuntimeError(
                "dummy-api-key dummy-api-secret dummy-checksum"
            )
            error.code = "401"
            raise error

    class FailingAdapterFactory:
        normalize_symbol = staticmethod(GrowwMarketAdapter.normalize_symbol)

        def __new__(cls, **kwargs):
            return GrowwMarketAdapter(
                api_key=kwargs["api_key"],
                api_secret=kwargs["api_secret"],
                sdk_factory=FailingSDK,
            )

    monkeypatch.setenv("GROWW_API_KEY", "dummy-api-key")
    monkeypatch.setenv("GROWW_API_SECRET", "dummy-api-secret")
    monkeypatch.setattr(
        groww_diagnostic,
        "GrowwMarketAdapter",
        FailingAdapterFactory,
    )

    result = groww_diagnostic.run_read_only_diagnostic(
        credentials_file=tmp_path / "unused.env"
    )

    assert result["authenticated"] is False
    assert result["stage"] == "authentication"
    assert result["error_status"] == 401
    assert result["provider_error_code"] is None
    assert result["error"] == "authentication"
    serialized = str(result)
    assert "dummy-api-key" not in serialized
    assert "dummy-api-secret" not in serialized
    assert "dummy-checksum" not in serialized


def test_groww_sdk_business_error_code_is_not_misreported_as_http_status():
    error = RuntimeError("private provider message")
    error.code = "GA001"

    normalized = GrowwMarketAdapter._provider_error("quote", error)

    assert normalized.http_status is None
    assert normalized.provider_code == "GA001"
    assert normalized.failure_type == "api"
    assert "private provider message" not in normalized.diagnostic_message


def test_groww_diagnostic_does_not_claim_authentication_when_calls_fail():
    class FailingAdapter:
        def get_instrument(self, symbol):
            return make_adapter().get_instrument(symbol)

        def get_snapshot(self, symbol):
            raise GrowwMarketDataError(
                "Groww quote request failed (authentication).",
                failure_type="authentication",
            )

        def get_history(self, symbol, *, period, interval):
            raise GrowwMarketDataError(
                "Groww historical candles request failed (authentication).",
                failure_type="authentication",
            )

    result = groww_diagnostic.run_read_only_diagnostic(
        adapter=FailingAdapter(),
    )

    assert result["authenticated"] is False
    assert result["success"] is False
    assert result["reason"] == (
        "No authenticated Groww market-data call completed successfully."
    )
