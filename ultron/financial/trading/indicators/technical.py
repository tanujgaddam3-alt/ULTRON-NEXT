from __future__ import annotations

from math import sqrt
from statistics import mean
from typing import Sequence


def _validate_period(period: int) -> None:
    if period <= 0:
        raise ValueError("Period must be positive.")


def _validate_values(
    values: Sequence[float],
) -> list[float]:

    if not values:
        raise ValueError("Values cannot be empty.")

    result = [float(value) for value in values]

    if any(value < 0 for value in result):
     raise ValueError(
        "Indicator values cannot be negative."
    )

    return result


def sma(
    values: Sequence[float],
    period: int,
) -> float:

    _validate_period(period)
    values = _validate_values(values)

    if len(values) < period:
        raise ValueError(
            "Not enough values for SMA."
        )

    return mean(values[-period:])


def ema(
    values: Sequence[float],
    period: int,
) -> float:

    _validate_period(period)
    values = _validate_values(values)

    if len(values) < period:
        raise ValueError(
            "Not enough values for EMA."
        )

    multiplier = 2.0 / (period + 1)

    result = mean(values[:period])

    for value in values[period:]:
        result = (
            (value - result) * multiplier
            + result
        )

    return result


def rsi(
    values: Sequence[float],
    period: int = 14,
) -> float:

    _validate_period(period)
    values = _validate_values(values)

    if len(values) <= period:
        raise ValueError(
            "Not enough values for RSI."
        )

    gains: list[float] = []
    losses: list[float] = []

    for previous, current in zip(
        values[:-1],
        values[1:],
    ):
        change = current - previous

        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    average_gain = mean(gains[:period])
    average_loss = mean(losses[:period])

    for index in range(period, len(gains)):
        average_gain = (
            (
                average_gain * (period - 1)
            ) + gains[index]
        ) / period

        average_loss = (
            (
                average_loss * (period - 1)
            ) + losses[index]
        ) / period

    if average_loss == 0:
        return 100.0

    relative_strength = (
        average_gain / average_loss
    )

    return 100.0 - (
        100.0 / (1.0 + relative_strength)
    )


def macd(
    values: Sequence[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> dict[str, float]:

    _validate_period(fast_period)
    _validate_period(slow_period)
    _validate_period(signal_period)

    values = _validate_values(values)

    if fast_period >= slow_period:
        raise ValueError(
            "Fast period must be smaller than slow period."
        )

    if len(values) < slow_period:
        raise ValueError(
            "Not enough values for MACD."
        )

    multiplier_fast = (
        2.0 / (fast_period + 1)
    )

    multiplier_slow = (
        2.0 / (slow_period + 1)
    )

    fast = mean(values[:fast_period])
    slow = mean(values[:slow_period])

    macd_values: list[float] = []

    for index in range(
        fast_period,
        len(values),
    ):
        fast = (
            (values[index] - fast)
            * multiplier_fast
            + fast
        )

    for index in range(
        slow_period,
        len(values),
    ):
        slow = (
            (values[index] - slow)
            * multiplier_slow
            + slow
        )

        fast_at_point = ema(
            values[
                : index + 1
            ],
            fast_period,
        )

        macd_values.append(
            fast_at_point - slow
        )

    if not macd_values:
        raise ValueError(
            "Not enough values for MACD signal."
        )

    if len(macd_values) < signal_period:
        signal = mean(macd_values)
    else:
        signal = ema(
            macd_values,
            signal_period,
        )

    line = macd_values[-1]

    return {
        "macd": line,
        "signal": signal,
        "histogram": line - signal,
    }


def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> float:

    _validate_period(period)

    if not (
        len(highs)
        == len(lows)
        == len(closes)
    ):
        raise ValueError(
            "High, low and close lengths must match."
        )

    if len(closes) <= period:
        raise ValueError(
            "Not enough values for ATR."
        )

    highs = [float(value) for value in highs]
    lows = [float(value) for value in lows]
    closes = [float(value) for value in closes]

    true_ranges: list[float] = []

    for index in range(1, len(closes)):
        true_range = max(
            highs[index] - lows[index],
            abs(
                highs[index]
                - closes[index - 1]
            ),
            abs(
                lows[index]
                - closes[index - 1]
            ),
        )

        true_ranges.append(true_range)

    result = mean(true_ranges[:period])

    for value in true_ranges[period:]:
        result = (
            (
                result * (period - 1)
            ) + value
        ) / period

    return result


def bollinger_bands(
    values: Sequence[float],
    period: int = 20,
    deviations: float = 2.0,
) -> dict[str, float]:

    _validate_period(period)

    if deviations <= 0:
        raise ValueError(
            "Deviations must be positive."
        )

    values = _validate_values(values)

    if len(values) < period:
        raise ValueError(
            "Not enough values for Bollinger Bands."
        )

    window = values[-period:]
    middle = mean(window)

    variance = mean(
        (value - middle) ** 2
        for value in window
    )

    standard_deviation = sqrt(variance)

    upper = (
        middle
        + deviations * standard_deviation
    )

    lower = (
        middle
        - deviations * standard_deviation
    )

    return {
        "upper": upper,
        "middle": middle,
        "lower": lower,
        "width": upper - lower,
    }


class TechnicalIndicatorEngine:
    """
    Calculates the core technical indicators used
    by ULTRON's market-intelligence layer.
    """

    def snapshot(
        self,
        closes: Sequence[float],
        highs: Sequence[float] | None = None,
        lows: Sequence[float] | None = None,
    ) -> dict[str, object]:

        closes = _validate_values(closes)

        if highs is None:
            highs = closes

        if lows is None:
            lows = closes

        if len(highs) != len(closes):
            raise ValueError(
                "Highs and closes must have equal length."
            )

        if len(lows) != len(closes):
            raise ValueError(
                "Lows and closes must have equal length."
            )

        result: dict[str, object] = {
            "sma_20": sma(closes, 20),
            "ema_20": ema(closes, 20),
            "rsi_14": rsi(closes, 14),
            "macd": macd(closes),
            "bollinger": bollinger_bands(
                closes,
                20,
                2.0,
            ),
        }

        result["atr_14"] = atr(
            highs,
            lows,
            closes,
            14,
        )

        return result
