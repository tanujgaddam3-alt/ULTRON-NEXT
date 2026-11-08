import asyncio
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from ultron.application.runtime.interface_gateway import InterfaceGateway
import ultron.application.runtime.interface_gateway as gateway_module
from ultron.application.runtime.interface_gateway import VALID_OPERATION_LIFECYCLE
from ultron.application.runtime.operation_store import SQLiteOperationStore
from ultron.financial.trading.market_data.service import MarketDataService
from ultron.financial.trading.models import Order, OrderSide
from ultron.financial.trading.paper_broker import PaperBroker
from tests.interface.auth_test_utils import (
    authenticate_test_websocket,
    make_test_authenticator,
)


class DeterministicMarketService:
    def __init__(self, result):
        self.result = result

    def get_current_price(self, symbol):
        return dict(self.result)

class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send(self, message):
        self.messages.append(json.loads(message))


def make_gateway(market_service, authorizer=None):
    broker = PaperBroker(initial_capital=10_000.0, commission_rate=0.0)
    broker.submit(Order(symbol="TEST", side=OrderSide.BUY, quantity=2.0, price=100.0))
    container = SimpleNamespace(
        event_bus=SimpleNamespace(),
        runtime=SimpleNamespace(),
        executor=SimpleNamespace(),
        skills=SimpleNamespace(),
        financial_paper_broker=broker,
        financial_trading_market_service=market_service,
        financial_trading_controller=SimpleNamespace(
            price_guard=object(),
            daily_loss_guard=object(),
            trading_control=object(),
            reconciler=object(),
            idempotency=object(),
        ),
        interface_approval_authorizer=authorizer,
    )
    return InterfaceGateway(
        container,
        operation_store=SQLiteOperationStore(":memory:"),
    )


def test_gateway_trading_payload_contains_financial_simulation_telemetry(monkeypatch):
    monkeypatch.setenv("ULTRON_FINANCIAL_MARKET_PROVIDER", "alpaca")
    timestamp = datetime.now(UTC)
    gateway = make_gateway(
        DeterministicMarketService({
            "valid": True,
            "symbol": "TEST",
            "price": 101.0,
            "timestamp": timestamp,
            "provider": "alpaca",
            "age_seconds": 0.4,
        })
    )

    payload = gateway._build_trading()
    json.dumps(payload, default=str)
    financial = payload["financial"]

    assert financial["initialization"]["state"] == "READY"
    assert financial["marketData"]["state"] == "VALID"
    assert financial["marketData"]["latestPrice"] == 101.0
    assert financial["execution"]["mode"] == "simulation"
    assert financial["execution"]["liveEnabled"] is False
    assert financial["execution"]["orderSubmissionFromDashboard"] is False
    assert financial["portfolio"]["positions"][0]["symbol"] == "TEST"
    assert financial["executions"]["records"][0]["side"] == "buy"
    assert financial["risk"]["state"] == "READY"

    rendered = json.dumps(payload, default=str).lower()
    assert "api_key" not in rendered
    assert "secret_key" not in rendered
    assert "authorization" not in rendered


def test_gateway_financial_telemetry_is_honest_when_provider_unavailable(monkeypatch):
    monkeypatch.setenv("ULTRON_FINANCIAL_MARKET_PROVIDER", "alpaca")
    gateway = make_gateway(None)

    financial = gateway._build_trading()["financial"]

    assert financial["marketData"]["state"] == "UNAVAILABLE"
    assert financial["marketData"]["latestPrice"] is None
    assert financial["marketData"]["latestValidatedAt"] is None
    assert financial["marketData"]["error"]["category"] == "not_configured"
    assert financial["execution"]["mode"] == "simulation"


def test_gateway_financial_snapshot_failure_is_not_reported_as_an_empty_account(monkeypatch):
    monkeypatch.setenv("ULTRON_FINANCIAL_MARKET_PROVIDER", "alpaca")
    gateway = make_gateway(None)
    broker = gateway.container.financial_paper_broker

    def fail_snapshot(prices):
        raise RuntimeError("provider snapshot unavailable")

    broker.get_account_snapshot = fail_snapshot
    financial = gateway._build_trading()["financial"]

    assert financial["portfolio"]["state"] == "UNAVAILABLE"
    assert financial["portfolio"]["cashUsd"] is None
    assert financial["portfolio"]["equityUsd"] is None
    assert financial["portfolio"]["positions"] is None
    assert financial["portfolio"]["error"]["category"] == "snapshot_failed"


