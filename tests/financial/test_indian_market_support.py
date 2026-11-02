from datetime import UTC, date, datetime, timedelta
from math import sin
from zoneinfo import ZoneInfo
from unittest.mock import patch

import pandas as pd
import pytest

from ultron.financial.research.backtest import evaluate_signals
from ultron.financial.research.dataset import load_ohlcv
from ultron.financial.research.features import build_features
from ultron.financial.research.train import train_and_evaluate
from ultron.financial.trading.controller import FinancialTradingController
from ultron.financial.trading.engine import PaperTradingEngine
from ultron.financial.trading.market.adapters.paper import PaperMarketAdapter
from ultron.financial.trading.market.adapters.yahoo import YahooMarketAdapter
from ultron.financial.trading.market.models import (
    Instrument,
    InstrumentType,
    MarketType,
)
from ultron.financial.trading.market_data.models import Candle
from ultron.financial.trading.market_data.service import MarketDataService
from ultron.financial.trading.models import Order, OrderSide
from ultron.financial.trading.paper_broker import PaperBroker
from ultron.financial.trading.signal import SignalAction, TradingSignal


IST = ZoneInfo("Asia/Kolkata")


@pytest.mark.parametrize(
    ("symbol", "canonical", "provider_symbol", "exchange"),
    [
        ("NIFTY 50", "NIFTY50", "^NSEI", "NSE"),
        ("NIFTY50", "NIFTY50", "^NSEI", "NSE"),
        ("SENSEX", "SENSEX", "^BSESN", "BSE"),
        ("BSE Sensex", "SENSEX", "^BSESN", "BSE"),
        ("BSESENSEX", "SENSEX", "^BSESN", "BSE"),
    ],
)
def test_indian_index_is_inr_market_data_only(
    symbol, canonical, provider_symbol, exchange
):
    instrument = YahooMarketAdapter().get_instrument(symbol)

    assert instrument.symbol == canonical
    assert instrument.provider_instrument_id == provider_symbol
    assert instrument.exchange == exchange
    assert instrument.instrument_type is InstrumentType.INDEX
    assert instrument.market is MarketType.STOCK
    assert instrument.quote_currency == "INR"
    assert instrument.orderable is False
    assert instrument.tick_size is None
    assert instrument.quantity_step is None
    assert instrument.eligible_for_order(1, 1) is False


def test_generic_paper_adapter_and_broker_reject_index_orders():
    broker = PaperBroker(initial_capital=100_000)
    market_adapter = PaperMarketAdapter(
        broker,
        prices={"NIFTY50": 25_000},
    )
    market_adapter.connect()
    instrument = market_adapter.get_instrument("NIFTY50")

    direct_broker_result = broker.submit(
        Order(
            symbol="NIFTY50",
            side=OrderSide.BUY,
            quantity=1,
            price=25_000,
        )
    )
    engine_result = PaperTradingEngine(broker).execute(
        TradingSignal(
            symbol="NIFTY50",
            action=SignalAction.BUY,
            price=25_000,
            stop_loss=24_000,
            take_profit=27_000,
            confidence=0.9,
        )
    )

    assert instrument.instrument_type is InstrumentType.INDEX
    assert instrument.orderable is False
    assert direct_broker_result["success"] is False
    assert engine_result["success"] is False
    assert broker.snapshot({"NIFTY50": 25_000})["trade_count"] == 0


def test_indian_cash_equity_is_not_orderable_without_verified_metadata():
    instrument = YahooMarketAdapter().get_instrument("RELIANCE.NS")

    assert instrument.instrument_type is InstrumentType.EQUITY
    assert instrument.exchange == "NSE"
    assert instrument.quote_currency == "INR"
    assert instrument.orderable is False
    assert instrument.metadata_verified is False


