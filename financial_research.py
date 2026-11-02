#!/usr/bin/env python3
"""Offline ML research CLI for ULTRON Financial Mode; never places orders."""
from __future__ import annotations
import argparse
import json
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Train/evaluate an offline financial signal model on historical OHLCV CSV data.")
    parser.add_argument("--data", required=True, help="CSV file or directory containing OHLCV CSV files")
    parser.add_argument("--output", default="data/financial/research", help="Output directory for model and report")
    parser.add_argument("--train-fraction", type=float, default=0.70)
    parser.add_argument("--validation-fraction", type=float, default=0.15)
    parser.add_argument("--threshold", type=float, default=0.55)
    parser.add_argument("--cost-bps-per-side", type=float, default=10.0)
    args = parser.parse_args(argv)
    try:
        from ultron.financial.research.train import train_and_evaluate
        result = train_and_evaluate(
            args.data, args.output,
            train_fraction=args.train_fraction,
            validation_fraction=args.validation_fraction,
            threshold=args.threshold,
            cost_bps_per_side=args.cost_bps_per_side,
        )
        print(json.dumps(result, indent=2))
        return 0
    except (ImportError, ModuleNotFoundError) as exc:
        print(json.dumps({"success": False, "stage": "dependencies", "error": str(exc), "hint": "Install requirements-financial-ml.txt in the project virtual environment."}, indent=2), file=sys.stderr)
        return 2
    except Exception as exc:
        print(json.dumps({"success": False, "stage": "research", "error": str(exc)}, indent=2), file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
