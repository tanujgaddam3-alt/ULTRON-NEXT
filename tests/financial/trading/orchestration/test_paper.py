from datetime import UTC, datetime

from ultron.financial.trading.controller import FinancialTradingController
from ultron.financial.trading.engine import PaperTradingEngine
from ultron.financial.trading.intelligence.decision import TradingDecisionEngine
from ultron.financial.trading.orchestration.paper import PaperTradingOrchestrator
from ultron.financial.trading.market.adapters.paper import PaperMarketAdapter
from ultron.financial.trading.market_data.service import MarketDataService
from ultron.financial.trading.paper_broker import PaperBroker


def make_orchestrator():
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

    return (
        PaperTradingOrchestrator(
            controller,
            TradingDecisionEngine(),
        ),
        broker,
    )


def candles():
    return {
        "1h": [
            100 + i * 0.5
            for i in range(40)
        ]
    }


def test_orchestrator_rejects_empty_symbol():
    orchestrator, _ = make_orchestrator()

    result = orchestrator.run(
        equity=100_000,
        symbol=" ",
        candles_by_timeframe=candles(),
    )

    assert result["success"] is False
    assert result["stage"] == "validation"


def test_orchestrator_produces_decision():
    orchestrator, _ = make_orchestrator()

    result = orchestrator.run(
        equity=100_000,
        symbol="TEST",
        candles_by_timeframe=candles(),
    )

    assert result["success"] is True
    assert "decision" in result
    assert result["market_data"]["price"] == 100.0


def test_orchestrator_handles_hold():
    orchestrator, broker = make_orchestrator()

    result = orchestrator.run(
        equity=100_000,
        symbol="TEST",
        candles_by_timeframe={
            "1h": [100.0] * 40,
        },
    )

    assert result["success"] is True
    assert result["status"] == "held"
    assert broker.snapshot(
        {"TEST": 100.0}
    )["trade_count"] == 0

def test_orchestrator_snapshot_returns_portfolio():
    orchestrator, _ = make_orchestrator()

    snapshot = orchestrator.snapshot(
        {"TEST": 100.0}
    )

    assert snapshot["cash"] == 100_000
    assert snapshot["equity"] == 100_000
    assert snapshot["positions"] == {}
    assert snapshot["trade_count"] == 0


def test_orchestrator_snapshot_after_trade():
    orchestrator, _ = make_orchestrator()

    result = orchestrator.run(
        equity=100_000,
        symbol="TEST",
        candles_by_timeframe=candles(),
    )

    if result["status"] == "executed":
        snapshot = orchestrator.snapshot(
            {"TEST": 100.0}
        )

        assert snapshot["trade_count"] == 1
        assert "TEST" in snapshot["positions"]
