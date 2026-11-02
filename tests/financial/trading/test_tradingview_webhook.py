from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import http.client
import json
from types import SimpleNamespace
from threading import Thread

import pytest

from ultron.financial.trading.autonomous_runtime import AutonomousTradingRuntime
from ultron.financial.trading.controller import FinancialTradingController
from ultron.financial.trading.engine import PaperTradingEngine
from ultron.financial.trading.market.adapters.paper import PaperMarketAdapter
from ultron.financial.trading.market_data.service import MarketDataService
from ultron.financial.trading.paper_broker import PaperBroker
from ultron.financial.trading.tradingview_webhook import (
    MAX_REQUEST_BYTES,
    TradingViewAlertError,
    TradingViewWebhookProcessor,
    TradingViewWebhookServer,
)


SECRET = "test-only-secret-that-is-long-enough-123456"
NOW = datetime(2026, 10, 16, 15, 0, tzinfo=UTC)


def _processor():
    return TradingViewWebhookProcessor(
        secret=SECRET,
        allowed_symbols={"AAPL", "MSFT"},
        strategy_id="ultron-paper-v1",
        clock=lambda: NOW,
    )


def _payload(**overrides):
    payload = {
        "schema": "ultron.tradingview.v1",
        "secret": SECRET,
        "event_id": "tv-event-001",
        "strategy_id": "ultron-paper-v1",
        "symbol": "AAPL",
        "action": "buy",
        "price": "100.25",
        "timestamp": (NOW - timedelta(seconds=5)).isoformat(),
    }
    payload.update(overrides)
    return json.dumps(payload, allow_nan=True).encode("utf-8")


def test_valid_alert_parsing_normalizes_and_keeps_price_as_reference_only():
    alert = _processor().validate(_payload())

    assert alert.event_id == "tv-event-001"
    assert alert.symbol == "AAPL"
    assert alert.action == "buy"
    assert alert.reference_price == 100.25
    assert alert.timestamp.tzinfo is not None
    assert "secret" not in alert.__dict__


@pytest.mark.parametrize("body", [b"", b"{", b"[]", b'{"x":NaN}'])
def test_malformed_json_is_rejected(body):
    with pytest.raises(TradingViewAlertError):
        _processor().validate(body)


def test_oversized_payload_is_rejected():
    with pytest.raises(TradingViewAlertError, match="too large"):
        _processor().validate(b" " * (MAX_REQUEST_BYTES + 1))


def test_bad_authentication_is_rejected_without_echoing_secret():
    with pytest.raises(TradingViewAlertError) as caught:
        _processor().validate(_payload(secret="incorrect-secret"))

    assert caught.value.code == "unauthorized"
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", "other.v1"),
        ("event_id", "contains spaces"),
        ("strategy_id", "unconfigured"),
        ("symbol", "TSLA"),
        ("action", "hold"),
        ("price", float("nan")),
        ("price", float("inf")),
        ("price", 0),
        ("price", True),
        ("timestamp", "not-a-timestamp"),
        ("timestamp", "2026-10-16T15:00:00"),
        ("timestamp", (NOW + timedelta(seconds=1)).isoformat()),
    ],
)
def test_invalid_alert_fields_are_rejected(field, value):
    with pytest.raises(TradingViewAlertError):
        _processor().validate(_payload(**{field: value}))


def test_stale_alert_is_rejected():
    old = NOW - timedelta(seconds=MAX_REQUEST_BYTES)
    with pytest.raises(TradingViewAlertError) as caught:
        _processor().validate(_payload(timestamp=old.isoformat()))

    assert caught.value.code == "stale_alert"


def test_unknown_fields_and_duplicate_json_fields_are_rejected():
    with pytest.raises(TradingViewAlertError, match="fields"):
        _processor().validate(_payload(extra="not documented"))

    duplicate = (
        b'{"schema":"ultron.tradingview.v1","schema":"ultron.tradingview.v1"}'
    )
    with pytest.raises(TradingViewAlertError):
        _processor().validate(duplicate)


@pytest.mark.parametrize(
    "injected_field",
    ["broker", "execution_backend", "risk", "decision", "quantity"],
)
def test_alert_cannot_inject_execution_policy(injected_field):
    with pytest.raises(TradingViewAlertError) as caught:
        _processor().validate(_payload(**{injected_field: "unsafe"}))
    assert caught.value.code == "invalid_schema"


