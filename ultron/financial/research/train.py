"""Train and evaluate a lightweight time-ordered market-signal baseline."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    brier_score_loss,
    log_loss,
    roc_auc_score,
)
import joblib

from .backtest import evaluate_signals
from .dataset import load_ohlcv
from .features import FEATURE_COLUMNS, build_features

_INDIAN_INDEX_SYMBOLS = {
    "NIFTY", "NIFTY50", "^NSEI", "SENSEX", "BSESENSEX", "^BSESN",
}


def _uses_indian_market(tickers: pd.Series) -> bool:
    for ticker in tickers:
        symbol = str(ticker).strip().upper().replace(" ", "")
        if symbol in _INDIAN_INDEX_SYMBOLS or symbol.endswith((".NS", ".BO")):
            return True
    return False


def _classification_metrics(y_true: pd.Series, probability: np.ndarray) -> dict[str, Any]:
    predicted = (probability >= 0.5).astype("int8")
    result: dict[str, Any] = {
        "accuracy": round(float(accuracy_score(y_true, predicted)), 5),
        "balanced_accuracy": round(float(balanced_accuracy_score(y_true, predicted)), 5),
        "brier_score": round(float(brier_score_loss(y_true, probability)), 5),
        "observations": int(len(y_true)),
        "positive_rate": round(float(y_true.mean()), 5),
    }
    if y_true.nunique() > 1:
        result["roc_auc"] = round(float(roc_auc_score(y_true, probability)), 5)
        result["log_loss"] = round(float(log_loss(y_true, probability, labels=[0, 1])), 5)
    else:
        result["roc_auc"] = None
        result["log_loss"] = None
    return result


def _walk_forward_evaluation(
    data: pd.DataFrame,
    validation_dates: np.ndarray,
    *,
    validation_end: pd.Timestamp,
    threshold: float,
    cost_bps_per_side: float,
) -> dict[str, Any]:
    """Evaluate expanding-window models on successive validation-only blocks."""
    date_blocks = np.array_split(
        validation_dates,
        min(3, len(validation_dates)),
    )
    scored_blocks: list[pd.DataFrame] = []
    fold_reports: list[dict[str, Any]] = []

    for index, block in enumerate(date_blocks):
        start = pd.Timestamp(block[0])
        end = (
            pd.Timestamp(date_blocks[index + 1][0])
            if index + 1 < len(date_blocks)
            else validation_end
        )
        training = data[
            (data["date"] < start)
            & (data["next_session_date"] < start)
        ]
        scored = data[
            (data["date"] >= start)
            & (data["date"] < end)
            & (data["next_session_date"] < end)
        ].copy()
        if training.empty or training["target_up"].nunique() < 2:
            raise ValueError(
                f"Walk-forward fold {index + 1} has insufficient prior "
                "training history or only one target class."
            )
        if scored.empty:
            raise ValueError(
                f"Walk-forward fold {index + 1} has no observations after "
                "purging boundary labels."
            )

        model = HistGradientBoostingClassifier(
            learning_rate=0.06,
            max_iter=150,
            max_leaf_nodes=15,
            l2_regularization=1.0,
            random_state=42,
        )
        model.fit(training[FEATURE_COLUMNS], training["target_up"])
        probability = model.predict_proba(scored[FEATURE_COLUMNS])[:, 1]
        signals = scored[
            ["date", "ticker", "target_up", "next_open_to_close_return"]
        ].copy()
        signals["probability_up"] = probability
        scored_blocks.append(signals)

        fold_reports.append(
            {
                "fold": index + 1,
                "training_end": str(training["date"].max().date()),
                "training_label_end": str(
                    training["next_session_date"].max().date()
                ),
                "out_of_sample_start": str(scored["date"].min().date()),
                "out_of_sample_end": str(scored["date"].max().date()),
                "training_observations": int(len(training)),
                "out_of_sample_observations": int(len(scored)),
                "classification": _classification_metrics(
                    scored["target_up"], probability
                ),
            }
        )

    walk_forward_signals = pd.concat(scored_blocks, ignore_index=True)
    return {
        "method": "expanding_window",
        "folds": fold_reports,
        "out_of_sample_observations": int(len(walk_forward_signals)),
        "classification": _classification_metrics(
            walk_forward_signals["target_up"],
            walk_forward_signals["probability_up"].to_numpy(),
        ),
        "backtest": evaluate_signals(
            walk_forward_signals[
                ["date", "ticker", "probability_up", "next_open_to_close_return"]
            ],
            threshold=threshold,
            cost_bps_per_side=cost_bps_per_side,
        ),
        "protocol": (
            "Each fold trains only on earlier dates with labels ending before "
            "the fold; boundary labels are purged. Folds cover the validation "
            "period only, use fixed model parameters and supplied threshold, "
            "and do not inspect or tune against the final test period."
        ),
    }


def train_and_evaluate(
    data_path: str | Path,
    output_dir: str | Path,
    *,
    train_fraction: float = 0.70,
    validation_fraction: float = 0.15,
    threshold: float = 0.55,
    cost_bps_per_side: float | None = None,
) -> dict[str, Any]:
    """Train on earliest dates, validate on later dates, evaluate final dates once.

    The validation and test sets are separated by date, not randomly. No model
    or threshold selection is performed on the final test segment.
    """
    if (
        not math.isfinite(train_fraction)
        or not math.isfinite(validation_fraction)
        or train_fraction <= 0
        or validation_fraction <= 0
        or train_fraction + validation_fraction >= 1
    ):
        raise ValueError("train_fraction and validation_fraction must be positive and sum to less than 1")
    raw = load_ohlcv(data_path)
    has_indian_market = _uses_indian_market(raw["ticker"])
    if cost_bps_per_side is None:
        if has_indian_market:
            raise ValueError(
                "Indian-market research requires an explicit user-supplied "
                "per-side cost assumption; no market-specific cost is configured."
            )
        cost_bps_per_side = 10.0
    if not math.isfinite(cost_bps_per_side) or cost_bps_per_side < 0:
        raise ValueError("cost_bps_per_side must be finite and non-negative")
    data = build_features(raw)
    dates = np.array(sorted(data["date"].unique()))
    if len(dates) < 40:
        raise ValueError(f"Need at least 40 usable distinct dates after feature warm-up; got {len(dates)}")
    train_cut = dates[int(len(dates) * train_fraction)]
    validation_cut = dates[int(len(dates) * (train_fraction + validation_fraction))]
    # Purge boundary labels: a row belongs to a partition only when its next-session
    # outcome also falls strictly inside that partition. This prevents a training
    # label from consuming a validation-day return.
    train = data[(data["date"] < train_cut) & (data["next_session_date"] < train_cut)]
    validation = data[(data["date"] >= train_cut) & (data["date"] < validation_cut) & (data["next_session_date"] < validation_cut)]
    test = data[data["date"] >= validation_cut]
    if min(len(train), len(validation), len(test)) == 0:
        raise ValueError("Chronological split produced an empty partition")
    if train["target_up"].nunique() < 2:
        raise ValueError("Training segment contains only one target class")

    model = HistGradientBoostingClassifier(
        learning_rate=0.06,
        max_iter=150,
        max_leaf_nodes=15,
        l2_regularization=1.0,
        random_state=42,
    )
    model.fit(train[FEATURE_COLUMNS], train["target_up"])
    validation_probability = model.predict_proba(validation[FEATURE_COLUMNS])[:, 1]
    test_probability = model.predict_proba(test[FEATURE_COLUMNS])[:, 1]

    validation_signals = validation[["date", "ticker", "next_open_to_close_return"]].copy()
    validation_signals["probability_up"] = validation_probability
    test_signals = test[["date", "ticker", "next_open_to_close_return"]].copy()
    test_signals["probability_up"] = test_probability
    walk_forward = _walk_forward_evaluation(
        data,
        np.array(sorted(validation["date"].unique())),
        validation_end=validation_cut,
        threshold=threshold,
        cost_bps_per_side=cost_bps_per_side,
    )

    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    model_path = out / "market_signal_model.joblib"
    joblib.dump({"model": model, "features": FEATURE_COLUMNS, "target": "next_open_to_close_return > 0"}, model_path)

    metrics = {
        "success": True,
        "model_type": type(model).__name__,
        "data_path": str(Path(data_path).expanduser().resolve()),
        "rows": {"raw": int(len(raw)), "usable": int(len(data)), "train": int(len(train)), "validation": int(len(validation)), "test": int(len(test))},
        "date_ranges": {
            "train": [str(train["date"].min().date()), str(train["date"].max().date())],
            "validation": [str(validation["date"].min().date()), str(validation["date"].max().date())],
            "test": [str(test["date"].min().date()), str(test["date"].max().date())],
        },
        "features": FEATURE_COLUMNS,
        "validation_classification": _classification_metrics(validation["target_up"], validation_probability),
        "walk_forward_validation": walk_forward,
        "test_classification": _classification_metrics(test["target_up"], test_probability),
        "validation_backtest": evaluate_signals(validation_signals, threshold=threshold, cost_bps_per_side=cost_bps_per_side),
        "test_backtest": evaluate_signals(test_signals, threshold=threshold, cost_bps_per_side=cost_bps_per_side),
        "model_path": str(model_path),
        "threshold": threshold,
        "cost_assumption": (
            "User-supplied per-side estimate; not verified market-specific and "
            "excludes statutory charges, spread, slippage, and liquidity."
            if has_indian_market
            else "Generic 10 bps per-side default unless explicitly overridden."
        ),
        "limitations": [
            "Daily OHLCV only; no fundamentals, news, order book, or intraday microstructure.",
            "Threshold is supplied by the user and is not optimized on the final test set.",
            "The final test set must be treated as untouched after this report is generated.",
            "This model emits research signals only and cannot place orders.",
        ],
    }
    report_path = out / "training_report.json"
    report_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    metrics["report_path"] = str(report_path)
    return metrics
