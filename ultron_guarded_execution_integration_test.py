#!/usr/bin/env python3
"""Deterministic guarded execution + journal recovery test for ULTRON.

Uses the real FinancialTradingController, PaperTradingEngine, PaperBroker,
PaperMarketAdapter, and AutonomousTradingRuntime. No real broker orders.
All SQLite state lives under a temporary directory.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import traceback
from pathlib import Path

def require(ok, msg):
    if not ok:
        raise AssertionError(msg)

def make_analysis():
    return {
        "symbol": "TEST",
        "decision": {"decision": "consider", "confidence": 0.90},
        "reasoning": {"confidence": 0.90},
        "risk": {"risk": {"risk_level": "low"}},
        "strategies": ["trend_following"],
    }

def make_container(controller):
    # Runtime needs a financial skill and controller. The skill is never invoked
    # by this direct guarded-execution test; it exists to satisfy the constructor.
    class Skill:
        def execute(self, *args, **kwargs):
            raise AssertionError("This test must not invoke the autonomous analysis skill")
    class Container:
        financial_skill = Skill()
        financial_trading_controller = controller
    return Container()

def main():
    require(os.getenv("ULTRON_FINANCIAL_MARKET_PROVIDER", "").strip().lower() == "alpaca",
            "Expected ULTRON_FINANCIAL_MARKET_PROVIDER=alpaca")
    require(os.getenv("ULTRON_EXECUTION_BACKEND", "").strip().lower() == "simulation",
            "Refusing to run unless ULTRON_EXECUTION_BACKEND=simulation")

    from ultron.financial.trading.market.adapters.paper import PaperMarketAdapter
    from ultron.financial.trading.market_data.service import MarketDataService
    from ultron.financial.trading.paper_broker import PaperBroker
    from ultron.financial.trading.engine import PaperTradingEngine
    from ultron.financial.trading.controller import FinancialTradingController
    from ultron.financial.trading.autonomous_runtime import AutonomousTradingRuntime
    from datetime import UTC, datetime, timedelta

    with tempfile.TemporaryDirectory(prefix="ultron_guarded_exec_") as td:
        db_path = str(Path(td) / "runtime.sqlite3")

        broker = PaperBroker(initial_capital=100_000)
        adapter = PaperMarketAdapter(broker, prices={"TEST": 100.0})
        adapter.connect()
        market_data = MarketDataService(adapter)
        controller = FinancialTradingController(
            PaperTradingEngine(broker), market_data=market_data
        )
        # Crucial: inject the deterministic controller BEFORE runtime init.
        # Runtime performs journal recovery in __init__, so swapping controller
        # after construction would recover a different broker than the one tested.
        runtime = AutonomousTradingRuntime(
            container=make_container(controller),
            symbols=["TEST"],
            state_path=db_path,
        )
        require(runtime.controller is controller, "Runtime did not retain injected controller")
        require(runtime.controller.trading_engine.broker is broker,
                "Runtime controller is not using the test PaperBroker")

        before = broker.snapshot({"TEST": 100.0})
        require(before["trade_count"] == 0, "Paper broker should start empty")

        buy = controller.execute_from_market(make_analysis())
        require(buy.get("success") is True, f"BUY controller execution failed: {buy!r}")
        require(buy.get("execution", {}).get("status") == "filled",
                f"BUY did not fill: {buy!r}")
        runtime._record_execution("TEST", buy, "guarded_execution_integration_test")

        with sqlite3.connect(db_path) as db:
            rule = db.execute(
                "SELECT stop_loss, take_profit FROM position_rules WHERE symbol='TEST'"
            ).fetchone()
        require(rule is not None, "BUY journal did not create position stop/target rules")
        stop_loss, take_profit = map(float, rule)
        require(stop_loss < 100 < take_profit,
                f"Invalid stop/target values: stop={stop_loss}, target={take_profit}")
        require(broker.snapshot({"TEST": 100.0})["trade_count"] == 1,
                "Expected exactly one simulated BUY fill")

        # Cross target. Runtime monitor must submit SELL through guarded controller.
        adapter.prices["TEST"] = take_profit + 1.0
        runtime._monitor_exits()
        require("TEST" not in broker.portfolio.positions,
                "Take-profit monitor did not close TEST position")
        require(broker.snapshot({"TEST": adapter.prices["TEST"]})["trade_count"] == 2,
                "Expected BUY and SELL in paper broker")

        with sqlite3.connect(db_path) as db:
            rows = db.execute(
                "SELECT action, price, result_json, replayed FROM executions ORDER BY id"
            ).fetchall()
            remaining_rules = db.execute(
                "SELECT COUNT(*) FROM position_rules WHERE symbol='TEST'"
            ).fetchone()[0]
        require(len(rows) == 2, f"Expected two execution journal rows, got {len(rows)}")
        require([str(row[0]).lower() for row in rows] == ["buy", "sell"],
                f"Unexpected journal actions: {[row[0] for row in rows]}")
        require(remaining_rules == 0, "Position rules remain after SELL")

        # Verify persisted records include the exact fill quantity required by recovery.
        for action, price, result_json, _ in rows:
            result = json.loads(result_json or "{}")
            execution = result.get("execution", {})
            quantity = execution.get("quantity")
            if quantity is None and isinstance(execution.get("order"), dict):
                quantity = execution["order"].get("quantity")
            require(quantity is not None and float(quantity) > 0,
                    f"{action} journal record has no exact fill quantity")

        # Runtime initialization recovers into the broker already held by its
        # injected controller, then validates trade_count == number of fills.
        recovered_broker = PaperBroker(initial_capital=100_000)
        recovered_adapter = PaperMarketAdapter(
            recovered_broker, prices={"TEST": adapter.prices["TEST"]}
        )
        recovered_adapter.connect()
        recovered_market_data = MarketDataService(recovered_adapter)
        recovered_controller = FinancialTradingController(
            PaperTradingEngine(recovered_broker), market_data=recovered_market_data
        )
        recovered_runtime = AutonomousTradingRuntime(
            container=make_container(recovered_controller),
            symbols=["TEST"],
            state_path=db_path,
        )
        require(recovered_runtime.controller is recovered_controller,
                "Recovery runtime replaced injected controller")
        recovered = recovered_broker.snapshot({"TEST": adapter.prices["TEST"]})
        require(recovered["trade_count"] == 2,
                f"Expected two recovered fills in injected broker, got {recovered['trade_count']}")
        require("TEST" not in recovered_broker.portfolio.positions,
                "Closed TEST position reappeared after replaying BUY then SELL")
        with sqlite3.connect(db_path) as db:
            replayed_count = db.execute(
                "SELECT COUNT(*) FROM executions WHERE replayed=1"
            ).fetchone()[0]
        require(replayed_count == 2, f"Expected 2 replay flags, got {replayed_count}")

        stale = controller.execute(
            analysis=make_analysis(),
            price=100.0,
            price_timestamp=datetime.now(UTC) - timedelta(seconds=60),
        )
        require(stale.get("success") is False and stale.get("stage") == "price_guard",
                f"Stale price was not rejected: {stale!r}")
        require(broker.snapshot({"TEST": adapter.prices["TEST"]})["trade_count"] == 2,
                "Stale-price rejection changed trade count")

        print("PASS: deterministic PaperMarketAdapter and real controller stack")
        print("PASS: guarded BUY filled and exact quantity journaled")
        print(f"PASS: stop-loss={stop_loss:.4f}; take-profit={take_profit:.4f}")
        print("PASS: take-profit exit used runtime guarded controller path")
        print("PASS: BUY and SELL journaled; position rules cleared")
        print("PASS: runtime recovered exact BUY then SELL into the injected broker")
        print("PASS: recovered closed position remains closed; both rows marked replayed")
        print("PASS: stale price rejected without additional execution")
        print("PASS: all state is isolated in a temporary directory")
        print("SAFETY: simulation-only PaperBroker; no live orders")
        print("RESULT: guarded simulated execution integration test passed")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        print("SAFETY: simulation-only PaperBroker and temporary SQLite only")
        raise SystemExit(1)