def test_environment_requires_simulation_and_explicit_configuration(monkeypatch):
    monkeypatch.setenv("ULTRON_EXECUTION_BACKEND", "alpaca_paper")
    with pytest.raises(RuntimeError, match="simulation"):
        TradingViewWebhookProcessor.from_environment()

    monkeypatch.setenv("ULTRON_EXECUTION_BACKEND", "simulation")
    monkeypatch.setenv("ULTRON_TRADINGVIEW_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("ULTRON_TRADINGVIEW_ALLOWED_SYMBOLS", "AAPL,MSFT")
    monkeypatch.setenv("ULTRON_TRADINGVIEW_STRATEGY_ID", "ultron-paper-v1")
    assert TradingViewWebhookProcessor.from_environment().strategy_id == "ultron-paper-v1"


def _analysis(symbol="AAPL", decision="consider", price=100.0):
    return {
        "success": True,
        "symbol": symbol,
        "market": {"current_price": price},
        "decision": {"decision": decision, "confidence": 0.9},
        "reasoning": {"confidence": 0.9, "risks": []},
        "risk": {"risk": {"risk_level": "low"}},
        "strategies": ["tv-triggered-ultron-analysis"],
    }


def _runtime(monkeypatch, tmp_path, *, initial_capital=100_000.0, analysis=None):
    monkeypatch.setenv("ULTRON_EXECUTION_BACKEND", "simulation")
    monkeypatch.setenv("ULTRON_FINANCIAL_MARKET_PROVIDER", "alpaca")
    monkeypatch.delenv("ULTRON_IDEMPOTENCY_DB", raising=False)
    broker = PaperBroker(initial_capital=initial_capital)
    adapter = PaperMarketAdapter(broker, prices={"AAPL": 100.0})
    adapter.connect()
    controller = FinancialTradingController(
        PaperTradingEngine(broker), market_data=MarketDataService(adapter)
    )

    class Skill:
        analyze_calls = 0

        def execute(self, command, **kwargs):
            if command == "analyze":
                self.analyze_calls += 1
                return analysis or _analysis(kwargs["symbol"])
            if command == "paper_trade":
                return controller.execute_from_market(kwargs["analysis"])
            raise AssertionError(f"Unexpected financial skill action: {command}")

    skill = Skill()
    container = SimpleNamespace(
        financial_skill=skill,
        financial_trading_controller=controller,
    )
    runtime = AutonomousTradingRuntime(
        container=container,
        symbols=["AAPL"],
        state_path=tmp_path / "autonomous.sqlite3",
    )
    return runtime, broker, skill


def _event(processor, *, action="buy", event_id="tv-event-001", price=100.0):
    return processor.validate(
        _payload(action=action, event_id=event_id, price=price)
    )


def test_buy_trigger_requires_ultron_analysis_and_uses_validated_price(monkeypatch, tmp_path):
    runtime, broker, skill = _runtime(monkeypatch, tmp_path)
    processor = _processor()

    result = runtime.process_tradingview_alert(
        _event(processor, price=100.5)
    )

    assert result["status"] == "completed"
    assert result["execution_status"] == "filled"
    assert skill.analyze_calls == 1
    assert broker.snapshot({"AAPL": 100.0})["trade_count"] == 1
    assert broker.portfolio.trades[0].price == 100.0


def test_duplicate_alert_and_event_id_conflict_never_reexecute(monkeypatch, tmp_path):
    runtime, broker, skill = _runtime(monkeypatch, tmp_path)
    processor = _processor()
    event = _event(processor)

    first = runtime.process_tradingview_alert(event)
    duplicate = runtime.process_tradingview_alert(event)
    conflict = runtime.process_tradingview_alert(
        _event(processor, action="sell", event_id=event.event_id)
    )

    assert first["status"] == "completed"
    assert duplicate["status"] == "duplicate"
    assert conflict["status"] == "event_id_conflict"
    assert skill.analyze_calls == 1
    assert broker.snapshot({"AAPL": 100.0})["trade_count"] == 1


def test_concurrent_duplicate_alerts_produce_one_paper_fill(monkeypatch, tmp_path):
    runtime, broker, skill = _runtime(monkeypatch, tmp_path)
    processor = _processor()
    event = _event(processor)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(runtime.process_tradingview_alert, [event] * 8))

    assert sum(result["status"] == "completed" for result in results) == 1
    assert all(
        result["status"] in {"completed", "processing", "duplicate"}
        for result in results
    )
    assert skill.analyze_calls == 1
    assert broker.snapshot({"AAPL": 100.0})["trade_count"] == 1


