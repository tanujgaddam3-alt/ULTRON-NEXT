from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from ultron.financial.trading.market.adapters.yahoo import YahooMarketAdapter


def make_adapter():
    return YahooMarketAdapter()


@patch("ultron.financial.trading.market.adapters.yahoo.yf.Ticker")
def test_rejects_empty_market_data(mock_ticker):
    ticker = MagicMock()
    ticker.history.return_value = pd.DataFrame()
    mock_ticker.return_value = ticker

    with pytest.raises(ValueError, match="No market data"):
        make_adapter().get_price("AAPL")


@patch("ultron.financial.trading.market.adapters.yahoo.yf.Ticker")
def test_rejects_missing_close_column(mock_ticker):
    ticker = MagicMock()
    ticker.history.return_value = pd.DataFrame(
        {"Open": [100.0]}
    )
    mock_ticker.return_value = ticker

    with pytest.raises(ValueError, match="No market data"):
        make_adapter().get_price("AAPL")


@patch("ultron.financial.trading.market.adapters.yahoo.yf.Ticker")
def test_rejects_all_nan_close_values(mock_ticker):
    ticker = MagicMock()
    ticker.history.return_value = pd.DataFrame(
        {"Close": [float("nan"), float("nan")]}
    )
    mock_ticker.return_value = ticker

    with pytest.raises(ValueError, match="No valid price"):
        make_adapter().get_price("AAPL")


@patch("ultron.financial.trading.market.adapters.yahoo.yf.Ticker")
def test_rejects_non_positive_price(mock_ticker):
    ticker = MagicMock()
    ticker.history.return_value = pd.DataFrame(
        {"Close": [0.0]}
    )
    mock_ticker.return_value = ticker

    with pytest.raises(ValueError, match="Invalid market price"):
        make_adapter().get_price("AAPL")


@patch("ultron.financial.trading.market.adapters.yahoo.yf.Ticker")
def test_uses_latest_valid_close(mock_ticker):
    ticker = MagicMock()
    ticker.history.return_value = pd.DataFrame(
        {"Close": [100.0, float("nan"), 125.5]}
    )
    mock_ticker.return_value = ticker

    assert make_adapter().get_price("AAPL") == 125.5


def test_rejects_non_string_symbol():
    with pytest.raises(ValueError, match="Symbol must be a string"):
        make_adapter().get_instrument(None)


def test_rejects_empty_symbol():
    with pytest.raises(ValueError, match="Symbol is required"):
        make_adapter().get_instrument("   ")


def test_normalizes_pair_symbol():
    instrument = make_adapter().get_instrument(" usd / inr ")

    assert instrument.symbol == "USD/INR"
    assert instrument.base_currency == "USD"
    assert instrument.quote_currency == "INR"


def test_crypto_capabilities():
    capabilities = make_adapter().get_capabilities("BTC/USD")

    assert capabilities["market_type"] == "crypto"
    assert capabilities["market_hours"] == "24/7"
    assert capabilities["supports_fractional_quantity"] is True


def test_forex_capabilities():
    capabilities = make_adapter().get_capabilities("USD/INR")

    assert capabilities["market_type"] == "forex"
    assert capabilities["market_hours"] == "24/5"