@pytest.mark.parametrize(
    "instrument",
    [
        Instrument(
            symbol="NIFTY50",
            market=MarketType.STOCK,
            base_currency="INR",
            quote_currency="INR",
            instrument_type=InstrumentType.INDEX,
            orderable=False,
        ),
        Instrument(
            symbol="BANKNIFTY-FUT",
            market=MarketType.STOCK,
            base_currency="INR",
            quote_currency="INR",
            exchange="NSE",
            instrument_type=InstrumentType.FUTURE,
            underlying="NIFTY BANK",
            expiry=date(2027, 1, 28),
        ),
        Instrument(
            symbol="NIFTY-25000-CE",
            market=MarketType.STOCK,
            base_currency="INR",
            quote_currency="INR",
            exchange="NSE",
            instrument_type=InstrumentType.OPTION,
            underlying="NIFTY50",
            expiry=date(2027, 1, 28),
            strike=25000,
            option_type="CE",
        ),
        Instrument(
            symbol="NIFTYETF",
            market=MarketType.STOCK,
            base_currency="INR",
            quote_currency="INR",
            exchange="NSE",
            instrument_type=InstrumentType.ETF,
        ),
    ],
)
def test_indian_instrument_types_are_distinct(instrument):
    assert instrument.instrument_type in {
        InstrumentType.INDEX,
        InstrumentType.FUTURE,
        InstrumentType.OPTION,
        InstrumentType.ETF,
    }


def test_option_metadata_is_validated():
    with pytest.raises(ValueError, match="underlying and expiry"):
        Instrument(
            symbol="NIFTY-CE",
            market=MarketType.STOCK,
            base_currency="INR",
            quote_currency="INR",
            instrument_type=InstrumentType.OPTION,
        )
    with pytest.raises(ValueError, match="CE or PE"):
        Instrument(
            symbol="NIFTY-CE",
            market=MarketType.STOCK,
            base_currency="INR",
            quote_currency="INR",
            instrument_type=InstrumentType.OPTION,
            underlying="NIFTY50",
            expiry=date(2027, 1, 28),
            strike=25000,
            option_type="CALL",
        )


def test_indian_order_eligibility_requires_current_verified_metadata():
    now = datetime.now(IST)
    current = Instrument(
        symbol="NIFTYETF",
        market=MarketType.STOCK,
        base_currency="INR",
        quote_currency="INR",
        exchange="NSE",
        instrument_type=InstrumentType.ETF,
        metadata_verified=True,
        metadata_observed_at=now - timedelta(hours=1),
        metadata_expires_at=now + timedelta(hours=1),
    )
    expired = Instrument(
        symbol="NIFTYETF",
        market=MarketType.STOCK,
        base_currency="INR",
        quote_currency="INR",
        exchange="NSE",
        instrument_type=InstrumentType.ETF,
        metadata_verified=True,
        metadata_observed_at=now - timedelta(days=2),
        metadata_expires_at=now - timedelta(days=1),
    )

    assert current.eligible_for_order(1, 100) is True
    assert expired.eligible_for_order(1, 100) is False


@patch("ultron.financial.trading.market.adapters.yahoo.yf.Ticker")
def test_yahoo_india_snapshot_preserves_timestamp_and_reports_unknown_latency(
    mock_ticker,
):
    observed_at = pd.Timestamp("2026-10-15 10:00:00", tz="Asia/Kolkata")
    frame = pd.DataFrame({"Close": [25_000.0]}, index=[observed_at])
    mock_ticker.return_value.history.return_value = frame

    snapshot = YahooMarketAdapter().get_snapshot("NIFTY 50")

    mock_ticker.assert_called_once_with("^NSEI")
    assert snapshot["symbol"] == "NIFTY50"
    assert snapshot["price"] == 25_000.0
    assert snapshot["timestamp"].tzinfo is not None
    assert snapshot["timestamp"].utcoffset() == timedelta(hours=5, minutes=30)
    assert snapshot["real_time"] is False
    assert snapshot["latency_status"] == "unknown_or_delayed"
    assert snapshot["orderable"] is False
    assert snapshot["currency"] == "INR"


