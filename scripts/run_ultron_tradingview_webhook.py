#!/usr/bin/env python3
"""Run ULTRON's localhost-only TradingView paper webhook."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ultron.financial.trading.autonomous_runtime import AutonomousTradingRuntime
from ultron.financial.trading.tradingview_webhook import (
    TradingViewWebhookProcessor,
    TradingViewWebhookServer,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the loopback-only ULTRON TradingView paper webhook."
    )
    parser.add_argument("--state-db", help="Durable SQLite paper-runtime state path.")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    if os.getenv("ULTRON_EXECUTION_BACKEND", "simulation").strip().lower() != "simulation":
        parser.error("ULTRON_EXECUTION_BACKEND must be simulation.")
    logging.basicConfig(
        level=os.getenv("ULTRON_AUTONOMOUS_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    runtime = AutonomousTradingRuntime(state_path=args.state_db)
    processor = TradingViewWebhookProcessor.from_environment()
    server = TradingViewWebhookServer(processor, runtime, port=args.port)
    host, port = server.address
    print(f"ULTRON TradingView paper webhook listening on http://{host}:{port}{'/webhook/tradingview'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())