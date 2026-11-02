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


def make_cycle():
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

    return broker, controller, decision_engine, market_data


def make_candles():
    now = datetime.now(UTC)

    return [
        Candle(
            symbol="TEST",
            timestamp=now + timedelta(minutes=i),
            open=100 + i * 0.5,
            high=101 + i * 0.5,
            low=99 + i * 0.5,
            close=100 + i * 0.5,
            volume=1000,
        )
        for i in range(40)
    ]


def test_paper_cycle_produces_decision():
    _, _, decision_engine, market_data = make_cycle()

    result = market_data.validate_series(
        make_candles()
    )

    closes = [
        candle.close
        for candle in result["series"].candles
    ]

    decision = decision_engine.evaluate(
        equity=100_000,
        symbol="TEST",
        candles_by_timeframe={"1h": closes},
        entry_price=100,
    )

    assert decision["symbol"] == "TEST"
    assert "approved" in decision
    assert "indicators" in decision


def test_paper_cycle_can_convert_approved_decision():
    _, _, decision_engine, market_data = make_cycle()

    result = market_data.validate_series(
        make_candles()
    )

    closes = [
        candle.close
        for candle in result["series"].candles
    ]

    decision = decision_engine.evaluate(
        equity=100_000,
        symbol="TEST",
        candles_by_timeframe={"1h": closes},
        entry_price=100,
    )

    if decision["approved"]:
        signal = DecisionSignalAdapter.convert(
            decision
        )

        assert signal.symbol == "TEST"
        assert signal.price == 100


def test_paper_cycle_executes_through_controller():
    broker, controller, _, _ = make_cycle()

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

    portfolio = broker.snapshot(
        {"TEST": 100.0}
    )

    assert portfolio["trade_count"] == 1