@patch("ultron.financial.trading.market.adapters.yahoo.yf.Ticker")
def test_yahoo_history_builds_timezone_aware_provenance_candles(mock_ticker):
    dates = pd.DatetimeIndex(
        [
            pd.Timestamp("2026-10-14 09:15", tz="Asia/Kolkata"),
            pd.Timestamp("2026-10-15 09:15", tz="Asia/Kolkata"),
        ]
    )
    frame = pd.DataFrame(
        {
            "Open": [24_900.0, 25_000.0],
            "High": [25_100.0, 25_200.0],
            "Low": [24_800.0, 24_950.0],
            "Close": [25_000.0, 25_100.0],
            "Volume": [0.0, 0.0],
        },
        index=dates,
    )
    mock_ticker.return_value.history.return_value = frame

    candles = YahooMarketAdapter().get_history(
        "NIFTY50",
        period="5d",
        interval="1d",
        now=datetime(2026, 10, 15, 16, 0, tzinfo=IST),
    )

    assert mock_ticker.call_args.args == ("^NSEI",)
    assert len(candles) == 2
    assert all(candle.symbol == "NIFTY50" for candle in candles)
    assert all(candle.timestamp.tzinfo is not None for candle in candles)
    assert all(candle.source == "Yahoo Finance via yfinance" for candle in candles)
    assert all(candle.realtime_status == "unknown_or_delayed" for candle in candles)
    assert all(candle.is_complete for candle in candles)


@patch("ultron.financial.trading.market.adapters.yahoo.yf.Ticker")
def test_yahoo_history_marks_current_session_candle_incomplete(mock_ticker):
    timestamp = pd.Timestamp("2026-10-15 09:15", tz="Asia/Kolkata")
    frame = pd.DataFrame(
        {
            "Open": [25_000.0],
            "High": [25_200.0],
            "Low": [24_950.0],
            "Close": [25_100.0],
            "Volume": [0.0],
        },
        index=[timestamp],
    )
    mock_ticker.return_value.history.return_value = frame

    candle = YahooMarketAdapter().get_history(
        "SENSEX",
        interval="1d",
        now=datetime(2026, 10, 15, 10, 0, tzinfo=IST),
    )[0]

    assert candle.is_complete is False
    result = MarketDataService(YahooMarketAdapter()).validate_series([candle])
    assert result["valid"] is False
    assert "incomplete" in result["reason"].lower()


def test_yahoo_history_rejects_missing_and_invalid_rows():
    with patch(
        "ultron.financial.trading.market.adapters.yahoo.yf.Ticker"
    ) as mock_ticker:
        mock_ticker.return_value.history.return_value = pd.DataFrame()
        with pytest.raises(ValueError, match="No historical market data"):
            YahooMarketAdapter().get_history("NIFTY50")

        invalid = pd.DataFrame(
            {
                "Open": [25_000.0],
                "High": [25_200.0],
                "Low": [24_950.0],
                "Close": [float("inf")],
                "Volume": [0.0],
            },
            index=[pd.Timestamp("2026-10-14 09:15", tz="Asia/Kolkata")],
        )
        mock_ticker.return_value.history.return_value = invalid
        with pytest.raises(ValueError, match="finite"):
            YahooMarketAdapter().get_history(
                "NIFTY50",
                now=datetime(2026, 10, 15, 16, 0, tzinfo=IST),
            )


def test_india_session_is_timezone_aware_and_discloses_calendar_gap():
    adapter = YahooMarketAdapter()
    open_result = adapter.get_market_session(
        "NIFTY50",
        datetime(2026, 10, 15, 9, 15, tzinfo=IST),
    )
    weekend_result = adapter.get_market_session(
        "SENSEX",
        datetime(2026, 10, 17, 10, 0, tzinfo=IST),
    )

    assert open_result["scheduled_open"] is True
    assert open_result["timezone"] == "Asia/Kolkata"
    assert open_result["calendar_verified"] is False
    assert weekend_result["scheduled_open"] is False


