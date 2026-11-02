"""Load and validate OHLCV CSV data from Kaggle or other historical sources."""
from __future__ import annotations

from pathlib import Path
import re

import pandas as pd

_REQUIRED = ("date", "open", "high", "low", "close", "volume")
_ALIASES = {
    "date": {"date", "datetime", "timestamp", "time"},
    "open": {"open", "o"},
    "high": {"high", "h"},
    "low": {"low", "l"},
    "close": {"close", "price", "c"},
    "volume": {"volume", "vol", "v"},
    "ticker": {"ticker", "symbol", "stock", "name"},
}


def _key(value: object) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower().replace("_", " "))


def _normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    rename: dict[object, str] = {}
    for column in frame.columns:
        key = _key(column)
        for canonical, aliases in _ALIASES.items():
            if key in aliases:
                rename[column] = canonical
                break
    normalized = frame.rename(columns=rename)
    # Prefer raw Close when both Close and Adj Close are present. Mixing an
    # adjusted close with unadjusted OHLC would corrupt range/gap features.
    if "close" not in normalized.columns:
        for column in frame.columns:
            if _key(column) in {"adj close", "adjusted close"}:
                normalized = normalized.rename(columns={column: "close"})
                break
    return normalized


def load_ohlcv(path: str | Path) -> pd.DataFrame:
    """Load one CSV or a directory of CSVs into canonical OHLCV rows.

    A directory may contain one CSV per symbol (symbol inferred from filename)
    or a combined file with a ticker/symbol column. Invalid rows are rejected
    rather than silently imputed. Output is sorted by ticker and date.
    """
    source = Path(path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Historical data path does not exist: {source}")
    files = sorted(source.glob("*.csv")) if source.is_dir() else [source]
    if not files or any(file.suffix.lower() != ".csv" for file in files):
        raise ValueError("Provide a CSV file or a directory containing CSV files")

    frames: list[pd.DataFrame] = []
    for file in files:
        frame = _normalize_columns(pd.read_csv(file))
        missing = [name for name in _REQUIRED if name not in frame.columns]
        if missing:
            raise ValueError(f"{file.name}: missing required OHLCV columns: {missing}")
        if "ticker" not in frame.columns:
            frame["ticker"] = file.stem.upper() if source.is_dir() else "ASSET"
        frame = frame[["ticker", *_REQUIRED]].copy()
        frame["ticker"] = frame["ticker"].astype(str).str.strip().str.upper()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce", utc=True).dt.tz_convert(None)
        for column in ("open", "high", "low", "close", "volume"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        invalid = frame[list(_REQUIRED)].isna().any(axis=1)
        invalid |= (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
        invalid |= frame["volume"] < 0
        invalid |= frame["high"] < frame[["open", "close", "low"]].max(axis=1)
        invalid |= frame["low"] > frame[["open", "close", "high"]].min(axis=1)
        if invalid.any():
            examples = frame.loc[invalid, ["ticker", "date"]].head(5).to_dict("records")
            raise ValueError(f"{file.name}: {int(invalid.sum())} invalid OHLCV rows; examples={examples}")
        frames.append(frame)

    result = pd.concat(frames, ignore_index=True)
    if result.empty:
        raise ValueError("No OHLCV rows found")
    if (result["ticker"] == "").any():
        raise ValueError("Ticker/symbol values cannot be empty")
    duplicates = result.duplicated(["ticker", "date"], keep=False)
    if duplicates.any():
        examples = result.loc[duplicates, ["ticker", "date"]].head(5).to_dict("records")
        raise ValueError(f"Duplicate ticker/date rows found; examples={examples}")
    return result.sort_values(["ticker", "date"], kind="stable").reset_index(drop=True)