def test_gateway_trading_unknown_metrics_are_null_not_zero(monkeypatch):
    monkeypatch.setenv("ULTRON_FINANCIAL_MARKET_PROVIDER", "alpaca")
    gateway = make_gateway(None)
    gateway.container.financial_paper_broker = None

    payload = gateway._build_trading()

    assert payload["performance"]["unrealizedPnlUsd"] is None
    assert payload["performance"]["winRatePct"] is None
    assert payload["portfolio"]["totalValueUsd"] is None
    assert payload["efficiencyLoss"]["totalLossUsd"] is None
    assert payload["benchmarkComparison"]["alphaPct"] is None
    assert payload["financial"]["portfolio"]["positions"] is None


def test_gateway_position_fields_missing_from_snapshot_are_not_defaulted_to_zero(monkeypatch):
    monkeypatch.setenv("ULTRON_FINANCIAL_MARKET_PROVIDER", "alpaca")
    gateway = make_gateway(None)
    broker = gateway.container.financial_paper_broker
    broker.get_account_snapshot = lambda prices: {
        "cash": 9_800.0,
        "equity": 10_000.0,
        "positions": {"TEST": {}},
    }

    portfolio = gateway._build_trading()["financial"]["portfolio"]

    assert portfolio["state"] == "PARTIAL"
    assert portfolio["positions"] is None
    assert portfolio["error"]["category"] == "incomplete_snapshot"


def test_gateway_reuses_cached_market_snapshot_without_duplicate_requests(monkeypatch):
    monkeypatch.setenv("ULTRON_FINANCIAL_MARKET_PROVIDER", "alpaca")

    class CountingService:
        calls = 0

        def get_current_price(self, symbol):
            self.calls += 1
            return {
                "valid": True,
                "symbol": symbol,
                "price": 100.0,
                "timestamp": datetime.now(UTC),
                "provider": "alpaca",
                "age_seconds": 0.0,
            }

    service = CountingService()
    gateway = make_gateway(service)
    gateway._build_trading()
    gateway._build_trading()

    assert service.calls == 1


def test_gateway_market_snapshot_exposes_only_validated_quote_fields(monkeypatch):
    monkeypatch.setenv("ULTRON_FINANCIAL_MARKET_PROVIDER", "alpaca")
    timestamp = datetime.now(UTC)
    gateway = make_gateway(DeterministicMarketService({
        "valid": True,
        "symbol": "TEST",
        "price": 101.0,
        "timestamp": timestamp,
        "provider": "alpaca",
        "age_seconds": 0.4,
        "bid": 100.9,
        "ask": 101.1,
        "volume": "2500",
    }))

    market = gateway._financial_market_data()

    assert market["state"] == "VALID"
    assert market["freshnessLimitSeconds"] == 30.0
    assert market["bid"] == 100.9
    assert market["ask"] == 101.1
    assert market["volume"] == 2500.0


def test_gateway_market_snapshot_rejects_invalid_volume(monkeypatch):
    monkeypatch.setenv("ULTRON_FINANCIAL_MARKET_PROVIDER", "alpaca")
    gateway = make_gateway(DeterministicMarketService({
        "valid": True,
        "symbol": "TEST",
        "price": 101.0,
        "timestamp": datetime.now(UTC),
        "provider": "alpaca",
        "age_seconds": 0.4,
        "volume": float("inf"),
    }))

    assert gateway._financial_market_data()["volume"] is None


def test_gateway_cached_market_quote_expires_against_source_timestamp(monkeypatch):
    monkeypatch.setenv("ULTRON_FINANCIAL_MARKET_PROVIDER", "alpaca")
    gateway = make_gateway(None)
    gateway._financial_market_cache = {
        "state": "VALID",
        "provider": "alpaca",
        "source": "AlpacaMarketAdapter",
        "symbol": "TEST",
        "latestPrice": 100.0,
        "latestValidatedAt": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        "freshnessSeconds": 0.0,
        "freshnessLimitSeconds": 30.0,
        "bid": 99.9,
        "ask": 100.1,
        "volume": 1000.0,
        "error": None,
    }
    gateway._financial_market_cache_at = gateway_module._now_epoch()

    expired = gateway._financial_market_data()

    assert expired["state"] == "STALE"
    assert expired["error"]["category"] == "stale"