def test_market_data_service_rejects_stale_indian_index_snapshot():
    class StaleIndexAdapter(YahooMarketAdapter):
        def is_market_open(self, symbol):
            return True

        def get_snapshot(self, symbol):
            return {
                "symbol": "NIFTY50",
                "price": 25_000,
                "timestamp": datetime.now(UTC) - timedelta(minutes=2),
            }

    result = MarketDataService(StaleIndexAdapter()).get_current_price("NIFTY50")

    assert result["valid"] is False
    assert "stale" in result["reason"].lower()


def test_controller_blocks_paper_order_for_market_data_only_index():
    broker = PaperBroker(initial_capital=100_000)
    adapter = YahooMarketAdapter()
    controller = FinancialTradingController(
        PaperTradingEngine(broker),
        market_data=MarketDataService(adapter),
    )
    analysis = {
        "symbol": "NIFTY50",
        "decision": {"decision": "consider", "confidence": 0.9},
        "reasoning": {"confidence": 0.9},
        "risk": {"risk": {"risk_level": "low"}},
        "strategies": ["test"],
    }

    result = controller.execute(
        analysis=analysis,
        price=25_000,
        price_timestamp=datetime.now(UTC),
    )

    assert result["success"] is False
    assert result["stage"] == "instrument_validation"
    assert broker.snapshot({"NIFTY50": 25_000})["trade_count"] == 0


def test_unconfigured_indian_market_provider_reports_unavailable():
    controller = FinancialTradingController(
        PaperTradingEngine(PaperBroker(initial_capital=100_000))
    )

    result = controller.execute_from_market({"symbol": "NIFTY50"})

    assert result["success"] is False
    assert result["stage"] == "market_data"
    assert result["reason"] == "Market data service is not configured."


def test_indian_indices_fit_existing_research_loader_but_require_cost_input(
    tmp_path,
):
    dates = pd.bdate_range("2024-01-01", periods=70)
    close = [100.0 + i + (2.0 if i % 2 else 0.0) for i in range(len(dates))]
    frame = pd.DataFrame(
        {
            "Ticker": "NIFTY50",
            "Date": dates,
            "Open": close,
            "High": [value + 2 for value in close],
            "Low": [value - 2 for value in close],
            "Close": close,
            "Volume": [1_000 + 10 * i for i in range(len(dates))],
        }
    )
    path = tmp_path / "nifty.csv"
    frame.to_csv(path, index=False)
    loaded = load_ohlcv(path)
    features = build_features(loaded)

    assert set(loaded["ticker"]) == {"NIFTY50"}
    assert not features.empty
    with pytest.raises(ValueError, match="explicit user-supplied"):
        train_and_evaluate(path, tmp_path / "model")
    signals = pd.DataFrame(
        {
            "date": [pd.Timestamp("2024-01-01")],
            "ticker": ["NIFTY50"],
            "probability_up": [0.9],
            "next_open_to_close_return": [0.01],
        }
    )
    with pytest.raises(ValueError, match="explicit user-supplied"):
        evaluate_signals(signals)

    scored = evaluate_signals(signals, cost_bps_per_side=5.0)
    assert scored["cost_bps_per_side"] == 5.0
    assert "not verified market-specific" in scored["cost_assumption"]


