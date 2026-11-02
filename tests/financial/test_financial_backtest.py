
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ultron.financial.trading.backtest import (
    BacktestConfig,
    FinancialBacktestEngine,
)
from ultron.financial.trading.market_data.models import Candle


def make_candles(
    count: int = 60,
    start_price: float = 100.0,
):
    start = datetime(
        2025,
        1,
        1,
        tzinfo=UTC,
    )

    candles = []

    for i in range(count):
        price = start_price + i * 0.5

        candles.append(
            Candle(
                symbol="TEST",
                timestamp=start + timedelta(days=i),
                open=price,
                high=price + 1.0,
                low=max(0.01, price - 1.0),
                close=price + 0.5,
                volume=1000.0,
            )
        )

    return candles


def _deterministic_execution_result(
    result: dict[str, Any],
) -> dict[str, Any]:
    """
    Normalize execution results for deterministic backtest comparisons.

    Paper-broker order IDs are UUID-based and therefore differ between
    independent simulation runs. They are operational identifiers, not
    part of the financial outcome of a backtest.
    """
    if not isinstance(result, dict):
        return result

    normalized = dict(result)

    order = normalized.get("order")

    if isinstance(order, dict):
        order = dict(order)
        order.pop("order_id", None)
        normalized["order"] = order

    normalized.pop("order_id", None)

    return normalized


def _deterministic_event(
    event: dict[str, Any],
) -> dict[str, Any]:
    """
    Normalize an event so independent backtest runs can be compared.
    """
    normalized = dict(event)

    execution = normalized.get("execution")

    if isinstance(execution, dict):
        normalized["execution"] = (
            _deterministic_execution_result(execution)
        )

    return normalized


def _economic_event(event: dict[str, Any]) -> dict[str, Any]:
    execution = event.get("execution", {})
    signal = execution.get("signal")
    order = execution.get("order", {})

    return {
        "type": event.get("type"),
        "timestamp": event.get("timestamp"),
        "reason": event.get("reason"),
        "price": event.get("price"),
        "execution": {
            "success": execution.get("success"),
            "status": execution.get("status"),
            "symbol": execution.get("symbol"),
            "action": execution.get("action"),
            "quantity": execution.get("quantity"),
            "price": execution.get("price"),
            "fees": order.get("fees"),
            "signal": (
                {
                    "symbol": signal.symbol,
                    "action": signal.action.value,
                    "price": signal.price,
                    "stop_loss": signal.stop_loss,
                    "take_profit": signal.take_profit,
                }
                if signal is not None
                else None
            ),
        },
    }


class RecordingDecisionEngine:
    def __init__(self):
        from ultron.financial.trading.intelligence.decision import (
            TradingDecisionEngine,
        )

        self.delegate = TradingDecisionEngine()
        self.calls: list[dict[str, Any]] = []

    def evaluate(self, **kwargs):
        closes = list(
            kwargs["candles_by_timeframe"]["1d"]
        )
        self.calls.append(
            {
                "closes": closes,
                "entry_price": kwargs["entry_price"],
            }
        )
        return self.delegate.evaluate(**kwargs)


def test_rejects_empty_history():
    engine = FinancialBacktestEngine()

    with pytest.raises(ValueError):
        engine.run([])


def test_rejects_multiple_symbols():
    candles = make_candles()

    candles.append(
        Candle(
            symbol="OTHER",
            timestamp=(
                candles[-1].timestamp
                + timedelta(days=1)
            ),
            open=130,
            high=131,
            low=129,
            close=130,
            volume=1000,
        )
    )

    engine = FinancialBacktestEngine()

    with pytest.raises(ValueError):
        engine.run(candles)


def test_rejects_unordered_history():
    candles = make_candles()

    candles[10], candles[11] = (
        candles[11],
        candles[10],
    )

    engine = FinancialBacktestEngine()

    with pytest.raises(ValueError):
        engine.run(candles)


