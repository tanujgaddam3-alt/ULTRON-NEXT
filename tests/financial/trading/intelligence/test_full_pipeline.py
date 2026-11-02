from datetime import UTC, datetime, timedelta

from ultron.financial.trading.controller import (
    FinancialTradingController,
)
from ultron.financial.trading.engine import PaperTradingEngine
from ultron.financial.trading.intelligence.decision import (
    TradingDecisionEngine,
)
from ultron.financial.trading.intelligence.decision_adapter import (
    DecisionSignalAdapter,
)
from ultron.financial.trading.market.adapters.paper import (
    PaperMarketAdapter,
)
from ultron.financial.trading.market_data.models import Candle
from ultron.financial.trading.market_data.service import (
    MarketDataService,
)
from ultron.financial.trading.paper_broker import PaperBroker


def make_pipeline():
    broker = PaperBroker(initial_capital=100_000)
    engine = PaperTradingEngine(broker)

    adapter = PaperMarketAdapter(
        broker,
        prices={"TEST": 100.0},
    )
    adapter.connect()

    market_data = MarketDataService(adapter)

    controller = FinancialTradingController(
        engine,
        market_data=market_data,
    )

    decision_engine = TradingDecisionEngine()

    return (
        broker,
        controller,
        decision_engine,
        market_data,
    )


def make_candles():
    now = datetime.now(UTC)

    candles = []

    for index in range(40):
        price = 100.0 + index * 0.5

        candles.append(
            Candle(
                symbol="TEST",
                timestamp=now + timedelta(minutes=index),
                open=price,
                high=price + 1.0,
                low=price - 1.0,
                close=price,
                volume=1000.0,
            )
        )

    return candles


def test_market_data_reaches_decision_engine():
    (
        _,
        _,
        decision_engine,
        market_data,
    ) = make_pipeline()

    candles = make_candles()
    series_result = market_data.validate_series(candles)

    assert series_result["valid"] is True

    closes = [
        candle.close
        for candle in series_result["series"].candles
    ]

    decision = decision_engine.evaluate(
        equity=100_000,
        symbol="TEST",
        candles_by_timeframe={
            "1h": closes,
        },
        entry_price=100.0,
    )

    assert "approved" in decision
    assert decision["symbol"] == "TEST"
    assert "indicators" in decision


def test_approved_decision_becomes_signal():
    (
        _,
        _,
        decision_engine,
        market_data,
    ) = make_pipeline()

    candles = make_candles()

    result = market_data.validate_series(candles)

    closes = [
        candle.close
        for candle in result["series"].candles
    ]

    decision = decision_engine.evaluate(
        equity=100_000,
        symbol="TEST",
        candles_by_timeframe={"1h": closes},
        entry_price=100.0,
    )

    if decision["approved"]:
        signal = DecisionSignalAdapter.convert(decision)

        assert signal.symbol == "TEST"
        assert signal.price == 100.0
        assert signal.action.value in {
            "buy",
            "sell",
            "hold",
        }


def test_signal_reaches_controller():
    (
        broker,
        controller,
        _,
        _,
    ) = make_pipeline()

    analysis = {
        "symbol": "TEST",
        "decision": {
            "decision": "consider",
            "confidence": 0.90,
        },
        "reasoning": {
            "confidence": 0.90,
        },
        "risk": {
            "risk": {
                "risk_level": "low",
            }
        },
        "strategies": [
            "trend_following",
        ],
    }

    result = controller.execute_from_market(
        analysis
    )

    assert result["success"] is True
    assert result["signal"].symbol == "TEST"

    assert broker.snapshot(
        {"TEST": 100.0}
    )["trade_count"] == 1
