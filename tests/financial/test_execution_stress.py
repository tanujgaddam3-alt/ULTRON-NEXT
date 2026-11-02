from __future__ import annotations

import json
import math
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ultron.financial.trading.autonomous_runtime import AutonomousTradingRuntime
from ultron.financial.trading.controller import FinancialTradingController
from ultron.financial.trading.engine import PaperTradingEngine
from ultron.financial.trading.market.adapters.paper import PaperMarketAdapter
from ultron.financial.trading.market_data.service import MarketDataService
from ultron.financial.trading.models import Order, OrderSide
from ultron.financial.trading.paper_broker import PaperBroker
from ultron.financial.trading.reliability.idempotency import OrderIdempotency
from ultron.financial.trading.risk.daily_loss_guard import DailyLossGuard


def _analysis(symbol: str = "TEST", decision: str = "consider", confidence: float = 0.90):
    return {
        "symbol": symbol,
        "decision": {"decision": decision, "confidence": confidence},
        "reasoning": {"confidence": confidence},
        "risk": {"risk": {"risk_level": "low"}},
        "strategies": ["trend_following"],
    }


def test_paper_broker_cash_and_position_accounting():
    broker = PaperBroker(initial_capital=100_000.0)
    buy = broker.submit(Order(symbol="TEST", side=OrderSide.BUY, quantity=10, price=100.0))
    assert buy["success"] is True
    assert broker.snapshot({"TEST": 100.0})["cash"] == pytest.approx(100_000.0 - (10 * 100.0) - (10 * 100.0 * broker.commission_rate))
    assert broker.snapshot({"TEST": 100.0})["positions"]["TEST"]["quantity"] == 10

    sell = broker.submit(Order(symbol="TEST", side=OrderSide.SELL, quantity=4, price=110.0))
    assert sell["success"] is True
    snapshot = broker.snapshot({"TEST": 110.0})
    assert snapshot["positions"]["TEST"]["quantity"] == 6
    assert snapshot["trade_count"] == 2
    assert snapshot["cash"] > 0

    rejected = broker.submit(Order(symbol="TEST", side=OrderSide.SELL, quantity=100, price=110.0))
    assert rejected["success"] is False
    assert rejected["status"] == "rejected"
    assert broker.snapshot({"TEST": 110.0})["trade_count"] == 2


