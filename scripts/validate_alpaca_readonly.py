#!/usr/bin/env python3
"""Run a read-only Alpaca connectivity and paper-account diagnostic.

This script never submits, cancels, or resubmits an order. It does not print
credential values and refuses to run provider calls when execution is not set
to the safe simulation backend.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from typing import Any, Callable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


CREDENTIAL_NAMES = (
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
)
LEGACY_CREDENTIAL_NAMES = (
    "APCA_API_KEY_ID",
    "APCA_API_SECRET_KEY",
)


def credential_status(environ: dict[str, str] | None = None) -> dict[str, bool]:
    values = environ if environ is not None else os.environ
    return {
        name: bool(values.get(name, "").strip())
        for name in CREDENTIAL_NAMES + LEGACY_CREDENTIAL_NAMES
    }


def _credentials_available(status: dict[str, bool]) -> tuple[bool, list[str]]:
    api_available = status["ALPACA_API_KEY"] or status["APCA_API_KEY_ID"]
    secret_available = status["ALPACA_SECRET_KEY"] or status["APCA_API_SECRET_KEY"]
    missing: list[str] = []
    if not api_available:
        missing.extend(("ALPACA_API_KEY", "APCA_API_KEY_ID"))
    if not secret_available:
        missing.extend(("ALPACA_SECRET_KEY", "APCA_API_SECRET_KEY"))
    return api_available and secret_available, missing


def classify_error(error: BaseException) -> str:
    category = getattr(error, "category", None)
    if category:
        return str(category)
    text = str(error).lower()
    error_type = type(error).__name__.lower()

    if any(marker in text for marker in ("401", "unauthorized", "invalid api", "authentication")):
        return "authentication"
    if isinstance(error, PermissionError) or any(marker in text for marker in ("403", "forbidden", "permission", "subscription", "feed")):
        return "permission_or_subscription"
    if any(marker in text for marker in ("429", "rate limit", "too many requests")):
        return "rate_limit"
    if any(marker in text for marker in ("timeout", "timed out", "connection", "dns", "network")) or "timeout" in error_type:
        return "network"
    if "market is closed" in text or "market closed" in text:
        return "market_closed"
    return "provider_error"


def safe_error_message(error: BaseException, category: str) -> str:
    message = getattr(error, "safe_message", None)
    if isinstance(message, str) and message:
        return message
    if type(error).__name__ == "AlpacaMarketDataError":
        return str(error)
    descriptions = {
        "authentication": "Provider authentication failed.",
        "permission_or_subscription": "Provider access or feed permission was denied.",
        "rate_limit": "Provider rate limit was reached.",
        "network": "Provider could not be reached.",
        "market_closed": "Market is closed.",
        "invalid_trade": "Provider returned an invalid trade.",
        "trade_unavailable": "Provider returned no latest trade.",
    }
    return descriptions.get(category, "Provider request failed.")


def validate_snapshot(snapshot: dict[str, Any], now: datetime | None = None) -> list[str]:
    issues: list[str] = []
    if not isinstance(snapshot, dict):
        return ["response_not_object"]

    price = snapshot.get("price")
    timestamp = snapshot.get("timestamp")
    try:
        numeric_price = float(price)
    except (TypeError, ValueError):
        numeric_price = 0.0
    if numeric_price <= 0:
        issues.append("non_positive_or_malformed_price")

    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        issues.append("missing_or_naive_timestamp")
    else:
        age = ((now or datetime.now(UTC)) - timestamp.astimezone(UTC)).total_seconds()
        if age < 0:
            issues.append("future_timestamp")

    if snapshot.get("provider") != "alpaca":
        issues.append("unexpected_provider")
    if not str(snapshot.get("symbol", "")).strip():
        issues.append("missing_symbol")
    return issues


def validate_trade_report(report: dict[str, Any], symbol: str) -> list[str]:
    issues: list[str] = []
    if report.get("provider_request_status") != "succeeded":
        issues.append("provider_request_failed")
    if report.get("provider") != "alpaca":
        issues.append("unexpected_provider")
    if str(report.get("symbol", "")).upper() != symbol.upper():
        issues.append("unexpected_symbol")
    if report.get("trade_available") is not True:
        issues.append("latest_trade_unavailable")
    if report.get("trade_status") != "fresh":
        issues.append("trade_not_fresh")

    session = report.get("market_status")
    if not isinstance(session, dict) or session.get("status") != "open":
        issues.append("market_not_open")
    elif session.get("calendar_verified") is not True:
        issues.append("market_calendar_unverified")

    timestamp = report.get("trade_timestamp")
    retrieved_at = report.get("retrieved_at")
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        issues.append("missing_or_naive_timestamp")
    if not isinstance(retrieved_at, datetime) or retrieved_at.tzinfo is None:
        issues.append("missing_or_naive_retrieval_timestamp")
    if (
        isinstance(timestamp, datetime) and timestamp.tzinfo is not None
        and isinstance(retrieved_at, datetime) and retrieved_at.tzinfo is not None
        and timestamp > retrieved_at
    ):
        issues.append("future_timestamp")

    try:
        age = float(report.get("age_seconds"))
        threshold = float(report.get("freshness_threshold_seconds"))
    except (TypeError, ValueError, OverflowError):
        age = float("nan")
        threshold = float("nan")
    if not isfinite(age) or age < 0:
        issues.append("invalid_trade_age")
    if not isfinite(threshold) or threshold <= 0:
        issues.append("invalid_freshness_threshold")
    elif isfinite(age) and age > threshold:
        issues.append("trade_exceeds_freshness_threshold")

    try:
        price = float(report.get("price"))
    except (TypeError, ValueError, OverflowError):
        price = float("nan")
    if not isfinite(price) or price <= 0:
        issues.append("invalid_current_quote_price")
    if report.get("current_quote_permitted") is not True:
        issues.append("current_quote_not_permitted")
    return sorted(set(issues))


def validate_history(history: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not isinstance(history, dict):
        return ["response_not_object"]
    bars = history.get("bars")
    if not isinstance(bars, list):
        return ["bars_not_list"]

    timestamps = []
    for bar in bars:
        if not isinstance(bar, dict):
            issues.append("bar_not_object")
            continue
        timestamp = bar.get("timestamp")
        try:
            parsed_timestamp = datetime.fromisoformat(str(timestamp))
        except (TypeError, ValueError):
            issues.append("malformed_bar_timestamp")
            continue
        if parsed_timestamp.tzinfo is None:
            issues.append("naive_bar_timestamp")
        timestamps.append(parsed_timestamp)
        try:
            values = [float(bar[field]) for field in ("open", "high", "low", "close", "volume")]
        except (TypeError, ValueError, KeyError):
            issues.append("malformed_bar_fields")
            continue
        if any(value <= 0 for value in values[:4]) or values[4] < 0:
            issues.append("invalid_bar_values")
        if values[1] < max(values[0], values[3]) or values[2] > min(values[0], values[3]):
            issues.append("invalid_ohlc_relationship")

    if timestamps != sorted(timestamps):
        issues.append("history_out_of_order")
    if len(set(timestamps)) != len(timestamps):
        issues.append("history_duplicate_timestamps")
    return sorted(set(issues))


def _safe_call(name: str, operation: Callable[[], Any]) -> dict[str, Any]:
    try:
        value = operation()
    except Exception as error:
        category = classify_error(error)
        return {
            "name": name,
            "status": "failed",
            "category": category,
            "error_type": type(error).__name__,
            "error": safe_error_message(error, category),
        }
    return {"name": name, "status": "passed", "value": value}


def run_diagnostic(
    *,
    symbol: str = "AAPL",
    environ: dict[str, str] | None = None,
    adapter_factory: Callable[[], Any] | None = None,
    broker_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    values = environ if environ is not None else os.environ
    backend = values.get("ULTRON_EXECUTION_BACKEND", "simulation").strip().lower()
    provider = values.get("ULTRON_FINANCIAL_MARKET_PROVIDER", "yahoo").strip().lower()
    credentials = credential_status(values)
    report: dict[str, Any] = {
        "configuration": {
            "execution_backend": backend,
            "market_provider": provider,
            "configured_feed": values.get("ALPACA_DATA_FEED") or "account-default",
            "credential_presence": credentials,
            "supported_credential_names": list(CREDENTIAL_NAMES + LEGACY_CREDENTIAL_NAMES),
        },
        "read_only_provider_checks": [],
        "paper_account_verification": {"status": "not_run"},
        "paper_order_lifecycle": {"status": "not_run", "reason": "Order submission is not part of this diagnostic."},
        "safety": {
            "order_submission_attempted": False,
            "live_execution_enabled": False,
        },
    }

    if backend != "simulation":
        report["read_only_provider_checks"] = [{
            "name": "configuration_guard",
            "status": "blocked",
            "category": "non_simulation_backend",
        }]
        report["paper_account_verification"] = {"status": "blocked", "reason": "Execution backend is not simulation."}
        return report

    credentials_available, missing = _credentials_available(credentials)
    if not credentials_available:
        report["read_only_provider_checks"] = [{
            "name": "credential_gate",
            "status": "skipped",
            "category": "missing_credentials",
            "missing_configuration": missing,
        }]
        report["paper_account_verification"] = {
            "status": "skipped",
            "reason": "Credentials are unavailable; no provider call was attempted.",
        }
        return report

    from ultron.financial.trading.alpaca_paper_broker import AlpacaPaperBroker
    from ultron.financial.trading.market_data.alpaca_adapter import AlpacaMarketAdapter

    adapter = adapter_factory() if adapter_factory else AlpacaMarketAdapter()
    broker_builder = broker_factory or AlpacaPaperBroker

    session_result = _safe_call("market_session", adapter.get_market_session_status)
    if session_result["status"] == "passed" and isinstance(session_result["value"], dict):
        session = session_result["value"]
        session_state = session.get("status", "unknown")
        calendar_verified = session.get("calendar_verified") is True
        session_check = {
            "name": "market_session",
            "status": "passed" if session_state in {"open", "closed"} and calendar_verified else "failed",
            "market_status": session_state,
            "calendar_verified": calendar_verified,
            "session_date": session.get("session_date"),
            "session_open": session.get("session_open"),
            "session_close": session.get("session_close"),
            "clock_timestamp": session.get("clock_timestamp"),
            "category": session.get("error_category"),
            "error": session.get("error"),
        }
    else:
        session_state = "unknown"
        session_check = {
            "name": "market_session", "status": "failed",
            "market_status": session_state,
            "category": session_result.get("category"),
            "error": session_result.get("error"),
        }
    report["read_only_provider_checks"].append(session_check)

    trade_result = _safe_call("latest_trade", lambda: adapter.get_latest_trade_report(symbol))
    if trade_result["status"] == "passed" and isinstance(trade_result["value"], dict):
        trade = trade_result["value"]
        trade_issues = validate_trade_report(trade, symbol)
        current_quote_permitted = not trade_issues
        trade_status = trade.get("trade_status", "invalid")
        trade_check = {
            "name": "latest_trade",
            "status": "passed" if current_quote_permitted else "failed",
            "provider_request_status": trade.get("provider_request_status", "unknown"),
            "trade_available": trade.get("trade_available", False),
            "trade_status": trade_status,
            "market_status": trade.get("market_status", {}).get("status", session_state),
            "feed": trade.get("feed", values.get("ALPACA_DATA_FEED") or "account-default"),
            "price": trade.get("price") if current_quote_permitted else None,
            "trade_timestamp": trade.get("trade_timestamp"),
            "retrieved_at": trade.get("retrieved_at"),
            "age_seconds": trade.get("age_seconds"),
            "freshness_threshold_seconds": trade.get("freshness_threshold_seconds"),
            "current_quote_permitted": current_quote_permitted,
            "last_known_trade": trade.get("last_known_trade"),
            "category": trade.get("error_category"),
            "error": trade.get("error"),
            "issues": trade_issues,
        }
    else:
        trade_check = {
            "name": "latest_trade", "status": "failed",
            "provider_request_status": "failed",
            "trade_available": False, "trade_status": "unknown",
            "market_status": session_state,
            "feed": values.get("ALPACA_DATA_FEED") or "account-default",
            "current_quote_permitted": False,
            "category": trade_result.get("category"),
            "error_type": trade_result.get("error_type"),
            "error": trade_result.get("error"),
        }
    report["read_only_provider_checks"].append(trade_check)

    history_result = _safe_call(
        "historical_bars",
        lambda: adapter.get_history(symbol, period="5d", timeframe="1Day", limit=5),
    )
    if history_result["status"] == "passed":
        history = history_result["value"]
        issues = validate_history(history)
        bars = history.get("bars") if isinstance(history, dict) else None
        bar_count = len(bars) if isinstance(bars, list) else 0
        if bar_count == 0:
            issues.append("no_bars_returned")
        report["read_only_provider_checks"].append({
            "name": "historical_bars_validation",
            "provider_request_status": "succeeded",
            "response_status": "bars_returned" if bar_count else "empty",
            "symbol": history.get("symbol") if isinstance(history, dict) else symbol,
            "feed": history.get("feed") if isinstance(history, dict) else None,
            "timeframe": history.get("timeframe") if isinstance(history, dict) else None,
            "period": history.get("period") if isinstance(history, dict) else None,
            "start": history.get("start") if isinstance(history, dict) else None,
            "end": history.get("end") if isinstance(history, dict) else None,
            "limit": history.get("limit") if isinstance(history, dict) else None,
            "adjustment": history.get("adjustment") if isinstance(history, dict) else None,
            "status": "passed" if not issues else "failed",
            "historical_bars_returned": bar_count > 0,
            "bar_count": bar_count,
            "issues": issues,
        })
    else:
        report["read_only_provider_checks"].append({
            **history_result,
            "provider_request_status": "failed",
            "response_status": "unavailable",
            "historical_bars_returned": False,
            "bar_count": 0,
        })

    account_result = _safe_call("paper_account", broker_builder)
    if account_result["status"] == "passed":
        report["paper_account_verification"] = {
            "status": "passed",
            "endpoint": "paper",
            "account_status_checked": True,
            "order_lifecycle_tested": False,
        }
    else:
        report["paper_account_verification"] = {
            "status": "failed",
            "category": account_result["category"],
            "error_type": account_result["error_type"],
            "error": account_result["error"],
            "endpoint": "paper",
        }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Alpaca validation; never submits orders.")
    parser.add_argument("--symbol", default="AAPL")
    args = parser.parse_args()
    report = run_diagnostic(symbol=args.symbol)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    checks = report["read_only_provider_checks"]
    failed = any(check.get("status") == "failed" for check in checks)
    blocked = any(check.get("status") == "blocked" for check in checks)
    skipped = any(check.get("status") == "skipped" for check in checks)
    account_failed = report["paper_account_verification"].get("status") != "passed"
    return 2 if failed or blocked or skipped or account_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