def test_gateway_withholds_financial_account_data_from_unauthenticated_clients():
    gateway = make_gateway(None)
    websocket = FakeWebSocket()

    asyncio.run(gateway._publish_channel(websocket, "trading"))

    assert websocket.messages == [{
        "channel": "trading",
        "payload": {
            "access": {
                "available": False,
                "status": "AUTHENTICATION_REQUIRED",
            }
        },
        "provenance": "LIVE",
    }]


def test_gateway_does_not_broadcast_financial_updates_to_unauthenticated_clients():
    gateway = make_gateway(None)
    websocket = FakeWebSocket()
    gateway.add_client(websocket)
    gateway._subscriptions[websocket] = {"trading"}

    asyncio.run(gateway._broadcast({
        "channel": "trading",
        "payload": {"financial": {"portfolio": {"cashUsd": 1}}},
        "provenance": "LIVE",
    }))

    assert websocket.messages == []


def test_gateway_publishes_financial_snapshot_to_authenticated_clients():
    authorizer = make_test_authenticator()
    gateway = make_gateway(None, authorizer)
    websocket = FakeWebSocket()
    authenticate_test_websocket(authorizer, websocket)

    asyncio.run(gateway._publish_channel(websocket, "trading"))

    assert websocket.messages[0]["payload"]["financial"]["execution"]["mode"] == "simulation"


def test_gateway_withholds_omega_state_from_unauthenticated_clients():
    gateway = make_gateway(None)
    websocket = FakeWebSocket()

    asyncio.run(gateway._publish_channel(websocket, "omega"))

    assert websocket.messages == [{
        "channel": "omega",
        "payload": {
            "access": {
                "available": False,
                "status": "AUTHENTICATION_REQUIRED",
            }
        },
        "provenance": "LIVE",
    }]


def test_gateway_does_not_broadcast_omega_state_to_unauthenticated_clients():
    gateway = make_gateway(None)
    websocket = FakeWebSocket()
    gateway.add_client(websocket)
    gateway._subscriptions[websocket] = {"omega"}

    asyncio.run(gateway._broadcast({
        "channel": "omega",
        "payload": {"status": {"overall_health": "READY"}},
        "provenance": "LIVE",
    }))

    assert websocket.messages == []


def test_gateway_publishes_omega_state_to_authenticated_clients():
    authorizer = make_test_authenticator()
    gateway = make_gateway(None, authorizer)
    gateway._build_omega = lambda: {
        "linkState": "CONNECTED",
        "bridgeLatencyMs": 1.0,
        "communicationTrace": [],
        "model": {"provider": "ollama"},
    }
    websocket = FakeWebSocket()
    authenticate_test_websocket(authorizer, websocket)

    asyncio.run(gateway._publish_channel(websocket, "omega"))

    assert websocket.messages[0]["payload"]["linkState"] == "CONNECTED"


def test_omega_refresh_does_not_make_requests_without_a_gateway_token(monkeypatch):
    monkeypatch.setattr(gateway_module, "OMEGA_INTEGRATION_TOKEN", "")
    gateway = make_gateway(None)
    gateway._omega_request = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("An unauthenticated OMEGA request must not be made.")
    )

    gateway._refresh_omega()

    assert gateway._omega_state is None
    assert gateway._omega_failure["detail"]["category"] == "token_not_configured"
    assert gateway._omega_last_attempt_at is None
    assert gateway._omega_latency_ms is None
    assert gateway._build_omega()["integration"]["authenticationState"] == "NOT_CONFIGURED"
    assert gateway._build_omega()["communicationTrace"] is None
    assert gateway._build_omega()["measuredAt"] is None


