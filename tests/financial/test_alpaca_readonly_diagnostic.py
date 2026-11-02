from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import json
import sys

import scripts.validate_alpaca_readonly as diagnostic
from scripts.validate_alpaca_readonly import run_diagnostic


def _environment():
    return {
        "ALPACA_API_KEY": "configured",
        "ALPACA_SECRET_KEY": "configured",
        "ULTRON_EXECUTION_BACKEND": "simulation",
        "ULTRON_FINANCIAL_MARKET_PROVIDER": "alpaca",
        "ALPACA_DATA_FEED": "iex",
    }


def _market_status(status="open"):
    return {
        "status": status,
        "calendar_verified": True,
        "session_date": "2026-10-16",
        "clock_timestamp": datetime.now(UTC),
    }


def _trade_report(timestamp, *, market_status="open", age_seconds=1.0):
    fresh = age_seconds <= 30.0
    permitted = fresh and market_status == "open"
    return {
        "provider_request_status": "succeeded",
        "provider": "alpaca",
        "symbol": "AAPL",
        "feed": "IEX",
        "currency": "USD",
        "market_status": _market_status(market_status),
        "trade_available": True,
        "trade_status": "fresh" if fresh else "stale",
        "current_quote_permitted": permitted,
        "freshness_threshold_seconds": 30.0,
        "trade_timestamp": timestamp,
        "retrieved_at": datetime.now(UTC),
        "age_seconds": age_seconds,
        "last_known_trade": None if permitted else {
            "status": "stale" if not fresh else "not_current_session",
            "session_classification": "previous_session" if market_status == "closed" else "stale_unclassified",
            "price": 200.0,
            "timestamp": timestamp,
            "age_seconds": age_seconds,
            "provider": "alpaca",
            "feed": "IEX",
            "currency": "USD",
        },
        "price": 200.0 if permitted else None,
        "error_category": None if permitted else "stale_trade" if not fresh else "market_closed",
    }


def test_diagnostic_reports_missing_credentials_without_provider_calls():
    calls = []

    def adapter_factory():
        calls.append("adapter")
        raise AssertionError("provider must not be constructed")

    report = run_diagnostic(
        environ={"ULTRON_EXECUTION_BACKEND": "simulation"},
        adapter_factory=adapter_factory,
    )

    check = report["read_only_provider_checks"][0]
    assert check["status"] == "skipped"
    assert check["category"] == "missing_credentials"
    assert "ALPACA_API_KEY" in check["missing_configuration"]
    assert "ALPACA_SECRET_KEY" in check["missing_configuration"]
    assert calls == []
    assert report["safety"]["order_submission_attempted"] is False
    assert report["safety"]["live_execution_enabled"] is False


def test_diagnostic_rejects_non_simulation_backend_without_provider_calls():
    calls = []

    def adapter_factory():
        calls.append("adapter")
        raise AssertionError("provider must not be constructed")

    report = run_diagnostic(
        environ={
            **_environment(),
            "ULTRON_EXECUTION_BACKEND": "alpaca_paper",
        },
        adapter_factory=adapter_factory,
    )

    assert report["read_only_provider_checks"][0]["status"] == "blocked"
    assert report["read_only_provider_checks"][0]["category"] == "non_simulation_backend"
    assert calls == []
    assert report["safety"]["order_submission_attempted"] is False
    assert report["safety"]["live_execution_enabled"] is False


def test_diagnostic_validates_read_only_market_data_and_paper_account():
    timestamp = datetime.now(UTC) - timedelta(seconds=1)

    class Adapter:
        feed = "iex"
        history_request = None

        def get_market_session_status(self):
            return _market_status()

        def get_latest_trade_report(self, symbol):
            return _trade_report(timestamp)

        def get_history(self, symbol, period, timeframe, limit):
            type(self).history_request = (symbol, period, timeframe, limit)
            return {
                "symbol": symbol,
                "provider": "alpaca",
                "feed": "iex",
                "timeframe": timeframe,
                "period": period,
                "start": "2026-10-11T04:00:00+00:00",
                "end": "2026-10-16T04:00:00+00:00",
                "limit": limit,
                "adjustment": "all",
                "bars": [
                    {
                        "timestamp": timestamp.isoformat(),
                        "open": 199.0,
                        "high": 201.0,
                        "low": 198.0,
                        "close": 200.0,
                        "volume": 1000.0,
                    }
                ]
            }

    class Broker:
        order_calls = 0

        def __init__(self):
            self.mode = "paper"

        def submit(self, *args, **kwargs):
            type(self).order_calls += 1
            raise AssertionError("order submission is forbidden")

        def cancel_order(self, *args, **kwargs):
            type(self).order_calls += 1
            raise AssertionError("order cancellation is forbidden")

    report = run_diagnostic(
        environ=_environment(),
        adapter_factory=Adapter,
        broker_factory=Broker,
    )

    assert all(
        check["status"] == "passed"
        for check in report["read_only_provider_checks"]
    )
    assert report["paper_account_verification"]["status"] == "passed"
    assert report["paper_account_verification"]["endpoint"] == "paper"
    assert report["paper_order_lifecycle"]["status"] == "not_run"
    assert report["safety"]["order_submission_attempted"] is False
    trade_check = next(check for check in report["read_only_provider_checks"] if check["name"] == "latest_trade")
    assert trade_check["current_quote_permitted"] is True
    assert trade_check["feed"] == "IEX"
    assert report["configuration"]["configured_feed"] == "iex"
    assert Broker.order_calls == 0
    assert Adapter.history_request == ("AAPL", "5d", "1Day", 5)
    history_check = next(
        check for check in report["read_only_provider_checks"]
        if check["name"] == "historical_bars_validation"
    )
    assert history_check["provider_request_status"] == "succeeded"
    assert history_check["response_status"] == "bars_returned"
    assert history_check["feed"] == "iex"
    assert history_check["period"] == "5d"
    assert history_check["limit"] == 5
    assert history_check["adjustment"] == "all"


