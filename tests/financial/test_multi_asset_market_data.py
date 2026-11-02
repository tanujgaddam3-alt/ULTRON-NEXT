from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd

from ultron.financial.trading.market_data.alpaca_adapter import AlpacaMarketDataError
from ultron.skills.market_data.assets import (
    AssetClass,
    classify_asset,
    normalize_symbol,
)
from ultron.skills.market_data.provider import MarketDataProvider
import pytest


def test_classifies_equity():
    assert classify_asset("AAPL") is AssetClass.EQUITY


def test_classifies_crypto():
    assert classify_asset("BTC/USD") is AssetClass.CRYPTO
    assert classify_asset("BTC-USD") is AssetClass.CRYPTO


def test_classifies_forex():
    assert classify_asset("USD/INR") is AssetClass.FOREX
    assert classify_asset("EURUSD=X") is AssetClass.FOREX


def test_normalizes_crypto_symbol():
    assert normalize_symbol("BTC/USD") == "BTC-USD"


def test_normalizes_forex_symbol():
    assert normalize_symbol("USD/INR") == "USDINR=X"


def test_normalizes_equity_symbol():
    assert normalize_symbol(" aapl ") == "AAPL"


@pytest.mark.parametrize(
    ("symbol", "provider_symbol"),
    [
        ("NIFTY 50", "^NSEI"),
        ("NIFTY50", "^NSEI"),
        ("SENSEX", "^BSESN"),
        ("BSE Sensex", "^BSESN"),
        ("BSESENSEX", "^BSESN"),
    ],
)
def test_normalizes_indian_indices(symbol, provider_symbol):
    assert normalize_symbol(symbol) == provider_symbol


@patch("ultron.skills.market_data.provider.yf.Ticker")
def test_snapshot_normalizes_crypto_and_returns_metadata(mock_ticker):
    index = pd.DatetimeIndex(
        [
            datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 5, 13, 0, tzinfo=timezone.utc),
        ]
    )

    frame = pd.DataFrame(
        {
            "Close": [100.0, 105.0],
            "Volume": [1000.0, 1200.0],
        },
        index=index,
    )

    mock_ticker.return_value.history.return_value = frame

    snapshot = MarketDataProvider(provider="yahoo").get_snapshot("BTC/USD")

    mock_ticker.assert_called_once_with("BTC-USD")
    assert snapshot.symbol == "BTC/USD"
    assert snapshot.asset_class is AssetClass.CRYPTO
    assert snapshot.current_price == 105.0
    assert snapshot.change_percent == pytest.approx(5.0)
    assert snapshot.source == "yfinance"
    assert snapshot.volume == 1200.0


@patch("ultron.skills.market_data.provider.yf.Ticker")
def test_snapshot_normalizes_forex(mock_ticker):
    index = pd.DatetimeIndex(
        [
            datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 5, 13, 0, tzinfo=timezone.utc),
        ]
    )

    frame = pd.DataFrame(
        {
            "Close": [83.0, 84.0],
            "Volume": [0.0, 0.0],
        },
        index=index,
    )

    mock_ticker.return_value.history.return_value = frame

    snapshot = MarketDataProvider(provider="yahoo").get_snapshot("USD/INR")

    mock_ticker.assert_called_once_with("USDINR=X")
    assert snapshot.asset_class is AssetClass.FOREX
    assert snapshot.currency == "INR"


@patch("ultron.skills.market_data.provider.yf.Ticker")
def test_provider_rejects_unsupported_alpaca_crypto_and_forex_symbols(mock_ticker):
    index = pd.DatetimeIndex(
        [
            datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 5, 13, 0, tzinfo=timezone.utc),
        ]
    )
    mock_ticker.return_value.history.return_value = pd.DataFrame(
        {"Close": [100.0, 105.0], "Volume": [1000.0, 1200.0]},
        index=index,
    )

    with pytest.raises(AlpacaMarketDataError, match="not a supported Alpaca US-equity symbol"):
        MarketDataProvider(provider="alpaca").get_snapshot("BTC/USD")

    with pytest.raises(AlpacaMarketDataError, match="not a supported Alpaca US-equity symbol"):
        MarketDataProvider(provider="alpaca").get_snapshot("USD/INR")

    mock_ticker.assert_not_called()


@patch("ultron.skills.market_data.provider.yf.Ticker")
def test_snapshot_marks_indian_index_non_realtime_with_inr_currency(mock_ticker):
    timestamp = datetime(2026, 9, 5, 13, 0, tzinfo=timezone.utc)
    frame = pd.DataFrame(
        {"Close": [25_000.0, 25_100.0], "Volume": [0.0, 0.0]},
        index=pd.DatetimeIndex(
            [
                timestamp - pd.Timedelta(minutes=1),
                timestamp,
            ]
        ),
    )
    mock_ticker.return_value.history.return_value = frame

    snapshot = MarketDataProvider(provider="india_yahoo").get_snapshot("NIFTY 50")

    mock_ticker.assert_called_once_with("^NSEI")
    assert snapshot.instrument_type == "index"
    assert snapshot.currency == "INR"
    assert snapshot.realtime_status == "unknown_or_delayed"


@patch("ultron.skills.market_data.provider.yf.Ticker")
def test_bse_sensex_alias_is_index_and_quoted_inr(mock_ticker):
    timestamp = datetime(2026, 9, 5, 13, 0, tzinfo=timezone.utc)
    frame = pd.DataFrame(
        {"Close": [80_000.0], "Volume": [0.0]},
        index=pd.DatetimeIndex([timestamp]),
    )
    mock_ticker.return_value.history.return_value = frame

    snapshot = MarketDataProvider(provider="india_yahoo").get_snapshot("BSE Sensex")

    mock_ticker.assert_called_once_with("^BSESN")
    assert snapshot.instrument_type == "index"
    assert snapshot.currency == "INR"


@patch("ultron.skills.market_data.provider.yf.Ticker")
def test_india_provider_rejects_timezone_naive_timestamps(mock_ticker):
    frame = pd.DataFrame(
        {"Close": [25_000.0], "Volume": [0.0]},
        index=pd.DatetimeIndex(["2026-09-05 13:00"]),
    )
    mock_ticker.return_value.history.return_value = frame

    with pytest.raises(ValueError, match="timezone-naive"):
        MarketDataProvider(provider="india_yahoo").get_snapshot("NIFTY50")


def test_market_snapshot_defaults_preserve_phase1_shape():
    from ultron.skills.market_data.models import MarketSnapshot

    timestamp = datetime.now(timezone.utc)

    snapshot = MarketSnapshot(
        "AAPL",
        100.0,
        1.5,
        timestamp,
    )

    assert snapshot.symbol == "AAPL"
    assert snapshot.current_price == 100.0
    assert snapshot.change_percent == 1.5
    assert snapshot.timestamp == timestamp
    assert snapshot.asset_class is AssetClass.EQUITY