def test_omega_background_refresh_keeps_websocket_requests_responsive_and_never_overlaps():
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    calls = []

    gateway = make_gateway(None)

    def slow_refresh():
        calls.append(threading.get_ident())
        started.set()
        release.wait(timeout=2)
        finished.set()

    gateway._refresh_omega = slow_refresh

    async def exercise():
        gateway._schedule_omega_refresh()
        assert await asyncio.to_thread(started.wait, 1)

        websocket = FakeWebSocket()
        before = time.monotonic()
        await gateway.handle(websocket, {"action": "PING", "correlationId": "live"})
        assert time.monotonic() - before < 0.1
        assert websocket.messages[-1]["type"] == "PONG"

        gateway._schedule_omega_refresh()
        assert len(calls) == 1
        release.set()
        assert await asyncio.to_thread(finished.wait, 1)
        await asyncio.sleep(0)
        assert gateway._omega_refresh_task is None

    try:
        asyncio.run(exercise())
    finally:
        release.set()
        gateway.operation_store.close()


def test_authenticated_omega_subscription_schedules_poll_without_blocking():
    authorizer = make_test_authenticator()
    gateway = make_gateway(None, authorizer)
    websocket = FakeWebSocket()
    authenticate_test_websocket(authorizer, websocket)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def slow_refresh():
        started.set()
        release.wait(timeout=2)
        finished.set()

    gateway._refresh_omega = slow_refresh

    async def exercise():
        before = time.monotonic()
        await gateway._handle_subscribe(
            websocket,
            {"channels": ["omega"], "correlationId": "subscribe"},
        )
        assert time.monotonic() - before < 0.1
        assert await asyncio.to_thread(started.wait, 1)
        assert gateway._omega_refresh_task is not None
        release.set()
        assert await asyncio.to_thread(finished.wait, 1)
        for _ in range(10):
            if gateway._omega_refresh_task is None:
                break
            await asyncio.sleep(0.01)
        assert gateway._omega_refresh_task is None

    try:
        asyncio.run(exercise())
    finally:
        release.set()
        gateway.operation_store.close()


def test_omega_refresh_timeout_and_exception_do_not_stop_heartbeat(
    monkeypatch,
    caplog,
):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    gateway = make_gateway(None)
    gateway.runtime.state = SimpleNamespace(cycle_count=0, running=True)
    monkeypatch.setattr(gateway_module, "HEARTBEAT_INTERVAL", 0.01)
    monkeypatch.setattr(gateway_module, "WORLD_REFRESH_INTERVAL", 3600.0)
    monkeypatch.setattr(gateway_module, "OMEGA_REFRESH_TIMEOUT", 0.01)

    def slow_refresh():
        started.set()
        release.wait(timeout=2)
        finished.set()

    gateway._refresh_omega = slow_refresh

    async def exercise():
        heartbeat = asyncio.create_task(gateway._heartbeat())
        gateway._schedule_omega_refresh()
        assert await asyncio.to_thread(started.wait, 1)
        await asyncio.sleep(0.04)
        assert heartbeat.done() is False
        assert "component=omega" in caplog.text
        assert "timed out" in caplog.text

        gateway._schedule_omega_refresh()
        release.set()
        assert await asyncio.to_thread(finished.wait, 1)
        await asyncio.sleep(0)
        gateway._refresh_omega = lambda: (_ for _ in ()).throw(RuntimeError("private"))
        gateway._schedule_omega_refresh()
        await asyncio.sleep(0.02)
        assert heartbeat.done() is False
        assert "component=omega exception_type=RuntimeError" in caplog.text
        heartbeat.cancel()
        with pytest.raises(asyncio.CancelledError):
            await heartbeat

    try:
        asyncio.run(exercise())
    finally:
        release.set()
        gateway.operation_store.close()


def test_omega_failed_refresh_marks_cached_state_degraded(monkeypatch):
    monkeypatch.setattr(gateway_module, "OMEGA_INTEGRATION_TOKEN", "test-token")
    gateway = make_gateway(None)
    previous_state = {
        "peer": "OMEGA",
        "bridgeLatencyMs": 2.0,
        "communicationTrace": [],
        "model": {"provider": "ollama"},
    }
    gateway._omega_state = previous_state
    gateway._omega_request = lambda *args, **kwargs: (
        False,
        {"status_code": 401, "detail": "unauthorized"},
        3.0,
    )

    gateway._refresh_omega()

    payload = gateway._build_omega()
    assert gateway._omega_state is previous_state
    assert payload["linkState"] == "DEGRADED"
    assert payload["integration"]["authenticationState"] == "REJECTED"
    assert payload["integration"]["lastSuccessfulExchangeAt"] is None
    assert payload["integration"]["failure"]["detail"]["statusCode"] == 401