def test_indian_research_training_requires_and_records_explicit_costs(tmp_path):
    dates = pd.bdate_range("2020-01-01", periods=300)
    close = [1_000 + i + 10 * sin(i / 5) for i in range(len(dates))]
    open_prices = [
        price * (1.01 if index % 2 == 0 else 0.99)
        for index, price in enumerate(close)
    ]
    frame = pd.DataFrame(
        {
            "Ticker": "^NSEI",
            "Date": dates,
            "Open": open_prices,
            "High": [max(open_, close_) + 5 for open_, close_ in zip(open_prices, close)],
            "Low": [min(open_, close_) - 5 for open_, close_ in zip(open_prices, close)],
            "Close": close,
            "Volume": [10_000 + index for index in range(len(dates))],
        }
    )
    path = tmp_path / "nse-index.csv"
    frame.to_csv(path, index=False)

    with pytest.raises(ValueError, match="explicit user-supplied"):
        train_and_evaluate(path, tmp_path / "missing-cost")

    report = train_and_evaluate(
        path,
        tmp_path / "with-cost",
        cost_bps_per_side=5.0,
    )

    assert report["success"] is True
    assert report["validation_backtest"]["cost_bps_per_side"] == 5.0
    assert report["test_backtest"]["cost_bps_per_side"] == 5.0
    assert "not verified market-specific" in report["cost_assumption"]


def test_bootstrap_can_opt_into_india_yahoo_read_only_provider(monkeypatch):
    from ultron.financial.bootstrap import build_financial_runtime

    monkeypatch.setenv("ULTRON_FINANCIAL_MARKET_PROVIDER", "india_yahoo")
    runtime = build_financial_runtime(memory_db=":memory:")
    try:
        assert isinstance(
            runtime.trading_controller.market_data.adapter,
            YahooMarketAdapter,
        )
        assert runtime.paper_broker.supports_live_orders is False
    finally:
        runtime.close()


def test_instrument_rejects_nonfinite_constraints_and_naive_metadata_time():
    with pytest.raises(ValueError, match="finite and positive"):
        Instrument(
            symbol="NIFTYETF",
            market=MarketType.STOCK,
            base_currency="INR",
            quote_currency="INR",
            tick_size=float("nan"),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        Instrument(
            symbol="NIFTYETF",
            market=MarketType.STOCK,
            base_currency="INR",
            quote_currency="INR",
            exchange="NSE",
            metadata_observed_at=datetime(2026, 10, 15, 10),
        )


def test_paper_engine_enforces_verified_indian_tick_and_quantity_constraints():
    now = datetime.now(UTC)
    instrument = Instrument(
        symbol="NIFTYETF",
        market=MarketType.STOCK,
        base_currency="INR",
        quote_currency="INR",
        quantity_step=300,
        tick_size=0.05,
        contract_size=300,
        min_quantity=300,
        min_notional=1_000,
        exchange="NSE",
        instrument_type=InstrumentType.ETF,
        metadata_verified=True,
        metadata_observed_at=now - timedelta(minutes=1),
        metadata_expires_at=now + timedelta(minutes=5),
    )
    broker = PaperBroker(initial_capital=100_000)
    engine = PaperTradingEngine(broker)
    signal = TradingSignal(
        symbol="NIFTYETF",
        action=SignalAction.BUY,
        price=100,
        stop_loss=96,
        take_profit=108,
        confidence=0.9,
    )

    result = engine.execute(
        signal,
        instrument=instrument,
    )

    assert result["success"] is False
    assert "quantity, notional, or metadata" in result["reason"]
    assert broker.snapshot({"NIFTYETF": 100})["trade_count"] == 0

    mispriced = TradingSignal(
        symbol="NIFTYETF",
        action=SignalAction.BUY,
        price=100.03,
        stop_loss=96,
        take_profit=108,
        confidence=0.9,
    )
    result = engine.execute(mispriced, instrument=instrument)
    assert result["success"] is False
    assert "tick size" in result["reason"]
    assert broker.snapshot({"NIFTYETF": 100.03})["trade_count"] == 0


def test_candle_rejects_invalid_prices_for_indian_index():
    with pytest.raises(ValueError, match="finite and positive"):
        Candle(
            symbol="NIFTY50",
            timestamp=datetime.now(IST),
            open=25_000,
            high=25_200,
            low=24_950,
            close=float("nan"),
            volume=0,
        )
