#!/usr/bin/env python3
"""ULTRON combined financial acceptance checks.

Safe-by-design:
- requires configured Alpaca market provider and simulation execution backend
- makes no network requests and submits no orders
- never opens the configured production runtime DB
- uses temporary SQLite files only where journaling/recovery are tested
- checks current behavior; does not change strategy thresholds
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path

PASS_COUNT = 0
FAILURES = []

def check(name, fn):
    global PASS_COUNT
    try:
        fn()
        PASS_COUNT += 1
        print(f"PASS: {name}")
    except Exception as exc:
        FAILURES.append((name, f"{type(exc).__name__}: {exc}"))
        print(f"FAIL: {name}: {type(exc).__name__}: {exc}")

def require(condition, message):
    if not condition:
        raise AssertionError(message)

def make_analysis(symbol="TEST", decision="consider", confidence=0.90, risk_level="low"):
    return {
        "symbol": symbol,
        "decision": {"decision": decision, "confidence": confidence},
        "reasoning": {"confidence": confidence},
        "risk": {"risk": {"risk_level": risk_level}},
        "strategies": ["trend_following"],
    }

def main():
    provider = os.getenv("ULTRON_FINANCIAL_MARKET_PROVIDER", "").strip().lower()
    backend = os.getenv("ULTRON_EXECUTION_BACKEND", "").strip().lower()
    require(provider == "alpaca", f"Expected provider=alpaca, got {provider!r}")
    require(backend == "simulation", f"Expected backend=simulation, got {backend!r}")

    from ultron.financial.decision.engine import FinancialDecisionEngine
    from ultron.financial.reasoning.memory_reasoner import MemoryAwareFinancialReasoning
    from ultron.financial.trading.controller import FinancialTradingController
    from ultron.financial.trading.engine import PaperTradingEngine
    from ultron.financial.trading.market.adapters.paper import PaperMarketAdapter
    from ultron.financial.trading.market_data.service import MarketDataService
    from ultron.financial.trading.models import Order, OrderSide
    from ultron.financial.trading.paper_broker import PaperBroker
    from ultron.financial.trading.autonomous_runtime import AutonomousTradingRuntime

    # A. Decision engine threshold matrix.
    engine = FinancialDecisionEngine()
    decision_cases = [
        ("confidence .75, no risks", {"confidence": .75, "risks": []}, "consider"),
        ("high confidence, one risk", {"confidence": .95, "risks": ["valuation"]}, "watch"),
        ("confidence .50", {"confidence": .50, "risks": []}, "watch"),
        ("confidence below .50", {"confidence": .499, "risks": []}, "avoid"),
    ]
    for label, reasoning, expected in decision_cases:
        def run_case(reasoning=reasoning, expected=expected, label=label):
            result = engine.decide(reasoning, "bull", ["trend_following"])
            require(result.get("decision") == expected,
                    f"{label}: expected {expected}, got {result!r}")
        check(f"decision threshold: {label}", run_case)

    # B. Reasoner risk propagation and deduplication.
    def check_reasoner():
        class Memory:
            def recall_company(self, company):
                return []
        reasoner = MemoryAwareFinancialReasoning(Memory())
        result = reasoner.analyze(
            company="TEST",
            current_analysis={"analysis": {
                "score": 90, "reasons": ["strong margin"],
                "risks": ["valuation"],
            }},
            market={"trend": "bullish"},
            regime={"regime": "bull", "confidence": .75},
            risk={"risk": {"risk_level": "low", "warnings": ["valuation", "debt"]}},
            strategies=["trend_following"],
        )
        require(result["risks"].count("valuation") == 1, f"Risk not deduplicated: {result['risks']!r}")
        require("debt" in result["risks"], f"Risk warning lost: {result['risks']!r}")
        require(0 <= result["confidence"] <= .95, "Confidence outside clamp")
    check("reasoner risk propagation, deduplication, confidence clamp", check_reasoner)

    # C. Controller/market integration with deterministic adapter.
    broker = PaperBroker(initial_capital=100_000)
    adapter = PaperMarketAdapter(broker, prices={"TEST": 100.0})
    adapter.connect()
    market_data = MarketDataService(adapter)
    controller = FinancialTradingController(PaperTradingEngine(broker), market_data=market_data)

    def valid_market_price():
        result = market_data.get_current_price("TEST")
        require(result.get("valid") is True and result.get("price") == 100.0,
                f"Unexpected validated price: {result!r}")
    check("validated deterministic market price", valid_market_price)

    def guarded_buy():
        result = controller.execute_from_market(make_analysis())
        require(result.get("success") is True, f"BUY failed: {result!r}")
        require(result.get("execution", {}).get("status") == "filled",
                f"BUY not filled: {result!r}")
        require(broker.snapshot({"TEST": 100.0})["trade_count"] == 1,
                "Expected exactly one trade after BUY")
    check("controller guarded BUY through PaperTradingEngine", guarded_buy)

    def stale_price_rejected():
        result = controller.execute(
            analysis=make_analysis(), price=100.0,
            price_timestamp=datetime.now(UTC) - timedelta(seconds=60),
        )
        require(result.get("success") is False and result.get("stage") == "price_guard",
                f"Stale price not rejected: {result!r}")
        require(broker.snapshot({"TEST": 100.0})["trade_count"] == 1,
                "Stale rejection changed trade count")
    check("stale market timestamp rejected without fill", stale_price_rejected)

    def invalid_price_rejected():
        adapter.prices["TEST"] = -1.0
        try:
            market_data.get_current_price("TEST")
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid negative paper price was accepted")
        require(broker.snapshot({"TEST": 100.0})["trade_count"] == 1,
                "Invalid-price check changed trade count")
        adapter.prices["TEST"] = 100.0
    check("invalid market price rejected", invalid_price_rejected)

    # A direct insufficient-cash / insufficient-position test confirms broker rejection.
    def broker_rejections():
        poor = PaperBroker(initial_capital=10.0)
        rejected_buy = poor.submit(Order(symbol="TEST", side=OrderSide.BUY, quantity=1, price=100))
        require(rejected_buy.get("success") is False, f"Insufficient-cash BUY accepted: {rejected_buy!r}")
        rejected_sell = poor.submit(Order(symbol="TEST", side=OrderSide.SELL, quantity=1, price=100))
        require(rejected_sell.get("success") is False, f"Unbacked SELL accepted: {rejected_sell!r}")
        require(poor.snapshot({})["trade_count"] == 0, "Rejected orders changed trade count")
    check("broker rejects insufficient cash and unbacked SELL", broker_rejections)

    # D. Runtime WATCH gate. No persistent DB is opened; __new__ bypasses constructor.
    def watch_gate():
        class Skill:
            def __init__(self): self.calls = []
            def execute(self, command, **kwargs):
                self.calls.append((command, kwargs))
                if command == "analyze":
                    return {"success": True, "symbol": "TEST",
                            "decision": {"decision": "watch", "confidence": .81},
                            "risk": {"risk": {"risk_level": "low"}}}
                raise AssertionError(f"Unexpected command {command!r}")
        class Market:
            def get_current_price(self, symbol):
                raise AssertionError("WATCH should stop before market data")
        class Controller:
            market_data = Market()
        runtime = AutonomousTradingRuntime.__new__(AutonomousTradingRuntime)
        runtime.skill = Skill()
        runtime.controller = Controller()
        runtime.max_positions = 3
        runtime.max_price_divergence_pct = 1.0
        runtime._snapshot = lambda: {"equity": 100000, "cash": 100000, "positions": {}}
        runtime._positions = lambda: []
        rows = []
        runtime._record_cycle = lambda symbol, outcome, details: rows.append((symbol, outcome, details))
        runtime._evaluate_symbol("TEST")
        require(runtime.skill.calls == [("analyze", {"symbol": "TEST"})],
                f"WATCH did not stop before paper_trade: {runtime.skill.calls!r}")
        require(len(rows) == 1 and rows[0][1] == "watch",
                f"WATCH outcome not journaled: {rows!r}")
    check("runtime WATCH decision blocks market-data and paper-trade calls", watch_gate)

    # E. Journal/recovery lifecycle in a temporary DB with a pre-injected controller.
    def journal_recovery():
        with tempfile.TemporaryDirectory(prefix="ultron_acceptance_") as td:
            db_path = str(Path(td) / "acceptance.sqlite3")
            local_broker = PaperBroker(initial_capital=100_000)
            local_adapter = PaperMarketAdapter(local_broker, prices={"TEST": 100.0})
            local_adapter.connect()
            local_market = MarketDataService(local_adapter)
            local_controller = FinancialTradingController(
                PaperTradingEngine(local_broker), market_data=local_market
            )
            class Skill:
                def execute(self, *args, **kwargs):
                    raise AssertionError("Analysis is not part of this journal test")
            class Container:
                financial_skill = Skill()
                financial_trading_controller = local_controller
            runtime = AutonomousTradingRuntime(
                container=Container(), symbols=["TEST"], state_path=db_path
            )
            buy = local_controller.execute_from_market(make_analysis())
            require(buy.get("success") is True, f"Journal BUY failed: {buy!r}")
            runtime._record_execution("TEST", buy, "acceptance test")
            with sqlite3.connect(db_path) as db:
                row = db.execute(
                    "SELECT stop_loss, take_profit FROM position_rules WHERE symbol='TEST'"
                ).fetchone()
            require(row is not None, "BUY stop/target rules missing")
            local_adapter.prices["TEST"] = float(row[1]) + 1
            runtime._monitor_exits()
            require("TEST" not in local_broker.portfolio.positions, "Take-profit exit did not close position")
            with sqlite3.connect(db_path) as db:
                actions = [r[0].lower() for r in db.execute(
                    "SELECT action FROM executions ORDER BY id"
                ).fetchall()]
            require(actions == ["buy", "sell"], f"Unexpected journal actions: {actions!r}")

            recovered_broker = PaperBroker(initial_capital=100_000)
            recovered_adapter = PaperMarketAdapter(
                recovered_broker, prices={"TEST": local_adapter.prices["TEST"]}
            )
            recovered_adapter.connect()
            recovered_controller = FinancialTradingController(
                PaperTradingEngine(recovered_broker),
                market_data=MarketDataService(recovered_adapter),
            )
            class RecoveredContainer:
                financial_skill = Skill()
                financial_trading_controller = recovered_controller
            recovered_runtime = AutonomousTradingRuntime(
                container=RecoveredContainer(), symbols=["TEST"], state_path=db_path
            )
            snap = recovered_broker.snapshot({"TEST": recovered_adapter.prices["TEST"]})
            require(snap["trade_count"] == 2, f"Expected 2 replayed fills, got {snap['trade_count']}")
            require("TEST" not in recovered_broker.portfolio.positions,
                    "Closed position reappeared after recovery")
            with sqlite3.connect(db_path) as db:
                replayed = db.execute("SELECT COUNT(*) FROM executions WHERE replayed=1").fetchone()[0]
            require(replayed == 2, f"Expected two replay flags, got {replayed}")
    check("temporary journal BUY/SELL, take-profit exit, exact recovery and replay flags", journal_recovery)

    # F. Position cap check: exercise existing runtime guard before analysis.
    def position_limit_gate():
        class Skill:
            def execute(self, *args, **kwargs):
                raise AssertionError("Position limit should return before analysis")
        class Controller: pass
        runtime = AutonomousTradingRuntime.__new__(AutonomousTradingRuntime)
        runtime.skill = Skill()
        runtime.controller = Controller()
        runtime.max_positions = 1
        runtime._snapshot = lambda: {"equity": 100000, "cash": 50000, "positions": {"OTHER": {}}}
        runtime._positions = lambda: [("OTHER", {"quantity": 1})]
        rows = []
        runtime._record_cycle = lambda symbol, outcome, details: rows.append((symbol, outcome, details))
        runtime._evaluate_symbol("TEST")
        require(len(rows) == 1 and rows[0][1] == "position_limit",
                f"Position limit did not block evaluation: {rows!r}")
    check("runtime position cap blocks new analysis when full", position_limit_gate)

    print()
    print("COMBINED ACCEPTANCE SUMMARY")
    print(f"Passed: {PASS_COUNT}")
    print(f"Failed: {len(FAILURES)}")
    print("Skipped: 0 (all included checks were attempted)")
    print("Untested here: real-time Alpaca network behavior, market-hours transitions, profitability, slippage, concurrent execution, daily-loss trip scenarios, and live brokerage reconciliation.")
    for name, detail in FAILURES:
        print(f"FAILURE DETAIL: {name}: {detail}")
    if FAILURES:
        print("RESULT: combined acceptance suite FAILED")
        raise SystemExit(1)
    print("RESULT: combined acceptance suite passed")

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        print("SAFETY: no live broker orders; journal tests use temporary SQLite only")
        raise SystemExit(2)