def test_result_contains_full_equity_curve():
    engine = FinancialBacktestEngine(
        config=BacktestConfig(
            initial_capital=100_000,
            min_history=30,
        )
    )

    result = engine.run(
        make_candles()
    )

    assert result.symbol == "TEST"
    assert result.initial_capital == 100_000
    assert result.final_equity > 0
    assert result.equity_curve
    assert result.equity_curve[-1].timestamp == (
        make_candles()[-1].timestamp
    )


def test_backtest_has_no_lookahead_requirement():
    candles = make_candles()

    engine = FinancialBacktestEngine(
        config=BacktestConfig(
            min_history=30,
        )
    )

    result_a = engine.run(candles)

    altered = list(candles)

    # Changing a future candle must not change decisions/events
    # before that candle.
    altered[-1] = Candle(
        symbol="TEST",
        timestamp=altered[-1].timestamp,
        open=1000,
        high=1100,
        low=900,
        close=1050,
        volume=1000,
    )

    result_b = engine.run(altered)

    midpoint = len(candles) - 1

    early_a = [
        _deterministic_event(event)
        for event in result_a.events
        if event["timestamp"]
        < candles[midpoint].timestamp
    ]

    early_b = [
        _deterministic_event(event)
        for event in result_b.events
        if event["timestamp"]
        < candles[midpoint].timestamp
    ]

    assert early_a == early_b


def test_future_candle_cannot_change_prior_inputs_or_economic_events():
    candles = make_candles()
    altered_index = 45

    baseline_engine = RecordingDecisionEngine()
    baseline = FinancialBacktestEngine(
        decision_engine=baseline_engine,
        config=BacktestConfig(min_history=30),
    ).run(candles)

    altered = list(candles)
    altered[altered_index] = Candle(
        symbol="TEST",
        timestamp=altered[altered_index].timestamp,
        open=1000.0,
        high=1500.0,
        low=500.0,
        close=1200.0,
        volume=10_000.0,
    )

    altered_engine = RecordingDecisionEngine()
    changed = FinancialBacktestEngine(
        decision_engine=altered_engine,
        config=BacktestConfig(min_history=30),
    ).run(altered)

    prior_call_count = altered_index - 30 + 1
    assert baseline_engine.calls[:prior_call_count] == (
        altered_engine.calls[:prior_call_count]
    )

    cutoff = candles[altered_index].timestamp
    baseline_events = [
        _economic_event(event)
        for event in baseline.events
        if event["timestamp"] < cutoff
    ]
    changed_events = [
        _economic_event(event)
        for event in changed.events
        if event["timestamp"] < cutoff
    ]

    assert baseline_events == changed_events


def test_backtest_rejects_incomplete_historical_candles():
    candles = make_candles()
    incomplete = candles[-1]
    candles[-1] = Candle(
        symbol=incomplete.symbol,
        timestamp=incomplete.timestamp,
        open=incomplete.open,
        high=incomplete.high,
        low=incomplete.low,
        close=incomplete.close,
        volume=incomplete.volume,
        is_complete=False,
    )

    with pytest.raises(ValueError, match="finalized candles"):
        FinancialBacktestEngine().run(candles)


def test_result_is_serializable_to_dict():
    result = FinancialBacktestEngine(
        config=BacktestConfig(
            min_history=30,
        )
    ).run(
        make_candles()
    )

    data = result.to_dict()

    assert data["symbol"] == "TEST"
    assert "equity_curve" in data
    assert "trades" in data
    assert "events" in data
    assert "max_drawdown_percent" in data
    assert "sharpe_ratio" in data


def test_config_rejects_invalid_capital():
    with pytest.raises(ValueError):
        BacktestConfig(
            initial_capital=0
        )


def test_config_rejects_invalid_history():
    with pytest.raises(ValueError):
        BacktestConfig(
            min_history=1
        )