@pytest.mark.parametrize("invalid_value", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("field", ["quantity", "price"])
def test_order_rejects_non_finite_execution_values(field, invalid_value):
    values = {
        "symbol": "TEST",
        "side": OrderSide.BUY,
        "quantity": 1.0,
        "price": 100.0,
    }
    values[field] = invalid_value

    with pytest.raises(ValueError, match="finite"):
        Order(**values)


def test_paper_broker_rejects_overflowing_notional_without_mutation():
    broker = PaperBroker(initial_capital=1_000.0, commission_rate=0.0)
    order = Order(
        symbol="TEST",
        side=OrderSide.BUY,
        quantity=1e308,
        price=1e308,
    )

    result = broker.submit(order)

    assert result["success"] is False
    assert result["status"] == "rejected"
    assert broker.snapshot({})["cash"] == 1_000.0
    assert broker.snapshot({})["positions"] == {}
    assert broker.snapshot({})["trade_count"] == 0


@pytest.mark.parametrize("invalid_price", [math.nan, math.inf, -math.inf])
def test_controller_rejects_non_finite_price_without_execution(invalid_price):
    broker = PaperBroker(initial_capital=100_000.0)
    controller = FinancialTradingController(PaperTradingEngine(broker))

    result = controller.execute(
        analysis=_analysis(),
        price=invalid_price,
        price_timestamp=datetime.now(UTC),
    )

    assert result["success"] is False
    assert result["stage"] == "validation"
    assert broker.snapshot({})["trade_count"] == 0


def test_order_idempotency_claim_is_fail_closed_across_restart():
    with tempfile.TemporaryDirectory(prefix="ultron-idempotency-") as td:
        db_path = Path(td) / "idempotency.sqlite3"

        idem = OrderIdempotency(db_path=db_path)
        assert idem.claim("intent:alpha") is True
        assert idem.claim("intent:alpha") is False
        assert idem.check("intent:alpha") is None

        restarted = OrderIdempotency(db_path=db_path)
        assert restarted.claim("intent:alpha") is False
        assert restarted.check("intent:alpha") is None

        record = restarted.record("intent:alpha", "order-123")
        assert record.order_id == "order-123"
        assert restarted.check("intent:alpha").order_id == "order-123"


def test_controller_duplicate_logical_intent_is_not_replayed():
    broker = PaperBroker(initial_capital=100_000.0)
    adapter = PaperMarketAdapter(broker, prices={"TEST": 100.0})
    adapter.connect()
    market_data = MarketDataService(adapter)
    controller = FinancialTradingController(PaperTradingEngine(broker), market_data=market_data)

    analysis = _analysis()
    analysis["logical_intent_id"] = "intent-duplicate"

    first = controller.execute_from_market(analysis)
    assert first["success"] is True
    assert broker.snapshot({"TEST": 100.0})["trade_count"] == 1

    second = controller.execute_from_market(analysis)
    assert second["success"] is True
    assert second["status"] == "duplicate"
    assert broker.snapshot({"TEST": 100.0})["trade_count"] == 1


def test_runtime_refuses_unresolved_intent_recovery(monkeypatch):
    monkeypatch.setenv("ULTRON_FINANCIAL_MARKET_PROVIDER", "alpaca")
    monkeypatch.setenv("ULTRON_EXECUTION_BACKEND", "simulation")

    with tempfile.TemporaryDirectory(prefix="ultron-runtime-") as td:
        db_path = Path(td) / "runtime.sqlite3"
        broker = PaperBroker(initial_capital=100_000.0)
        adapter = PaperMarketAdapter(broker, prices={"TEST": 100.0})
        adapter.connect()
        controller = FinancialTradingController(PaperTradingEngine(broker), market_data=MarketDataService(adapter))

        class Skill:
            def execute(self, *args, **kwargs):
                raise AssertionError("No analysis should run during unresolved-intent recovery")

        class Container:
            financial_skill = Skill()
            financial_trading_controller = controller

        with sqlite3.connect(db_path) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_intents (
                    intent_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    state TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT
                )
                """
            )
            db.execute(
                "INSERT INTO execution_intents (intent_id, created_at, updated_at, symbol, reason, state) VALUES (?, ?, ?, ?, ?, ?)",
                ("intent-unresolved", datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat(), "TEST", "pending trade", "PREPARED"),
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS executions (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, symbol TEXT NOT NULL, action TEXT NOT NULL, price REAL NOT NULL, stop_loss REAL, take_profit REAL, confidence REAL NOT NULL, strategy TEXT NOT NULL, reason TEXT NOT NULL, result_json TEXT NOT NULL, replayed INTEGER NOT NULL DEFAULT 0, intent_id TEXT)"
            )

        with pytest.raises(RuntimeError, match="Unresolved execution intent"):
            AutonomousTradingRuntime(container=Container(), symbols=["TEST"], state_path=db_path)


def test_concurrent_same_logical_intent_is_single_fill():
    broker = PaperBroker(initial_capital=100_000.0)
    adapter = PaperMarketAdapter(broker, prices={"TEST": 100.0})
    adapter.connect()
    controller = FinancialTradingController(PaperTradingEngine(broker), market_data=MarketDataService(adapter))

    analysis = _analysis()
    analysis["logical_intent_id"] = "intent-thread-duplicate"

    def invoke() -> dict:
        return controller.execute_from_market(dict(analysis))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: invoke(), range(8)))

    successful = [result for result in results if result.get("success") and result.get("status") != "duplicate"]
    duplicates = [result for result in results if result.get("status") == "duplicate"]

    assert len(successful) == 1
    assert len(duplicates) >= 1
    assert broker.snapshot({"TEST": 100.0})["trade_count"] == 1


def test_concurrent_distinct_logical_intents_are_independent():
    broker = PaperBroker(initial_capital=100_000.0)
    prices = {symbol: 100.0 for symbol in ["AAPL", "MSFT", "NVDA", "GOOG"]}
    adapter = PaperMarketAdapter(broker, prices=prices)
    adapter.connect()
    controller = FinancialTradingController(
        PaperTradingEngine(broker, risk_per_trade_percent=0.1),
        market_data=MarketDataService(adapter),
    )

    def invoke(intent_id: str, symbol: str):
        analysis = _analysis(symbol=symbol)
        analysis["logical_intent_id"] = intent_id
        return controller.execute_from_market(analysis)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda pair: invoke(pair[0], pair[1]),
                [("intent-a", "AAPL"), ("intent-b", "MSFT"), ("intent-c", "NVDA"), ("intent-d", "GOOG")],
            )
        )

    assert all(result.get("success") for result in results)
    assert broker.snapshot(prices)["trade_count"] == 4


def test_daily_loss_guard_and_sell_exit_stay_safe():
    broker = PaperBroker(initial_capital=100_000.0)
    adapter = PaperMarketAdapter(broker, prices={"TEST": 100.0})
    adapter.connect()
    controller = FinancialTradingController(
        PaperTradingEngine(broker),
        market_data=MarketDataService(adapter),
        daily_loss_guard=DailyLossGuard(max_daily_loss_percent=2.0),
    )

    controller.trading_engine.broker.portfolio.cash = 97_000.0
    controller.daily_starting_equity = 100_000.0

    buy_blocked = controller.execute(
        analysis=_analysis(),
        price=100.0,
        price_timestamp=datetime.now(UTC),
    )
    assert buy_blocked["success"] is False
    assert buy_blocked["stage"] == "daily_loss_guard"

    broker.submit(Order(symbol="TEST", side=OrderSide.BUY, quantity=10, price=100.0))
    exit_result = controller.execute(
        analysis={
            "symbol": "TEST",
            "decision": {"decision": "hold", "confidence": 0.9},
            "reasoning": {"confidence": 0.9},
            "risk": {"risk": {"risk_level": "low"}},
            "strategies": ["trend_following"],
            "logical_intent_id": "intent-exit",
        },
        price=100.0,
        price_timestamp=datetime.now(UTC),
    )
    assert exit_result["success"] is False or exit_result.get("execution", {}).get("status") in {"filled", "rejected", "held", "duplicate"}
    assert broker.snapshot({"TEST": 100.0})["trade_count"] >= 1


def test_repeated_runtime_cycle_does_not_duplicate_completed_execution(monkeypatch):
    monkeypatch.setenv("ULTRON_FINANCIAL_MARKET_PROVIDER", "alpaca")
    monkeypatch.setenv("ULTRON_EXECUTION_BACKEND", "simulation")

    with tempfile.TemporaryDirectory(prefix="ultron-cycle-") as td:
        db_path = Path(td) / "cycle.sqlite3"
        broker = PaperBroker(initial_capital=100_000.0)
        adapter = PaperMarketAdapter(broker, prices={"TEST": 100.0})
        adapter.connect()
        controller = FinancialTradingController(PaperTradingEngine(broker), market_data=MarketDataService(adapter))

        class Skill:
            def execute(self, command, **kwargs):
                if command == "analyze":
                    return {
                        "success": True,
                        "symbol": kwargs["symbol"],
                        "decision": {"decision": "consider", "confidence": 0.9},
                        "reasoning": {"confidence": 0.9},
                        "risk": {"risk": {"risk_level": "low"}},
                        "strategies": ["trend_following"],
                    }
                if command == "paper_trade":
                    return controller.execute_from_market({
                        "symbol": kwargs["symbol"],
                        "decision": {"decision": "consider", "confidence": 0.9},
                        "reasoning": {"confidence": 0.9},
                        "risk": {"risk": {"risk_level": "low"}},
                        "strategies": ["trend_following"],
                        "logical_intent_id": kwargs["analysis"]["logical_intent_id"],
                    })
                raise AssertionError(f"Unexpected command: {command}")

        class Container:
            financial_skill = Skill()
            financial_trading_controller = controller

        runtime = AutonomousTradingRuntime(container=Container(), symbols=["TEST"], state_path=db_path)
        runtime.run_cycle()
        first_trade_count = broker.snapshot({"TEST": 100.0})["trade_count"]
        runtime.run_cycle()
        second_trade_count = broker.snapshot({"TEST": 100.0})["trade_count"]
        assert first_trade_count == second_trade_count
        assert broker.snapshot({"TEST": 100.0})["positions"].get("TEST", {}).get("quantity", 0) in {0, 1}
