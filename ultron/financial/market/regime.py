import pandas as pd
import pytest

from ultron.financial.market.analyzer import MarketRegimeAnalyzer


def make_history(rows=250):
    dates = pd.date_range(
        "2020-01-01",
        periods=rows,
        freq="D",
    )

    prices = pd.Series(
        range(100, 100 + rows),
        index=dates,
        dtype=float,
    )

    return pd.DataFrame(
        {
            "Close": prices,
        },
        index=dates,
    )


def test_bull_regime():
    analyzer = MarketRegimeAnalyzer()

    result = analyzer.analyze(
        make_history()
    )

    assert result["regime"] == "bull"
    assert result["confidence"] == 0.75
    assert result["indicators"]["short_ma"] > 0
    assert result["indicators"]["long_ma"] > 0


def test_dictionary_history_is_supported():
    analyzer = MarketRegimeAnalyzer()

    history = make_history()

    result = analyzer.analyze(
        history.to_dict()
    )

    assert result["regime"] == "bull"


def test_insufficient_history_returns_unknown():
    analyzer = MarketRegimeAnalyzer()

    result = analyzer.analyze(
        make_history(50)
    )

    assert result["regime"] == "unknown"
    assert result["confidence"] == 0.0
    assert "reason" in result


def test_empty_history_is_rejected():
    analyzer = MarketRegimeAnalyzer()

    with pytest.raises(ValueError):
        analyzer.analyze(
            pd.DataFrame()
        )


def test_missing_close_is_rejected():
    analyzer = MarketRegimeAnalyzer()

    history = pd.DataFrame(
        {
            "Open": [1, 2, 3],
        }
    )

    with pytest.raises(ValueError):
        analyzer.analyze(history)


def test_invalid_history_type_is_rejected():
    analyzer = MarketRegimeAnalyzer()

    with pytest.raises(TypeError):
        analyzer.analyze("invalid")


def test_original_dataframe_is_not_mutated():
    analyzer = MarketRegimeAnalyzer()

    history = make_history()
    original_columns = history.columns.tolist()

    analyzer.analyze(history)

    assert history.columns.tolist() == original_columns