def test_sell_trigger_closes_only_existing_position_through_controller(monkeypatch, tmp_path):
    runtime, broker, _skill = _runtime(monkeypatch, tmp_path)
    processor = _processor()
    assert runtime.process_tradingview_alert(_event(processor))["status"] == "completed"

    result = runtime.process_tradingview_alert(
        _event(processor, action="sell", event_id="tv-event-sell")
    )

    assert result["status"] == "completed"
    assert broker.snapshot({"AAPL": 100.0})["positions"] == {}
    assert broker.snapshot({"AAPL": 100.0})["trade_count"] == 2


@pytest.mark.parametrize("decision", ["watch", "avoid"])
def test_unapproved_ultron_analysis_rejects_buy_without_position(
    monkeypatch, tmp_path, decision
):
    runtime, broker, _skill = _runtime(
        monkeypatch, tmp_path, analysis=_analysis(decision=decision)
    )

    result = runtime.process_tradingview_alert(_event(_processor()))

    assert result["status"] == "rejected"
    assert broker.snapshot({"AAPL": 100.0})["trade_count"] == 0
    assert broker.snapshot({"AAPL": 100.0})["positions"] == {}


def test_price_divergence_rejects_alert_without_execution(monkeypatch, tmp_path):
    runtime, broker, _skill = _runtime(monkeypatch, tmp_path)

    result = runtime.process_tradingview_alert(
        _event(_processor(), price=110.0)
    )

    assert result["status"] == "rejected"
    assert result["stage"] == "price_divergence"
    assert broker.snapshot({"AAPL": 100.0})["trade_count"] == 0


def test_stale_market_data_rejects_alert_without_execution(monkeypatch, tmp_path):
    runtime, broker, _skill = _runtime(monkeypatch, tmp_path)
    runtime.controller.market_data.adapter.get_snapshot = lambda symbol: {
        "symbol": symbol,
        "price": 100.0,
        "timestamp": datetime.now(UTC) - timedelta(seconds=60),
    }

    result = runtime.process_tradingview_alert(_event(_processor()))

    assert result["status"] == "rejected"
    assert result["stage"] == "market_data"
    assert broker.snapshot({"AAPL": 100.0})["trade_count"] == 0


def test_unavailable_market_data_rejects_alert_without_execution(monkeypatch, tmp_path):
    runtime, broker, _skill = _runtime(monkeypatch, tmp_path)
    runtime.controller.market_data.adapter.get_snapshot = lambda _symbol: (_ for _ in ()).throw(
        TimeoutError("provider unavailable")
    )

    result = runtime.process_tradingview_alert(_event(_processor()))

    assert result["status"] == "rejected"
    assert result["stage"] == "market_data"
    assert broker.snapshot({"AAPL": 100.0})["trade_count"] == 0


def test_trading_pause_and_daily_loss_guard_are_preserved(monkeypatch, tmp_path):
    runtime, broker, _skill = _runtime(monkeypatch, tmp_path)
    runtime.controller.trading_control.pause("operator pause")
    paused = runtime.process_tradingview_alert(_event(_processor()))
    assert paused["status"] == "rejected"
    assert broker.snapshot({"AAPL": 100.0})["trade_count"] == 0

    runtime, broker, _skill = _runtime(monkeypatch, tmp_path / "loss")
    broker.portfolio.cash = 97_000.0
    halted = runtime.process_tradingview_alert(
        _event(_processor(), event_id="tv-event-loss")
    )
    assert halted["status"] == "rejected"
    assert broker.snapshot({"AAPL": 100.0})["trade_count"] == 0


def test_paper_broker_insufficient_funds_rejection_does_not_create_position(
    monkeypatch, tmp_path
):
    runtime, broker, _skill = _runtime(monkeypatch, tmp_path, initial_capital=1_000.0)
    broker.commission_rate = 10.0

    result = runtime.process_tradingview_alert(_event(_processor()))

    assert result["status"] == "rejected"
    assert broker.snapshot({"AAPL": 100.0})["trade_count"] == 0
    assert broker.snapshot({"AAPL": 100.0})["positions"] == {}


