from ultron.financial.trading.intelligence.decision import (
    TradingDecisionEngine,
)


def make_candles():
    return {
        "1h": [100 + i for i in range(30)],
        "15m": [100 + i for i in range(30)],
        "5m": [100 + i for i in range(30)],
    }


def test_decision_engine_rejects_invalid_equity():
    engine = TradingDecisionEngine()

    result = engine.evaluate(
        equity=0,
        symbol="TEST",
        candles_by_timeframe=make_candles(),
        entry_price=100,
    )

    assert result["approved"] is False
    assert result["action"] == "hold"


def test_decision_engine_rejects_invalid_price():
    engine = TradingDecisionEngine()

    result = engine.evaluate(
        equity=100_000,
        symbol="TEST",
        candles_by_timeframe=make_candles(),
        entry_price=0,
    )

    assert result["approved"] is False
    assert result["action"] == "hold"


def test_decision_engine_rejects_empty_timeframes():
    engine = TradingDecisionEngine()

    result = engine.evaluate(
        equity=100_000,
        symbol="TEST",
        candles_by_timeframe={},
        entry_price=100,
    )

    assert result["approved"] is False
    assert result["action"] == "hold"


def test_decision_engine_returns_analysis():
    engine = TradingDecisionEngine()

    result = engine.evaluate(
        equity=100_000,
        symbol="TEST",
        candles_by_timeframe=make_candles(),
        entry_price=129,
    )

    assert result["symbol"] == "TEST"
    assert "timeframe" in result
    assert "indicators" in result
