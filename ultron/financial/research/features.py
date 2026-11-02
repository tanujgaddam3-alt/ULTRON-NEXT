"""Past-only feature engineering and next-session labels."""
from __future__ import annotations

import numpy as np
import pandas as pd

FEATURE_COLUMNS = [
    "return_1", "return_2", "return_5", "return_10", "return_20",
    "sma_ratio_5", "sma_ratio_10", "sma_ratio_20", "sma_ratio_50",
    "volatility_5", "volatility_10", "volatility_20",
    "range_pct", "gap_pct", "volume_ratio_5", "volume_ratio_20",
    "rsi_14", "close_location",
]


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    change = close.diff()
    gain = change.clip(lower=0).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    loss = (-change.clip(upper=0)).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def build_features(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """Build features known after session t close; label is t+1 open-to-close.

    The target intentionally models a trade entered at the next session's open
    and exited at that session's close. It avoids pretending that a signal
    generated after close t could fill at close t.
    """
    required = {"ticker", "date", "open", "high", "low", "close", "volume"}
    missing = required.difference(ohlcv.columns)
    if missing:
        raise ValueError(f"OHLCV input missing columns: {sorted(missing)}")
    parts: list[pd.DataFrame] = []
    for ticker, raw in ohlcv.groupby("ticker", sort=False):
        frame = raw.sort_values("date").copy()
        close = frame["close"]
        volume = frame["volume"]
        daily_return = close.pct_change()
        for period in (1, 2, 5, 10, 20):
            frame[f"return_{period}"] = close.pct_change(period)
        for period in (5, 10, 20, 50):
            frame[f"sma_ratio_{period}"] = close / close.rolling(period, min_periods=period).mean() - 1
        for period in (5, 10, 20):
            frame[f"volatility_{period}"] = daily_return.rolling(period, min_periods=period).std() * np.sqrt(252)
        frame["range_pct"] = (frame["high"] - frame["low"]) / close
        frame["gap_pct"] = frame["open"] / close.shift(1) - 1
        for period in (5, 20):
            frame[f"volume_ratio_{period}"] = volume / volume.rolling(period, min_periods=period).mean()
        frame["rsi_14"] = _rsi(close)
        denominator = (frame["high"] - frame["low"]).replace(0, np.nan)
        frame["close_location"] = (close - frame["low"]) / denominator
        frame["next_session_date"] = frame["date"].shift(-1)
        frame["next_open_to_close_return"] = frame["close"].shift(-1) / frame["open"].shift(-1) - 1
        frame["target_up"] = (frame["next_open_to_close_return"] > 0).astype("int8")
        frame["ticker"] = str(ticker)
        parts.append(frame)
    data = pd.concat(parts, ignore_index=True)
    data = data.replace([np.inf, -np.inf], np.nan)
    data = data.dropna(subset=FEATURE_COLUMNS + ["next_open_to_close_return", "next_session_date"])
    data["target_up"] = (data["next_open_to_close_return"] > 0).astype("int8")
    return data.sort_values(["date", "ticker"], kind="stable").reset_index(drop=True)