def test_uncertain_execution_is_not_retried_and_blocks_restart(monkeypatch, tmp_path):
    runtime, broker, skill = _runtime(monkeypatch, tmp_path)
    submit_calls = 0

    def uncertain_submit(_order):
        nonlocal submit_calls
        submit_calls += 1
        raise TimeoutError("paper broker outcome is uncertain")

    broker.submit = uncertain_submit
    processor = _processor()
    event = _event(processor)

    first = runtime.process_tradingview_alert(event)
    duplicate = runtime.process_tradingview_alert(event)

    assert first["status"] == "reconciliation_required"
    assert duplicate["status"] == "reconciliation_required"
    assert submit_calls == 1
    assert skill.analyze_calls == 1

    with pytest.raises(RuntimeError, match="Unresolved execution intent"):
        _runtime(monkeypatch, tmp_path)


def test_completed_event_and_paper_positions_recover_without_reexecution(monkeypatch, tmp_path):
    runtime, broker, _skill = _runtime(monkeypatch, tmp_path)
    processor = _processor()
    event = _event(processor)
    assert runtime.process_tradingview_alert(event)["status"] == "completed"

    recovered, recovered_broker, recovered_skill = _runtime(monkeypatch, tmp_path)
    duplicate = recovered.process_tradingview_alert(event)

    assert recovered_broker.snapshot({"AAPL": 100.0})["trade_count"] == 1
    assert duplicate["status"] == "duplicate"
    assert recovered_skill.analyze_calls == 0


def test_runtime_refuses_non_simulation_backend_and_non_paper_broker(monkeypatch, tmp_path):
    monkeypatch.setenv("ULTRON_EXECUTION_BACKEND", "alpaca_paper")
    with pytest.raises(RuntimeError, match="simulation-only"):
        AutonomousTradingRuntime(container=object(), state_path=tmp_path / "unsafe.sqlite3")

    monkeypatch.setenv("ULTRON_EXECUTION_BACKEND", "simulation")
    controller = FinancialTradingController(PaperTradingEngine(PaperBroker()))

    class OtherBroker:
        pass

    controller.trading_engine.broker = OtherBroker()
    paper_market_broker = PaperBroker()
    paper_adapter = PaperMarketAdapter(paper_market_broker, prices={"AAPL": 100.0})
    paper_adapter.connect()
    controller.market_data = MarketDataService(paper_adapter)
    container = SimpleNamespace(
        financial_skill=object(),
        financial_trading_controller=controller,
    )
    with pytest.raises(RuntimeError, match="local PaperBroker"):
        AutonomousTradingRuntime(container=container, state_path=tmp_path / "wrong-broker.sqlite3")


def test_loopback_http_endpoint_accepts_only_authenticated_json():
    processor = _processor()

    class Runtime:
        calls = 0

        def process_tradingview_alert(self, alert):
            type(self).calls += 1
            return {
                "accepted": True,
                "event_id": alert.event_id,
                "status": "completed",
                "execution_status": "filled",
            }

    server = TradingViewWebhookServer(processor, Runtime(), port=0)
    host, port = server.address
    assert host == "127.0.0.1"
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(host, port, timeout=3)
        connection.request(
            "POST",
            "/webhook/tradingview",
            body=_payload(),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        connection.close()
        assert response.status == 200
        assert json.loads(body)["execution_status"] == "filled"
        assert SECRET not in body
        assert Runtime.calls == 1

        connection = http.client.HTTPConnection(host, port, timeout=3)
        connection.request(
            "POST",
            "/webhook/tradingview",
            body=_payload(secret="incorrect-secret"),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        connection.close()
        assert response.status == 401
        assert SECRET not in body
        assert Runtime.calls == 1
    finally:
        server.close()
        thread.join(timeout=3)


def test_loopback_http_endpoint_rejects_oversized_body_before_processing():
    processor = _processor()

    class Runtime:
        calls = 0

        def process_tradingview_alert(self, _alert):
            type(self).calls += 1
            raise AssertionError("oversized request reached runtime")

    server = TradingViewWebhookServer(processor, Runtime(), port=0)
    host, port = server.address
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(host, port, timeout=3)
        connection.request(
            "POST",
            "/webhook/tradingview",
            body=b" " * (MAX_REQUEST_BYTES + 1),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        body = json.loads(response.read())
        connection.close()
        assert response.status == 413
        assert body["error"] == "request_too_large"
        assert Runtime.calls == 0
    finally:
        server.close()
        thread.join(timeout=3)