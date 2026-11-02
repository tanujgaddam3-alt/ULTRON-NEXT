from unittest.mock import MagicMock, patch

import pandas as pd

from ultron.financial.trading.market.adapters.yahoo import (
    YahooMarketAdapter,
)
from ultron.financial.trading.market.models import MarketType


def make_adapter():
    return YahooMarketAdapter()


def test_classifies_stock():
    adapter = make_adapter()

    instrument = adapter.get_instrument("AAPL")

    assert instrument.market is MarketType.STOCK
    assert instrument.symbol == "AAPL"


def test_classifies_crypto():
    adapter = make_adapter()

    instrument = adapter.get_instrument("BTC/USD")

    assert instrument.market is MarketType.CRYPTO
    assert instrument.symbol == "BTC/USD"
    assert instrument.base_currency == "BTC"
    assert instrument.quote_currency == "USD"


def test_classifies_forex():
    adapter = make_adapter()

    instrument = adapter.get_instrument("USD/INR")

    assert instrument.market is MarketType.FOREX
    assert instrument.symbol == "USD/INR"
    assert instrument.base_currency == "USD"
    assert instrument.quote_currency == "INR"


@patch("ultron.financial.trading.market.adapters.yahoo.yf.Ticker")
def test_crypto_provider_symbol(mock_ticker):
    ticker = MagicMock()
    ticker.history.return_value = pd.DataFrame(
        {"Close": [100000.0]}
    )
    mock_ticker.return_value = ticker

    adapter = make_adapter()

    price = adapter.get_price("BTC/USD")

    assert price == 100000.0
    mock_ticker.assert_called_once_with("BTC-USD")


@patch("ultron.financial.trading.market.adapters.yahoo.yf.Ticker")
def test_forex_provider_symbol(mock_ticker):
    ticker = MagicMock()
    ticker.history.return_value = pd.DataFrame(
        {"Close": [83.5]}
    )
    mock_ticker.return_value = ticker

    adapter = make_adapter()

    price = adapter.get_price("USD/INR")

    assert price == 83.5
    mock_ticker.assert_called_once_with("USDINR=X")


@patch("ultron.financial.trading.market.adapters.yahoo.yf.Ticker")
def test_stock_price(mock_ticker):
    ticker = MagicMock()
    ticker.history.return_value = pd.DataFrame(
        {"Close": [245.25]}
    )
    mock_ticker.return_value = ticker

    adapter = make_adapter()

    price = adapter.get_price("AAPL")

    assert price == 245.25
    mock_ticker.assert_called_once_with("AAPL")


def test_crypto_is_open():
    adapter = make_adapter()

    assert adapter.is_market_open("BTC/USD") is True


@patch("ultron.financial.trading.market.adapters.yahoo.datetime")
def test_forex_is_open_on_weekday(mock_datetime):
    mock_now = MagicMock()
    mock_now.weekday.return_value = 2  # Wednesday
    mock_datetime.now.return_value = mock_now

    adapter = make_adapter()

    assert adapter.is_market_open("USD/INR") is True


def test_capabilities_identify_market():
    adapter = make_adapter()

    capabilities = adapter.get_capabilities("BTC/USD")

    assert capabilities["market_type"] == "crypto"
    assert capabilities["provider"] == "yahoo_finance"