def test_diagnostic_labels_closed_market_without_treating_it_as_price_failure():
    timestamp = datetime.now(UTC) - timedelta(hours=8)

    class Adapter:
        feed = "iex"

        def get_market_session_status(self):
            return _market_status("closed")

        def get_latest_trade_report(self, symbol):
            return _trade_report(timestamp, market_status="closed", age_seconds=28800.0)

        def get_history(self, symbol, period, timeframe, limit):
            return {"bars": []}

    class Broker:
        def __init__(self):
            self.mode = "paper"

    report = run_diagnostic(
        environ=_environment(),
        adapter_factory=Adapter,
        broker_factory=Broker,
    )

    session_check, trade_check = report["read_only_provider_checks"][:2]
    assert session_check["market_status"] == "closed"
    assert trade_check["status"] == "failed"
    assert trade_check["trade_status"] == "stale"
    assert trade_check["current_quote_permitted"] is False
    assert trade_check["last_known_trade"]["session_classification"] == "previous_session"
    assert trade_check["age_seconds"] == 28800.0
    assert trade_check["freshness_threshold_seconds"] == 30.0
    assert trade_check["trade_timestamp"] == timestamp
    assert trade_check["retrieved_at"] is not None


def test_diagnostic_reports_missing_trade_and_empty_history_without_substitution():
    class Adapter:
        def get_market_session_status(self):
            return _market_status("open")

        def get_latest_trade_report(self, symbol):
            return {
                "provider_request_status": "succeeded",
                "provider": "alpaca",
                "symbol": symbol,
                "feed": "IEX",
                "market_status": _market_status("open"),
                "trade_available": False,
                "trade_status": "unavailable",
                "current_quote_permitted": False,
                "freshness_threshold_seconds": 30.0,
                "trade_timestamp": None,
                "retrieved_at": datetime.now(UTC),
                "age_seconds": None,
                "last_known_trade": None,
                "error_category": "trade_unavailable",
                "error": "Alpaca returned no latest trade for AAPL.",
            }

        def get_history(self, symbol, period, timeframe, limit):
            return {"bars": []}

    class Broker:
        mode = "paper"

    report = run_diagnostic(
        environ=_environment(), adapter_factory=Adapter, broker_factory=Broker,
    )
    checks = report["read_only_provider_checks"]
    trade_check = next(check for check in checks if check["name"] == "latest_trade")
    history_check = next(check for check in checks if check["name"] == "historical_bars_validation")

    assert trade_check["provider_request_status"] == "succeeded"
    assert trade_check["trade_available"] is False
    assert trade_check["trade_status"] == "unavailable"
    assert trade_check["current_quote_permitted"] is False
    assert history_check["historical_bars_returned"] is False
    assert history_check["bar_count"] == 0
    assert history_check["status"] == "failed"
    assert history_check["issues"] == ["no_bars_returned"]
    assert history_check["provider_request_status"] == "succeeded"
    assert history_check["response_status"] == "empty"


def test_diagnostic_sanitizes_provider_failures():
    class Adapter:
        def get_market_session_status(self):
            raise TimeoutError("network details must not be reported")

        def get_latest_trade_report(self, symbol):
            raise PermissionError("private response details")

        def get_history(self, symbol, period, timeframe, limit):
            raise RuntimeError("rate limit response details")

    class Broker:
        def __init__(self):
            self.mode = "paper"

    report = run_diagnostic(
        environ=_environment(),
        adapter_factory=Adapter,
        broker_factory=Broker,
    )

    checks = report["read_only_provider_checks"]
    assert checks[0]["category"] == "network"
    assert checks[1]["category"] == "permission_or_subscription"
    assert checks[2]["category"] == "rate_limit"
    rendered = str(report)
    assert "network details must not be reported" not in rendered
    assert "private response details" not in rendered
    assert "rate limit response details" not in rendered


def test_diagnostic_exit_code_requires_a_fresh_current_quote(monkeypatch, capsys):
    stale_report = {
        "read_only_provider_checks": [{"name": "latest_trade", "status": "failed"}],
        "paper_account_verification": {"status": "passed"},
    }
    monkeypatch.setattr(diagnostic, "run_diagnostic", lambda symbol: stale_report)
    monkeypatch.setattr(sys, "argv", ["validate_alpaca_readonly.py"])

    assert diagnostic.main() == 2
    rendered = json.loads(capsys.readouterr().out)
    assert rendered["read_only_provider_checks"][0]["status"] == "failed"


def test_diagnostic_exit_code_is_zero_only_when_required_checks_pass(monkeypatch, capsys):
    fresh_report = {
        "read_only_provider_checks": [{"name": "latest_trade", "status": "passed"}],
        "paper_account_verification": {"status": "passed"},
    }
    monkeypatch.setattr(diagnostic, "run_diagnostic", lambda symbol: fresh_report)
    monkeypatch.setattr(sys, "argv", ["validate_alpaca_readonly.py"])

    assert diagnostic.main() == 0
    assert json.loads(capsys.readouterr().out)["paper_account_verification"]["status"] == "passed"
