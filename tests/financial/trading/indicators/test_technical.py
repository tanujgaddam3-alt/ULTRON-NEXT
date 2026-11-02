import pytest

from ultron.financial.trading.indicators.technical import (
    TechnicalIndicatorEngine,
    atr,
    bollinger_bands,
    ema,
    macd,
    rsi,
    sma,
)


def test_sma():
    assert sma([1, 2, 3, 4, 5], 3) == pytest.approx(4.0)


def test_ema():
    assert ema([1, 2, 3, 4, 5], 3) == pytest.approx(4.0)


def test_rsi():
    values = list(range(1, 20))
    assert rsi(values, 14) == pytest.approx(100.0)


def test_macd():
    values = [float(i) for i in range(1, 40)]

    result = macd(values)

    assert "macd" in result
    assert "signal" in result
    assert "histogram" in result


def test_atr():
    highs = [105, 106, 107, 108, 109, 110]
    lows = [95, 96, 97, 98, 99, 100]
    closes = [100, 101, 102, 103, 104, 105]

    result = atr(highs, lows, closes, 3)

    assert result > 0


def test_bollinger_bands():
    values = [float(i) for i in range(1, 21)]

    result = bollinger_bands(values, 20, 2.0)

    assert result["upper"] > result["middle"]
    assert result["middle"] > result["lower"]
    assert result["width"] > 0


def test_invalid_period():
    with pytest.raises(ValueError):
        sma([1, 2, 3], 0)


def test_insufficient_sma_data():
    with pytest.raises(ValueError):
        sma([1, 2], 3)


def test_insufficient_ema_data():
    with pytest.raises(ValueError):
        ema([1, 2], 3)


def test_insufficient_rsi_data():
    with pytest.raises(ValueError):
        rsi([1, 2, 3], 14)


def test_insufficient_macd_data():
    with pytest.raises(ValueError):
        macd([1, 2, 3])


def test_mismatched_atr_lengths():
    with pytest.raises(ValueError):
        atr(
            [105, 106, 107],
            [95, 96],
            [100, 101, 102],
            2,
        )


def test_insufficient_bollinger_data():
    with pytest.raises(ValueError):
        bollinger_bands([1, 2, 3], 20)


def test_engine_snapshot():
    values = [float(i) for i in range(1, 31)]

    engine = TechnicalIndicatorEngine()
    result = engine.snapshot(values)

    assert "sma_20" in result
    assert "ema_20" in result
    assert "rsi_14" in result
    assert "macd" in result
    assert "bollinger" in result
    assert "atr_14" in result
