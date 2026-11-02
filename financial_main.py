#!/usr/bin/env python3
"""Standalone command-line entry point for ULTRON Financial Mode."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run ULTRON's financial subsystem without starting the full "
            "cognitive runtime, dashboard, voice, vision, or autonomy stack."
        )
    )
    parser.add_argument("--symbol", help="Ticker to analyze, e.g. AAPL or RELIANCE.NS")
    parser.add_argument(
        "--backtest",
        action="store_true",
        help="Enable the existing backtesting skill (slower and data-intensive).",
    )
    parser.add_argument(
        "--memory-db",
        default=None,
        help="SQLite path for financial experience memory; use :memory: for ephemeral storage.",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=100_000.0,
        help="Initial paper-trading capital (default: 100000).",
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Initialize financial services and report their status without market requests.",
    )
    args = parser.parse_args(argv)

    if not args.self_check and not args.symbol:
        parser.error("provide --symbol TICKER or --self-check")
    if args.capital <= 0:
        parser.error("--capital must be positive")

    try:
        from ultron.financial.bootstrap import build_financial_runtime

        runtime = build_financial_runtime(
            memory_db=args.memory_db,
            initial_capital=args.capital,
            enable_backtesting=args.backtest,
        )
    except (ImportError, ModuleNotFoundError) as exc:
        print(
            json.dumps(
                {
                    "success": False,
                    "stage": "startup",
                    "error": f"Missing financial dependency: {exc}",
                    "hint": "Activate the project .venv and install requirements.txt.",
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2
    except Exception as exc:
        print(
            json.dumps(
                {"success": False, "stage": "startup", "error": str(exc)},
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2

    try:
        if args.self_check:
            result = {
                "success": True,
                "mode": "financial-only",
                "llm_loaded_by_bootstrap": False,
                "paper_broker": type(runtime.paper_broker).__name__,
                "initial_capital": runtime.paper_broker.snapshot({}).get("equity"),
                "market_data": type(runtime.market_data).__name__,
                "analysis": type(runtime.analysis).__name__,
                "risk": type(runtime.risk).__name__,
                "backtesting_enabled": runtime.backtesting is not None,
            }
        else:
            result = runtime.agent.analyze(
                symbol=args.symbol,
                run_backtest=args.backtest,
            )
        print(json.dumps(result, indent=2, default=_json_default))
        return 0 if result.get("success") else 1
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