def test_omega_refresh_bridges_capabilities_and_records_sanitized_http_exchanges(monkeypatch):
    monkeypatch.setattr(gateway_module, "OMEGA_INTEGRATION_TOKEN", "test-token")
    gateway = make_gateway(None)
    responses = iter([
        (True, {
            "peer": "OMEGA",
            "bridgeLatencyMs": 2.0,
            "communicationTrace": [{"verb": "status"}],
            "model": {"provider": "ollama"},
        }, 2.0),
        (True, {
            "total": 1,
            "capabilities": [{"name": "read_only_state", "enabled": True}],
            "provenance": "OMEGA_SERVICE",
            "error": None,
        }, 1.0),
    ])
    gateway._omega_request = lambda *args, **kwargs: next(responses)

    gateway._refresh_omega()

    payload = gateway._build_omega()
    assert payload["capabilities"]["available"] is True
    assert payload["communicationTrace"] == [{"verb": "status"}]
    assert payload["capabilities"]["total"] == 1
    assert payload["capabilities"]["capabilities"] == [
        {"name": "read_only_state", "enabled": True}
    ]
    exchanges = payload["integration"]["exchangeTrace"]
    assert [exchange["endpoint"] for exchange in exchanges] == [
        "/capabilities",
        "/state",
    ]
    assert all(exchange["direction"] == "ULTRON_GATEWAY -> OMEGA_INTEGRATION_SERVICE" for exchange in exchanges)
    assert all(exchange["responseStatus"] == 200 for exchange in exchanges)
    serialized = json.dumps(payload)
    assert "test-token" not in serialized
    assert "authorization" not in serialized.lower()
    assert "response body" not in serialized.lower()


def test_omega_command_trace_is_recorded_separately_from_omega_communication_trace(monkeypatch):
    monkeypatch.setattr(gateway_module, "OMEGA_INTEGRATION_TOKEN", "test-token")
    gateway = make_gateway(None)
    original_trace = [{"verb": "OMEGA-reported event"}]
    gateway._omega_state = {
        "peer": "OMEGA",
        "communicationTrace": original_trace,
        "model": {"provider": "ollama"},
    }
    gateway._omega_request = lambda *args, **kwargs: (
        True,
        {"success": True, "result": {"status": "ready"}},
        4.0,
    )
    websocket = FakeWebSocket()

    asyncio.run(gateway._dispatch_omega(
        websocket,
        "correlation-omega",
        "monitor",
        {},
        "fingerprint",
    ))

    payload = gateway._build_omega()
    assert payload["communicationTrace"] == original_trace
    assert payload["bridgeLatencyMs"] is None
    exchange = payload["integration"]["exchangeTrace"][0]
    assert exchange["method"] == "POST"
    assert exchange["endpoint"] == "/command"
    assert exchange["success"] is True
    assert exchange["responseValid"] is True
    assert len(websocket.messages) == 1


def test_vital_matrix_marks_uninstrumented_and_failed_metrics_unavailable(monkeypatch):
    gateway = make_gateway(None)
    gateway.runtime = SimpleNamespace(state=SimpleNamespace(
        cycle_count=0,
        running=False,
        last_cycle=None,
    ))
    gateway._build_world = lambda: {
        "system": {"os": None, "cpu_usage": None, "memory_usage": None}
    }
    monkeypatch.setattr(gateway_module, "OMEGA_INTEGRATION_TOKEN", "")

    payload = gateway._build_vital_matrix()

    assert payload["health"]["primaryValue"] == "NOT ASSESSED"
    assert payload["anomalies"]["primaryValue"] == "NOT INSTRUMENTED"
    assert payload["anomalies"]["count"] is None
    assert payload["agents"]["primaryValue"] == "UNAVAILABLE"
    assert payload["capability"]["primaryValue"] == "UNAVAILABLE"
    assert payload["performance"]["primaryValue"] == "UNAVAILABLE"
    assert payload["performance"]["avgCycleMs"] is None


def test_cognitive_state_does_not_invent_an_empty_agent_registry():
    gateway = make_gateway(None)
    gateway.runtime = SimpleNamespace(state=SimpleNamespace(
        cycle_count=0,
        running=False,
        last_cycle=None,
    ))

    payload = gateway._build_cognitive_state()

    assert payload["agents"] is None
    assert payload["agentsError"].startswith("Agent registry unavailable")


