from __future__ import annotations

from typing import Any

import pandas as pd

from .engine import BacktestEngine
from .models import TradingStrategy


class WalkForwardValidator:
    """
    Validates an already-selected strategy on unseen historical data.

    The validation period is never used to evolve or select the strategy.
    """

    def __init__(self, backtester: BacktestEngine | None = None):
        self.backtester = backtester or BacktestEngine()

    def validate(
        self,
        history: pd.DataFrame,
        strategy: TradingStrategy,
        train_ratio: float = 0.8,
    ) -> dict[str, Any]:
        if not isinstance(history, pd.DataFrame):
            raise TypeError("history must be a pandas DataFrame.")

        if not 0.5 <= train_ratio < 1.0:
            raise ValueError(
                "train_ratio must be between 0.5 and 1.0."
            )

        if history.empty:
            raise ValueError("history cannot be empty.")

        split_index = int(len(history) * train_ratio)

        if split_index <= strategy.long_window:
            raise ValueError(
                "Training window is too short for the strategy."
            )

        if split_index >= len(history):
            raise ValueError(
                "Validation window must contain historical data."
            )

        train = history.iloc[:split_index].copy()
        validation = history.iloc[split_index:].copy()

        # The strategy has already been selected using training data.
        # Validation is deliberately isolated from strategy selection.
        validation_result = self.backtester.run(
            validation,
            strategy,
        )

        benchmark_return = float(
            validation_result.get(
                "benchmark_return_percent",
                0.0,
            )
        )

        strategy_return = float(
            validation_result.get(
                "return_percent",
                0.0,
            )
        )

        warnings = list(
            validation_result.get(
                "warnings",
                [],
            )
        )

        outperformed_benchmark = (
            strategy_return > benchmark_return
        )

        robust = (
            strategy_return > 0
            and validation_result.get("trade_count", 0) > 0
            and outperformed_benchmark
            and not any(
                "High historical drawdown." in warning
                for warning in warnings
            )
        )

        if not outperformed_benchmark:
            warnings.append(
                "Strategy underperformed the validation benchmark."
            )

        if strategy_return <= 0:
            warnings.append(
                "Strategy produced a non-positive validation return."
            )

        return {
            "success": True,
            "strategy": strategy.name,
            "train_periods": len(train),
            "validation_periods": len(validation),
            "train_ratio": train_ratio,
            "validation": validation_result,
            "benchmark_return_percent": benchmark_return,
            "validation_return_percent": strategy_return,
            "outperformed_benchmark": (
                strategy_return > benchmark_return
            ),
            "robust": robust,
            "warnings": warnings,
        }
