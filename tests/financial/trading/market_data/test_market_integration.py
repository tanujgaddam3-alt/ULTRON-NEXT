from datetime import UTC, datetime, timedelta

import pytest

from ultron.financial.trading.market.adapters.paper import PaperMarketAdapter
from ultron.financial.trading.market_data.models import Candle
from ultron.financial.trading.market_data.series import MarketSeries
from ultron.financial.trading.market_data.service import MarketDataService
from ultron.financial.trading.paper_broker import PaperBroker


class SnapshotAdapter:
    def __init__(self, snapshot=None, open_=True):
        self.snapshot = snapshot
        self.open = open_

    def is_market_open(self, symbol):
        return self.open

    def get_snapshot(self, symbol):
        if isinstance(self.snapshot, Exception):
            raise self.snapshot
        return self.snapshot


def test_market_data_service_rejects_closed_market_without_price():
    service = MarketDataService(
        SnapshotAdapter(
            {"price": 100.0, "timestamp": datetime.now(UTC)},
            open_=False,
        )
    )

    result = service.get_current_price("TEST")

    assert result["valid"] is False
    assert result["reason"] == "Market is closed."
    assert "price" not in result


def test_market_data_service_rejects_malformed_snapshot_without_fallback():
    service = MarketDataService(
        SnapshotAdapter(
            {"price": "not-a-price", "timestamp": datetime.now(UTC)}
        )
    )

    result = service.get_current_price("TEST")

    assert result["valid"] is False
    assert "non-positive price" in result["reason"]
    assert "price" not in result


def test_market_data_service_rejects_non_finite_snapshot_price():
    service = MarketDataService(
        SnapshotAdapter(
            {"price": float("nan"), "timestamp": datetime.now(UTC)}
        )
    )

    result = service.get_current_price("TEST")

    assert result["valid"] is False
    assert "non-finite" in result["reason"]
    assert "price" not in result


def test_market_data_service_fails_closed_without_source_timestamp():
    class PriceOnlyAdapter:
        def is_market_open(self, symbol):
            return True

        def get_price(self, symbol):
            return 100.0

    result = MarketDataService(PriceOnlyAdapter()).get_current_price("TEST")

    assert result["valid"] is False
    assert result["reason"] == "A timestamped market snapshot is required."


def test_market_data_service_recovers_after_temporary_provider_outage():
    now = datetime.now(UTC)
    adapter = SnapshotAdapter(ConnectionError("temporary outage"))
    service = MarketDataService(adapter)

    try:
        service.get_current_price("TEST")
        assert False, "Expected provider outage to propagate."
    except ConnectionError:
        pass

    adapter.snapshot = {
        "price": 101.0,
        "timestamp": now,
    }
    recovered = service.get_current_price("TEST")

    assert recovered["valid"] is True
    assert recovered["price"] == 101.0


def test_market_data_service_validates_bid_ask_and_reports_spread():
    service = MarketDataService(
        SnapshotAdapter(
            {
                "price": 101.0,
                "bid": 100.0,
                "ask": 102.0,
                "timestamp": datetime.now(UTC),
            }
        )
    )

    result = service.get_current_price("TEST")

    assert result["valid"] is True
    assert result["midpoint"] == 101.0
    assert result["spread"] == 2.0
    assert result["spread_bps"] == 2 / 101 * 10_000


@pytest.mark.parametrize(
    ("quote", "reason"),
    [
        ({"bid": 100.0}, "incomplete bid/ask"),
        ({"bid": "invalid", "ask": 101.0}, "invalid bid/ask prices"),
        ({"bid": 102.0, "ask": 101.0}, "invalid bid/ask spread"),
        ({"bid": 100.0, "ask": float("inf")}, "invalid bid/ask spread"),
    ],
)
def test_market_data_service_rejects_invalid_bid_ask(quote, reason):
    service = MarketDataService(
        SnapshotAdapter(
            {
                "price": 101.0,
                "timestamp": datetime.now(UTC),
                **quote,
            }
        )
    )

    result = service.get_current_price("TEST")

    assert result["valid"] is False
    assert reason in result["reason"]
    assert "price" not in result


def make_service():
    broker = PaperBroker(initial_capital=100_000)

    adapter = PaperMarketAdapter(
        broker,
        prices={"TEST": 100.0},
    )

    adapter.connect()

    service = MarketDataService(adapter)

    return service, adapter


def test_market_data_service_returns_current_price():
    service, _ = make_service()

    result = service.get_current_price(" test ")

    assert result["valid"] is True
    assert result["symbol"] == "TEST"
    assert result["price"] == 100.0
    assert result["timestamp"].tzinfo is not None


def test_market_data_service_rejects_empty_symbol():
    service, _ = make_service()

    result = service.get_current_price(" ")

    assert result["valid"] is False
    assert result["reason"] == "Symbol is required."


def test_market_data_service_validates_series():
    service, _ = make_service()

    now = datetime.now(UTC)

    candles = [
        Candle(
            symbol="TEST",
            timestamp=now,
            open=100.0,
            high=105.0,
            low=95.0,
            close=102.0,
            volume=1000.0,
        ),
        Candle(
            symbol="TEST",
            timestamp=now + timedelta(minutes=1),
            open=102.0,
            high=108.0,
            low=101.0,
            close=106.0,
            volume=1200.0,
        ),
    ]

    result = service.validate_series(candles)

    assert result["valid"] is True
    assert result["symbol"] == "TEST"
    assert result["count"] == 2
    assert result["latest_price"] == 106.0
    assert isinstance(result["series"], MarketSeries)


def test_market_data_service_rejects_invalid_series():
    service, _ = make_service()

    result = service.validate_series([])

    assert result["valid"] is False
    assert "empty" in result["reason"].lower()


def test_market_adapter_requires_connection():
    broker = PaperBroker(initial_capital=100_000)

    adapter = PaperMarketAdapter(
        broker,
        prices={"TEST": 100.0},
    )

    try:
        adapter.get_price("TEST")
        assert False, "Expected connection error."
    except RuntimeError as exc:
        assert "not connected" in str(exc).lower()


def test_market_adapter_returns_paper_capabilities():
    _, adapter = make_service()

    capabilities = adapter.get_capabilities("TEST")

    assert capabilities["paper"] is True
    assert capabilities["supports_market_orders"] is True
    assert capabilities["supports_limit_orders"] is False
    assert capabilities["supports_short_selling"] is False


def test_market_adapter_returns_instrument():
    _, adapter = make_service()

    instrument = adapter.get_instrument(" test ")

    assert instrument.symbol == "TEST"
    assert instrument.base_currency == "USD"
    assert instrument.quote_currency == "USD"


def test_market_adapter_disconnects():
    _, adapter = make_service()

    assert adapter.is_connected() is True

    adapter.disconnect()

    assert adapter.is_connected() is False
