from __future__ import annotations

from typing import Any

import pandas as pd


class MarketRegimeAnalyzer:
    """
    Detects the current market regime from historical OHLCV data.

    Accepted input:
        - pandas.DataFrame
        - dictionary produced by DataFrame.to_dict()

    The analyzer normalizes the input before calculating indicators so
    upstream transport/serialization details do not leak into domain logic.
    """

    SHORT_WINDOW = 50
    LONG_WINDOW = 200
    VOLATILITY_WINDOW = 30
    HIGH_VOLATILITY_THRESHOLD = 0.03

    def analyze(self, history: Any) -> dict[str, Any]:
        data = self._normalize_history(history)

        if data.empty:
            raise ValueError("Historical market data is empty.")

        if "Close" not in data.columns:
            raise ValueError(
                "Historical market data must contain a 'Close' column."
            )

        close = pd.to_numeric(
            data["Close"],
            errors="coerce",
        ).dropna()

        if len(close) < self.LONG_WINDOW:
            raise ValueError(
                f"At least {self.LONG_WINDOW} valid closing prices are "
                f"required for regime analysis; received {len(close)}."
            )

        short_ma = (
            close
            .rolling(self.SHORT_WINDOW)
            .mean()
            .iloc[-1]
        )

        long_ma = (
            close
            .rolling(self.LONG_WINDOW)
            .mean()
            .iloc[-1]
        )

        volatility = (
            close
            .pct_change()
            .rolling(self.VOLATILITY_WINDOW)
            .std()
            .iloc[-1]
        )

        if pd.isna(short_ma) or pd.isna(long_ma) or pd.isna(volatility):
            raise ValueError(
                "Insufficient valid market data for regime indicators."
            )

        if short_ma > long_ma:
            regime = "bull"
        else:
            regime = "bear"

        if volatility > self.HIGH_VOLATILITY_THRESHOLD:
            regime = "high_volatility"

        confidence = {
            "bull": 0.75,
            "bear": 0.75,
            "high_volatility": 0.85,
        }.get(regime, 0.50)

        return {
            "regime": regime,
            "confidence": confidence,
            "indicators": {
                "short_ma": float(short_ma),
                "long_ma": float(long_ma),
                "volatility": float(volatility),
            },
        }

    @staticmethod
    def _normalize_history(history: Any) -> pd.DataFrame:
        if isinstance(history, pd.DataFrame):
            data = history.copy()

        elif isinstance(history, dict):
            try:
                data = pd.DataFrame(history)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Unable to reconstruct historical market data: {exc}"
                ) from exc

        else:
            raise TypeError(
                "Historical market data must be a pandas DataFrame "
                "or dictionary."
            )

        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        return data