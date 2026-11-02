#!/usr/bin/env python3
"""Decision-pipeline acceptance checks for the current ULTRON source tree.

No network calls, credentials, real broker, or production database are used.
The tests preserve current strategy thresholds and assert observed source behavior.
"""
from __future__ import annotations

import os
import traceback

def require(condition, message):
    if not condition:
        raise AssertionError(message)

def main():
    provider = os.getenv("ULTRON_FINANCIAL_MARKET_PROVIDER", "").strip().lower()
    backend = os.getenv("ULTRON_EXECUTION_BACKEND", "").strip().lower()
    require(provider == "alpaca", f"Expected provider=alpaca, got {provider!r}")
    require(backend == "simulation", f"Expected backend=simulation, got {backend!r}")

    from ultron.financial.decision.engine import FinancialDecisionEngine
    from ultron.financial.reasoning.memory_reasoner import MemoryAwareFinancialReasoning
    from ultron.financial.trading.autonomous_runtime import AutonomousTradingRuntime

    engine = FinancialDecisionEngine()

    # Decision engine boundaries as implemented today:
    # consider requires confidence >= .75 AND zero risks; watch if confidence >= .5;
    # otherwise avoid.
    result = engine.decide(
        {"confidence": 0.75, "risks": []}, "bull", ["trend_following"]
    )
    require(result["decision"] == "consider", f".75/no-risk boundary: {result!r}")
    print("PASS: confidence=0.75 with zero risks -> consider")

    result = engine.decide(
        {"confidence": 0.95, "risks": ["Elevated valuation"]},
        "bull", ["trend_following"]
    )
    require(result["decision"] == "watch",
            f"High confidence with any risk should currently be watch: {result!r}")
    print("PASS: confidence=0.95 with one risk -> watch (current policy)")

    result = engine.decide(
        {"confidence": 0.50, "risks": []}, "neutral", []
    )
    require(result["decision"] == "watch", f".50 boundary: {result!r}")
    print("PASS: confidence=0.50 -> watch")

    result = engine.decide(
        {"confidence": 0.499, "risks": []}, "neutral", []
    )
    require(result["decision"] == "avoid", f"below .50 boundary: {result!r}")
    print("PASS: confidence below 0.50 -> avoid")

    # Check risk propagation and deduplication in the reasoning layer.
    class Memory:
        def recall_company(self, company):
            return []

    reasoner = MemoryAwareFinancialReasoning(Memory())
    assessment = reasoner.analyze(
        company="TEST",
        current_analysis={
            "analysis": {
                "score": 90,
                "reasons": ["Strong margins"],
                "risks": ["Elevated valuation"],
            }
        },
        market={"trend": "bullish"},
        regime={"regime": "bull", "confidence": 0.75},
        risk={"risk": {
            "risk_level": "low",
            "warnings": ["Elevated valuation", "Debt exposure"],
        }},
        strategies=["trend_following"],
    )
    require("Elevated valuation" in assessment["risks"],
            f"Fundamental risk was lost: {assessment['risks']!r}")
    require(assessment["risks"].count("Elevated valuation") == 1,
            f"Duplicate risk was not removed: {assessment['risks']!r}")
    require("Debt exposure" in assessment["risks"],
            f"Risk warning was not propagated: {assessment['risks']!r}")
    require(assessment["confidence"] >= 0 and assessment["confidence"] <= 0.95,
            f"Reasoning confidence outside expected clamp: {assessment['confidence']!r}")
    print("PASS: reasoner preserves risk warnings and deduplicates repeated risks")
    print(f"INFO: synthetic reasoning confidence={assessment['confidence']:.3f}; "
          f"risks={assessment['risks']!r}")

    # Test runtime gating in isolation. __new__ avoids creating/opening any DB.
    # The fake analysis deliberately returns watch; runtime must record watch and
    # return before market-data access or paper_trade execution.
    class Skill:
        def __init__(self):
            self.calls = []
        def execute(self, command, **kwargs):
            self.calls.append((command, kwargs))
            if command == "analyze":
                return {
                    "success": True,
                    "symbol": "TEST",
                    "decision": {"decision": "watch", "confidence": 0.81},
                    "risk": {"risk": {"risk_level": "low"}},
                }
            raise AssertionError(f"Unexpected skill command reached: {command!r}")

    class MarketData:
        def get_current_price(self, symbol):
            raise AssertionError("watch decision must return before market data access")

    class Controller:
        market_data = MarketData()

    runtime = AutonomousTradingRuntime.__new__(AutonomousTradingRuntime)
    runtime.skill = Skill()
    runtime.controller = Controller()
    runtime.max_positions = 3
    runtime.max_price_divergence_pct = 1.0
    runtime._snapshot = lambda: {"equity": 100_000, "cash": 100_000, "positions": {}}
    runtime._positions = lambda: []
    recorded = []
    runtime._record_cycle = lambda symbol, outcome, details: recorded.append(
        (symbol, outcome, details)
    )
    runtime._evaluate_symbol("TEST")

    require(runtime.skill.calls == [("analyze", {"symbol": "TEST"})],
            f"Runtime should analyze only, not paper_trade: {runtime.skill.calls!r}")
    require(len(recorded) == 1 and recorded[0][1] == "watch",
            f"Runtime did not record watch outcome: {recorded!r}")
    require(recorded[0][2].get("decision") == "watch",
            f"Runtime journal details missing decision: {recorded!r}")
    print("PASS: runtime WATCH gate records outcome and prevents market-data/trade calls")

    print()
    print("DECISION ACCEPTANCE SUMMARY")
    print("PASS: 6 assertions across decision thresholds, risk propagation, runtime gating")
    print("LIMITATION: unit-level decision-path tests; not proof of profitability or live execution")
    print("RESULT: decision-pipeline acceptance checks passed")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        print("SAFETY: no network requests, broker orders, or persistent database writes intended")
        raise SystemExit(1)
