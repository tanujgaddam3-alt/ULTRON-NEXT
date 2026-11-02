from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ultron.financial.research.dataset import load_ohlcv
from ultron.financial.research.features import FEATURE_COLUMNS, build_features
from ultron.financial.research.backtest import evaluate_signals
from ultron.financial.research.train import train_and_evaluate


def make_ohlcv(days=260, tickers=("AAA", "BBB")):
    dates = pd.bdate_range("2020-01-01", periods=days)
    frames = []
    for ticker_i, ticker in enumerate(tickers):
        rng = np.random.default_rng(1234 + ticker_i)
        returns = rng.normal(0.0005, 0.012, days)
        close = (80 + ticker_i * 20) * np.cumprod(1 + returns)
        open_ = close * (1 + rng.normal(0, 0.003, days))
        high = np.maximum(open_, close) * (1 + rng.uniform(0.001, 0.012, days))
        low = np.minimum(open_, close) * (1 - rng.uniform(0.001, 0.012, days))
        frames.append(pd.DataFrame({
            "Ticker": ticker, "Date": dates, "Open": open_, "High": high,
            "Low": low, "Close": close, "Volume": rng.integers(100_000, 1_000_000, days),
        }))
    return pd.concat(frames, ignore_index=True)


def test_load_and_features_are_ordered_and_have_no_nan_features(tmp_path):
    frame = make_ohlcv()
    path = tmp_path / "combined.csv"
    frame.to_csv(path, index=False)
    loaded = load_ohlcv(path)
    features = build_features(loaded)
    assert set(FEATURE_COLUMNS).issubset(features.columns)
    assert not features[FEATURE_COLUMNS].isna().any().any()
    assert features["next_session_date"].gt(features["date"]).all()


def test_loader_rejects_duplicate_ticker_dates(tmp_path):
    frame = make_ohlcv(days=80, tickers=("AAA",))
    frame = pd.concat([frame, frame.iloc[[10]]], ignore_index=True)
    path = tmp_path / "duplicates.csv"
    frame.to_csv(path, index=False)
    with pytest.raises(ValueError, match="Duplicate ticker/date"):
        load_ohlcv(path)



def test_loader_prefers_raw_close_when_adj_close_is_present(tmp_path):
    frame = make_ohlcv(days=80, tickers=("AAA",))
    frame["Adj Close"] = frame["Close"] * 0.5
    path = tmp_path / "adjusted.csv"
    frame.to_csv(path, index=False)
    loaded = load_ohlcv(path)
    assert np.allclose(loaded["close"].to_numpy(), frame["Close"].to_numpy())


def test_backtest_charges_cost_and_validates_threshold():
    signals = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-01", "2024-01-01", "2024-01-02"]),
        "ticker": ["AAA", "BBB", "AAA"],
        "probability_up": [0.8, 0.2, 0.9],
        "next_open_to_close_return": [0.01, -0.01, 0.02],
    })
    result = evaluate_signals(signals, threshold=0.55, cost_bps_per_side=10)
    assert result["trade_observations"] == 2
    gross = evaluate_signals(signals, threshold=0.55, cost_bps_per_side=0)
    assert result["strategy"]["total_return_pct"] < gross["strategy"]["total_return_pct"]
    with pytest.raises(ValueError):
        evaluate_signals(signals, threshold=1.1)
    with pytest.raises(ValueError, match="finite"):
        evaluate_signals(signals, cost_bps_per_side=float("nan"))
    invalid_probability = signals.copy()
    invalid_probability.loc[0, "probability_up"] = float("inf")
    with pytest.raises(ValueError, match="finite"):
        evaluate_signals(invalid_probability)


def test_train_uses_chronological_partitions_and_writes_artifacts(tmp_path):
    frame = make_ohlcv(days=300)
    csv = tmp_path / "market.csv"
    frame.to_csv(csv, index=False)
    output = tmp_path / "model"
    report = train_and_evaluate(csv, output)
    assert report["success"] is True
    assert report["date_ranges"]["train"][1] < report["date_ranges"]["validation"][0]
    assert report["date_ranges"]["validation"][1] < report["date_ranges"]["test"][0]
    walk_forward = report["walk_forward_validation"]
    assert walk_forward["method"] == "expanding_window"
    assert walk_forward["out_of_sample_observations"] > 0
    assert 0 <= walk_forward["classification"]["brier_score"] <= 1
    assert len(walk_forward["folds"]) == 3
    for fold in walk_forward["folds"]:
        assert fold["training_label_end"] < fold["out_of_sample_start"]
        assert fold["training_end"] < fold["out_of_sample_start"]
    assert (output / "market_signal_model.joblib").exists()
    assert (output / "training_report.json").exists()


def test_training_is_reproducible_and_split_labels_are_purged(tmp_path):
    frame = make_ohlcv(days=300)
    csv = tmp_path / "market.csv"
    frame.to_csv(csv, index=False)
    first = train_and_evaluate(csv, tmp_path / "first")
    second = train_and_evaluate(csv, tmp_path / "second")

    for key in (
        "rows",
        "date_ranges",
        "validation_classification",
        "walk_forward_validation",
        "test_classification",
        "validation_backtest",
        "test_backtest",
    ):
        assert first[key] == second[key]

    from joblib import load

    data = build_features(load_ohlcv(csv))
    dates = np.array(sorted(data["date"].unique()))
    train_cut = dates[int(len(dates) * 0.70)]
    validation_cut = dates[int(len(dates) * 0.85)]
    train = data[(data["date"] < train_cut) & (data["next_session_date"] < train_cut)]
    validation = data[
        (data["date"] >= train_cut)
        & (data["date"] < validation_cut)
        & (data["next_session_date"] < validation_cut)
    ]
    assert train["next_session_date"].max() < train_cut
    assert validation["next_session_date"].max() < validation_cut

    first_model = load(first["model_path"])["model"]
    second_model = load(second["model_path"])["model"]
    held_out = data.loc[data["date"] >= validation_cut, FEATURE_COLUMNS]
    assert np.array_equal(
        first_model.predict_proba(held_out),
        second_model.predict_proba(held_out),
    )


@pytest.mark.parametrize(
    "train_fraction,validation_fraction",
    [(float("nan"), 0.15), (0.7, float("nan"))],
)
def test_training_rejects_nonfinite_partition_fractions(
    tmp_path,
    train_fraction,
    validation_fraction,
):
    with pytest.raises(ValueError, match="train_fraction"):
        train_and_evaluate(
            tmp_path / "missing.csv",
            tmp_path / "model",
            train_fraction=train_fraction,
            validation_fraction=validation_fraction,
        )
