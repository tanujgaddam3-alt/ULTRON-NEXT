"""Simple, transparent long/flat holdout evaluation with explicit costs."""
from __future__ import annotations

import math
import pandas as pd

_INDIAN_INDEX_SYMBOLS = {
    "NIFTY", "NIFTY50", "^NSEI", "SENSEX", "BSESENSEX", "^BSESN",
}


def _is_indian_market_ticker(ticker: object) -> bool:
    symbol = str(ticker).strip().upper().replace(" ", "")
    return symbol in _INDIAN_INDEX_SYMBOLS or symbol.endswith((".NS", ".BO"))


def evaluate_signals(
    frame: pd.DataFrame,
    *,
    threshold: float = 0.55,
    cost_bps_per_side: float | None = None,
) -> dict:
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0 and 1")
    required = {"date", "ticker", "probability_up", "next_open_to_close_return"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Signal frame missing columns: {sorted(missing)}")
    indian_market_present = any(
        _is_indian_market_ticker(ticker) for ticker in frame["ticker"]
    )
    if cost_bps_per_side is None:
        if indian_market_present:
            raise ValueError(
                "Indian-market evaluation requires an explicit user-supplied "
                "per-side cost assumption; no market-specific cost is configured."
            )
        cost_bps_per_side = 10.0
    if not math.isfinite(cost_bps_per_side) or cost_bps_per_side < 0:
        raise ValueError("cost_bps_per_side must be finite and non-negative")
    data = frame.copy().sort_values(["date", "ticker"], kind="stable")
    numeric = data[["probability_up", "next_open_to_close_return"]].to_numpy(
        dtype=float
    )
    if not all(math.isfinite(value) for value in numeric.flat):
        raise ValueError("Signal probabilities and returns must be finite")
    data["probability_up"] = numeric[:, 0]
    data["next_open_to_close_return"] = numeric[:, 1]
    if ((data["probability_up"] < 0) | (data["probability_up"] > 1)).any():
        raise ValueError("Signal probabilities must be between 0 and 1")
    data["position"] = (data["probability_up"] >= threshold).astype(float)
    # Cost is charged for entry and exit, and only on days when a position is taken.
    round_trip_cost = 2.0 * cost_bps_per_side / 10_000.0
    data["strategy_return"] = data["position"] * (data["next_open_to_close_return"] - round_trip_cost)
    data["benchmark_return"] = data["next_open_to_close_return"]
    grouped = data.groupby("date", sort=True)
    daily = grouped[["strategy_return", "benchmark_return"]].mean()
    if daily.empty:
        raise ValueError("No holdout observations to evaluate")

    def summarize(series: pd.Series) -> dict:
        equity = (1.0 + series).cumprod()
        drawdown = equity / equity.cummax() - 1.0
        std = float(series.std(ddof=1)) if len(series) > 1 else 0.0
        sharpe = (float(series.mean()) / std * math.sqrt(252)) if std > 0 else 0.0
        return {
            "total_return_pct": round((float(equity.iloc[-1]) - 1.0) * 100, 4),
            "annualized_sharpe": round(sharpe, 4),
            "max_drawdown_pct": round(float(drawdown.min()) * 100, 4),
            "daily_observations": int(len(series)),
        }

    trades = int(data["position"].sum())
    return {
        "threshold": threshold,
        "cost_bps_per_side": cost_bps_per_side,
        "trade_observations": trades,
        "trade_rate_pct": round(100 * trades / len(data), 3),
        "cost_assumption": (
            "User-supplied per-side estimate; not verified market-specific and "
            "excludes statutory charges, spread, slippage, and liquidity."
            if indian_market_present
            else "Generic 10 bps per-side default unless explicitly overridden."
        ),
        "strategy": summarize(daily["strategy_return"]),
        "always_long_benchmark": summarize(daily["benchmark_return"]),
        "warning": "Research backtest only; excludes slippage, spread, taxes, liquidity limits, and operational failures beyond the configured cost assumption.",
    }