def test_device_snapshot_uses_null_for_unavailable_host_metrics(monkeypatch):
    import builtins
    import shutil

    gateway = make_gateway(None)
    original_open = builtins.open

    def unavailable_proc(path, *args, **kwargs):
        if path in {"/proc/loadavg", "/proc/meminfo"}:
            raise OSError("host telemetry unavailable")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", unavailable_proc)
    monkeypatch.setattr(gateway_module.os, "cpu_count", lambda: None)
    monkeypatch.setattr(shutil, "disk_usage", lambda path: (_ for _ in ()).throw(
        OSError("disk telemetry unavailable")
    ))

    payload = gateway._build_device()

    assert payload["assessment"]["status"] == "NOT_ASSESSED"
    assert payload["hardware"]["cpu"]["cores"] is None
    assert payload["hardware"]["cpu"]["load1"] is None
    assert payload["hardware"]["memory"]["totalGb"] is None
    assert payload["hardware"]["memory"]["usedPct"] is None
    assert payload["hardware"]["storage"]["totalGb"] is None
    assert payload["capabilities"] is None


def test_omega_capabilities_failure_is_reported_without_losing_state(monkeypatch):
    monkeypatch.setattr(gateway_module, "OMEGA_INTEGRATION_TOKEN", "test-token")
    gateway = make_gateway(None)
    responses = iter([
        (True, {
            "peer": "OMEGA",
            "bridgeLatencyMs": 2.0,
            "communicationTrace": [],
            "model": {"provider": "ollama"},
        }, 2.0),
        (False, {"status_code": 503, "detail": "private response body"}, 1.0),
    ])
    gateway._omega_request = lambda *args, **kwargs: next(responses)

    gateway._refresh_omega()

    payload = gateway._build_omega()
    assert payload["linkState"] == "CONNECTED"
    assert payload["capabilities"]["available"] is False
    assert payload["capabilities"]["error"]["category"] == "service_error"
    assert "private response body" not in json.dumps(payload)


def test_gateway_accepts_only_contract_operation_lifecycle_states():
    gateway = make_gateway(None)

    for state in VALID_OPERATION_LIFECYCLE:
        if state == "VERIFIED":
            continue
        operation: dict[str, Any] = {
            "operationId": state,
            "title": "test",
            "intent": "test",
            "expectedEffect": "test",
            "lifecycleState": state,
        }
        if state in {"COMPLETED", "VERIFIED", "UNVERIFIED", "UNCERTAIN"}:
            operation["executionResult"] = {"success": state != "UNCERTAIN"}
        gateway._record_operation(operation)

    verified = {
        "operationId": "verified-operation",
        "requestFingerprint": "fingerprint",
        "lifecycleState": "VERIFIED",
        "executionResult": {"success": True},
        "verificationAudit": {
            "verified": True,
            "outcome": "CONFIRMED_SUCCESS",
            "operationId": "verified-operation",
            "requestFingerprint": "fingerprint",
            "verifier": "test-independent-verifier",
            "provenance": "ISOLATED_TEST",
            "observedAt": datetime.now(UTC).isoformat(),
        },
    }
    gateway._validate_operation_evidence(verified)

    try:
        gateway._record_operation({"lifecycleState": "completed"})
        assert False, "Unknown lifecycle state was emitted."
    except ValueError as exc:
        assert "refusing" in str(exc).lower()


def test_gateway_rejects_completion_without_success_evidence():
    gateway = make_gateway(None)
    operation = {
        "operationId": "test",
        "lifecycleState": "SUGGESTED",
        "title": "test",
        "intent": "test",
        "expectedEffect": "test",
    }
    gateway._record_operation(operation)

    try:
        gateway._op_update(operation, lifecycleState="COMPLETED")
        assert False, "Operation completed without execution evidence."
    except ValueError as exc:
        assert "successful execution evidence" in str(exc)

    assert operation["lifecycleState"] == "SUGGESTED"


def test_gateway_rejects_verification_without_verification_audit():
    gateway = make_gateway(None)

    try:
        gateway._record_operation({
            "operationId": "test",
            "lifecycleState": "VERIFIED",
            "executionResult": {"success": True},
        })
        assert False, "Operation was verified without a verification audit."
    except ValueError as exc:
        assert "verification evidence" in str(exc)
