from __future__ import annotations

import asyncio
import asyncio
import base64
import binascii
import copy
import errno
import hashlib
import ipaddress
import json
import logging
import math
import os
import re
import sqlite3
import stat
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import UTC, datetime
from io import BytesIO
from math import isfinite
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from ultron.domain.event import Event  # noqa: F401  (shared domain event contract)
from ultron.application.runtime.events import RuntimeEvent
from ultron.application.runtime.operation_store import SQLiteOperationStore
from ultron.application.runtime.system_intelligence import (
    SystemIntelligence,
    request_helper_restart,
)
from ultron.application.runtime.operation_verification import (
    LocalReadOnlyOperationVerifier,
    valid_verification_evidence,
)
from ultron.application.content.status import (
    ContentStatusService,
    create_content_runtime,
    resolve_content_database_path,
)
from ultron.application.content.store import ContentSchemaVersionMismatch
from ultron.application.content.script_preview import OriginalScriptPreviewService
from ultron.application.content.intelligence_service import ContentIntelligenceService
from ultron.application.content.rendering import ContentClipRenderingService
from ultron.application.content.limits import MAX_SOURCE_BYTES
from ultron.application.content.script_scenes import SceneVisualRenderError
from ultron.domain.content import (
    AcquisitionMethod,
    AssetCategory,
    ContentEditProject,
    ContentEditSegment,
    ContentPlatform,
    ContentTargetContext,
    EvidenceDecision,
    GrantorAuthority,
    GrantorIdentity,
    OwnershipClaim,
    RightsAction,
    RightsAgreement,
    RightsEvidence,
    RightsGrant,
    RightsTerms,
    RightsUse,
    SourceAsset,
    SourceProvenance,
)
from ultron.domain.content_intelligence import (
    HighlightConfig,
    TranscriptionConfig,
)
from ultron.application.task_service import TaskService
from ultron.domain.plan import Plan, PlanStep
from ultron.execution.executor import Executor
from ultron.executive.tasks.sqlite_queue import SQLiteTaskQueue
from ultron.security.identity.authorization import (
    AuthorizationContext,
    resource_fingerprint,
)
from ultron.security.identity.authenticator import WebSocketAuthenticator

PROVENANCE = "LIVE"
ENDPOINT_PATH = "/ultron/stream"
MAX_CHAT_MESSAGE_CHARS = 12_000
logger = logging.getLogger(__name__)
VALID_OPERATION_LIFECYCLE = frozenset({
    "SUGGESTED", "PLANNED", "APPROVED", "EXECUTING",
    "COMPLETED", "VERIFIED", "UNVERIFIED", "UNCERTAIN",
    "FAILED", "CANCELLED",
})

# ---------------------------------------------------------------------------
# Real integration wiring (all environment-driven; absent config degrades the
# OMEGA link honestly instead of fabricating state).
# ---------------------------------------------------------------------------
OMEGA_INTEGRATION_BASE = os.environ.get(
    "OMEGA_INTEGRATION_URL",
    "http://127.0.0.1:8090/omega/integration",
)
OMEGA_INTEGRATION_TOKEN = os.environ.get("OMEGA_INTEGRATION_TOKEN", "")
OMEGA_POLL_INTERVAL = float(os.environ.get("OMEGA_POLL_INTERVAL", "8.0"))
HEARTBEAT_INTERVAL = float(os.environ.get("ULTRON_HEARTBEAT_INTERVAL", "5.0"))
WORLD_REFRESH_INTERVAL = float(os.environ.get("ULTRON_WORLD_REFRESH_INTERVAL", "15.0"))
SERVICE_TIMEOUT = 12.0
CHAT_MODEL_TIMEOUT_SECONDS = 125.0
MAX_RECENT_EVENTS = 60
MAX_RECENT_OPERATIONS = 40
TASK_EXPECTED_EFFECT = (
    "Run the approved read-only local inspection steps and verify each result."
)

# Real network-surface verbs forwarded to the real OMEGA integration service.
_OMEGA_VERBS = {
    "monitor": "status",
    "research": "research",
    "analyze": "analyze",
    "investigate": "research",
    "search": "research",
}

# Verbs executed through the real cognitive pipeline, which can execute plans.
_ULTRON_VERBS = frozenset({"ask", "plan", "debug"})

# Real verb -> real skill action mappings used after an operation is approved.
_EXECUTION_MAPPINGS = {
    "execute": ("run_command", ("subject", "command")),
    "execute_task": ("run_task", ("goal", "steps")),
}

# Only actions with a concrete dispatch mapping can be proposed for execution.
_CONSEQUENTIAL_VERBS = frozenset(_EXECUTION_MAPPINGS)

_SUPPORTED_ACTIONS = [
    {
        "actionVerb": "monitor",
        "scope": "OMEGA",
        "approval": "none",
        "description": "Read real OMEGA status through the OMEGA integration service.",
    },
    {
        "actionVerb": "research",
        "scope": "OMEGA",
        "approval": "none",
        "description": "Read the real OMEGA evidence ledger.",
    },
    {
        "actionVerb": "analyze",
        "scope": "OMEGA",
        "approval": "none",
        "description": "Read the real OMEGA environment profile.",
    },
    {
        "actionVerb": "execute",
        "scope": "ULTRON",
        "approval": "required",
        "description": "Execute a real terminal command through the terminal skill "
                      "after explicit operator approval.",
    },
    {
        "actionVerb": "execute_task",
        "scope": "ULTRON",
        "approval": "required",
        "description": "Execute a bounded read-only multi-step task after explicit operator approval.",
    },
]


def _utc() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _now_epoch() -> float:
    return time.time()


class InterfaceGateway:
    """
    Production WebSocket boundary for the ULTRON interface.

    This gateway publishes the full combined dashboard stream (all eleven
    channels) from REAL backend state and executes commands through the real
    ULTRON Executor / cognitive pipeline or the real OMEGA integration
    service.  No synthetic state is produced here; every payload is derived
    from a real subsystem measurement, and the OMEGA link is marked honestly
    when the integration service is not configured or unreachable.
    """

    def __init__(
        self,
        container,
        operation_store: SQLiteOperationStore | None = None,
    ):
        self.container = container
        self.event_bus = container.event_bus
        self.runtime = container.runtime
        self.executor = container.executor
        self.skills = container.skills
        configured_authorizer = getattr(
            container,
            "interface_approval_authorizer",
            None,
        )
        if (
            configured_authorizer is not None
            and type(configured_authorizer) is not WebSocketAuthenticator
        ):
            raise TypeError(
                "Interface approvals require the configured "
                "WebSocketAuthenticator trust authority."
            )
        self.approval_authorizer: WebSocketAuthenticator | None = (
            configured_authorizer
        )
        self.cre_status_service = getattr(container, "cre_status_service", None)
        self.cre_unavailable_reason = getattr(
            container,
            "cre_unavailable_reason",
            None,
        )
        if self.cre_status_service is not None and type(
            self.cre_status_service
        ) is not ContentStatusService:
            raise TypeError("CRE status requires the configured read-only status service.")
        self.cre_script_preview_service = getattr(
            container,
            "cre_script_preview_service",
            None,
        )
        if self.cre_script_preview_service is not None and type(
            self.cre_script_preview_service
        ) is not OriginalScriptPreviewService:
            raise TypeError(
                "CRE script previews require the configured local preview service."
            )
        self.cre_intelligence_service = getattr(
            container,
            "cre_intelligence_service",
            None,
        )
        if self.cre_intelligence_service is not None and type(
            self.cre_intelligence_service
        ) is not ContentIntelligenceService:
            raise TypeError("CRE transcription requires the configured intelligence service.")
        self.cre_clip_rendering_service = getattr(
            container,
            "cre_clip_rendering_service",
            None,
        )
        self.cre_import_directory = (
            Path.home().resolve(strict=True) / "Videos" / "CRE_Import"
        )
        if self.cre_clip_rendering_service is not None and type(
            self.cre_clip_rendering_service
        ) is not ContentClipRenderingService:
            raise TypeError("CRE clips require the configured source-footage renderer.")
        self.operation_verifier = getattr(
            container,
            "interface_operation_verifier",
            LocalReadOnlyOperationVerifier(),
        )
        configured_operation_store = getattr(
            container,
            "execution_audit_store",
            None,
        )
        if (
            configured_operation_store is not None
            and type(configured_operation_store) is not SQLiteOperationStore
        ):
            raise TypeError(
                "Gateway operation storage requires the configured SQLite operation store."
            )
        self.operation_store = (
            operation_store
            or configured_operation_store
            or SQLiteOperationStore()
        )
        configured_intelligence = getattr(container, "system_intelligence", None)
        if configured_intelligence is not None and type(configured_intelligence) is not SystemIntelligence:
            raise TypeError("System intelligence requires the configured read-only collector.")
        self.system_intelligence = configured_intelligence or SystemIntelligence()
        configured_task_service = getattr(container, "task_service", None)
        self.task_service = (
            configured_task_service
            if isinstance(configured_task_service, TaskService)
            else None
        )
        if self.task_service is not None:
            self.task_service.operation_store = self.operation_store
            self.task_service.task_manager.operation_store = self.operation_store
        if (
            self.approval_authorizer is not None
            and isinstance(self.executor, Executor)
        ):
            self.executor.configure_trusted_authorization(
                self.approval_authorizer,
                self.operation_store,
            )
        if self.task_service is not None:
            task_queue = self.task_service.task_manager.queue
            if (
                not isinstance(task_queue, SQLiteTaskQueue)
                or task_queue.database_identity == ":memory:"
                or task_queue.database_identity
                != self.operation_store.database_identity
            ):
                raise RuntimeError(
                    "Gateway task recovery requires a durable task queue "
                    "sharing the operation database."
                )
        self.operation_store.recover_gateway_state()

        self._clients: set[Any] = set()
        self._subscriptions: dict[Any, set[str]] = {}
        self._chat_session_locks: dict[str, asyncio.Lock] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribed = False
        self._started_at = _now_epoch()
        self._cycle_durations: deque[float] = deque(maxlen=40)
        self._recent_events: deque[dict[str, Any]] = deque(maxlen=MAX_RECENT_EVENTS)
        self._operations: deque[dict[str, Any]] = deque(
            self.operation_store.recent(MAX_RECENT_OPERATIONS),
            maxlen=MAX_RECENT_OPERATIONS,
        )
        self._omega_state: dict[str, Any] | None = None
        self._omega_capabilities: dict[str, Any] | None = None
        self._omega_failure: dict[str, Any] | None = None
        self._omega_latency_ms: float | None = None
        self._omega_last_poll: float = 0.0
        self._omega_capabilities_last_poll: float = 0.0
        self._omega_last_attempt_at: str | None = None
        self._omega_last_success_at: str | None = None
        self._omega_exchange_trace: deque[dict[str, Any]] = deque(maxlen=24)
        self._last_world_refresh: float = 0.0
        self._heartbeat_task: asyncio.Task | None = None
        self._financial_market_cache: dict[str, Any] | None = None
        self._financial_market_cache_at: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._subscribed:
            return

        self._loop = asyncio.get_running_loop()
        self.event_bus.subscribe("*", self._on_event)
        self._subscribed = True
        self._heartbeat_task = self._loop.create_task(self._heartbeat())

    def stop(self) -> None:
        if not self._subscribed:
            return

        self.event_bus.unsubscribe("*", self._on_event)
        self._subscribed = False
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        self._loop = None

    # ------------------------------------------------------------------
    # Clients
    # ------------------------------------------------------------------

    def add_client(self, websocket: Any) -> None:
        self._clients.add(websocket)
        self._subscriptions.setdefault(websocket, set())

    def remove_client(self, websocket: Any) -> None:
        self._clients.discard(websocket)
        self._subscriptions.pop(websocket, None)
        if isinstance(self.approval_authorizer, WebSocketAuthenticator):
            identity = self.approval_authorizer.identity_for(websocket)
            if identity is not None:
                self._chat_session_locks.pop(identity["sessionId"], None)
        revoke = getattr(self.approval_authorizer, "revoke", None)
        if callable(revoke):
            revoke(websocket)

    async def _send(self, websocket: Any, payload: dict[str, Any]) -> None:
        await websocket.send(json.dumps(payload, default=str))

    def _is_authenticated_connection(self, websocket: Any) -> bool:
        authenticate = getattr(
            self.approval_authorizer,
            "is_authenticated",
            None,
        )
        return bool(callable(authenticate) and authenticate(websocket))

    async def _broadcast(self, payload: dict[str, Any]) -> None:
        if not self._clients:
            return

        channel = payload.get("channel")
        message = json.dumps(payload, default=str)

        clients = tuple(
            client
            for client in self._clients
            if channel in self._subscriptions.get(client, set())
            and (
                channel not in {"operation", "command", "trading", "omega"}
                or self._is_authenticated_connection(client)
            )
        )

        if not clients:
            return

        results = await asyncio.gather(
            *(client.send(message) for client in clients),
            return_exceptions=True,
        )

        for client, result in zip(clients, results):
            if isinstance(result, Exception):
                self.remove_client(client)

    # ------------------------------------------------------------------
    # Heartbeat (real facts, broadcast on runtimeEvents)
    # ------------------------------------------------------------------

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            try:
                state = self.runtime.state
                facts = {
                    "eventId": str(uuid4()),
                    "type": "heartbeat",
                    "summary": (
                        f"runtime cycle_count={state.cycle_count} "
                        f"running={state.running} uptime={round(_now_epoch() - self._started_at, 1)}s"
                    ),
                    "provenance": PROVENANCE,
                    "source": "interface-gateway",
                    "domain": "runtime",
                    "severity": "INFO",
                    "at": _utc(),
                    "cycleCount": state.cycle_count,
                    "uptimeSeconds": round(_now_epoch() - self._started_at, 1),
                }
                self._recent_events.appendleft(facts)
                await self._broadcast(
                    {
                        "channel": "runtimeEvents",
                        "payload": [facts],
                        "provenance": PROVENANCE,
                    }
                )

                if (
                    OMEGA_INTEGRATION_TOKEN
                    and _now_epoch() - self._omega_last_poll >= OMEGA_POLL_INTERVAL
                ):
                    self._refresh_omega()

                if _now_epoch() - self._last_world_refresh >= WORLD_REFRESH_INTERVAL:
                    await self._refresh_world()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive
                await self._broadcast(
                    {
                        "channel": "runtimeEvents",
                        "payload": [
                            {
                                "eventId": str(uuid4()),
                                "type": "heartbeat",
                                "summary": f"heartbeat error: {exc}",
                                "provenance": PROVENANCE,
                                "source": "interface-gateway",
                                "domain": "runtime",
                                "severity": "ERROR",
                                "at": _utc(),
                            }
                        ],
                        "provenance": PROVENANCE,
                    }
                )

    async def _refresh_world(self) -> None:
        try:
            world = self.container.world.refresh()
            payload = self.container.world.summary()
            payload["provenance"] = PROVENANCE
            self._last_world_refresh = _now_epoch()
            await self._broadcast(
                {"channel": "world", "payload": payload, "provenance": PROVENANCE}
            )
        except Exception:  # pragma: no cover - environment dependent
            self._last_world_refresh = _now_epoch()

    # ------------------------------------------------------------------
    # OMEGA integration client (real service, real measurements)
    # ------------------------------------------------------------------

    def _omega_request(self, method: str, path: str, body: dict[str, Any] | None = None
                       ) -> tuple[bool, dict[str, Any] | None, float]:
        started = _now_epoch()
        url = f"{OMEGA_INTEGRATION_BASE}{path}"
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if OMEGA_INTEGRATION_TOKEN:
            headers["X-Omega-Token"] = OMEGA_INTEGRATION_TOKEN
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=SERVICE_TIMEOUT) as response:
                raw = response.read().decode("utf-8")
                latency_ms = round((_now_epoch() - started) * 1000.0, 1)
                if not raw:
                    return True, {"output": ""}, latency_ms
                try:
                    return True, json.loads(raw), latency_ms
                except json.JSONDecodeError:
                    return True, {"output": raw[:400]}, latency_ms
        except urllib.error.HTTPError as exc:
            latency_ms = round((_now_epoch() - started) * 1000.0, 1)
            detail = ""
            try:
                detail = exc.read().decode("utf-8")[:300]
            except Exception:  # pragma: no cover - defensive
                detail = str(exc)
            return False, {"status_code": exc.code, "detail": detail}, latency_ms
        except Exception as exc:
            latency_ms = round((_now_epoch() - started) * 1000.0, 1)
            return False, {"error": f"{type(exc).__name__}: {exc}"}, latency_ms

    def _refresh_omega(self) -> None:
        self._omega_last_poll = _now_epoch()
        if not OMEGA_INTEGRATION_TOKEN:
            self._omega_last_attempt_at = None
            self._omega_latency_ms = None
            self._omega_failure = {
                "detail": {
                    "category": "token_not_configured",
                    "message": "OMEGA_INTEGRATION_TOKEN is not configured for this gateway.",
                },
            }
            return

        self._omega_last_attempt_at = _utc()
        ok, payload, latency_ms = self._omega_request("GET", "/state")
        valid_state = ok and isinstance(payload, dict)
        self._record_omega_exchange(
            "/state", ok=ok, valid=valid_state, payload=payload, latency_ms=latency_ms
        )
        self._omega_latency_ms = latency_ms
        if valid_state:
            self._omega_state = payload
            self._omega_failure = None
            self._omega_last_success_at = self._omega_last_attempt_at
        else:
            self._omega_failure = {
                "at": self._omega_last_attempt_at,
                "latencyMs": latency_ms,
                "detail": self._omega_safe_failure(payload),
            }

        if (
            valid_state
            and _now_epoch() - self._omega_capabilities_last_poll >= 60.0
        ):
            self._omega_capabilities_last_poll = _now_epoch()
            capabilities_ok, capabilities_payload, capabilities_latency = (
                self._omega_request("GET", "/capabilities")
            )
            valid_capabilities = (
                capabilities_ok
                and isinstance(capabilities_payload, dict)
                and isinstance(capabilities_payload.get("capabilities"), list)
                and isinstance(capabilities_payload.get("total"), int)
                and capabilities_payload.get("error") is None
            )
            self._record_omega_exchange(
                "/capabilities",
                ok=capabilities_ok,
                valid=valid_capabilities,
                payload=capabilities_payload,
                latency_ms=capabilities_latency,
            )
            self._omega_capabilities = (
                {
                    "available": True,
                    "provenance": capabilities_payload.get("provenance"),
                    "total": capabilities_payload["total"],
                    "capabilities": capabilities_payload["capabilities"],
                    "lastUpdatedAt": _utc(),
                }
                if valid_capabilities
                else {
                    "available": False,
                    "error": self._omega_safe_failure(capabilities_payload),
                    "lastAttemptAt": _utc(),
                }
            )
            if valid_capabilities:
                self._omega_last_success_at = _utc()

    @staticmethod
    def _omega_safe_failure(payload: dict[str, Any] | None) -> dict[str, Any]:
        response_code = payload.get("status_code") if isinstance(payload, dict) else None
        if response_code == 401:
            return {
                "category": "authentication_rejected",
                "statusCode": response_code,
                "message": "OMEGA rejected the configured integration credential.",
            }
        if response_code == 403:
            return {
                "category": "authorization_rejected",
                "statusCode": response_code,
                "message": "OMEGA refused the integration request.",
            }
        if response_code == 404:
            return {
                "category": "endpoint_unavailable",
                "statusCode": response_code,
                "message": "The OMEGA integration endpoint is unavailable.",
            }
        return {
            "category": "service_unreachable" if response_code is None else "service_error",
            "statusCode": response_code,
            "message": "The OMEGA integration request failed.",
        }

    def _record_omega_exchange(
        self,
        endpoint: str,
        *,
        ok: bool,
        valid: bool,
        payload: dict[str, Any] | None,
        latency_ms: float,
        method: str = "GET",
    ) -> None:
        response_code = (
            payload.get("status_code")
            if isinstance(payload, dict) and isinstance(payload.get("status_code"), int)
            else 200 if ok else None
        )
        self._omega_exchange_trace.appendleft({
            "direction": "ULTRON_GATEWAY -> OMEGA_INTEGRATION_SERVICE",
            "method": method,
            "endpoint": endpoint,
            "at": _utc(),
            "latencyMs": latency_ms,
            "responseStatus": response_code,
            "success": ok,
            "responseValid": valid,
            "error": None if ok and valid else self._omega_safe_failure(payload),
        })

    # ------------------------------------------------------------------
    # Runtime events
    # ------------------------------------------------------------------

    def _on_event(self, event: Any) -> None:
        if self._loop is None:
            return

        normalized = self._normalize_event(event)
        self._recent_events.appendleft(normalized)

        messages = [
            {
                "channel": "runtimeEvents",
                "payload": [normalized],
                "provenance": PROVENANCE,
            }
        ]

        event_type = getattr(event, "type", None)
        if hasattr(event_type, "value"):
            event_type = event_type.value

        payload = getattr(event, "payload", None)

        if event_type == "system" and isinstance(payload, dict):
            if isinstance(payload.get("cognitiveState"), dict):
                cognitive_state = dict(payload["cognitiveState"])
                cognitive_state["provenance"] = PROVENANCE
                messages.append(
                    {
                        "channel": "cognitiveState",
                        "payload": cognitive_state,
                        "provenance": PROVENANCE,
                    }
                )

            if isinstance(payload.get("world"), dict):
                world = dict(payload["world"])
                world["provenance"] = PROVENANCE
                messages.append(
                    {
                        "channel": "world",
                        "payload": world,
                        "provenance": PROVENANCE,
                    }
                )

        for message in messages:
            asyncio.run_coroutine_threadsafe(
                self._broadcast(message),
                self._loop,
            )

    def _normalize_event(self, event: Any) -> dict[str, Any]:
        """
        Convert a real EventBus event into the authoritative frontend
        RuntimeEvent contract, retaining the real domain, timestamp and
        severity of the originating event.
        """

        event_type = getattr(event, "type", None)

        if hasattr(event_type, "value"):
            event_type = event_type.value
        elif event_type is not None:
            event_type = str(event_type)

        if not event_type:
            event_type = event.__class__.__name__

        metadata = getattr(event, "metadata", None)
        source = getattr(event, "source", None)

        if source is None and isinstance(metadata, dict):
            source = metadata.get("source")

        payload = getattr(event, "payload", None)

        if payload is None:
            payload = {}

            if hasattr(event, "concept"):
                payload["concept"] = event.concept

            if hasattr(event, "description"):
                payload["description"] = event.description

            if hasattr(event, "project_name"):
                payload["project_name"] = event.project_name

            if hasattr(event, "data"):
                payload["data"] = event.data

            if isinstance(metadata, dict):
                payload["metadata"] = metadata

        if isinstance(payload, dict):
            if event_type == "system":
                decision = payload.get("decision", {})
                execution = payload.get("execution", {})

                summary = (
                    f"Cognitive cycle {payload.get('cycleCount', '?')}: "
                    f"decision={decision.get('action', 'unknown')}, "
                    f"execution={'success' if execution.get('success') else 'not successful'}."
                )
            elif event_type == "learning":
                summary = payload.get("description") or "Learning event observed."
            else:
                summary = (
                    payload.get("description")
                    or payload.get("message")
                    or f"{event_type} event observed."
                )
        else:
            summary = (
                str(payload)
                if payload is not None
                else f"{event_type} event observed."
            )

        domain = "system"
        if event_type == "learning":
            domain = "learning"
        elif event_type == "heartbeat":
            domain = "runtime"

        severity = "INFO"
        if event_type == "error":
            severity = "ERROR"
        elif event_type == "learning":
            severity = "INFO"

        return {
            "eventId": str(uuid4()),
            "type": str(event_type),
            "summary": summary,
            "provenance": PROVENANCE,
            "source": str(source) if source is not None else "event-bus",
            "domain": domain,
            "severity": severity,
            "timestamp": getattr(event, "timestamp", None) or _utc(),
            "at": _utc(),
        }

    # ------------------------------------------------------------------
    # Protocol
    # ------------------------------------------------------------------

    async def handle(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        action = message.get("action")

        if action == "AUTHENTICATE":
            await self._handle_authenticate(websocket, message)
            return

        if isinstance(self.approval_authorizer, WebSocketAuthenticator):
            if not self._is_authenticated_connection(websocket):
                await self._send(
                    websocket,
                    {
                        "action": "ERROR",
                        "correlationId": message.get("correlationId"),
                        "success": False,
                        "status": "AUTHENTICATION_REQUIRED",
                        "error": "Authenticate this WebSocket session before sending requests.",
                        "provenance": PROVENANCE,
                    },
                )
                return
            if not self.approval_authorizer.touch(websocket):
                await self._send(
                    websocket,
                    {
                        "action": "ERROR",
                        "correlationId": message.get("correlationId"),
                        "success": False,
                        "status": "AUTHENTICATION_EXPIRED",
                        "error": "The authenticated WebSocket session has expired.",
                        "provenance": PROVENANCE,
                    },
                )
                return

        if (
            isinstance(action, str)
            and action.startswith("CRE_")
            and self.cre_unavailable_reason is not None
        ):
            result_action = (
                "CRE_STATUS_RESULT"
                if action == "CRE_STATUS_REQUEST"
                else action.removesuffix("_REQUEST") + "_RESULT"
            )
            reason = self.cre_unavailable_reason
            await self._send(
                websocket,
                {
                    "action": result_action,
                    "correlationId": message.get("correlationId"),
                    "success": False,
                    "status": "UNAVAILABLE",
                    "subsystem": "CRE",
                    "reason": reason,
                    "execution": "DISABLED",
                    "error": (
                        "CRE is unavailable because its database schema does not "
                        "match the runtime."
                        if reason == "SCHEMA_VERSION_MISMATCH"
                        else "CRE is unavailable."
                    ),
                    **(
                        {
                            "payload": {
                                "status": "UNAVAILABLE",
                                "subsystem": "CRE",
                                "reason": reason,
                                "execution": "DISABLED",
                            }
                        }
                        if action == "CRE_STATUS_REQUEST"
                        else {}
                    ),
                    "provenance": PROVENANCE,
                },
            )
            return

        if action == "SUBSCRIBE":
            await self._handle_subscribe(websocket, message)
            return

        if action == "PING":
            await self._send(
                websocket,
                {
                    "type": "PONG",
                    "correlationId": message.get("correlationId"),
                    "timestamp": datetime.now(UTC).isoformat(),
                    "provenance": PROVENANCE,
                },
            )
            return

        if action == "CHAT_MESSAGE":
            if not isinstance(self.approval_authorizer, WebSocketAuthenticator):
                await self._send(
                    websocket,
                    {
                        "action": "CHAT_RESULT",
                        "correlationId": message.get("correlationId"),
                        "success": False,
                        "status": "AUTHENTICATION_UNAVAILABLE",
                        "error": "Chat requires the configured operator authenticator.",
                        "provenance": PROVENANCE,
                    },
                )
                return
            await self._handle_chat_message(websocket, message)
            return

        if action == "DISPATCH_COMMAND":
            await self._handle_dispatch(websocket, message)
            return

        if action == "APPROVE_OPERATION":
            await self._handle_approve(websocket, message)
            return

        if action == "RECONCILE_OPERATION":
            await self._handle_reconcile(websocket, message)
            return

        if action == "CANCEL_TASK":
            await self._handle_cancel_task(websocket, message)
            return

        if action == "CRE_STATUS_REQUEST":
            await self._handle_cre_status(websocket, message)
            return

        if action == "CRE_SCRIPT_PREVIEW_REQUEST":
            await self._handle_cre_script_preview(websocket, message)
            return

        if action == "CRE_SCRIPT_PREVIEW_REVIEW_REQUEST":
            await self._handle_cre_script_preview_review(websocket, message)
            return

        if action == "CRE_SOURCE_VIDEO_REQUEST":
            await self._handle_cre_source_video(websocket, message)
            return

        if action == "CRE_FOLDER_IMPORT_SCAN_REQUEST":
            await self._handle_cre_folder_import_scan(websocket, message)
            return

        if action == "CRE_FOLDER_IMPORT_REGISTER_REQUEST":
            await self._handle_cre_folder_import_register(websocket, message)
            return

        if action == "CRE_SOURCE_VIDEO_RESUME_REQUEST":
            await self._handle_cre_source_video_resume(websocket, message)
            return

        if action == "CRE_SOURCE_ASSET_STATUS_REQUEST":
            await self._handle_cre_source_asset_status(websocket, message)
            return

        if action == "CRE_SOURCE_RIGHTS_REQUEST":
            await self._handle_cre_source_rights(websocket, message)
            return

        if action == "CRE_SOURCE_CLIP_REQUEST":
            await self._handle_cre_source_clip(websocket, message)
            return

        if action == "CRE_PROJECT_CREATE_REQUEST":
            await self._handle_cre_project_create(websocket, message)
            return

        if action == "CRE_PROJECT_TIMELINE_REQUEST":
            await self._handle_cre_project_timeline(websocket, message)
            return

        if action == "CRE_PROJECT_RENDER_REQUEST":
            await self._handle_cre_project_render(websocket, message)
            return

        if action == "CRE_PROJECT_AUTOPLAN_REQUEST":
            await self._handle_cre_project_autoplan(websocket, message)
            return

        if action == "CRE_PRIVATE_PROJECT_RENDER_REQUEST":
            await self._handle_cre_private_project_render(websocket, message)
            return

        if action == "CRE_PRIVATE_RENDER_STATUS_REQUEST":
            await self._handle_cre_private_render_status(websocket, message)
            return

        if action == "CRE_PRIVATE_RENDER_ARTIFACT_REQUEST":
            await self._handle_cre_private_render_artifact(websocket, message)
            return

        if action == "SYSTEM_INTELLIGENCE_REQUEST":
            await self._handle_system_intelligence(websocket, message)
            return

        if action == "SYSTEM_RESTART_AUTH_REQUEST":
            await self._handle_system_restart_auth(websocket, message)
            return

        await self._error(
            websocket,
            message.get("correlationId"),
            "Unsupported action.",
        )

    async def _handle_system_intelligence(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        correlation_id = message.get("correlationId")
        payload = message.get("payload", {})
        identity = (
            self.approval_authorizer.identity_for(websocket)
            if type(self.approval_authorizer) is WebSocketAuthenticator
            else None
        )
        if (
            not isinstance(correlation_id, str)
            or not correlation_id.strip()
            or len(correlation_id) > 128
            or not isinstance(payload, dict)
            or payload
        ):
            await self._send(websocket, {
                "action": "SYSTEM_INTELLIGENCE_RESULT",
                "correlationId": correlation_id,
                "success": False,
                "status": "INVALID_REQUEST",
                "error": "System inventory request is invalid.",
                "provenance": PROVENANCE,
            })
            return
        if identity is None or identity["principal"] != self.approval_authorizer.principal:
            await self._send(websocket, {
                "action": "SYSTEM_INTELLIGENCE_RESULT",
                "correlationId": correlation_id,
                "success": False,
                "status": "AUTHENTICATION_REQUIRED",
                "error": "System intelligence requires the configured operator session.",
                "provenance": PROVENANCE,
            })
            return
        try:
            inventory = await asyncio.to_thread(self.system_intelligence.collect)
        except (OSError, sqlite3.Error, ValueError):
            logger.exception("System intelligence collection failed.")
            await self._send(websocket, {
                "action": "SYSTEM_INTELLIGENCE_RESULT",
                "correlationId": correlation_id,
                "success": False,
                "status": "FAILED",
                "error": "System intelligence collection failed; no inventory was returned.",
                "provenance": PROVENANCE,
            })
            return
        await self._send(websocket, {
            "action": "SYSTEM_INTELLIGENCE_RESULT",
            "correlationId": correlation_id,
            "success": True,
            "status": inventory["status"],
            "payload": inventory,
            "provenance": PROVENANCE,
        })

    def _cre_import_directory_fd(self, requested_path: str | None = None) -> int:
        if requested_path is not None:
            requested_directory = Path(requested_path)
            if (
                not requested_directory.is_absolute()
                or ".." in requested_directory.parts
                or requested_path != str(self.cre_import_directory)
            ):
                raise PermissionError(errno.EACCES, "Unauthorized CRE import directory")
            try:
                canonical_directory = requested_directory.resolve(strict=True)
            except RuntimeError as exc:
                raise OSError(errno.ELOOP, "CRE import directory contains a symbolic-link loop") from exc
            if canonical_directory != self.cre_import_directory:
                raise PermissionError(errno.EACCES, "CRE import directory is not canonical")
        import_root = self.cre_import_directory.parent
        root_fd = os.open(
            import_root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        try:
            return os.open(
                "CRE_Import",
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=root_fd,
            )
        finally:
            os.close(root_fd)

    @staticmethod
    def _cre_import_file_id(name: str, file_stat: os.stat_result) -> str:
        identity = "\0".join(
            (
                name,
                str(file_stat.st_dev),
                str(file_stat.st_ino),
                str(file_stat.st_size),
                str(file_stat.st_mtime_ns),
                str(file_stat.st_ctime_ns),
            )
        )
        return hashlib.sha256(identity.encode("utf-8", errors="surrogateescape")).hexdigest()

    def _cre_import_entries(self, directory_fd: int) -> list[dict[str, Any]]:
        video_extensions = {
            ".mp4", ".webm", ".mov", ".mkv", ".avi", ".wmv",
            ".mpeg", ".mpg", ".flv", ".3gp", ".m4v",
        }
        supported_extensions = {".mp4", ".webm", ".mov", ".mkv"}
        entries: list[dict[str, Any]] = []
        for name in os.listdir(directory_fd):
            if name.startswith(".") or Path(name).suffix.lower() not in video_extensions:
                continue
            try:
                file_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(file_stat.st_mode):
                continue
            extension = Path(name).suffix.lower()
            if any(ord(character) < 32 for character in name):
                status = "INVALID"
                validation = "The file name contains unsupported control characters."
            elif extension not in supported_extensions:
                status = "INVALID"
                validation = "Unsupported format. Use MP4, WebM, MOV, or MKV."
            elif file_stat.st_size == 0:
                status = "INVALID"
                validation = "The video file is empty."
            elif file_stat.st_size > MAX_SOURCE_BYTES:
                status = "INVALID"
                validation = (
                    f"The video exceeds the {MAX_SOURCE_BYTES // (1024 * 1024)} MiB limit."
                )
            else:
                status = "ELIGIBLE"
                validation = (
                    "Supported format and size. Media integrity is checked during registration."
                )
            entries.append(
                {
                    "fileId": self._cre_import_file_id(name, file_stat),
                    "fileName": name,
                    "sizeBytes": file_stat.st_size,
                    "status": status,
                    "validation": validation,
                }
            )
        if len(entries) > 200:
            raise ValueError("The import folder contains more than 200 video files; remove some and scan again.")
        return sorted(entries, key=lambda entry: str(entry["fileName"]).casefold())

    @staticmethod
    def _cre_import_error_status(error: OSError) -> tuple[str, str]:
        if isinstance(error, FileNotFoundError):
            return "DIRECTORY_MISSING", "The configured ~/Videos/CRE_Import directory is missing."
        if isinstance(error, PermissionError):
            return "DIRECTORY_INACCESSIBLE", "The configured import directory is not accessible to the CRE service."
        return "DIRECTORY_INACCESSIBLE", "The configured import directory is not a safe, accessible directory."

    async def _handle_cre_folder_import_scan(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        correlation_id = message.get("correlationId")
        identity = (
            self.approval_authorizer.identity_for(websocket)
            if type(self.approval_authorizer) is WebSocketAuthenticator
            else None
        )
        status = "INVALID_REQUEST"
        error = "CRE folder-import scan request is invalid."
        entries = None
        payload = message.get("payload")
        requested_directory = str(self.cre_import_directory)
        if isinstance(payload, dict) and set(payload) == {"directory"}:
            requested_directory = payload.get("directory")
        if (
            not isinstance(correlation_id, str)
            or not correlation_id.strip()
            or len(correlation_id) > 128
            or not (
                payload == {}
                or (
                    isinstance(payload, dict)
                    and set(payload) == {"directory"}
                    and isinstance(requested_directory, str)
                    and 0 < len(requested_directory) <= 4096
                )
            )
        ):
            pass
        elif identity is None:
            status, error = "AUTHENTICATION_REQUIRED", "Authenticate before scanning the CRE import folder."
        elif requested_directory != str(self.cre_import_directory):
            status, error = (
                "UNAUTHORIZED_DIRECTORY",
                "Only the configured ~/Videos/CRE_Import directory is authorized for scanning.",
            )
        else:
            directory_fd = None
            try:
                directory_fd = self._cre_import_directory_fd(requested_directory)
                entries = self._cre_import_entries(directory_fd)
                status = "IMPORT_SCAN_READY" if entries else "DIRECTORY_EMPTY"
                error = None if entries else "No supported video files were found in ~/Videos/CRE_Import."
            except ValueError as exc:
                status, error = "SCAN_LIMIT", str(exc)
            except OSError as exc:
                status, error = self._cre_import_error_status(exc)
            finally:
                if directory_fd is not None:
                    os.close(directory_fd)
        await self._send(
            websocket,
            {
                "action": "CRE_FOLDER_IMPORT_SCAN_RESULT",
                "correlationId": correlation_id,
                "success": status == "IMPORT_SCAN_READY",
                "status": status,
                "error": error,
                "payload": {"files": entries} if entries is not None else None,
                "provenance": PROVENANCE,
            },
        )

    async def _handle_cre_folder_import_register(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        correlation_id = message.get("correlationId")
        payload = message.get("payload")
        identity = (
            self.approval_authorizer.identity_for(websocket)
            if type(self.approval_authorizer) is WebSocketAuthenticator
            else None
        )
        if (
            not isinstance(correlation_id, str)
            or not correlation_id.strip()
            or len(correlation_id) > 128
            or not isinstance(payload, dict)
            or set(payload) != {"fileId", "targetContext", "accountId", "territory"}
            or not isinstance(payload.get("fileId"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", payload["fileId"])
        ):
            await self._send(websocket, {
                "action": "CRE_FOLDER_IMPORT_REGISTER_RESULT",
                "correlationId": correlation_id,
                "success": False,
                "status": "INVALID_REQUEST",
                "error": "CRE folder-import registration request is invalid.",
                "provenance": PROVENANCE,
            })
            return
        if identity is None:
            await self._send(websocket, {
                "action": "CRE_FOLDER_IMPORT_REGISTER_RESULT",
                "correlationId": correlation_id,
                "success": False,
                "status": "AUTHENTICATION_REQUIRED",
                "error": "Authenticate before registering a CRE import-folder video.",
                "provenance": PROVENANCE,
            })
            return

        directory_fd = None
        try:
            directory_fd = self._cre_import_directory_fd()
            entries = self._cre_import_entries(directory_fd)
            selected = next(
                (entry for entry in entries if entry["fileId"] == payload["fileId"]),
                None,
            )
            if selected is None:
                raise ValueError("The selected video changed or is no longer in the import folder. Scan again.")
            if selected["status"] != "ELIGIBLE":
                raise ValueError(str(selected["validation"]))
            file_fd = os.open(
                str(selected["fileName"]),
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory_fd,
            )
            try:
                before = os.fstat(file_fd)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or self._cre_import_file_id(str(selected["fileName"]), before) != payload["fileId"]
                ):
                    raise ValueError("The selected video changed during import. Scan again.")
                chunks = bytearray()
                while len(chunks) <= MAX_SOURCE_BYTES:
                    chunk = os.read(file_fd, min(1024 * 1024, MAX_SOURCE_BYTES + 1 - len(chunks)))
                    if not chunk:
                        break
                    chunks.extend(chunk)
                after = os.fstat(file_fd)
                if (
                    len(chunks) == 0
                    or len(chunks) > MAX_SOURCE_BYTES
                    or self._cre_import_file_id(str(selected["fileName"]), after) != payload["fileId"]
                ):
                    raise ValueError("The selected video changed or exceeds the import limit. Scan again.")
            finally:
                os.close(file_fd)
        except ValueError as exc:
            await self._send(websocket, {
                "action": "CRE_FOLDER_IMPORT_REGISTER_RESULT",
                "correlationId": correlation_id,
                "success": False,
                "status": "INVALID_SOURCE",
                "error": str(exc),
                "provenance": PROVENANCE,
            })
            return
        except OSError as exc:
            if directory_fd is not None and isinstance(exc, PermissionError):
                status, error = (
                    "FILE_INACCESSIBLE",
                    "The selected video cannot be read by the CRE service identity.",
                )
            elif directory_fd is not None and exc.errno in {
                errno.ENOENT,
                errno.ELOOP,
                errno.ENOTDIR,
            }:
                status, error = (
                    "INVALID_SOURCE",
                    "The selected video changed or is no longer a regular file in the import folder. Scan again.",
                )
            else:
                status, error = self._cre_import_error_status(exc)
            await self._send(websocket, {
                "action": "CRE_FOLDER_IMPORT_REGISTER_RESULT",
                "correlationId": correlation_id,
                "success": False,
                "status": status,
                "error": error,
                "provenance": PROVENANCE,
            })
            return
        finally:
            if directory_fd is not None:
                os.close(directory_fd)

        await self._handle_cre_source_video(
            websocket,
            {
                "action": "CRE_SOURCE_VIDEO_REQUEST",
                "correlationId": correlation_id,
                "payload": {
                    "videoBase64": base64.b64encode(chunks).decode("ascii"),
                    "fileName": str(selected["fileName"]),
                    "targetContext": payload["targetContext"],
                    "accountId": payload["accountId"],
                    "territory": payload["territory"],
                },
            },
        )

    async def _handle_system_restart_auth(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        correlation_id = message.get("correlationId")
        payload = message.get("payload", {})
        identity = (
            self.approval_authorizer.identity_for(websocket)
            if type(self.approval_authorizer) is WebSocketAuthenticator
            else None
        )
        operation_id = payload.get("operationId") if isinstance(payload, dict) else None
        if (
            not isinstance(correlation_id, str)
            or not correlation_id.strip()
            or len(correlation_id) > 128
            or not isinstance(payload, dict)
            or set(payload) != {"operationId"}
            or not isinstance(operation_id, str)
            or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", operation_id)
        ):
            await self._send(websocket, {
                "action": "SYSTEM_RESTART_AUTH_RESULT",
                "correlationId": correlation_id,
                "success": False,
                "status": "INVALID_REQUEST",
                "error": "Helper restart request is invalid.",
                "provenance": PROVENANCE,
            })
            return
        if identity is None or identity["principal"] != self.approval_authorizer.principal:
            await self._send(websocket, {
                "action": "SYSTEM_RESTART_AUTH_RESULT",
                "correlationId": correlation_id,
                "success": False,
                "status": "AUTHENTICATION_REQUIRED",
                "error": "Helper restart requires the configured operator session.",
                "provenance": PROVENANCE,
            })
            return
        try:
            result = await asyncio.to_thread(
                request_helper_restart,
                self.system_intelligence.state_directory,
                operation_id,
            )
        except (OSError, ValueError, PermissionError, TimeoutError):
            logger.exception("Authenticated helper restart request failed.")
            await self._send(websocket, {
                "action": "SYSTEM_RESTART_AUTH_RESULT",
                "correlationId": correlation_id,
                "success": False,
                "status": "FAILED",
                "error": "Manager control request failed; inspect current helper state.",
                "provenance": PROVENANCE,
            })
            return
        await self._send(websocket, {
            "action": "SYSTEM_RESTART_AUTH_RESULT",
            "correlationId": correlation_id,
            "success": result["success"],
            "status": result.get("status", "UNKNOWN"),
            "payload": result,
            "error": None if result["success"] else result.get("detail", "Restart failed."),
            "provenance": PROVENANCE,
        })

    async def _handle_cre_status(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        correlation_id = message.get("correlationId")
        payload = message.get("payload", {})
        if (
            not isinstance(correlation_id, str)
            or not correlation_id.strip()
            or len(correlation_id) > 128
            or not isinstance(payload, dict)
            or set(payload) - {"limit"}
        ):
            await self._send(
                websocket,
                {
                    "action": "CRE_STATUS_RESULT",
                    "correlationId": correlation_id,
                    "success": False,
                    "status": "INVALID_REQUEST",
                    "error": "CRE status request is invalid.",
                    "provenance": PROVENANCE,
                },
            )
            return

        limit = payload.get("limit", 50)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            await self._send(
                websocket,
                {
                    "action": "CRE_STATUS_RESULT",
                    "correlationId": correlation_id,
                    "success": False,
                    "status": "INVALID_REQUEST",
                    "error": "CRE status limit must be between 1 and 100.",
                    "provenance": PROVENANCE,
                },
            )
            return

        authenticator = self.approval_authorizer
        if type(authenticator) is not WebSocketAuthenticator:
            status, error = (
                "AUTHENTICATION_UNAVAILABLE",
                "CRE status requires the configured operator authenticator.",
            )
        else:
            identity = authenticator.identity_for(websocket)
            if identity is None:
                status, error = (
                    "AUTHENTICATION_REQUIRED",
                    "Authenticate this WebSocket session before requesting CRE status.",
                )
            elif self.cre_status_service is None:
                status, error = (
                    "UNAVAILABLE",
                    "CRE status service is not configured.",
                )
            else:
                try:
                    result = self.cre_status_service.for_principal(
                        identity["principal"],
                        limit=limit,
                    )
                    if self.cre_script_preview_service is None:
                        result["scriptPreviews"] = {
                            "state": "UNAVAILABLE",
                            "reason": "NOT_CONFIGURED",
                            "generatedAt": result["generatedAt"],
                            "items": [],
                        }
                    else:
                        result["scriptPreviews"] = (
                            self.cre_script_preview_service.list_audio_evidence(
                                principal=identity["principal"],
                                limit=limit,
                            )
                        )
                    current_identity = authenticator.identity_for(websocket)
                    if (
                        current_identity is None
                        or current_identity["sessionId"] != identity["sessionId"]
                    ):
                        await self._send(
                            websocket,
                            {
                                "action": "CRE_STATUS_RESULT",
                                "correlationId": correlation_id,
                                "success": False,
                                "status": "AUTHENTICATION_REQUIRED",
                                "error": (
                                    "The authenticated session expired while reading "
                                    "CRE status; the response was not returned."
                                ),
                                "provenance": PROVENANCE,
                            },
                        )
                        return
                except PermissionError:
                    status, error = (
                        "AUTHORIZATION_DENIED",
                        "The authenticated principal is not authorized for CRE status.",
                    )
                except ValueError:
                    status, error = (
                        "INVALID_REQUEST",
                        "CRE status request is invalid.",
                    )
                except Exception:
                    logger.exception("CRE read-only status query failed.")
                    status, error = (
                        "UNAVAILABLE",
                        "CRE status is temporarily unavailable.",
                    )
                else:
                    await self._send(
                        websocket,
                        {
                            "action": "CRE_STATUS_RESULT",
                            "correlationId": correlation_id,
                            "success": True,
                            "status": "AVAILABLE",
                            "payload": result,
                            "provenance": PROVENANCE,
                        },
                    )
                    return

        await self._send(
            websocket,
            {
                "action": "CRE_STATUS_RESULT",
                "correlationId": correlation_id,
                "success": False,
                "status": status,
                "error": error,
                "provenance": PROVENANCE,
            },
        )

    async def _handle_cre_script_preview(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        correlation_id = message.get("correlationId")
        payload = message.get("payload")
        if (
            not isinstance(correlation_id, str)
            or not correlation_id.strip()
            or len(correlation_id) > 128
            or not isinstance(payload, dict)
            or set(payload) != {"script", "references"}
        ):
            await self._send(
                websocket,
                {
                    "action": "CRE_SCRIPT_PREVIEW_RESULT",
                    "correlationId": correlation_id,
                    "success": False,
                    "status": "INVALID_REQUEST",
                    "error": "CRE script preview request is invalid.",
                    "provenance": PROVENANCE,
                },
            )
            return

        script = payload["script"]
        references = payload["references"]
        if (
            not isinstance(script, str)
            or not script.strip()
            or len(script) > OriginalScriptPreviewService.MAX_SCRIPT_CHARACTERS
            or any(
                ord(character) < 32 and character not in "\t\n\r"
                for character in script
            )
        ):
            await self._send(
                websocket,
                {
                    "action": "CRE_SCRIPT_PREVIEW_RESULT",
                    "correlationId": correlation_id,
                    "success": False,
                    "status": "INVALID_REQUEST",
                    "error": (
                        "Script must contain 1 to 20,000 Unicode characters "
                        "with no control characters."
                    ),
                    "provenance": PROVENANCE,
                },
            )
            return
        if not isinstance(references, list):
            await self._send(
                websocket,
                {
                    "action": "CRE_SCRIPT_PREVIEW_RESULT",
                    "correlationId": correlation_id,
                    "success": False,
                    "status": "INVALID_REQUEST",
                    "error": "CRE script preview references are invalid.",
                    "provenance": PROVENANCE,
                },
            )
            return

        authenticator = self.approval_authorizer
        identity = (
            authenticator.identity_for(websocket)
            if type(authenticator) is WebSocketAuthenticator
            else None
        )
        if type(authenticator) is not WebSocketAuthenticator:
            status, error = (
                "AUTHENTICATION_UNAVAILABLE",
                "CRE script preview requires the configured operator authenticator.",
            )
        elif identity is None:
            status, error = (
                "AUTHENTICATION_REQUIRED",
                "Authenticate this WebSocket session before submitting a script.",
            )
        elif self.cre_script_preview_service is None:
            status, error = (
                "UNAVAILABLE",
                "CRE local script preview is not configured.",
            )
        else:
            try:
                result = await asyncio.to_thread(
                    self.cre_script_preview_service.create_preview,
                    principal=identity["principal"],
                    script=payload["script"],
                    references=payload["references"],
                    command_id=correlation_id,
                )
                current_identity = authenticator.identity_for(websocket)
                if (
                    current_identity is None
                    or current_identity["sessionId"] != identity["sessionId"]
                ):
                    await self._send(
                        websocket,
                        {
                            "action": "CRE_SCRIPT_PREVIEW_RESULT",
                            "correlationId": correlation_id,
                            "success": False,
                            "status": "AUTHENTICATION_REQUIRED",
                            "error": (
                                "The authenticated session expired during preview "
                                "rendering; the artifact was not returned."
                            ),
                            "provenance": PROVENANCE,
                        },
                    )
                    return
            except PermissionError:
                status, error = (
                    "AUTHORIZATION_DENIED",
                    "The authenticated principal cannot access this script preview.",
                )
            except ValueError as exc:
                status, error = "INVALID_REQUEST", str(exc)
            except SceneVisualRenderError as exc:
                status, error = "PREVIEW_FAILED", str(exc)
            except Exception:
                logger.exception("CRE original-script preview request failed.")
                status, error = (
                    "PREVIEW_FAILED",
                    "Local script preview failed; no approval or publication occurred.",
                )
            else:
                await self._send(
                    websocket,
                    {
                        "action": "CRE_SCRIPT_PREVIEW_RESULT",
                        "correlationId": correlation_id,
                        "success": True,
                        "status": "PREVIEW_READY",
                        "payload": result,
                        "provenance": PROVENANCE,
                    },
                )
                return

        await self._send(
            websocket,
            {
                "action": "CRE_SCRIPT_PREVIEW_RESULT",
                "correlationId": correlation_id,
                "success": False,
                "status": status,
                "error": error,
                "provenance": PROVENANCE,
            },
        )

    async def _handle_cre_script_preview_review(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        correlation_id = message.get("correlationId")
        payload = message.get("payload")
        expected_fields = {
            "scriptId",
            "artifactSha256",
            "editorialVerdict",
            "narrationAssessment",
            "pacingAssessment",
            "captionAssessment",
            "sceneAssessment",
            "factualReviewAttested",
            "notes",
        }
        if (
            not isinstance(correlation_id, str)
            or not correlation_id.strip()
            or len(correlation_id) > 128
            or not isinstance(payload, dict)
            or set(payload) != expected_fields
        ):
            await self._send(
                websocket,
                {
                    "action": "CRE_SCRIPT_PREVIEW_REVIEW_RESULT",
                    "correlationId": correlation_id,
                    "success": False,
                    "status": "INVALID_REQUEST",
                    "error": "CRE script preview review request is invalid.",
                    "provenance": PROVENANCE,
                },
            )
            return

        authenticator = self.approval_authorizer
        identity = (
            authenticator.identity_for(websocket)
            if type(authenticator) is WebSocketAuthenticator
            else None
        )
        if type(authenticator) is not WebSocketAuthenticator:
            status, error = (
                "AUTHENTICATION_UNAVAILABLE",
                "Editorial review requires the configured operator authenticator.",
            )
        elif identity is None:
            status, error = (
                "AUTHENTICATION_REQUIRED",
                "Authenticate this WebSocket session before recording a review.",
            )
        elif self.cre_script_preview_service is None:
            status, error = (
                "UNAVAILABLE",
                "CRE local script preview is not configured.",
            )
        else:
            try:
                result = await asyncio.to_thread(
                    self.cre_script_preview_service.record_editorial_review,
                    principal=identity["principal"],
                    script_id=payload["scriptId"],
                    artifact_sha256=payload["artifactSha256"],
                    command_id=correlation_id,
                    editorial_verdict=payload["editorialVerdict"],
                    narration_assessment=payload["narrationAssessment"],
                    pacing_assessment=payload["pacingAssessment"],
                    caption_assessment=payload["captionAssessment"],
                    scene_assessment=payload["sceneAssessment"],
                    factual_review_attested=payload["factualReviewAttested"],
                    notes=payload["notes"],
                )
                current_identity = authenticator.identity_for(websocket)
                if (
                    current_identity is None
                    or current_identity["sessionId"] != identity["sessionId"]
                ):
                    await self._send(
                        websocket,
                        {
                            "action": "CRE_SCRIPT_PREVIEW_REVIEW_RESULT",
                            "correlationId": correlation_id,
                            "success": False,
                            "status": "AUTHENTICATION_REQUIRED",
                            "error": (
                                "The authenticated session expired while recording "
                                "the review; the record was saved but not returned."
                            ),
                            "provenance": PROVENANCE,
                        },
                    )
                    return
            except PermissionError:
                status, error = (
                    "AUTHORIZATION_DENIED",
                    "The authenticated principal cannot review this script preview.",
                )
            except ValueError:
                status, error = (
                    "INVALID_REQUEST",
                    "CRE script preview review assessment is invalid.",
                )
            except Exception:
                logger.exception("CRE script preview editorial review failed.")
                status, error = (
                    "UNAVAILABLE",
                    "CRE script preview review is temporarily unavailable.",
                )
            else:
                await self._send(
                    websocket,
                    {
                        "action": "CRE_SCRIPT_PREVIEW_REVIEW_RESULT",
                        "correlationId": correlation_id,
                        "success": True,
                        "status": "RECORDED",
                        "payload": {
                            "scriptId": result["scriptId"],
                            "previewSha256": result["preview"]["sha256"],
                            "review": result["review"],
                        },
                        "provenance": PROVENANCE,
                    },
                )
                return

        await self._send(
            websocket,
            {
                "action": "CRE_SCRIPT_PREVIEW_REVIEW_RESULT",
                "correlationId": correlation_id,
                "success": False,
                "status": status,
                "error": error,
                "provenance": PROVENANCE,
            },
        )

    def _cre_authorization_context(
        self,
        websocket: Any,
        correlation_id: str,
        *,
        action: str,
        parameters: dict[str, Any],
    ) -> AuthorizationContext:
        authenticator = self.approval_authorizer
        if type(authenticator) is not WebSocketAuthenticator:
            raise PermissionError("CRE operator authentication is unavailable.")
        fingerprint = hashlib.sha256(
            json.dumps(
                {"action": action, "parameters": parameters},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        operation = {
            "operationId": str(uuid4()),
            "correlationId": correlation_id,
            "requestFingerprint": fingerprint,
        }
        grant = authenticator.authorize(
            websocket=websocket,
            operation=operation,
            purpose="cre_local_source_workflow",
            request_correlation_id=correlation_id,
            action=action,
            resource_fingerprint=resource_fingerprint(action, parameters),
        )
        if not isinstance(grant, dict):
            raise PermissionError("The authenticated CRE session is no longer active.")
        return AuthorizationContext.from_grant(grant)

    @staticmethod
    def _cre_target_use(
        *,
        asset_id: str,
        asset_sha256: str,
        target: ContentTargetContext,
        account_id: str,
        territory: str,
        action: RightsAction,
        duration_seconds: int,
    ) -> RightsUse:
        platform = {
            ContentTargetContext.YOUTUBE_SHORTS: ContentPlatform.YOUTUBE,
            ContentTargetContext.YOUTUBE_KIDS_AUDIENCE: ContentPlatform.YOUTUBE,
            ContentTargetContext.INSTAGRAM_REELS: ContentPlatform.INSTAGRAM,
        }.get(target)
        if platform is None:
            raise ValueError("Source-video workflow supports YouTube Shorts and Instagram Reels.")
        return RightsUse(
            asset_id=asset_id,
            expected_asset_sha256=asset_sha256,
            platform=platform,
            account_id=account_id,
            territory=territory,
            actions=frozenset({action}),
            commercial_intent=False,
            monetization_intent=False,
            duration_seconds=duration_seconds,
        )

    async def _handle_cre_project_create(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        correlation_id = message.get("correlationId")
        payload = message.get("payload")
        identity = (
            self.approval_authorizer.identity_for(websocket)
            if type(self.approval_authorizer) is WebSocketAuthenticator
            else None
        )
        status, error, project_summary = "INVALID_REQUEST", "CRE project request is invalid.", None
        if (
            not isinstance(correlation_id, str)
            or not correlation_id.strip()
            or len(correlation_id) > 128
            or not isinstance(payload, dict)
            or set(payload) != {"projectId", "script", "sourceAssetIds"}
        ):
            pass
        elif identity is None:
            status, error = "AUTHENTICATION_REQUIRED", "Authenticate before creating a CRE project."
        elif self.cre_clip_rendering_service is None:
            status, error = "NOT_CONFIGURED", "CRE project storage is unavailable."
        else:
            try:
                project_id = payload["projectId"]
                script = payload["script"]
                source_asset_ids = payload["sourceAssetIds"]
                if (
                    not isinstance(project_id, str)
                    or not 1 <= len(project_id.strip()) <= 200
                    or not isinstance(script, str)
                    or not script.strip()
                    or len(script) > 20_000
                    or any(ord(char) < 32 and char not in "\t\n\r" for char in script)
                    or not isinstance(source_asset_ids, list)
                    or not 1 <= len(source_asset_ids) <= 12
                    or any(not isinstance(asset_id, str) or not asset_id.strip() for asset_id in source_asset_ids)
                ):
                    raise ValueError("CRE project fields are invalid.")
                project = ContentEditProject(
                    project_id=project_id.strip(),
                    owner_id=identity["principal"],
                    source_asset_ids=tuple(dict.fromkeys(asset_id.strip() for asset_id in source_asset_ids)),
                    script=script,
                )
                context = self._cre_authorization_context(
                    websocket,
                    correlation_id,
                    action="content_create_edit_project",
                    parameters=project.model_dump(mode="json"),
                )
                saved = await asyncio.to_thread(
                    self.cre_clip_rendering_service.content_rights.create_edit_project,
                    project,
                    context=context,
                    command_id=str(uuid4()),
                )
                project_summary = saved.model_dump(mode="json")
                status, error = "PROJECT_CREATED", None
            except PermissionError as exc:
                status, error = "RIGHTS_OR_AUTHORIZATION_DENIED", str(exc)
            except (TypeError, ValueError) as exc:
                status, error = "INVALID_REQUEST", str(exc)
            except Exception:
                logger.exception("CRE project creation failed.")
                status, error = "RENDER_FAILED", "CRE project creation failed."

        await self._send(
            websocket,
            {
                "action": "CRE_PROJECT_CREATE_RESULT",
                "correlationId": correlation_id,
                "success": status == "PROJECT_CREATED",
                "status": status,
                "error": error,
                "payload": project_summary,
                "provenance": PROVENANCE,
            },
        )

    async def _handle_cre_project_timeline(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        correlation_id = message.get("correlationId")
        payload = message.get("payload")
        identity = (
            self.approval_authorizer.identity_for(websocket)
            if type(self.approval_authorizer) is WebSocketAuthenticator
            else None
        )
        status, error, timeline_summary = "INVALID_REQUEST", "CRE timeline request is invalid.", None
        if (
            not isinstance(correlation_id, str)
            or not correlation_id.strip()
            or len(correlation_id) > 128
            or not isinstance(payload, dict)
            or set(payload) != {"projectId", "segments"}
        ):
            pass
        elif identity is None:
            status, error = "AUTHENTICATION_REQUIRED", "Authenticate before editing a CRE timeline."
        elif self.cre_clip_rendering_service is None:
            status, error = "NOT_CONFIGURED", "CRE timeline storage is unavailable."
        else:
            try:
                project_id = payload["projectId"]
                raw_segments = payload["segments"]
                if not isinstance(project_id, str) or not isinstance(raw_segments, list) or not 2 <= len(raw_segments) <= 100:
                    raise ValueError("A CRE timeline must contain 2 to 100 segments.")
                segments: list[ContentEditSegment] = []
                for order, raw in enumerate(raw_segments, start=1):
                    if not isinstance(raw, dict) or set(raw) != {"sourceAssetId", "sourceInSeconds", "sourceOutSeconds", "timelineOrder"}:
                        raise ValueError("CRE timeline segments are invalid.")
                    segments.append(ContentEditSegment(
                        source_asset_id=raw["sourceAssetId"],
                        source_in_seconds=float(raw["sourceInSeconds"]),
                        source_out_seconds=float(raw["sourceOutSeconds"]),
                        timeline_order=int(raw["timelineOrder"]),
                    ))
                context = self._cre_authorization_context(
                    websocket,
                    correlation_id,
                    action="content_replace_edit_timeline",
                    parameters={"project_id": project_id, "owner_id": identity["principal"]},
                )
                saved = await asyncio.to_thread(
                    self.cre_clip_rendering_service.content_rights.replace_edit_timeline,
                    project_id,
                    identity["principal"],
                    tuple(segments),
                    context=context,
                    command_id=str(uuid4()),
                )
                timeline_summary = {"projectId": project_id, "segments": [segment.model_dump(mode="json") for segment in saved]}
                status, error = "TIMELINE_SAVED", None
            except PermissionError as exc:
                status, error = "RIGHTS_OR_AUTHORIZATION_DENIED", str(exc)
            except (TypeError, ValueError) as exc:
                status, error = "INVALID_REQUEST", str(exc)
            except Exception:
                logger.exception("CRE timeline update failed.")
                status, error = "RENDER_FAILED", "CRE timeline update failed."

        await self._send(
            websocket,
            {
                "action": "CRE_PROJECT_TIMELINE_RESULT",
                "correlationId": correlation_id,
                "success": status == "TIMELINE_SAVED",
                "status": status,
                "error": error,
                "payload": timeline_summary,
                "provenance": PROVENANCE,
            },
        )

    async def _handle_cre_project_autoplan(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        correlation_id = message.get("correlationId")
        payload = message.get("payload")
        identity = (
            self.approval_authorizer.identity_for(websocket)
            if type(self.approval_authorizer) is WebSocketAuthenticator
            else None
        )
        status, error, plan_summary = "INVALID_REQUEST", "CRE autonomous edit request is invalid.", None
        if (
            not isinstance(correlation_id, str)
            or not correlation_id.strip()
            or len(correlation_id) > 128
            or not isinstance(payload, dict)
            or set(payload) != {"projectId", "instruction"}
        ):
            pass
        elif identity is None:
            status, error = "AUTHENTICATION_REQUIRED", "Authenticate before planning a CRE edit."
        elif self.cre_clip_rendering_service is None:
            status, error = "NOT_CONFIGURED", "CRE edit planning is unavailable."
        else:
            try:
                project_id = payload["projectId"]
                instruction = payload["instruction"]
                if not isinstance(project_id, str) or not project_id.strip() or not isinstance(instruction, str):
                    raise ValueError("CRE autonomous edit fields are invalid.")
                if len(instruction) > 20_000 or any(ord(char) < 32 and char not in "\t\n\r" for char in instruction):
                    raise ValueError("CRE instruction contains invalid characters.")
                context = self._cre_authorization_context(
                    websocket,
                    correlation_id,
                    action="content_autoplan_edit_project",
                    parameters={"project_id": project_id, "instruction_length": len(instruction)},
                )
                plan_summary = await asyncio.to_thread(
                    self.cre_clip_rendering_service.autoplan_edit_project,
                    project_id.strip(),
                    instruction,
                    context=context,
                    command_id=str(uuid4()),
                )
                status, error = "AUTOPLAN_READY", None
            except PermissionError as exc:
                status, error = "RIGHTS_OR_AUTHORIZATION_DENIED", str(exc)
            except (TypeError, ValueError) as exc:
                status, error = "INVALID_REQUEST", str(exc)
            except Exception:
                logger.exception("CRE autonomous edit planning failed.")
                status, error = "AUTOPLAN_FAILED", "CRE autonomous edit planning failed."

        await self._send(websocket, {
            "action": "CRE_PROJECT_AUTOPLAN_RESULT",
            "correlationId": correlation_id,
            "success": status == "AUTOPLAN_READY",
            "status": status,
            "error": error,
            "payload": plan_summary,
            "provenance": PROVENANCE,
        })

    async def _handle_cre_private_project_render(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        correlation_id = message.get("correlationId")
        payload = message.get("payload")
        identity = (
            self.approval_authorizer.identity_for(websocket)
            if type(self.approval_authorizer) is WebSocketAuthenticator
            else None
        )
        status, error, render_summary = (
            "INVALID_REQUEST",
            "CRE private review render request is invalid.",
            None,
        )
        if (
            not isinstance(correlation_id, str)
            or not correlation_id.strip()
            or len(correlation_id) > 128
            or not isinstance(payload, dict)
            or set(payload) != {"operationId", "projectId", "vertical"}
        ):
            pass
        elif identity is None:
            status, error = (
                "AUTHENTICATION_REQUIRED",
                "Authenticate before rendering a private CRE project.",
            )
        elif self.cre_clip_rendering_service is None:
            status, error = (
                "NOT_CONFIGURED",
                "CRE private review rendering is unavailable.",
            )
        else:
            try:
                operation_id = payload["operationId"]
                project_id = payload["projectId"]
                vertical = payload["vertical"]
                if not isinstance(project_id, str) or not project_id.strip():
                    raise ValueError("Project ID is invalid.")
                if type(vertical) is not bool:
                    raise ValueError("Vertical render selection is invalid.")
                if not self.cre_clip_rendering_service.store.private_render_operations_enabled:
                    status = "RENDER_RECOVERY_NOT_ENABLED"
                    error = (
                        "Durable private render operations are unavailable until "
                        "the documented schema migration has been applied."
                    )
                    render_summary = None
                else:
                    context = self._cre_authorization_context(
                        websocket,
                        correlation_id,
                        action="content_submit_private_project_render",
                        parameters={
                            "operation_id": operation_id,
                            "project_id": project_id.strip(),
                            "vertical": vertical,
                        },
                    )
                    render_summary = await asyncio.to_thread(
                        self.cre_clip_rendering_service.submit_private_project_render_operation,
                        operation_id,
                        project_id.strip(),
                        vertical=vertical,
                        context=context,
                    )
                    status, error = "PRIVATE_RENDER_OPERATION_AVAILABLE", None
            except PermissionError as exc:
                status, error = "RIGHTS_OR_AUTHORIZATION_DENIED", str(exc)
            except (TypeError, ValueError) as exc:
                status, error = "INVALID_REQUEST", str(exc)
            except Exception:
                logger.exception("CRE private project rendering failed.")
                status, error = (
                    "RENDER_OPERATION_FAILED",
                    "CRE private render operation could not be completed.",
                )

        await self._send(websocket, {
            "action": "CRE_PRIVATE_PROJECT_RENDER_RESULT",
            "correlationId": correlation_id,
            "success": status == "PRIVATE_RENDER_OPERATION_AVAILABLE",
            "status": status,
            "error": error,
            "payload": render_summary,
            "provenance": PROVENANCE,
        })

    async def _handle_cre_private_render_status(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        correlation_id = message.get("correlationId")
        payload = message.get("payload")
        identity = (
            self.approval_authorizer.identity_for(websocket)
            if type(self.approval_authorizer) is WebSocketAuthenticator
            else None
        )
        status, error, summary = (
            "INVALID_REQUEST",
            "CRE private render status request is invalid.",
            None,
        )
        if (
            not isinstance(correlation_id, str)
            or not correlation_id.strip()
            or len(correlation_id) > 128
            or not isinstance(payload, dict)
            or set(payload) != {"operationId"}
        ):
            pass
        elif identity is None:
            status, error = (
                "AUTHENTICATION_REQUIRED",
                "Authenticate before reading a private render operation.",
            )
        elif self.cre_clip_rendering_service is None:
            status, error = (
                "NOT_CONFIGURED",
                "CRE private render recovery is unavailable.",
            )
        else:
            try:
                operation_id = payload["operationId"]
                context = self._cre_authorization_context(
                    websocket,
                    correlation_id,
                    action="content_read_private_project_render",
                    parameters={"operation_id": operation_id},
                )
                summary = await asyncio.to_thread(
                    self.cre_clip_rendering_service.get_private_render_operation,
                    operation_id,
                    context=context,
                )
                status, error = "PRIVATE_RENDER_OPERATION_AVAILABLE", None
            except PermissionError:
                status, error = (
                    "RENDER_OPERATION_UNAVAILABLE",
                    "Private render operation is unavailable.",
                )
            except (TypeError, ValueError) as exc:
                status, error = "INVALID_REQUEST", str(exc)
            except Exception:
                logger.exception("CRE private render status lookup failed.")
                status, error = (
                    "RENDER_OPERATION_UNAVAILABLE",
                    "Private render operation is unavailable.",
                )
        await self._send(
            websocket,
            {
                "action": "CRE_PRIVATE_RENDER_STATUS_RESULT",
                "correlationId": correlation_id,
                "success": status == "PRIVATE_RENDER_OPERATION_AVAILABLE",
                "status": status,
                "error": error,
                "payload": summary,
                "provenance": PROVENANCE,
            },
        )

    async def _handle_cre_private_render_artifact(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        correlation_id = message.get("correlationId")
        payload = message.get("payload")
        identity = (
            self.approval_authorizer.identity_for(websocket)
            if type(self.approval_authorizer) is WebSocketAuthenticator
            else None
        )
        status, error, artifact = (
            "INVALID_REQUEST",
            "CRE private render artifact request is invalid.",
            None,
        )
        if (
            not isinstance(correlation_id, str)
            or not correlation_id.strip()
            or len(correlation_id) > 128
            or not isinstance(payload, dict)
            or set(payload) != {"operationId"}
        ):
            pass
        elif identity is None:
            status, error = (
                "AUTHENTICATION_REQUIRED",
                "Authenticate before retrieving a private render artifact.",
            )
        elif self.cre_clip_rendering_service is None:
            status, error = (
                "NOT_CONFIGURED",
                "CRE private render artifact retrieval is unavailable.",
            )
        else:
            try:
                operation_id = payload["operationId"]
                context = self._cre_authorization_context(
                    websocket,
                    correlation_id,
                    action="content_read_private_project_render_artifact",
                    parameters={"operation_id": operation_id},
                )
                artifact = await asyncio.to_thread(
                    self.cre_clip_rendering_service.get_private_render_artifact,
                    operation_id,
                    context=context,
                )
                status, error = "PRIVATE_RENDER_ARTIFACT_READY", None
            except PermissionError:
                status, error = (
                    "RENDER_ARTIFACT_UNAVAILABLE",
                    "Private render artifact is unavailable.",
                )
            except (TypeError, ValueError) as exc:
                status, error = "ARTIFACT_INTEGRITY_FAILURE", str(exc)
            except Exception:
                logger.exception("CRE private render artifact retrieval failed.")
                status, error = (
                    "RENDER_ARTIFACT_UNAVAILABLE",
                    "Private render artifact is unavailable.",
                )
        await self._send(
            websocket,
            {
                "action": "CRE_PRIVATE_RENDER_ARTIFACT_RESULT",
                "correlationId": correlation_id,
                "success": status == "PRIVATE_RENDER_ARTIFACT_READY",
                "status": status,
                "error": error,
                "payload": artifact,
                "provenance": PROVENANCE,
            },
        )

    async def _handle_cre_project_render(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        correlation_id = message.get("correlationId")
        payload = message.get("payload")
        identity = (
            self.approval_authorizer.identity_for(websocket)
            if type(self.approval_authorizer) is WebSocketAuthenticator
            else None
        )
        status, error, render_summary = "INVALID_REQUEST", "CRE project render request is invalid.", None
        if (
            not isinstance(correlation_id, str)
            or not correlation_id.strip()
            or len(correlation_id) > 128
            or not isinstance(payload, dict)
            or set(payload) != {"projectId", "targetContext", "accountId", "territory", "vertical"}
        ):
            pass
        elif identity is None:
            status, error = "AUTHENTICATION_REQUIRED", "Authenticate before rendering a CRE project."
        elif self.cre_clip_rendering_service is None:
            status, error = "NOT_CONFIGURED", "CRE project rendering is unavailable."
        else:
            try:
                project_id = payload["projectId"]
                if not isinstance(project_id, str) or not project_id.strip():
                    raise ValueError("Project ID is invalid.")
                target = ContentTargetContext(payload["targetContext"])
                account_id = payload["accountId"]
                territory = payload["territory"]
                vertical = payload["vertical"]
                if not isinstance(account_id, str) or not account_id.strip() or len(account_id) > 200:
                    raise ValueError("Authorized platform account ID is invalid.")
                if not isinstance(territory, str) or len(territory) != 2 or not territory.isalpha():
                    raise ValueError("Territory must be a two-letter country code.")
                if type(vertical) is not bool:
                    raise ValueError("Vertical render selection is invalid.")
                territory = territory.upper()
                project = self.cre_clip_rendering_service.store.get_edit_project(project_id, identity["principal"])
                if project is None:
                    raise PermissionError("The CRE project is outside the authorized account.")
                rights_uses: dict[str, RightsUse] = {}
                for asset_id in project.source_asset_ids:
                    asset = self.cre_clip_rendering_service.store.get_asset(asset_id)
                    if asset is None or asset.owner_id != identity["principal"]:
                        raise PermissionError("A project source is outside the authorized account.")
                    segments = self.cre_clip_rendering_service.store.get_edit_segments(project_id, identity["principal"])
                    duration_seconds = max(
                        1,
                        int(math.ceil(max((segment.source_out_seconds for segment in segments if segment.source_asset_id == asset_id), default=1.0))),
                    )
                    rights_uses[asset_id] = self._cre_target_use(
                        asset_id=asset.asset_id,
                        asset_sha256=asset.content_sha256,
                        target=target,
                        account_id=account_id,
                        territory=territory,
                        action=RightsAction.CREATE_DERIVATIVE,
                        duration_seconds=duration_seconds,
                    )
                context = self._cre_authorization_context(
                    websocket,
                    correlation_id,
                    action="content_render_edit_project",
                    parameters={"project_id": project_id, "asset_ids": sorted(rights_uses), "vertical": vertical},
                )
                result = await asyncio.to_thread(
                    self.cre_clip_rendering_service.render_edit_project,
                    project_id,
                    rights_uses=rights_uses,
                    vertical=vertical,
                    context=context,
                    command_id=str(uuid4()),
                )
                output = self.cre_clip_rendering_service.content_storage.read_clip_output(
                    str(result["artifactId"]),
                    str(result["sha256"]),
                )
                if hashlib.sha256(output).hexdigest() != result["sha256"]:
                    raise RuntimeError("Persisted project output failed SHA-256 verification.")
                result["base64"] = base64.b64encode(output).decode("ascii")
                render_summary = result
                status, error = "PROJECT_RENDER_READY", None
            except PermissionError as exc:
                status, error = "RIGHTS_OR_AUTHORIZATION_DENIED", str(exc)
            except (TypeError, ValueError) as exc:
                status, error = "INVALID_REQUEST", str(exc)
            except Exception:
                logger.exception("CRE project rendering failed.")
                status, error = "RENDER_FAILED", "CRE project rendering failed; no publication occurred."

        await self._send(
            websocket,
            {
                "action": "CRE_PROJECT_RENDER_RESULT",
                "correlationId": correlation_id,
                "success": status == "PROJECT_RENDER_READY",
                "status": status,
                "error": error,
                "payload": render_summary,
                "provenance": PROVENANCE,
            },
        )

    async def _handle_cre_source_video(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        correlation_id = message.get("correlationId")
        payload = message.get("payload")
        identity = (
            self.approval_authorizer.identity_for(websocket)
            if type(self.approval_authorizer) is WebSocketAuthenticator
            else None
        )
        if (
            not isinstance(correlation_id, str)
            or not correlation_id.strip()
            or len(correlation_id) > 128
            or not isinstance(payload, dict)
            or set(payload)
            not in {
                frozenset({"videoBase64", "fileName", "targetContext", "accountId", "territory"}),
                frozenset({"videoBase64", "fileName", "mediaType", "targetContext", "accountId", "territory"}),
            }
        ):
            status, error, partial = (
                "INVALID_REQUEST",
                "CRE source-video request is invalid.",
                None,
            )
        elif identity is None:
            status, error, partial = (
                "AUTHENTICATION_REQUIRED",
                "Authenticate this WebSocket session before uploading source footage.",
                None,
            )
        elif self.cre_clip_rendering_service is None:
            status, error, partial = (
                "NOT_CONFIGURED",
                "Local source-video storage is unavailable.",
                None,
            )
        else:
            asset_summary = None
            try:
                encoded = payload["videoBase64"]
                file_name = payload["fileName"]
                media_type = payload.get("mediaType", "")
                allowed_media_types = {"", "video/mp4", "video/webm", "video/quicktime", "video/x-matroska"}
                allowed_extensions = {".mp4", ".webm", ".mov", ".mkv"}
                if (
                    not isinstance(encoded, str)
                    or not encoded
                    or len(encoded) > 4 * ((MAX_SOURCE_BYTES + 2) // 3)
                    or not isinstance(file_name, str)
                    or not file_name.strip()
                    or len(file_name) > 255
                    or any(ord(char) < 32 for char in file_name)
                    or not isinstance(media_type, str)
                    or media_type.lower() not in allowed_media_types
                ):
                    raise ValueError("Source video name, media type, or encoded size is invalid.")
                extension = "." + file_name.lower().rsplit(".", 1)[-1] if "." in file_name else ""
                if extension not in allowed_extensions:
                    raise ValueError("Unsupported source-video file type.")
                try:
                    source_bytes = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ValueError("Source video is not valid base64 data.") from exc
                if not source_bytes or len(source_bytes) > MAX_SOURCE_BYTES:
                    raise ValueError(
                        f"Source video must be non-empty and at most "
                        f"{MAX_SOURCE_BYTES // (1024 * 1024)} MiB."
                    )

                target = ContentTargetContext(payload["targetContext"])
                if target not in {
                    ContentTargetContext.YOUTUBE_SHORTS,
                    ContentTargetContext.INSTAGRAM_REELS,
                }:
                    raise ValueError("Unsupported short-form target.")
                account_id = payload["accountId"]
                territory = payload["territory"]
                probe_id = str(uuid4())
                staged_source = self.cre_clip_rendering_service.content_storage.stage_clip_source(
                    probe_id,
                    source_bytes,
                )
                try:
                    source_info = (
                        self.cre_clip_rendering_service.renderer.inspect_video_source(
                            staged_source
                        )
                    )
                finally:
                    self.cre_clip_rendering_service.content_storage.delete_clip_staging(
                        staged_source
                    )

                asset = SourceAsset(
                    asset_id=str(uuid4()),
                    owner_id=identity["principal"],
                    category=AssetCategory.FOOTAGE,
                    content_sha256=hashlib.sha256(source_bytes).hexdigest(),
                    provenance=SourceProvenance(
                        acquisition_method=AcquisitionMethod.OWNER_UPLOAD,
                        acquired_at=datetime.now(UTC),
                        notes=(
                            "Operator-uploaded footage. Rights are not independently "
                            "verified and remain subject to the existing CRE rights gate."
                        ),
                    ),
                    storage_key=str(uuid4()),
                )
                register_parameters = asset.model_dump(mode="json")
                register_context = self._cre_authorization_context(
                    websocket,
                    correlation_id,
                    action="content_register_asset",
                    parameters=register_parameters,
                )
                stored_asset = await asyncio.to_thread(
                    self.cre_clip_rendering_service.content_rights.register_asset,
                    asset,
                    source_stream=BytesIO(source_bytes),
                    context=register_context,
                    command_id=str(uuid4()),
                )
                asset_summary = {
                    "assetId": stored_asset.asset_id,
                    "sha256": stored_asset.content_sha256,
                    "sizeBytes": len(source_bytes),
                    "durationSeconds": source_info.duration_seconds,
                    "width": source_info.video_width,
                    "height": source_info.video_height,
                    "fileName": file_name,
                    "targetContext": target.value,
                    "accountId": account_id,
                    "territory": territory,
                    "rightsStatus": "PENDING_REVIEW",
                }
                current = self.approval_authorizer.identity_for(websocket)
                if current is None or current["sessionId"] != identity["sessionId"]:
                    raise PermissionError(
                        "The authenticated session expired; source registration completed "
                        "but its details were not returned."
                    )
                await self._send(
                    websocket,
                    {
                        "action": "CRE_SOURCE_VIDEO_RESULT",
                        "correlationId": correlation_id,
                        "success": True,
                        "status": "SOURCE_REGISTERED",
                        "payload": {"asset": asset_summary},
                        "provenance": PROVENANCE,
                    },
                )
                return
            except PermissionError as exc:
                status, error, partial = (
                    "RIGHTS_OR_AUTHORIZATION_DENIED",
                    str(exc),
                    asset_summary,
                )
            except (TypeError, ValueError) as exc:
                status, error, partial = "INVALID_SOURCE", str(exc), asset_summary
            except Exception:
                logger.exception("CRE source-video transcription request failed.")
                status, error, partial = (
                    "WORKFLOW_FAILED",
                    "Source-video processing failed; inspect CRE status before retrying.",
                    asset_summary,
                )

        await self._send(
            websocket,
            {
                "action": "CRE_SOURCE_VIDEO_RESULT",
                "correlationId": correlation_id,
                "success": False,
                "status": status,
                "error": error,
                "payload": {"asset": partial} if partial is not None else None,
                "provenance": PROVENANCE,
            },
        )

    async def _handle_cre_source_rights(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        correlation_id = message.get("correlationId")
        payload = message.get("payload")
        identity = (
            self.approval_authorizer.identity_for(websocket)
            if type(self.approval_authorizer) is WebSocketAuthenticator
            else None
        )
        if (
            not isinstance(correlation_id, str)
            or not correlation_id.strip()
            or len(correlation_id) > 128
            or not isinstance(payload, dict)
            or not isinstance(payload.get("step"), str)
        ):
            status, error, result = "INVALID_REQUEST", "CRE rights request is invalid.", None
        elif identity is None:
            status, error, result = (
                "AUTHENTICATION_REQUIRED",
                "Authenticate before accessing CRE rights records.",
                None,
            )
        elif self.cre_clip_rendering_service is None:
            status, error, result = "NOT_CONFIGURED", "CRE rights storage is unavailable.", None
        else:
            rights = self.cre_clip_rendering_service.content_rights
            principal = identity["principal"]

            async def invoke(action: str, parameters: dict[str, Any], function, **kwargs):
                context = self._cre_authorization_context(
                    websocket,
                    correlation_id,
                    action=action,
                    parameters=parameters,
                )
                return await asyncio.to_thread(
                    function,
                    context=context,
                    command_id=str(
                        uuid5(
                            NAMESPACE_URL,
                            f"cre-rights:{principal}:{action}:{json.dumps(parameters, sort_keys=True, default=str)}",
                        )
                    ),
                    **kwargs,
                )

            try:
                step = payload["step"]
                if step == "submit_claim":
                    expected = {
                        "step", "assetId", "targetContext", "accountId", "territory",
                        "rightsHolder", "authorityDescription", "evidenceKind",
                        "evidenceReference", "evidenceBase64", "terms",
                    }
                    if set(payload) != expected:
                        raise ValueError("Rights claim request has an invalid shape.")
                    asset = rights.store.get_asset(payload["assetId"])
                    if asset is None or asset.owner_id != principal:
                        raise PermissionError("Rights claim is outside the authenticated asset owner's account.")
                    target = ContentTargetContext(payload["targetContext"])
                    expected_platform = {
                        ContentTargetContext.YOUTUBE_SHORTS: ContentPlatform.YOUTUBE,
                        ContentTargetContext.INSTAGRAM_REELS: ContentPlatform.INSTAGRAM,
                    }.get(target)
                    terms = RightsTerms.model_validate(payload["terms"])
                    if (
                        expected_platform is None
                        or not isinstance(payload["accountId"], str)
                        or not isinstance(payload["territory"], str)
                        or terms.platform != expected_platform
                        or terms.account_scope != payload["accountId"]
                        or payload["territory"].upper() not in terms.territories
                        or terms.permitted_actions
                        != frozenset({RightsAction.ANALYZE, RightsAction.CREATE_DERIVATIVE})
                    ):
                        raise ValueError(
                            "Rights terms must exactly scope this account, territory, platform, "
                            "ANALYZE, and CREATE_DERIVATIVE only."
                        )
                    encoded_evidence = payload["evidenceBase64"]
                    if (
                        not isinstance(encoded_evidence, str)
                        or not encoded_evidence
                        or len(encoded_evidence)
                        > 4 * ((2_000_000 + 2) // 3)
                    ):
                        raise ValueError("Rights evidence exceeds the encoded 2 MB limit.")
                    try:
                        evidence_bytes = base64.b64decode(
                            encoded_evidence,
                            validate=True,
                        )
                    except (binascii.Error, ValueError, TypeError) as exc:
                        raise ValueError("Rights evidence must be valid base64 data.") from exc
                    if not evidence_bytes or len(evidence_bytes) > 2_000_000:
                        raise ValueError("Rights evidence must be between 1 byte and 2 MB.")
                    evidence_sha256 = hashlib.sha256(evidence_bytes).hexdigest()
                    rights_holder = payload["rightsHolder"]
                    authority_description = payload["authorityDescription"]
                    evidence_reference = payload["evidenceReference"]
                    seed = (
                        f"{asset.asset_id}:{asset.content_sha256}:{evidence_sha256}:"
                        f"{terms.fingerprint()}:{rights_holder}:{authority_description}"
                    )
                    claim_id = str(uuid5(NAMESPACE_URL, f"cre-claim:{seed}"))
                    evidence_id = str(uuid5(NAMESPACE_URL, f"cre-evidence:{seed}"))
                    grantor_id = str(uuid5(NAMESPACE_URL, f"cre-grantor:{seed}"))
                    claim = OwnershipClaim(
                        claim_id=claim_id,
                        asset_id=asset.asset_id,
                        claimant_id=principal,
                        rights_holder=rights_holder,
                        authority_description=authority_description,
                        created_at=asset.created_at,
                    )
                    await invoke(
                        "content_add_ownership_claim",
                        claim.model_dump(mode="json"),
                        rights.add_ownership_claim,
                        claim=claim,
                    )
                    evidence = RightsEvidence(
                        evidence_id=evidence_id,
                        asset_id=asset.asset_id,
                        claim_id=claim_id,
                        submitted_by=principal,
                        evidence_kind=payload["evidenceKind"],
                        evidence_bytes=evidence_bytes,
                        evidence_sha256=evidence_sha256,
                        grantor_identity=rights_holder,
                        grantor_authority=authority_description,
                        claimed_terms=terms,
                        created_at=asset.created_at,
                    )
                    await invoke(
                        "content_submit_rights_evidence",
                        evidence.model_dump(mode="json", exclude={"evidence_bytes"}),
                        rights.submit_evidence,
                        evidence=evidence,
                    )
                    grantor = GrantorIdentity(
                        grantor_id=grantor_id,
                        owner_id=principal,
                        claimed_identity=rights_holder,
                        verification_method=payload["evidenceKind"],
                        evidence_reference=evidence_reference,
                        evidence_sha256=evidence_sha256,
                        created_at=asset.created_at,
                    )
                    await invoke(
                        "content_register_grantor_identity",
                        grantor.model_dump(mode="json"),
                        rights.register_grantor_identity,
                        identity=grantor,
                    )
                    result = {
                        "assetId": asset.asset_id,
                        "assetSha256": asset.content_sha256,
                        "claimId": claim_id,
                        "evidenceId": evidence_id,
                        "grantorId": grantor_id,
                        "evidenceSha256": evidence_sha256,
                        "termsSha256": terms.fingerprint(),
                        "rightsStatus": "PENDING_INDEPENDENT_REVIEW",
                    }
                elif step == "read_evidence":
                    if set(payload) != {"step", "evidenceId"}:
                        raise ValueError("Evidence-read request has an invalid shape.")
                    evidence_id = payload["evidenceId"]
                    evidence = await invoke(
                        "content_read_rights_evidence",
                        {"evidence_id": evidence_id},
                        rights.get_evidence_for_review,
                        evidence_id=evidence_id,
                    )
                    result = {
                        "evidenceId": evidence.evidence_id,
                        "assetId": evidence.asset_id,
                        "claimId": evidence.claim_id,
                        "submittedBy": evidence.submitted_by,
                        "evidenceKind": evidence.evidence_kind,
                        "evidenceSha256": evidence.evidence_sha256,
                        "grantorIdentity": evidence.grantor_identity,
                        "grantorAuthority": evidence.grantor_authority,
                        "claimedTerms": evidence.claimed_terms.model_dump(mode="json"),
                        "termsSha256": evidence.claimed_terms.fingerprint(),
                        "evidenceBase64": base64.b64encode(
                            evidence.evidence_bytes
                        ).decode("ascii"),
                    }
                elif step == "review_initial":
                    if set(payload) != {
                        "step", "evidenceId", "grantorId", "decision", "reason",
                    }:
                        raise ValueError("Initial rights-review request has an invalid shape.")
                    decision = EvidenceDecision(payload["decision"])
                    evidence = rights.store.get_evidence(payload["evidenceId"])
                    grantor = rights.store.get_grantor_identity(payload["grantorId"])
                    claim = (
                        None
                        if evidence is None
                        else rights.store.get_ownership_claim(evidence.claim_id)
                    )
                    if evidence is None or grantor is None or claim is None:
                        raise LookupError("Evidence, ownership claim, or grantor identity does not exist.")
                    if (
                        claim.asset_id != evidence.asset_id
                        or claim.claimant_id != evidence.submitted_by
                        or claim.rights_holder != evidence.grantor_identity
                        or claim.authority_description != evidence.grantor_authority
                        or grantor.owner_id != evidence.submitted_by
                        or grantor.claimed_identity != evidence.grantor_identity
                        or grantor.evidence_sha256 != evidence.evidence_sha256
                    ):
                        raise PermissionError("Evidence, claim, and grantor identity are not bound to the same asset and document.")
                    verified_hash = (
                        evidence.claimed_terms.fingerprint()
                        if decision == EvidenceDecision.VERIFIED
                        else None
                    )
                    await invoke(
                        "content_verify_rights_evidence",
                        {
                            "evidence_id": evidence.evidence_id,
                            "decision": decision.value,
                            "verified_terms_sha256": verified_hash,
                        },
                        rights.verify_evidence,
                        evidence_id=evidence.evidence_id,
                        decision=decision,
                        verified_terms_sha256=verified_hash,
                    )
                    await invoke(
                        "content_review_grantor_identity",
                        {
                            "grantor_id": grantor.grantor_id,
                            "decision": decision.value,
                            "evidence_sha256": grantor.evidence_sha256,
                            "reason": payload["reason"],
                        },
                        rights.review_grantor_identity,
                        grantor_id=grantor.grantor_id,
                        decision=decision,
                        evidence_sha256=grantor.evidence_sha256,
                        reason=payload["reason"],
                    )
                    result = {
                        "evidenceId": evidence.evidence_id,
                        "grantorId": grantor.grantor_id,
                        "decision": decision.value,
                    }
                elif step == "register_authority":
                    if set(payload) != {
                        "step", "assetId", "claimId", "evidenceId", "grantorId", "rightsHolder",
                        "relationship", "scopeDescription", "evidenceReference",
                        "evidenceSha256",
                    }:
                        raise ValueError("Grantor-authority request has an invalid shape.")
                    asset = rights.store.get_asset(payload["assetId"])
                    claim = rights.store.get_ownership_claim(payload["claimId"])
                    evidence = rights.store.get_evidence(payload["evidenceId"])
                    grantor = rights.store.get_grantor_identity(payload["grantorId"])
                    if (
                        asset is None
                        or asset.owner_id != principal
                        or claim is None
                        or evidence is None
                        or grantor is None
                        or claim.asset_id != asset.asset_id
                        or evidence.asset_id != asset.asset_id
                        or evidence.claim_id != claim.claim_id
                        or grantor.owner_id != principal
                        or grantor.claimed_identity != payload["rightsHolder"]
                        or claim.rights_holder != payload["rightsHolder"]
                        or evidence.grantor_identity != payload["rightsHolder"]
                        or payload["evidenceSha256"] != evidence.evidence_sha256
                        or grantor.evidence_sha256 != evidence.evidence_sha256
                    ):
                        raise PermissionError("Grantor authority is outside the authenticated owner's account.")
                    authority_id = str(
                        uuid5(
                            NAMESPACE_URL,
                            f"cre-authority:{asset.asset_id}:{payload['grantorId']}:{payload['evidenceSha256']}",
                        )
                    )
                    authority = GrantorAuthority(
                        authority_id=authority_id,
                        grantor_id=payload["grantorId"],
                        asset_id=asset.asset_id,
                        rights_holder=payload["rightsHolder"],
                        relationship=payload["relationship"],
                        scope_description=payload["scopeDescription"],
                        evidence_reference=payload["evidenceReference"],
                        evidence_sha256=payload["evidenceSha256"],
                        created_at=asset.created_at,
                    )
                    await invoke(
                        "content_register_grantor_authority",
                        authority.model_dump(mode="json"),
                        rights.register_grantor_authority,
                        authority=authority,
                    )
                    result = {
                        "authorityId": authority_id,
                        "assetId": asset.asset_id,
                        "evidenceSha256": authority.evidence_sha256,
                        "status": "PENDING_REVIEW",
                    }
                elif step == "review_authority":
                    if set(payload) != {
                        "step", "authorityId", "decision", "evidenceSha256", "reason",
                    }:
                        raise ValueError("Grantor-authority review has an invalid shape.")
                    decision = EvidenceDecision(payload["decision"])
                    review = await invoke(
                        "content_review_grantor_authority",
                        {
                            "authority_id": payload["authorityId"],
                            "decision": decision.value,
                            "evidence_sha256": payload["evidenceSha256"],
                            "reason": payload["reason"],
                        },
                        rights.review_grantor_authority,
                        authority_id=payload["authorityId"],
                        decision=decision,
                        evidence_sha256=payload["evidenceSha256"],
                        reason=payload["reason"],
                    )
                    result = review.model_dump(mode="json")
                elif step == "create_agreement":
                    if set(payload) != {
                        "step", "agreementBase64", "evidenceReference", "terms",
                        "grantorId", "authorityId", "assetId", "rightsHolder",
                    }:
                        raise ValueError("Rights-agreement request has an invalid shape.")
                    asset = rights.store.get_asset(payload["assetId"])
                    if asset is None or asset.owner_id != principal:
                        raise PermissionError("Rights agreement is outside the authenticated owner's account.")
                    encoded_agreement = payload["agreementBase64"]
                    if (
                        not isinstance(encoded_agreement, str)
                        or not encoded_agreement
                        or len(encoded_agreement)
                        > 4 * ((2_000_000 + 2) // 3)
                    ):
                        raise ValueError("Agreement evidence exceeds the encoded 2 MB limit.")
                    agreement_bytes = base64.b64decode(encoded_agreement, validate=True)
                    if not agreement_bytes or len(agreement_bytes) > 2_000_000:
                        raise ValueError("Agreement evidence must be between 1 byte and 2 MB.")
                    agreement_sha256 = hashlib.sha256(agreement_bytes).hexdigest()
                    terms = RightsTerms.model_validate(payload["terms"])
                    agreement_id = str(
                        uuid5(
                            NAMESPACE_URL,
                            f"cre-agreement:{asset.asset_id}:{payload['authorityId']}:{agreement_sha256}:{terms.fingerprint()}",
                        )
                    )
                    agreement = RightsAgreement(
                        agreement_id=agreement_id,
                        version=1,
                        grantor_id=payload["grantorId"],
                        authority_id=payload["authorityId"],
                        rights_holder=payload["rightsHolder"],
                        grantee_owner_id=principal,
                        asset_ids=(asset.asset_id,),
                        terms=terms,
                        evidence_reference=payload["evidenceReference"],
                        evidence_sha256=agreement_sha256,
                        created_at=asset.created_at,
                    )
                    await invoke(
                        "content_create_rights_agreement",
                        agreement.model_dump(mode="json", exclude={"status"}),
                        rights.create_rights_agreement,
                        agreement=agreement,
                    )
                    result = {
                        "agreementId": agreement_id,
                        "agreementVersion": 1,
                        "agreementSha256": agreement.fingerprint(),
                        "evidenceSha256": agreement_sha256,
                        "status": "PENDING_REVIEW",
                    }
                elif step == "review_agreement":
                    if set(payload) != {
                        "step", "agreementId", "agreementVersion", "decision", "reason",
                    }:
                        raise ValueError("Rights-agreement review has an invalid shape.")
                    agreement = rights.store.get_rights_agreement(
                        payload["agreementId"],
                        payload["agreementVersion"],
                    )
                    if agreement is None:
                        raise LookupError("Rights agreement does not exist.")
                    decision = EvidenceDecision(payload["decision"])
                    review = await invoke(
                        "content_review_rights_agreement",
                        {
                            "agreement_id": agreement.agreement_id,
                            "agreement_version": agreement.version,
                            "decision": decision.value,
                            "agreement_sha256": agreement.fingerprint(),
                            "evidence_sha256": agreement.evidence_sha256,
                            "reason": payload["reason"],
                        },
                        rights.review_rights_agreement,
                        agreement_id=agreement.agreement_id,
                        agreement_version=agreement.version,
                        decision=decision,
                        agreement_sha256=agreement.fingerprint(),
                        evidence_sha256=agreement.evidence_sha256,
                        reason=payload["reason"],
                    )
                    result = review.model_dump(mode="json")
                elif step == "record_grant":
                    if set(payload) != {
                        "step", "assetId", "claimId", "evidenceId",
                        "agreementId", "agreementVersion",
                    }:
                        raise ValueError("Rights-grant request has an invalid shape.")
                    asset = rights.store.get_asset(payload["assetId"])
                    claim = rights.store.get_ownership_claim(payload["claimId"])
                    evidence = rights.store.get_evidence(payload["evidenceId"])
                    agreement = rights.store.get_rights_agreement(
                        payload["agreementId"],
                        payload["agreementVersion"],
                    )
                    if (
                        asset is None or claim is None or evidence is None
                        or agreement is None or asset.owner_id != principal
                        or claim.asset_id != asset.asset_id
                        or evidence.asset_id != asset.asset_id
                        or evidence.claim_id != claim.claim_id
                        or asset.asset_id not in agreement.asset_ids
                    ):
                        raise PermissionError("Grant records do not match the owned source asset.")
                    grant_id = str(
                        uuid5(
                            NAMESPACE_URL,
                            f"cre-grant:{asset.asset_id}:{evidence.evidence_sha256}:{agreement.fingerprint()}",
                        )
                    )
                    grant = RightsGrant(
                        grant_id=grant_id,
                        asset_id=asset.asset_id,
                        claim_id=claim.claim_id,
                        evidence_id=evidence.evidence_id,
                        grantor_identity=claim.rights_holder,
                        grantor_authority=claim.authority_description,
                        grantee_owner_id=principal,
                        terms=evidence.claimed_terms,
                        agreement_id=agreement.agreement_id,
                        agreement_version=agreement.version,
                        created_at=asset.created_at,
                    )
                    await invoke(
                        "content_create_rights_grant",
                        grant.model_dump(mode="json"),
                        rights.create_rights_grant,
                        grant=grant,
                    )
                    result = {
                        "grantId": grant_id,
                        "assetId": asset.asset_id,
                        "assetSha256": asset.content_sha256,
                        "status": "GRANT_RECORDED_REQUIRES_CURRENT_EVALUATION",
                    }
                else:
                    raise ValueError("Unsupported CRE rights workflow step.")
                current = self.approval_authorizer.identity_for(websocket)
                if current is None or current["sessionId"] != identity["sessionId"]:
                    raise PermissionError("Authenticated session expired before rights result delivery.")
                await self._send(
                    websocket,
                    {
                        "action": "CRE_SOURCE_RIGHTS_RESULT",
                        "correlationId": correlation_id,
                        "success": True,
                        "status": "RIGHTS_STEP_COMPLETED",
                        "payload": result,
                        "provenance": PROVENANCE,
                    },
                )
                return
            except PermissionError as exc:
                status, error = "RIGHTS_OR_AUTHORIZATION_DENIED", str(exc)
            except (TypeError, ValueError, LookupError) as exc:
                status, error = "INVALID_RIGHTS_REQUEST", str(exc)
            except Exception:
                logger.exception("CRE source rights operation failed.")
                status, error = "RIGHTS_OPERATION_FAILED", "Rights operation failed; inspect CRE status before retrying."

        await self._send(
            websocket,
            {
                "action": "CRE_SOURCE_RIGHTS_RESULT",
                "correlationId": correlation_id,
                "success": False,
                "status": status,
                "error": error,
                "provenance": PROVENANCE,
            },
        )

    async def _handle_cre_source_asset_status(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        correlation_id = message.get("correlationId")
        payload = message.get("payload")
        identity = (
            self.approval_authorizer.identity_for(websocket)
            if type(self.approval_authorizer) is WebSocketAuthenticator
            else None
        )
        if (
            not isinstance(correlation_id, str)
            or not correlation_id.strip()
            or len(correlation_id) > 128
            or not isinstance(payload, dict)
            or set(payload) != {"assetId", "targetContext", "accountId", "territory"}
        ):
            status, error = "INVALID_REQUEST", "CRE source asset status request is invalid."
        elif identity is None:
            status, error = "AUTHENTICATION_REQUIRED", "Authenticate before reading a source asset."
        elif self.cre_clip_rendering_service is None:
            status, error = "NOT_CONFIGURED", "CRE source storage is unavailable."
        else:
            try:
                asset_id = payload["assetId"]
                if not isinstance(asset_id, str) or not asset_id.strip() or len(asset_id) > 200:
                    raise ValueError("A registered source asset ID is required.")
                target = ContentTargetContext(payload["targetContext"])
                if target not in {
                    ContentTargetContext.YOUTUBE_SHORTS,
                    ContentTargetContext.INSTAGRAM_REELS,
                }:
                    raise ValueError("Unsupported short-form target.")
                clip_service = self.cre_clip_rendering_service
                asset = clip_service.content_rights.store.get_asset(asset_id)
                if (
                    asset is None
                    or asset.owner_id != identity["principal"]
                    or asset.category != AssetCategory.FOOTAGE
                    or asset.storage_key is None
                ):
                    raise PermissionError(
                        "Registered source asset is unavailable to this authenticated owner."
                    )
                source_bytes = clip_service.content_storage.read_verified_input(
                    asset.storage_key,
                    asset.content_sha256,
                )
                staged_source = clip_service.content_storage.stage_clip_source(
                    str(uuid4()),
                    source_bytes,
                )
                try:
                    source_info = clip_service.renderer.inspect_source_video(staged_source)
                finally:
                    clip_service.content_storage.delete_clip_staging(staged_source)
                use = self._cre_target_use(
                    asset_id=asset.asset_id,
                    asset_sha256=asset.content_sha256,
                    target=target,
                    account_id=payload["accountId"],
                    territory=payload["territory"],
                    action=RightsAction.ANALYZE,
                    duration_seconds=max(1, int(math.ceil(source_info.duration_seconds))),
                )
                parameters = use.model_dump(mode="json")
                context = self._cre_authorization_context(
                    websocket,
                    correlation_id,
                    action="content_evaluate_rights",
                    parameters=parameters,
                )
                decision = await asyncio.to_thread(
                    clip_service.content_rights.evaluate_rights,
                    use,
                    context=context,
                    command_id=str(
                        uuid5(
                            NAMESPACE_URL,
                            f"cre-rights-status:{asset.asset_id}:{asset.content_sha256}:{json.dumps(parameters, sort_keys=True)}",
                        )
                    ),
                )
                current = self.approval_authorizer.identity_for(websocket)
                if current is None or current["sessionId"] != identity["sessionId"]:
                    raise PermissionError("Authenticated session expired before status delivery.")
                await self._send(
                    websocket,
                    {
                        "action": "CRE_SOURCE_ASSET_STATUS_RESULT",
                        "correlationId": correlation_id,
                        "success": True,
                        "status": "ASSET_STATUS_AVAILABLE",
                        "payload": {
                            "assetId": asset.asset_id,
                            "sha256": asset.content_sha256,
                            "durationSeconds": source_info.duration_seconds,
                            "width": source_info.video_width,
                            "height": source_info.video_height,
                            "copyrightStatus": decision.copyright_state.value,
                            "analysisRightsStatus": decision.platform_rights_state.value,
                            "rightsDecisionId": decision.decision_id,
                            "reasonCodes": list(decision.reason_codes),
                        },
                        "provenance": PROVENANCE,
                    },
                )
                return
            except PermissionError as exc:
                status, error = "RIGHTS_OR_AUTHORIZATION_DENIED", str(exc)
            except (TypeError, ValueError, LookupError) as exc:
                status, error = "INVALID_SOURCE", str(exc)
            except Exception:
                logger.exception("CRE source asset status request failed.")
                status, error = "ASSET_STATUS_FAILED", "Source asset status could not be established."

        await self._send(
            websocket,
            {
                "action": "CRE_SOURCE_ASSET_STATUS_RESULT",
                "correlationId": correlation_id,
                "success": False,
                "status": status,
                "error": error,
                "provenance": PROVENANCE,
            },
        )

    async def _handle_cre_source_video_resume(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        correlation_id = message.get("correlationId")
        payload = message.get("payload")
        identity = (
            self.approval_authorizer.identity_for(websocket)
            if type(self.approval_authorizer) is WebSocketAuthenticator
            else None
        )
        if (
            not isinstance(correlation_id, str)
            or not correlation_id.strip()
            or len(correlation_id) > 128
            or not isinstance(payload, dict)
            or set(payload) != {"assetId", "targetContext", "accountId", "territory"}
        ):
            status, error = "INVALID_REQUEST", "CRE source-video resume request is invalid."
        elif identity is None:
            status, error = "AUTHENTICATION_REQUIRED", "Authenticate before resuming source processing."
        elif self.cre_intelligence_service is None or self.cre_clip_rendering_service is None:
            status, error = (
                "NOT_CONFIGURED",
                "Local transcription is unavailable; configure an existing "
                "Faster-Whisper model with ULTRON_CRE_FASTER_WHISPER_MODEL.",
            )
        else:
            try:
                asset_id = payload["assetId"]
                if not isinstance(asset_id, str) or not asset_id.strip() or len(asset_id) > 200:
                    raise ValueError("A registered source asset ID is required.")
                target = ContentTargetContext(payload["targetContext"])
                if target not in {
                    ContentTargetContext.YOUTUBE_SHORTS,
                    ContentTargetContext.INSTAGRAM_REELS,
                }:
                    raise ValueError("Unsupported short-form target.")
                asset = self.cre_clip_rendering_service.content_rights.store.get_asset(
                    asset_id
                )
                if (
                    asset is None
                    or asset.owner_id != identity["principal"]
                    or asset.category != AssetCategory.FOOTAGE
                    or asset.storage_key is None
                ):
                    raise PermissionError(
                        "Registered source asset is unavailable to this authenticated owner."
                    )
                source_bytes = (
                    self.cre_clip_rendering_service.content_storage.read_verified_input(
                        asset.storage_key,
                        asset.content_sha256,
                    )
                )
                if len(source_bytes) > MAX_SOURCE_BYTES:
                    raise ValueError("Registered source exceeds the current source size limit.")
                staged_source = self.cre_clip_rendering_service.content_storage.stage_clip_source(
                    str(uuid4()),
                    source_bytes,
                )
                try:
                    source_info = (
                        self.cre_clip_rendering_service.renderer.inspect_source_video(
                            staged_source
                        )
                    )
                finally:
                    self.cre_clip_rendering_service.content_storage.delete_clip_staging(
                        staged_source
                    )
                use = self._cre_target_use(
                    asset_id=asset.asset_id,
                    asset_sha256=asset.content_sha256,
                    target=target,
                    account_id=payload["accountId"],
                    territory=payload["territory"],
                    action=RightsAction.ANALYZE,
                    duration_seconds=max(1, int(math.ceil(source_info.duration_seconds))),
                )
                config = TranscriptionConfig(
                    max_duration_seconds=300,
                    max_input_bytes=32 * 1024 * 1024,
                )
                create_parameters = {
                    "use": use.model_dump(mode="json"),
                    "target_context": target.value,
                    "config": config.model_dump(mode="json"),
                }
                create_context = self._cre_authorization_context(
                    websocket,
                    correlation_id,
                    action="content_create_transcription_job",
                    parameters=create_parameters,
                )
                stable_seed = (
                    f"{asset.asset_id}:{asset.content_sha256}:"
                    f"{json.dumps(use.model_dump(mode='json'), sort_keys=True)}:"
                    f"{json.dumps(config.model_dump(mode='json'), sort_keys=True)}"
                )
                job = await asyncio.to_thread(
                    self.cre_intelligence_service.create_transcription_job,
                    use,
                    target_context=target,
                    config=config,
                    context=create_context,
                    command_id=str(uuid5(NAMESPACE_URL, f"cre-transcription:{stable_seed}")),
                )
                run_context = self._cre_authorization_context(
                    websocket,
                    correlation_id,
                    action="content_run_transcription_job",
                    parameters={"job_id": job.job_id},
                )
                transcript = await asyncio.to_thread(
                    self.cre_intelligence_service.run_transcription_job,
                    job.job_id,
                    context=run_context,
                    command_id=str(uuid5(NAMESPACE_URL, f"cre-run-transcription:{job.job_id}")),
                )
                staged_source = (
                    self.cre_clip_rendering_service.content_storage.stage_clip_source(
                        str(uuid4()),
                        source_bytes,
                    )
                )
                try:
                    scene_changes = (
                        self.cre_clip_rendering_service.renderer.detect_scene_changes(
                            staged_source
                        )
                    )
                finally:
                    self.cre_clip_rendering_service.content_storage.delete_clip_staging(
                        staged_source
                    )
                highlight_config = HighlightConfig(
                    min_duration_seconds=min(3.0, transcript.duration_seconds),
                    max_duration_seconds=min(60.0, transcript.duration_seconds),
                    max_candidates=5,
                    min_score=0,
                    scene_boundary_seconds=scene_changes,
                    scene_analysis_performed=True,
                )
                highlight_parameters = {
                    "transcript_id": transcript.transcript_id,
                    "config": highlight_config.model_dump(mode="json"),
                }
                highlight_context = self._cre_authorization_context(
                    websocket,
                    correlation_id,
                    action="content_generate_highlights",
                    parameters=highlight_parameters,
                )
                run = await asyncio.to_thread(
                    self.cre_intelligence_service.generate_highlights,
                    transcript.transcript_id,
                    config=highlight_config,
                    context=highlight_context,
                    command_id=str(uuid5(NAMESPACE_URL, f"cre-highlights:{transcript.transcript_id}")),
                )
                candidates = [
                    {
                        "candidateId": candidate.candidate_id,
                        "rank": candidate.rank,
                        "startSeconds": candidate.start_seconds,
                        "endSeconds": candidate.end_seconds,
                        "durationSeconds": candidate.duration_seconds,
                        "excerpt": " ".join(
                            segment.text
                            for segment in transcript.segments
                            if segment.segment_id in candidate.transcript_segment_ids
                        ),
                        "reason": candidate.explanation,
                        "score": candidate.score,
                        "warnings": list(candidate.warnings),
                        "visualEvidence": {
                            "sceneChangeCount": next(
                                (
                                    int(value.partition(":")[2])
                                    for value in candidate.evidence.get(
                                        "visual_boundary_alignment",
                                        (),
                                    )
                                    if value.startswith(
                                        "scene_changes_inside_window:"
                                    )
                                ),
                                0,
                            ),
                            "startAlignedToSceneCut": (
                                "scene_cut_near_start"
                                in candidate.evidence.get(
                                    "visual_boundary_alignment",
                                    (),
                                )
                            ),
                            "endAlignedToSceneCut": (
                                "scene_cut_near_end"
                                in candidate.evidence.get(
                                    "visual_boundary_alignment",
                                    (),
                                )
                            ),
                        },
                    }
                    for candidate in run.candidates[:5]
                ]
                current = self.approval_authorizer.identity_for(websocket)
                if current is None or current["sessionId"] != identity["sessionId"]:
                    raise PermissionError(
                        "The authenticated session expired; processing may have completed, "
                        "but results were not returned."
                    )
                await self._send(
                    websocket,
                    {
                        "action": "CRE_SOURCE_VIDEO_RESUME_RESULT",
                        "correlationId": correlation_id,
                        "success": True,
                        "status": "HIGHLIGHTS_READY",
                        "payload": {
                            "asset": {
                                "assetId": asset.asset_id,
                                "sha256": asset.content_sha256,
                                "durationSeconds": source_info.duration_seconds,
                                "width": source_info.video_width,
                                "height": source_info.video_height,
                                "rightsStatus": "CURRENT_ANALYSIS_RIGHTS_CLEARED",
                            },
                            "transcript": {
                                "transcriptId": transcript.transcript_id,
                                "durationSeconds": transcript.duration_seconds,
                                "language": transcript.language,
                                "segments": [
                                    {
                                        "startSeconds": segment.start_seconds,
                                        "endSeconds": segment.end_seconds,
                                        "text": segment.text,
                                    }
                                    for segment in transcript.segments
                                ],
                            },
                            "highlightRunId": run.run_id,
                            "visualAnalysis": {
                                "method": "ffmpeg_scene_change_detection",
                                "threshold": self.cre_clip_rendering_service.renderer.SCENE_CHANGE_THRESHOLD,
                                "semanticVideoUnderstanding": False,
                                "sceneChangeSeconds": list(scene_changes),
                            },
                            "candidates": candidates,
                        },
                        "provenance": PROVENANCE,
                    },
                )
                return
            except PermissionError as exc:
                status, error = "RIGHTS_OR_AUTHORIZATION_DENIED", str(exc)
            except (TypeError, ValueError, LookupError) as exc:
                status, error = "INVALID_SOURCE", str(exc)
            except Exception:
                logger.exception("CRE source-video resume failed.")
                status, error = (
                    "WORKFLOW_FAILED",
                    "Source-video processing failed; inspect CRE status before retrying.",
                )

        await self._send(
            websocket,
            {
                "action": "CRE_SOURCE_VIDEO_RESUME_RESULT",
                "correlationId": correlation_id,
                "success": False,
                "status": status,
                "error": error,
                "provenance": PROVENANCE,
            },
        )

    async def _handle_cre_source_clip(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        correlation_id = message.get("correlationId")
        payload = message.get("payload")
        identity = (
            self.approval_authorizer.identity_for(websocket)
            if type(self.approval_authorizer) is WebSocketAuthenticator
            else None
        )
        if (
            not isinstance(correlation_id, str)
            or not correlation_id.strip()
            or len(correlation_id) > 128
            or not isinstance(payload, dict)
            or set(payload)
            != {"candidateId", "highlightRunId", "targetContext", "accountId", "territory"}
        ):
            status, error = "INVALID_REQUEST", "CRE source clip request is invalid."
        elif identity is None:
            status, error = "AUTHENTICATION_REQUIRED", "Authenticate before rendering a clip."
        elif (
            self.cre_intelligence_service is None
            or self.cre_clip_rendering_service is None
        ):
            status, error = "NOT_CONFIGURED", "CRE source-video rendering is unavailable."
        else:
            try:
                target = ContentTargetContext(payload["targetContext"])
                candidate_context = self._cre_authorization_context(
                    websocket,
                    correlation_id,
                    action="content_read_highlight_candidates",
                    parameters={"run_id": payload["highlightRunId"]},
                )
                candidates = await asyncio.to_thread(
                    self.cre_intelligence_service.get_highlight_candidates,
                    payload["highlightRunId"],
                    context=candidate_context,
                    command_id=str(uuid4()),
                )
                candidate = next(
                    (
                        item
                        for item in candidates
                        if item.candidate_id == payload["candidateId"]
                    ),
                    None,
                )
                if candidate is None:
                    raise ValueError("Selected candidate is not in the authorized highlight run.")
                use = self._cre_target_use(
                    asset_id=candidate.asset_id,
                    asset_sha256=candidate.asset_sha256,
                    target=target,
                    account_id=payload["accountId"],
                    territory=payload["territory"],
                    action=RightsAction.CREATE_DERIVATIVE,
                    duration_seconds=max(1, int(math.ceil(candidate.duration_seconds))),
                )
                create_parameters = {
                    "candidate_id": candidate.candidate_id,
                    "rights_use": use.model_dump(mode="json"),
                }
                create_context = self._cre_authorization_context(
                    websocket,
                    correlation_id,
                    action="content_create_clip_render",
                    parameters=create_parameters,
                )
                render = await asyncio.to_thread(
                    self.cre_clip_rendering_service.create_clip_render,
                    candidate.candidate_id,
                    use,
                    context=create_context,
                    command_id=str(uuid4()),
                )
                run_context = self._cre_authorization_context(
                    websocket,
                    correlation_id,
                    action="content_run_clip_render",
                    parameters={"render_id": render.render_id},
                )
                render = await asyncio.to_thread(
                    self.cre_clip_rendering_service.run_clip_render,
                    render.render_id,
                    context=run_context,
                    command_id=str(uuid4()),
                )
                if render.state.value != "succeeded":
                    raise RuntimeError(
                        f"Clip rendering ended in state {render.state.value}."
                    )
                manifest_context = self._cre_authorization_context(
                    websocket,
                    correlation_id,
                    action="content_read_clip_review_manifest",
                    parameters={"render_id": render.render_id},
                )
                manifest = await asyncio.to_thread(
                    self.cre_clip_rendering_service.get_clip_review_manifest,
                    render.render_id,
                    context=manifest_context,
                )
                output_context = self._cre_authorization_context(
                    websocket,
                    correlation_id,
                    action="content_read_clip_render_output",
                    parameters={"render_id": render.render_id},
                )
                output = await asyncio.to_thread(
                    self.cre_clip_rendering_service.get_clip_output_bytes,
                    render.render_id,
                    context=output_context,
                )
                if hashlib.sha256(output).hexdigest() != render.output_sha256:
                    raise RuntimeError("Persisted clip output failed SHA-256 verification.")
                current = self.approval_authorizer.identity_for(websocket)
                if current is None or current["sessionId"] != identity["sessionId"]:
                    raise PermissionError(
                        "The authenticated session expired; the clip is stored but was not returned."
                    )
                await self._send(
                    websocket,
                    {
                        "action": "CRE_SOURCE_CLIP_RESULT",
                        "correlationId": correlation_id,
                        "success": True,
                        "status": "CLIP_READY",
                        "payload": {
                            "renderId": render.render_id,
                            "artifactId": render.output_key,
                            "sha256": render.output_sha256,
                            "durationSeconds": render.output_duration_seconds,
                            "width": 720,
                            "height": 1280,
                            "verification": manifest["technicalValidation"]["status"],
                            "manifest": manifest,
                            "mimeType": "video/mp4",
                            "base64": base64.b64encode(output).decode("ascii"),
                        },
                        "provenance": PROVENANCE,
                    },
                )
                return
            except PermissionError as exc:
                status, error = "RIGHTS_OR_AUTHORIZATION_DENIED", str(exc)
            except (TypeError, ValueError) as exc:
                status, error = "INVALID_REQUEST", str(exc)
            except Exception:
                logger.exception("CRE source clip render request failed.")
                status, error = "RENDER_FAILED", "Clip rendering failed; no publication occurred."

        await self._send(
            websocket,
            {
                "action": "CRE_SOURCE_CLIP_RESULT",
                "correlationId": correlation_id,
                "success": False,
                "status": status,
                "error": error,
                "provenance": PROVENANCE,
            },
        )

    async def _handle_cancel_task(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        correlation_id = message.get("correlationId")
        payload = message.get("payload")
        if (
            not isinstance(correlation_id, str)
            or not correlation_id.strip()
            or not isinstance(payload, dict)
            or set(payload) != {"taskId"}
            or not isinstance(payload.get("taskId"), str)
            or not payload["taskId"].strip()
        ):
            await self._send(
                websocket,
                {
                    "action": "TASK_CANCELLATION_RESULT",
                    "correlationId": correlation_id,
                    "success": False,
                    "status": "INVALID_REQUEST",
                    "error": "A correlationId and exact taskId are required.",
                    "provenance": PROVENANCE,
                },
            )
            return
        if (
            not isinstance(self.approval_authorizer, WebSocketAuthenticator)
            or self.task_service is None
            or self.task_service.task_manager.queue.database_identity == ":memory:"
            or self.task_service.task_manager.queue.database_identity
            != self.operation_store.database_identity
        ):
            await self._send(
                websocket,
                {
                    "action": "TASK_CANCELLATION_RESULT",
                    "correlationId": correlation_id,
                    "success": False,
                    "status": "CANCELLATION_UNAVAILABLE",
                    "error": "Trusted durable task cancellation is not configured.",
                    "provenance": PROVENANCE,
                },
            )
            return

        identity = self.approval_authorizer.identity_for(websocket)
        if identity is None:
            await self._send(
                websocket,
                {
                    "action": "TASK_CANCELLATION_RESULT",
                    "correlationId": correlation_id,
                    "success": False,
                    "status": "UNAUTHORIZED",
                    "error": "An active authenticated session is required.",
                    "provenance": PROVENANCE,
                },
            )
            return

        task_id = payload["taskId"]
        task = self.task_service.task_manager.get_task(task_id)
        if task is None:
            await self._send(
                websocket,
                {
                    "action": "TASK_CANCELLATION_RESULT",
                    "correlationId": correlation_id,
                    "success": False,
                    "status": "NOT_FOUND",
                    "taskId": task_id,
                    "provenance": PROVENANCE,
                },
            )
            return
        parent_operation_id = task.parent_operation_id
        operation = (
            self.operation_store.get_by_id(parent_operation_id)
            if isinstance(parent_operation_id, str)
            else None
        )
        authorization = (
            operation.get("authorization")
            if isinstance(operation, dict)
            else None
        )
        original_grant = (
            {
                "authenticated": authorization.get("authenticated"),
                "principal": authorization.get("authorizer"),
                "grantId": authorization.get("grantId"),
                "operationId": authorization.get("operationId"),
                "operationCorrelationId": authorization.get(
                    "operationCorrelationId"
                ),
                "requestCorrelationId": authorization.get(
                    "requestCorrelationId"
                ),
                "requestFingerprint": authorization.get("requestFingerprint"),
                "purpose": "execute",
                "action": authorization.get("action"),
                "resourceFingerprint": authorization.get(
                    "resourceFingerprint"
                ),
                "issuedAt": authorization.get("grantedAt"),
                "expiresAt": authorization.get("expiresAt"),
                "authorizationContext": authorization.get(
                    "authorizationContext"
                ),
                "signature": authorization.get("signature"),
            }
            if isinstance(authorization, dict)
            else {}
        )
        original_context = original_grant.get("authorizationContext")
        if (
            operation is None
            or operation.get("verb") != "execute_task"
            or operation.get("lifecycleState") != "EXECUTING"
            or operation.get("operationId") != parent_operation_id
            or task_id != f"task-{parent_operation_id}"
            or not isinstance(original_context, dict)
            or original_context.get("sessionId") != identity["sessionId"]
            or original_grant.get("principal") != identity["principal"]
            or not self._grant_is_valid(
                original_grant,
                operation,
                purpose="execute",
                request_correlation_id=original_grant.get(
                    "requestCorrelationId",
                    "",
                ),
            )
        ):
            await self._send(
                websocket,
                {
                    "action": "TASK_CANCELLATION_RESULT",
                    "correlationId": correlation_id,
                    "success": False,
                    "status": "UNAUTHORIZED",
                    "error": (
                        "Cancellation must use the active session that "
                        "authorized this exact task."
                    ),
                    "taskId": task_id,
                    "provenance": PROVENANCE,
                },
            )
            return

        cancellation_action = "cancel_task_before_dispatch"
        resource = resource_fingerprint(
            cancellation_action,
            {"taskId": task_id},
        )
        grant = self.approval_authorizer.authorize(
            websocket=websocket,
            operation=operation,
            purpose="cancel_task",
            request_correlation_id=correlation_id,
            action=cancellation_action,
            resource_fingerprint=resource,
        )
        if (
            not isinstance(grant, dict)
            or grant.get("requestCorrelationId") != correlation_id
            or not self.approval_authorizer.validate(
                grant=grant,
                operation=operation,
                purpose="cancel_task",
                action=cancellation_action,
                resource_fingerprint=resource,
            )
        ):
            await self._send(
                websocket,
                {
                    "action": "TASK_CANCELLATION_RESULT",
                    "correlationId": correlation_id,
                    "success": False,
                    "status": "AUTHORIZATION_EXPIRED",
                    "error": "A valid session-bound cancellation grant is required.",
                    "taskId": task_id,
                    "provenance": PROVENANCE,
                },
            )
            return

        result = self.approval_authorizer.validate_and_cancel_task(
            websocket,
            grant,
            self.operation_store,
            task_id=task_id,
        )
        if result is None:
            await self._send(
                websocket,
                {
                    "action": "TASK_CANCELLATION_RESULT",
                    "correlationId": correlation_id,
                    "success": False,
                    "status": "AUTHORIZATION_REPLAY_OR_EXPIRED",
                    "error": (
                        "The cancellation grant is expired, revoked, "
                        "mismatched, or already consumed."
                    ),
                    "taskId": task_id,
                    "provenance": PROVENANCE,
                },
            )
            return

        cancellation_before_dispatch = result["status"] == "CANCELLED"
        await self._send(
            websocket,
            {
                "action": "TASK_CANCELLATION_RESULT",
                "correlationId": correlation_id,
                "success": cancellation_before_dispatch,
                "status": result["status"],
                "taskId": task_id,
                "operationId": result["operationId"],
                "disposition": result["disposition"],
                "message": (
                    "Task was durably cancelled before consequential dispatch."
                    if cancellation_before_dispatch
                    else (
                        "A step may already have executed. The task remains "
                        "uncertain and cancellation did not stop an in-flight skill."
                    )
                ),
                "provenance": PROVENANCE,
            },
        )

    async def _handle_chat_message(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        supplied_correlation_id = message.get("correlationId")
        correlation_id = (
            supplied_correlation_id
            if isinstance(supplied_correlation_id, str)
            and supplied_correlation_id.strip()
            else str(uuid4())
        )
        if supplied_correlation_id is not None and not isinstance(
            supplied_correlation_id, str
        ):
            await self._send(
                websocket,
                {
                    "action": "CHAT_RESULT",
                    "correlationId": None,
                    "success": False,
                    "status": "INVALID_REQUEST",
                    "error": "correlationId must be a string.",
                    "provenance": PROVENANCE,
                },
            )
            return

        payload = message.get("payload")
        if not isinstance(payload, dict):
            await self._send(
                websocket,
                {
                    "action": "CHAT_RESULT",
                    "correlationId": correlation_id,
                    "success": False,
                    "status": "INVALID_MESSAGE",
                    "error": "Chat payload must be an object.",
                    "provenance": PROVENANCE,
                },
            )
            return

        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            await self._send(
                websocket,
                {
                    "action": "CHAT_RESULT",
                    "correlationId": correlation_id,
                    "success": False,
                    "status": "INVALID_MESSAGE",
                    "error": "A non-empty text message is required.",
                    "provenance": PROVENANCE,
                },
            )
            return
        text = text.strip()
        if len(text) > MAX_CHAT_MESSAGE_CHARS:
            await self._send(
                websocket,
                {
                    "action": "CHAT_RESULT",
                    "correlationId": correlation_id,
                    "success": False,
                    "status": "MESSAGE_TOO_LARGE",
                    "error": (
                        f"Chat messages must be at most "
                        f"{MAX_CHAT_MESSAGE_CHARS} characters."
                    ),
                    "provenance": PROVENANCE,
                },
            )
            return

        conversation = getattr(self.container, "conversation", None)
        if conversation is None or getattr(conversation, "llm", None) is None:
            await self._send(
                websocket,
                {
                    "action": "CHAT_RESULT",
                    "correlationId": correlation_id,
                    "success": False,
                    "status": "MODEL_UNAVAILABLE",
                    "error": "The configured ULTRON language model is unavailable.",
                    "provenance": PROVENANCE,
                },
            )
            return

        identity = self.approval_authorizer.identity_for(websocket)
        if identity is None:
            await self._send(
                websocket,
                {
                    "action": "CHAT_RESULT",
                    "correlationId": correlation_id,
                    "success": False,
                    "status": "AUTHENTICATION_EXPIRED",
                    "error": "The authenticated operator session has expired.",
                    "provenance": PROVENANCE,
                },
            )
            return

        try:
            session_id = identity["sessionId"]
            session_lock = self._chat_session_locks.setdefault(
                session_id,
                asyncio.Lock(),
            )

            async def run_chat() -> dict[str, Any]:
                async with session_lock:
                    context = conversation.context.build(text)
                    runtime_context = self._chat_runtime_context(text)
                    if runtime_context is not None:
                        context["authenticatedRuntimeTelemetry"] = runtime_context
                    return await conversation.handle_chat_async(
                        text,
                        context,
                        session_id,
                    )

            result = await asyncio.wait_for(
                run_chat(),
                timeout=CHAT_MODEL_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            await self._send(
                websocket,
                {
                    "action": "CHAT_RESULT",
                    "correlationId": correlation_id,
                    "success": False,
                    "status": "MODEL_TIMEOUT",
                    "error": "The configured ULTRON model did not respond before the chat timeout.",
                    "provenance": PROVENANCE,
                },
            )
            return

        except Exception:
            logger.exception("Authenticated ULTRON chat request failed")
            await self._send(
                websocket,
                {
                    "action": "CHAT_RESULT",
                    "correlationId": correlation_id,
                    "success": False,
                    "status": "CHAT_FAILED",
                    "error": "ULTRON could not complete this response.",
                    "provenance": PROVENANCE,
                },
            )
            return

        response = result.get("message") if isinstance(result, dict) else None
        if not isinstance(response, str) or not response.strip():
            await self._send(
                websocket,
                {
                    "action": "CHAT_RESULT",
                    "correlationId": correlation_id,
                    "success": False,
                    "status": "INVALID_MODEL_RESPONSE",
                    "error": "The configured language model returned no text response.",
                    "provenance": PROVENANCE,
                },
            )
            return

        await self._send(
            websocket,
            {
                "action": "CHAT_RESULT",
                "correlationId": correlation_id,
                "success": True,
                "status": "OK",
                "response": response,
                "provenance": PROVENANCE,
            },
        )

    def _chat_runtime_context(self, text: str) -> dict[str, Any] | None:
        keywords = (
            "system", "runtime", "host", "cpu", "memory", "ram",
            "agent", "uptime", "statistics", "stats", "hardware", "telemetry",
        )
        normalized = text.casefold()
        if not any(keyword in normalized for keyword in keywords):
            return None

        telemetry: dict[str, Any] = {
            "source": "authenticated ULTRON runtime gateway",
            "gatewayObservedAt": _utc(),
        }
        state = getattr(self.runtime, "state", None)
        runtime_fields: dict[str, Any] = {}
        running = getattr(state, "running", None)
        cycle_count = getattr(state, "cycle_count", None)
        if isinstance(running, bool):
            runtime_fields["running"] = running
        if isinstance(cycle_count, int) and not isinstance(cycle_count, bool):
            runtime_fields["cycleCount"] = cycle_count
        telemetry["runtime"] = runtime_fields or {"status": "NOT_REPORTED"}

        world = getattr(self.container, "world", None)
        if world is not None:
            try:
                snapshot = world.summary()
            except Exception:
                logger.exception("Unable to read the latest host snapshot for chat context")
                telemetry["hostStatus"] = "UNAVAILABLE"
            else:
                system = snapshot.get("system")
                if isinstance(system, dict):
                    telemetry["host"] = {
                        key: system[key]
                        for key in ("os", "hostname", "cpu_usage", "memory_usage")
                        if key in system and system[key] is not None
                    }
                last_updated = getattr(getattr(world, "state", None), "last_updated", None)
                if isinstance(last_updated, datetime):
                    telemetry["hostObservedAt"] = last_updated.isoformat()
                else:
                    telemetry["hostStatus"] = "TIMESTAMP_NOT_REPORTED"
        else:
            telemetry["hostStatus"] = "NOT_REPORTED"

        registry = getattr(
            getattr(self.container, "agent_orchestrator", None),
            "registry",
            None,
        )
        available = getattr(registry, "available", None)
        if callable(available):
            try:
                agents = available()
            except Exception:
                logger.exception("Unable to read the authenticated agent registry for chat context")
                telemetry["agentRegistry"] = {"status": "NOT_REPORTED"}
            else:
                telemetry["agentRegistry"] = {
                    "status": "REPORTED",
                    "registered": [
                        agent.get("name") if isinstance(agent, dict) else str(agent)
                        for agent in agents
                    ],
                }
        else:
            telemetry["agentRegistry"] = {"status": "NOT_EXPOSED"}
        return telemetry

    async def _handle_authenticate(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        authenticator = self.approval_authorizer
        if not isinstance(authenticator, WebSocketAuthenticator):
            await self._send(
                websocket,
                {
                    "action": "AUTHENTICATION_RESULT",
                    "correlationId": message.get("correlationId"),
                    "success": False,
                    "status": "AUTHENTICATION_UNAVAILABLE",
                    "error": "No production interface authenticator is configured.",
                    "provenance": PROVENANCE,
                },
            )
            return

        session = authenticator.authenticate(
            websocket,
            message.get("credential"),
        )
        if session is None:
            await self._send(
                websocket,
                {
                    "action": "AUTHENTICATION_RESULT",
                    "correlationId": message.get("correlationId"),
                    "success": False,
                    "status": "AUTHENTICATION_FAILED",
                    "error": "Authentication failed.",
                    "provenance": PROVENANCE,
                },
            )
            if authenticator.failed_attempts(websocket) >= authenticator.MAX_AUTH_FAILURES:
                close = getattr(websocket, "close", None)
                if callable(close):
                    await close(code=1008, reason="Authentication failed.")
            return

        await self._send(
            websocket,
            {
                "action": "AUTHENTICATION_RESULT",
                "correlationId": message.get("correlationId"),
                "success": True,
                "status": "AUTHENTICATED",
                "principal": session["principal"],
                "sessionId": session["sessionId"],
                "expiresAt": session["expiresAt"],
                "provenance": PROVENANCE,
            },
        )

    async def _handle_subscribe(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        requested = message.get("channels")

        channels = (
            {
                channel
                for channel in requested
                if isinstance(channel, str) and channel
            }
            if isinstance(requested, list)
            else set()
        )

        self._subscriptions[websocket] = channels

        await self._send(
            websocket,
            {
                "action": "SUBSCRIBE_ACK",
                "correlationId": message.get("correlationId"),
                "channels": sorted(channels),
                "provenance": PROVENANCE,
            },
        )

        # Deliver a real snapshot of every subscribed channel immediately.
        # This fixes the initialization race where LIVE clients had to wait
        # for the next cognitive cycle before any channel became populated.
        if (
            "omega" in channels
            and self._is_authenticated_connection(websocket)
            and self._omega_last_poll == 0.0
        ):
            self._refresh_omega()

        for channel in sorted(channels):
            try:
                await self._publish_channel(websocket, channel)
            except Exception as exc:  # pragma: no cover - defensive
                await self._error(
                    websocket,
                    message.get("correlationId"),
                    f"Unable to deliver snapshot for {channel}: {exc}",
                )

    async def _publish_channel(self, websocket: Any, channel: str) -> None:
        if (
            channel in {"trading", "omega"}
            and not self._is_authenticated_connection(websocket)
        ):
            payload = {
                "access": {
                    "available": False,
                    "status": "AUTHENTICATION_REQUIRED",
                }
            }
        elif (
            channel in {"operation", "command"}
            and not self._is_authenticated_connection(websocket)
        ):
            payload = (
                {"supportedActions": [], "activeOperations": []}
                if channel == "command"
                else []
            )
        else:
            payload = self._snapshot_channel(channel)
        await self._send(
            websocket,
            {
                "channel": channel,
                "payload": payload,
                "provenance": PROVENANCE,
            },
        )

    # ------------------------------------------------------------------
    # Real channel snapshots
    # ------------------------------------------------------------------

    def _snapshot_channel(self, channel: str) -> dict[str, Any] | list[Any]:
        if channel == "cognitiveState":
            return self._build_cognitive_state()
        if channel == "vitalMatrix":
            return self._build_vital_matrix()
        if channel == "pipeline":
            return self._build_pipeline()
        if channel == "world":
            return self._build_world()
        if channel == "omega":
            return self._build_omega()
        if channel == "trading":
            return self._build_trading()
        if channel == "observatory":
            return self._build_observatory()
        if channel == "device":
            return self._build_device()
        if channel == "command":
            return self._build_command_center()
        if channel == "operation":
            return list(self._operations)
        if channel == "runtimeEvents":
            return list(self._recent_events)[:MAX_RECENT_EVENTS]
        return {"channel": channel, "available": True}

    def _build_cognitive_state(self) -> dict[str, Any]:
        state = self.runtime.state
        last_cycle = getattr(state, "last_cycle", None)
        decision_action = "none"
        if last_cycle is not None:
            try:
                decision_action = last_cycle.decision.decision.action.value
            except Exception:  # pragma: no cover - defensive
                decision_action = "unknown"

        behavior = "IDLE"
        if state.running and state.cycle_count > 0:
            behavior = "ACTIVE"

        agents: list[dict[str, str]] | None = None
        agents_error: str | None = None
        try:
            registry = self.container.agent_orchestrator.registry
            raw = registry.available()
            agents = [
                {
                    "name": info.get("name") if isinstance(info, dict) else str(info),
                    "role": "agent",
                }
                for info in raw
            ]
        except Exception as exc:  # pragma: no cover - environment dependent
            agents_error = f"Agent registry unavailable ({type(exc).__name__})."

        return {
            "behavior": behavior,
            "headline": f"cycle_count={state.cycle_count} last_decision={decision_action}",
            "description": (
                "Real ULTRON runtime state: "
                f"running={state.running}, cycles={state.cycle_count}."
            ),
            "agents": agents,
            "agentsError": agents_error,
            "cycleCount": state.cycle_count,
            "running": bool(state.running),
            "lastDecision": decision_action,
            "uptimeSeconds": round(_now_epoch() - self._started_at, 1),
            "provenance": PROVENANCE,
        }

    def _build_world(self) -> dict[str, Any]:
        try:
            self.container.world.refresh()
            payload = self.container.world.summary()
            payload["provenance"] = PROVENANCE
            last_updated = getattr(
                getattr(self.container.world, "state", None),
                "last_updated",
                None,
            )
            payload["observedAt"] = (
                last_updated.isoformat()
                if isinstance(last_updated, datetime)
                else None
            )
            self._last_world_refresh = _now_epoch()
            return payload
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Unable to refresh authoritative world telemetry")
            return {
                "system": {"os": None, "hostname": None, "cpu_usage": None,
                           "memory_usage": None},
                "project": {"name": None, "directory": None, "git_branch": None},
                "runtime": {"internet_connected": None, "docker_running": None,
                            "active_applications": None},
                "entity_count": None,
                "entities": None,
                "provenance": PROVENANCE,
                "observedAt": None,
                "error": str(exc),
            }

    def _build_omega(self) -> dict[str, Any]:
        failure_detail = (
            self._omega_failure.get("detail")
            if self._omega_failure is not None
            else None
        )
        rejected = (
            isinstance(failure_detail, dict)
            and failure_detail.get("statusCode") == 401
        )
        authentication_state = (
            "AUTHENTICATED"
            if self._omega_state is not None and self._omega_failure is None
            else "NOT_CONFIGURED"
            if not OMEGA_INTEGRATION_TOKEN
            else "REJECTED"
            if rejected
            else "UNKNOWN"
        )
        integration = {
            "linkState": (
                "CONNECTED"
                if self._omega_state is not None and self._omega_failure is None
                else "DEGRADED"
                if self._omega_state is not None
                else "NOT_CONFIGURED"
                if not OMEGA_INTEGRATION_TOKEN
                else "OFFLINE"
            ),
            "authenticationState": authentication_state,
            "tokenConfigured": bool(OMEGA_INTEGRATION_TOKEN),
            "lastAttemptAt": self._omega_last_attempt_at,
            "lastSuccessfulExchangeAt": self._omega_last_success_at,
            "failure": self._omega_failure,
            "exchangeTrace": list(self._omega_exchange_trace),
        }
        if self._omega_state is not None:
            payload = dict(self._omega_state)
            trace = payload.get("communicationTrace")
            payload["communicationTrace"] = (
                [entry for entry in trace if isinstance(entry, dict)]
                if isinstance(trace, list)
                else None
            )
            omega_latency = payload.get("bridgeLatencyMs")
            payload["bridgeLatencyMs"] = (
                round(float(omega_latency), 1)
                if isinstance(omega_latency, (int, float)) and isfinite(omega_latency)
                else None
            )
            if payload.get("model") is None:
                payload["model"] = {"provider": None, "name": None, "available": None}
            payload["linkState"] = integration["linkState"]
            payload["integration"] = integration
            payload["capabilities"] = self._omega_capabilities or {
                "available": False,
                "error": {
                    "category": "not_reported",
                    "message": "OMEGA capability-registry data has not been received.",
                },
            }
            payload["provenance"] = PROVENANCE
            return payload

        model = {"provider": None, "name": None, "available": None}
        if self._omega_failure is not None:
            model = {"provider": None, "name": None, "available": None,
                     "reason": str(self._omega_failure.get("detail", ""))[:200]}
        return {
            "peer": "OMEGA",
            "linkState": integration["linkState"],
            "abilityState": "READ_ONLY_INSPECTION",
            "bridgeLatencyMs": None,
            "communicationTrace": None,
            "measuredAt": None,
            "provenance": PROVENANCE,
            "integration": integration,
            "capabilities": self._omega_capabilities or {
                "available": False,
                "error": {
                    "category": "not_reported",
                    "message": "OMEGA capability-registry data has not been received.",
                },
            },
            "model": model,
            "facts": [
                "OMEGA integration is not configured."
                if not OMEGA_INTEGRATION_TOKEN
                else "OMEGA state is unavailable; inspect the reported integration failure."
            ],
        }

    def _build_pipeline(self) -> dict[str, Any]:
        state = self.runtime.state
        last_cycle = getattr(state, "last_cycle", None)

        def stage_payload() -> list[dict[str, Any]]:
            if last_cycle is None:
                return []

            decision = getattr(last_cycle.decision, "decision", None)
            plan = getattr(last_cycle.plan, "plan", None)
            execution = getattr(last_cycle.execution, "record", None)
            reflection = last_cycle.reflection

            decision_confidence = 0.0
            if decision is not None:
                decision_confidence = float(getattr(decision, "confidence", 0.0) or 0.0)

            reflection_score = None
            if reflection is not None:
                reflection_score = getattr(reflection, "evaluation_score", None)

            execution_success = False
            execution_message = "no execution yet"
            if execution is not None:
                execution_success = bool(execution.success)
                execution_message = str(execution.message or "")

            plan_goal = "none"
            plan_steps = 0
            if plan is not None:
                plan_goal = str(getattr(plan, "goal", "none"))
                plan_steps = len(getattr(plan, "steps", []))

            decided_action = "unknown"
            if decision is not None:
                raw = getattr(decision, "action", None)
                decided_action = (
                    raw.value if hasattr(raw, "value") else str(raw)
                ) if raw is not None else "unknown"

            return [
                {
                    "id": "perception",
                    "title": "Perception",
                    "reasoningState": "COMPLETED" if state.cycle_count else "PENDING",
                    "decisionRationale": "Real EventBus input captured for the cycle.",
                    "inputs": [str(getattr(last_cycle.event, "payload", ""))[:200]],
                    "outputs": ["percept"],
                    "confidence": decision_confidence,
                    "evidence": ["EventBus"],
                    "executionState": "COMPLETED" if state.cycle_count else "PENDING",
                },
                {
                    "id": "understanding",
                    "title": "Understanding",
                    "reasoningState": "COMPLETED" if state.cycle_count else "PENDING",
                    "decisionRationale": "Real understanding engine output.",
                    "inputs": ["percept"],
                    "outputs": ["understanding"],
                    "confidence": decision_confidence,
                    "evidence": ["UnderstandingEngine"],
                    "executionState": "COMPLETED" if state.cycle_count else "PENDING",
                },
                {
                    "id": "context",
                    "title": "Context",
                    "reasoningState": "COMPLETED" if state.cycle_count else "PENDING",
                    "decisionRationale": "Real cognitive context assembled.",
                    "inputs": ["understanding"],
                    "outputs": ["context"],
                    "confidence": decision_confidence,
                    "evidence": ["ContextEngine"],
                    "executionState": "COMPLETED" if state.cycle_count else "PENDING",
                },
                {
                    "id": "decision",
                    "title": "Decision",
                    "reasoningState": "COMPLETED" if decision is not None else "PENDING",
                    "decisionRationale": f"action={decided_action}",
                    "inputs": ["context"],
                    "outputs": [decided_action],
                    "confidence": decision_confidence,
                    "evidence": ["DecisionEngine"],
                    "executionState": "COMPLETED" if decision is not None else "PENDING",
                },
                {
                    "id": "planning",
                    "title": "Planning",
                    "reasoningState": "COMPLETED" if plan is not None else "PENDING",
                    "decisionRationale": f"goal={plan_goal}",
                    "inputs": [decided_action],
                    "outputs": [f"{plan_steps} step(s)"],
                    "confidence": decision_confidence,
                    "evidence": ["PlanningService"],
                    "executionState": "COMPLETED" if plan is not None else "PENDING",
                },
                {
                    "id": "execution",
                    "title": "Execution",
                    "reasoningState": "COMPLETED" if execution is not None else "PENDING",
                    "decisionRationale": execution_message[:200],
                    "inputs": [plan_goal],
                    "outputs": ["success" if execution_success else "not successful"],
                    "confidence": decision_confidence,
                    "evidence": ["Executor", "SkillManager"],
                    "executionState": (
                        "COMPLETED" if execution is not None and execution_success
                        else "FAILED" if execution is not None
                        else "PENDING"
                    ),
                },
                {
                    "id": "reflection",
                    "title": "Reflection",
                    "reasoningState": "COMPLETED" if reflection is not None else "PENDING",
                    "decisionRationale": f"evaluation_score={reflection_score}",
                    "inputs": ["execution"],
                    "outputs": [str(reflection_score) if reflection_score is not None else "none"],
                    "confidence": decision_confidence,
                    "evidence": ["ReflectionService"],
                    "executionState": "COMPLETED" if reflection is not None else "PENDING",
                },
            ]

        stages = stage_payload()
        if not stages:
            stages = [
                {
                    "id": name,
                    "title": title,
                    "reasoningState": "PENDING",
                    "decisionRationale": "Awaiting the first real cognitive cycle.",
                    "inputs": [],
                    "outputs": [],
                    "confidence": 0.0,
                    "evidence": [],
                    "executionState": "PENDING",
                }
                for name, title in [
                    ("perception", "Perception"),
                    ("understanding", "Understanding"),
                    ("context", "Context"),
                    ("decision", "Decision"),
                    ("planning", "Planning"),
                    ("execution", "Execution"),
                    ("reflection", "Reflection"),
                ]
            ]
        return {"stages": stages, "cycleCount": state.cycle_count}

    def _metric(self, primary_value: str, question: str, **extra: Any) -> dict[str, Any]:
        block = {"primaryValue": primary_value, "question": question}
        block.update(extra)
        return block

    def _build_vital_matrix(self) -> dict[str, Any]:
        state = self.runtime.state
        omega_state = "UNKNOWN"
        if self._omega_state is not None:
            omega_state = "CONNECTED"
        elif not OMEGA_INTEGRATION_TOKEN:
            omega_state = "NOT CONFIGURED"
        elif self._omega_failure is not None:
            omega_state = "OFFLINE"

        skill_count: int | None = None
        action_count: int | None = None
        skills: list[dict[str, Any]] | None = None
        skills_error: str | None = None
        try:
            skills = self.skills.available_skills()
            skill_count = len(skills)
            action_count = sum(len(s.get("actions") or []) for s in skills)
        except Exception as exc:  # pragma: no cover - defensive
            skills = None
            skills_error = f"Skill registry unavailable ({type(exc).__name__})."

        agent_count: int | None = None
        agents: list[dict[str, Any]] | None = None
        agents_error: str | None = None
        try:
            agents = self.container.agent_orchestrator.registry.available()
            agent_count = len(agents)
        except Exception as exc:  # pragma: no cover - defensive
            agents = None
            agents_error = f"Agent registry unavailable ({type(exc).__name__})."

        last_decision = "none"
        last_cycle = getattr(state, "last_cycle", None)
        if last_cycle is not None:
            try:
                last_decision = last_cycle.decision.decision.action.value
            except Exception:  # pragma: no cover - defensive
                last_decision = "unknown"

        world = self._build_world()
        average_cycle_ms = self._avg_cycle_ms() if self._cycle_durations else None

        return {
            "health": self._metric(
                "NOT ASSESSED",
                "Has the runtime passed an authoritative health check?",
                qualifier="No authoritative runtime health check is exposed.",
                cpuUsagePct=world.get("system", {}).get("cpu_usage"),
                memoryUsagePct=world.get("system", {}).get("memory_usage"),
            ),
            "activity": self._metric(
                str(state.cycle_count),
                "How many cycles has the runtime completed?",
                running=bool(state.running),
            ),
            "focus": self._metric(
                last_decision,
                "What was the last runtime decision?",
                lastCycleAt=None,
            ),
            "agents": self._metric(
                str(agent_count) if agent_count is not None else "UNAVAILABLE",
                "How many agents are registered?",
                names=sorted(a.get("name", "?") for a in agents or [])
                if agents is not None else None,
                error=agents_error,
            ),
            "omega": self._metric(
                omega_state,
                "Is the OMEGA link connected?",
                lastGatewayExchangeLatencyMs=self._omega_latency_ms,
                tokenConfigured=bool(OMEGA_INTEGRATION_TOKEN),
            ),
            "currentTask": self._metric(
                "NOT REPORTED",
                "What task is currently running?",
                headline="Current task telemetry is not exposed.",
            ),
            "anomalies": self._metric(
                "NOT INSTRUMENTED",
                "Are there anomalies?",
                note="No anomaly detector has recorded a finding.",
                count=None,
            ),
            "capability": self._metric(
                f"{skill_count} skills / {action_count} actions"
                if skill_count is not None and action_count is not None
                else "UNAVAILABLE",
                "What capabilities are available?",
                skills=skill_count,
                actions=action_count,
                error=skills_error,
            ),
            "performance": self._metric(
                f"avg {average_cycle_ms:.0f}ms/cycle"
                if average_cycle_ms is not None else "UNAVAILABLE",
                "How is runtime performance?",
                cycles=state.cycle_count,
                avgCycleMs=round(average_cycle_ms, 1)
                if average_cycle_ms is not None else None,
            ),
            "attention": self._metric(
                f"{agent_count} agents"
                if agent_count is not None else "UNAVAILABLE",
                "Where is attention directed?",
                cycles=state.cycle_count,
            ),
        }

    def _avg_cycle_ms(self) -> float:
        if not self._cycle_durations:
            return 0.0
        return sum(self._cycle_durations) / len(self._cycle_durations) * 1000.0

    def _build_trading(self) -> dict[str, Any]:
        broker = getattr(self.container, "financial_paper_broker", None)
        portfolio = getattr(broker, "portfolio", None)

        financial = self._build_financial_telemetry(broker)

        if portfolio is None:
            return {
                "performance": {
                    "netRealizedPnlUsd": None,
                    "realizedPnlUsd": None,
                    "unrealizedPnlUsd": None,
                    "returnMtdPct": None,
                    "winRatePct": None,
                    "totalWins": None,
                    "totalLosses": None,
                    "sharpeRatio": None,
                    "sortinoRatio": None,
                    "profitFactor": None,
                    "maxDrawdownPct": None,
                    "annualizedVolPct": None,
                },
                "portfolio": {
                    "totalValueUsd": None,
                    "netDeltaExposureUsd": None,
                    "positions": None,
                },
                "efficiencyLoss": {"totalLossUsd": None, "breakdown": {}},
                "benchmarkComparison": {
                    "benchmarks": None,
                    "driversOfDifference": None,
                    "periodOverPeriod": None,
                    "alphaPct": None,
                },
                "decisionLog": [],
                "financial": financial,
                "provenance": PROVENANCE,
            }

        book_value = float(portfolio.cash)
        position_count = 0
        try:
            for symbol, position in (portfolio.positions or {}).items():
                qty = float(getattr(position, "quantity", 0) or 0)
                price = float(getattr(position, "average_price", 0) or 0)
                book_value += qty * price
                position_count += 1
        except Exception:  # pragma: no cover - defensive
            pass

        realized_pnl = float(getattr(portfolio, "realized_pnl", 0.0) or 0.0)
        trade_count = len(getattr(portfolio, "trades", []) or [])
        initial_capital = float(
            getattr(portfolio, "initial_capital", 100_000.0) or 0.0
        )

        performance = {
            "label": "real paper broker P&L",
            "totalReturnPct": round(realized_pnl / initial_capital * 100.0, 4)
            if initial_capital else None,
            "realizedPnlUsd": round(realized_pnl, 2),
            "unrealizedPnlUsd": None,
        }
        portfolio_block = {
            "label": "real paper broker portfolio",
            "cashUsd": round(float(portfolio.cash), 2),
            "initialCapitalUsd": round(initial_capital, 2),
            "bookValueUsd": round(book_value, 2),
            "positions": position_count,
            "trades": trade_count,
        }
        trades = list(getattr(portfolio, "trades", []) or [])
        commission_fees = sum(float(getattr(trade, "fees", 0.0) or 0.0) for trade in trades)
        efficiency = {
            "totalLossUsd": None,
            "breakdown": {
                "realizedLossUsd": round(max(0.0, -realized_pnl), 2),
                "unrealizedLossUsd": None,
                "commissionLossUsd": round(commission_fees, 2),
            },
        }
        return {
            "performance": performance,
            "portfolio": portfolio_block,
            "efficiencyLoss": efficiency,
            "benchmarkComparison": {
                "label": "no benchmark configured",
                "benchmark": "NONE",
                "alphaPct": None,
            },
            "decisionLog": [],
            "financial": financial,
            "provenance": PROVENANCE,
        }

    @staticmethod
    def _financial_error_category(error: Any) -> str:
        text = str(error).lower()
        if any(marker in text for marker in ("401", "unauthorized", "authentication", "invalid api")):
            return "authentication"
        if any(marker in text for marker in ("403", "forbidden", "permission", "subscription", "feed")):
            return "permission_or_subscription"
        if any(marker in text for marker in ("429", "rate limit", "too many requests")):
            return "rate_limit"
        if any(marker in text for marker in ("timeout", "connection", "network", "dns")):
            return "network"
        if "market" in text and "closed" in text:
            return "market_closed"
        return "provider_error"

    def _financial_market_data(self) -> dict[str, Any]:
        now = _now_epoch()
        if self._financial_market_cache is not None and now - self._financial_market_cache_at < 10.0:
            cached = dict(self._financial_market_cache)
            source_time = cached.get("latestValidatedAt")
            if isinstance(source_time, str):
                try:
                    parsed_time = datetime.fromisoformat(
                        source_time.replace("Z", "+00:00")
                    )
                    if parsed_time.tzinfo is not None:
                        age_seconds = (
                            datetime.now(UTC) - parsed_time.astimezone(UTC)
                        ).total_seconds()
                        cached["freshnessSeconds"] = age_seconds
                        age_limit = cached.get("freshnessLimitSeconds")
                        if (
                            age_seconds < 0
                            or isinstance(age_limit, (int, float))
                            and age_seconds > age_limit
                        ):
                            cached["state"] = "STALE"
                            cached["error"] = {
                                "category": "stale",
                                "message": "The last validated quote is stale and is not current market data.",
                            }
                except ValueError:
                    cached["state"] = "INVALID"
                    cached["error"] = {
                        "category": "invalid_timestamp",
                        "message": "The last market-data timestamp could not be parsed.",
                    }
            return cached

        service = getattr(self.container, "financial_trading_market_service", None)
        provider = os.environ.get("ULTRON_FINANCIAL_MARKET_PROVIDER", "yahoo").strip().lower()
        symbol = os.environ.get("ULTRON_AUTONOMOUS_SYMBOLS", "AAPL").split(",")[0].strip().upper() or "AAPL"
        if service is None:
            result = {
                "state": "UNAVAILABLE",
                "provider": provider,
                "source": "validated MarketDataService unavailable",
                "symbol": symbol,
                "latestPrice": None,
                "latestValidatedAt": None,
                "freshnessSeconds": None,
                "freshnessLimitSeconds": None,
                "bid": None,
                "ask": None,
                "volume": None,
                "error": {"category": "not_configured", "message": "Validated financial market-data service is not configured."},
            }
        else:
            try:
                market = service.get_current_price(symbol)
                adapter = getattr(service, "adapter", service)
                freshness_limit = float(
                    getattr(adapter, "max_quote_age_seconds", 30.0)
                )
                if not isinstance(market, dict) or not market.get("valid"):
                    reason = market.get("reason", "Market data validation failed.") if isinstance(market, dict) else "Market data response was malformed."
                    reason_text = str(reason).lower()
                    error_category = (
                        "stale"
                        if "stale" in reason_text or "future-dated" in reason_text
                        else self._financial_error_category(reason)
                    )
                    result = {
                        "state": (
                            "STALE"
                            if "stale" in str(reason).lower()
                            or "future-dated" in str(reason).lower()
                            else "MARKET_CLOSED"
                            if str(reason) == "Market is closed."
                            else "UNAVAILABLE"
                        ),
                        "provider": provider,
                        "source": type(adapter).__name__,
                        "symbol": symbol,
                        "latestPrice": None,
                        "latestValidatedAt": None,
                        "freshnessSeconds": market.get("age_seconds") if isinstance(market, dict) else None,
                        "freshnessLimitSeconds": market.get("max_age_seconds", freshness_limit) if isinstance(market, dict) else freshness_limit,
                        "bid": None,
                        "ask": None,
                        "volume": None,
                        "error": {"category": error_category, "message": str(reason)},
                    }
                else:
                    timestamp = market.get("timestamp")
                    bid = market.get("bid")
                    ask = market.get("ask")
                    volume = market.get("volume")
                    try:
                        volume = float(volume) if volume is not None else None
                    except (TypeError, ValueError):
                        volume = None
                    if volume is not None and (
                        not isfinite(volume) or volume < 0
                    ):
                        volume = None
                    result = {
                        "state": "MARKET_CLOSED" if market.get("reason") == "Market is closed." else "VALID",
                        "provider": str(market.get("provider", provider)),
                        "source": type(adapter).__name__,
                        "symbol": symbol,
                        "latestPrice": float(market["price"]),
                        "latestValidatedAt": timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp),
                        "freshnessSeconds": market.get("age_seconds"),
                        "freshnessLimitSeconds": freshness_limit,
                        "bid": bid,
                        "ask": ask,
                        "volume": volume,
                        "error": None,
                    }
            except Exception as exc:
                result = {
                    "state": "UNAVAILABLE",
                    "provider": provider,
                    "source": type(getattr(service, "adapter", service)).__name__,
                    "symbol": symbol,
                    "latestPrice": None,
                    "latestValidatedAt": None,
                    "freshnessSeconds": None,
                    "freshnessLimitSeconds": None,
                    "bid": None,
                    "ask": None,
                    "volume": None,
                    "error": {"category": self._financial_error_category(exc), "message": "Provider request failed; no tradable price available."},
                }

        self._financial_market_cache = result
        self._financial_market_cache_at = now
        return dict(result)

    def _build_financial_telemetry(self, broker: Any) -> dict[str, Any]:
        market = self._financial_market_data()
        controller = getattr(self.container, "financial_trading_controller", None)
        initialized = broker is not None and controller is not None
        broker_mode = str(getattr(broker, "mode", "simulation")) if broker is not None else "unavailable"

        snapshot: dict[str, Any] | None = None
        snapshot_error: dict[str, str] | None = None
        if broker is not None:
            try:
                prices = {market["symbol"]: market["latestPrice"]} if market.get("latestPrice") else {}
                get_snapshot = getattr(broker, "get_account_snapshot", None)
                snapshot_method = (
                    get_snapshot
                    if callable(get_snapshot)
                    else getattr(broker, "snapshot", None)
                )
                if not callable(snapshot_method):
                    raise TypeError("Broker does not expose an account snapshot method.")
                result = snapshot_method(prices)
                if not isinstance(result, dict):
                    raise TypeError("Broker returned a malformed account snapshot.")
                snapshot = result
            except Exception as exc:
                snapshot_error = {
                    "category": "snapshot_failed",
                    "message": (
                        f"Broker account snapshot failed ({type(exc).__name__}); "
                        "account values are unavailable."
                    ),
                }

        positions: list[dict[str, Any]] | None = None
        position_snapshot = snapshot.get("positions") if snapshot is not None else None
        if isinstance(position_snapshot, dict):
            positions = []
            for symbol, position in position_snapshot.items():
                if (
                    not isinstance(position, dict)
                    or "quantity" not in position
                    or "average_price" not in position
                ):
                    positions = None
                    snapshot_error = {
                        "category": "incomplete_snapshot",
                        "message": "Broker position data omitted quantity or average price.",
                    }
                    break
                try:
                    quantity = float(position["quantity"])
                    average = float(position["average_price"])
                except (TypeError, ValueError):
                    positions = None
                    snapshot_error = {
                        "category": "invalid_snapshot",
                        "message": "Broker position data contained a non-numeric value.",
                    }
                    break
                if not isfinite(quantity) or not isfinite(average):
                    positions = None
                    snapshot_error = {
                        "category": "invalid_snapshot",
                        "message": "Broker position data contained a non-finite value.",
                    }
                    break
                positions.append({
                    "symbol": str(symbol),
                    "quantity": quantity,
                    "averagePriceUsd": average,
                    "notionalUsd": quantity * average,
                })
        elif snapshot is not None:
            snapshot_error = {
                "category": "incomplete_snapshot",
                "message": "Broker snapshot did not include an authoritative positions map.",
            }
        if snapshot is not None and (
            snapshot.get("cash") is None or snapshot.get("equity") is None
        ):
            snapshot_error = {
                "category": "incomplete_snapshot",
                "message": "Broker snapshot did not include cash and equity values.",
            }

        trades: list[dict[str, Any]] | None = None if broker is None else []
        for trade in list(getattr(getattr(broker, "portfolio", None), "trades", []) or []):
            trades.append({
                "symbol": trade.symbol,
                "side": trade.side.value,
                "quantity": float(trade.quantity),
                "priceUsd": float(trade.price),
                "feesUsd": float(trade.fees),
                "realizedPnlUsd": float(trade.realized_pnl),
                "timestamp": trade.timestamp.isoformat(),
            })

        risk_engines = []
        if controller is not None:
            for name in ("price_guard", "daily_loss_guard", "trading_control", "reconciler", "idempotency"):
                if getattr(controller, name, None) is not None:
                    risk_engines.append(name)

        return {
            "initialization": {
                "state": "READY" if initialized else "UNAVAILABLE",
                "controllerConfigured": controller is not None,
                "brokerConfigured": broker is not None,
                "updatedAt": _utc(),
            },
            "marketData": market,
            "execution": {
                "mode": broker_mode,
                "source": type(broker).__name__ if broker is not None else "unavailable",
                "orderSubmissionFromDashboard": False,
                "liveEnabled": bool(
                    getattr(broker, "supports_live_orders", False)
                    and getattr(broker, "live_submission_enabled", False)
                ),
            },
            "portfolio": {
                "source": type(broker).__name__ if broker is not None else "unavailable",
                "state": (
                    "AVAILABLE"
                    if snapshot is not None
                    and positions is not None
                    and snapshot.get("cash") is not None
                    and snapshot.get("equity") is not None
                    else "PARTIAL"
                    if snapshot is not None
                    else "UNAVAILABLE"
                ),
                "cashUsd": snapshot.get("cash") if snapshot is not None else None,
                "equityUsd": snapshot.get("equity") if snapshot is not None else None,
                "positions": positions,
                "updatedAt": _utc(),
                "error": snapshot_error or (
                    {
                        "category": "not_configured",
                        "message": "Broker account snapshot is not configured.",
                    }
                    if broker is None else None
                ),
            },
            "risk": {
                "state": "READY" if risk_engines else "UNAVAILABLE",
                "engines": risk_engines,
                "rejections": {"state": "NOT_INSTRUMENTED", "count": None},
            },
            "executions": {
                "source": type(broker).__name__ if broker is not None else "unavailable",
                "records": trades,
                "orderSubmissionFromDashboard": False,
            },
            "diagnostics": [market["error"]] if market.get("error") else [],
        }

    def _build_observatory(self) -> dict[str, Any]:
        state = self.runtime.state
        world = self._build_world()
        skill_count = 0
        action_count = 0
        try:
            skills = self.skills.available_skills()
            skill_count = len(skills)
            action_count = sum(len(s.get("actions") or []) for s in skills)
        except Exception:  # pragma: no cover - defensive
            pass
        agent_count = 0
        try:
            agent_count = len(self.container.agent_orchestrator.registry.available())
        except Exception:  # pragma: no cover - defensive
            pass

        dimensions = [
            {
                "id": "cycle_throughput",
                "label": "Cognitive cycles",
                "value": float(state.cycle_count),
                "unit": "cycles",
                "detail": f"avg {self._avg_cycle_ms():.0f}ms/cycle" if state.cycle_count else "no cycles yet",
            },
            {
                "id": "capability_surface",
                "label": "Capabilities",
                "value": float(action_count),
                "unit": "actions",
                "detail": f"{skill_count} skills",
            },
            {
                "id": "agent_pool",
                "label": "Agents",
                "value": float(agent_count),
                "unit": "agents",
                "detail": "registered agents",
            },
            {
                "id": "world_observations",
                "label": "World entities",
                "value": float(world.get("entity_count") or 0),
                "unit": "entities",
                "detail": "observed entities",
            },
            {
                "id": "event_volume",
                "label": "Recent events",
                "value": float(len(self._recent_events)),
                "unit": "events",
                "detail": "ring buffer depth",
            },
            {
                "id": "omega_link",
                "label": "OMEGA link",
                "value": 1.0 if self._omega_state is not None else 0.0,
                "unit": "connected",
                "detail": f"latency {round(float(self._omega_latency_ms or 0), 1)}ms"
                if self._omega_state is not None else "offline",
            },
        ]
        swot = {
            "strengths": [
                "Real EventBus and cognitive runtime are publishing live state.",
                "Real OMEGA integration service is reachable.",
                "Real skills and agents are registered.",
            ] if self._omega_state is not None else [
                "Real EventBus and cognitive runtime are publishing live state.",
                "Real skills and agents are registered.",
            ],
            "weaknesses": [
                "Tensor inference rate is 0.0Hz (no tensor hardware present).",
            ],
            "opportunities": [
                "Authorized ULTRON promotion path is operational for the gateway.",
            ],
            "threats": [
                "OMEGA link requires OMEGA_INTEGRATION_TOKEN at gateway start.",
            ],
        }
        return {"dimensions": dimensions, "swot": swot, "provenance": PROVENANCE}

    def _build_device(self) -> dict[str, Any]:
        cpu_count = os.cpu_count()
        telemetry_errors: list[dict[str, str]] = []
        load1: float | None = None
        try:
            with open("/proc/loadavg", "r", encoding="utf-8") as fh:
                load_values = fh.read().split()
            if load_values:
                value = float(load_values[0])
                load1 = value if isfinite(value) else None
        except OSError:  # pragma: no cover - platform dependent
            load1 = None
            telemetry_errors.append({
                "source": "cpu.load1",
                "message": "Host load telemetry could not be read.",
            })
        except ValueError:  # pragma: no cover - malformed host telemetry
            load1 = None
            telemetry_errors.append({
                "source": "cpu.load1",
                "message": "Host load telemetry was malformed.",
            })
        if cpu_count is None:
            telemetry_errors.append({
                "source": "cpu.cores",
                "message": "Host CPU core count is unavailable.",
            })

        mem_total_kb: float | None = None
        mem_available_kb: float | None = None
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        mem_total_kb = float(line.split()[1])
                    elif line.startswith("MemAvailable:"):
                        mem_available_kb = float(line.split()[1])
        except OSError:  # pragma: no cover - platform dependent
            mem_total_kb = None
            mem_available_kb = None
            telemetry_errors.append({
                "source": "memory",
                "message": "Host memory telemetry could not be read.",
            })
        except (IndexError, ValueError):  # pragma: no cover - malformed host telemetry
            mem_total_kb = None
            mem_available_kb = None
            telemetry_errors.append({
                "source": "memory",
                "message": "Host memory telemetry was malformed.",
            })
        if (
            mem_total_kb is not None and not isfinite(mem_total_kb)
            or mem_available_kb is not None and not isfinite(mem_available_kb)
        ):
            mem_total_kb = None
            mem_available_kb = None
            telemetry_errors.append({
                "source": "memory",
                "message": "Host memory telemetry was non-finite.",
            })
        mem_used_pct: float | None = None
        if mem_total_kb is not None and mem_available_kb is not None and mem_total_kb > 0:
            mem_used_pct = round(
                (mem_total_kb - mem_available_kb) / mem_total_kb * 100.0, 1
            )

        disk_total_gb: float | None = None
        try:
            import shutil

            usage = shutil.disk_usage("/")
            disk_total_gb = round(usage.total / (1024 ** 3), 1)
        except OSError:  # pragma: no cover - platform dependent
            disk_total_gb = None
            telemetry_errors.append({
                "source": "storage",
                "message": "Host storage telemetry could not be read.",
            })

        skills: list[dict[str, Any]] | None = None
        skills_error: str | None = None
        try:
            skills = []
            for skill in self.skills.available_skills():
                skills.append({
                    "name": skill.get("name"),
                    "version": skill.get("version"),
                    "actions": skill.get("actions") or [],
                })
        except Exception as exc:  # pragma: no cover - defensive
            skills = None
            skills_error = f"Skill registry unavailable ({type(exc).__name__})."
            telemetry_errors.append({
                "source": "capabilities",
                "message": skills_error,
            })

        assessment = {
            "status": "NOT_ASSESSED",
            "verdict": "Host metrics are reported without an authoritative health assessment.",
            "provenance": PROVENANCE,
            "measuredAt": _utc(),
            "errors": telemetry_errors,
        }
        return {
            "assessment": assessment,
            "hardware": {
                "cpu": {
                    "cores": cpu_count,
                    "load1": load1,
                    "model": None,
                },
                "memory": {
                    "totalGb": round(mem_total_kb / (1024 * 1024), 1)
                    if mem_total_kb is not None else None,
                    "usedPct": mem_used_pct,
                },
                "storage": {
                    "totalGb": disk_total_gb,
                },
            },
            "capabilities": skills,
            "capabilitiesError": skills_error,
            "provenance": PROVENANCE,
        }

    def _build_command_center(self) -> dict[str, Any]:
        active = [
            op for op in list(self._operations)
            if op.get("lifecycleState") in (
                "SUGGESTED", "APPROVED", "EXECUTING", "UNVERIFIED", "UNCERTAIN"
            )
        ]
        supported_actions = [
            action
            for action in _SUPPORTED_ACTIONS
            if action["approval"] != "required"
            or self.approval_authorizer is not None
        ]
        return {
            "supportedActions": supported_actions,
            "activeOperations": active,
            "provenance": PROVENANCE,
        }

    def _record_operation(self, op: dict[str, Any]) -> None:
        lifecycle = op.get("lifecycleState") if isinstance(op, dict) else None
        if lifecycle not in VALID_OPERATION_LIFECYCLE:
            raise ValueError(
                "Invalid operation lifecycle state; refusing to emit operation telemetry."
            )
        op_id = str(uuid4())
        op["operationId"] = op_id
        op["id"] = op_id
        op["provenance"] = PROVENANCE
        op["createdAt"] = _utc()
        op["updatedAt"] = _utc()
        op["authorization"] = {
            "policy": "GOVERNOR",
            "required": bool(
                op.get("lifecycleState") == "SUGGESTED"
                or op.get("verb") in _CONSEQUENTIAL_VERBS
            ),
            "granted": False,
            "authorizer": None,
            "grantedAt": None,
        }
        self._validate_operation_evidence(op)
        self.operation_store.create(op)
        self._operations.appendleft(op)

    def _find_operation(self, operation_id: str) -> dict[str, Any] | None:
        for op in self._operations:
            if op.get("operationId") == operation_id or op.get("id") == operation_id:
                return op
        op = self.operation_store.get_by_id(operation_id)
        if op is not None:
            self._operations.appendleft(op)
        return op

    def _op_update(self, op: dict[str, Any], **changes: Any) -> None:
        updated = {**op, **changes, "updatedAt": _utc()}
        self._validate_operation_evidence(updated)
        self.operation_store.update(updated)
        op.clear()
        op.update(updated)

    @staticmethod
    def _validate_operation_evidence(op: dict[str, Any]) -> None:
        lifecycle = op.get("lifecycleState")
        result = op.get("executionResult")
        if lifecycle in {"COMPLETED", "VERIFIED"} and (
            not isinstance(result, dict) or result.get("success") is not True
        ):
            raise ValueError(
                f"{lifecycle} operations require successful execution evidence."
            )
        if lifecycle in {"UNVERIFIED", "UNCERTAIN"} and not isinstance(
            result, dict
        ):
            raise ValueError(
                f"{lifecycle} operations require an execution result."
            )
        if lifecycle == "VERIFIED" and (
            not isinstance(op.get("verificationAudit"), dict)
            or op["verificationAudit"].get("verified") is not True
            or op["verificationAudit"].get("outcome") != "CONFIRMED_SUCCESS"
            or op["verificationAudit"].get("operationId") != op.get("operationId")
            or op["verificationAudit"].get("requestFingerprint")
            != op.get("requestFingerprint")
            or not op["verificationAudit"].get("verifier")
            or not op["verificationAudit"].get("provenance")
            or not op["verificationAudit"].get("observedAt")
        ):
            raise ValueError(
                "VERIFIED operations require identity-bound independent verification evidence."
            )

    def _find_request(self, correlation_id: str) -> dict[str, Any] | None:
        return self.operation_store.get_by_correlation(correlation_id)

    @staticmethod
    def _request_fingerprint(
        verb: str,
        command_text: str,
        parameters: dict[str, Any],
    ) -> str:
        canonical = json.dumps(
            {
                "verb": verb,
                "commandText": command_text.strip(),
                "parameters": parameters,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def _respond_to_duplicate(
        self,
        websocket: Any,
        correlation_id: str,
        op: dict[str, Any],
    ) -> None:
        lifecycle = op.get("lifecycleState")
        status = (
            "PENDING_APPROVAL" if lifecycle == "SUGGESTED" else lifecycle
        )
        success = lifecycle in {
            "SUGGESTED", "APPROVED", "EXECUTING", "COMPLETED", "VERIFIED"
        }
        await self._send(
            websocket,
            {
                "action": "COMMAND_RESULT",
                "correlationId": correlation_id,
                "success": success,
                "status": status,
                "result": {
                    "operationId": op["operationId"],
                    "lifecycleState": lifecycle,
                    "executionResult": op.get("executionResult"),
                    "duplicate": True,
                },
                "provenance": PROVENANCE,
            },
        )

    # ------------------------------------------------------------------
    # Command dispatch (real execution, real authorization)
    # ------------------------------------------------------------------

    async def _handle_dispatch(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        supplied_correlation_id = message.get("correlationId")
        correlation_id = (
            supplied_correlation_id
            if isinstance(supplied_correlation_id, str)
            and supplied_correlation_id.strip()
            else str(uuid4())
        )
        payload = message.get("payload")

        if not isinstance(payload, dict):
            await self._error(
                websocket,
                correlation_id,
                "Command payload must be an object.",
            )
            return

        command_text = payload.get("commandText")
        action_verb = payload.get("actionVerb")
        parameters = payload.get("parameters")

        if not isinstance(command_text, str) or not command_text.strip():
            await self._error(
                websocket,
                correlation_id,
                "commandText is required.",
            )
            return

        if not isinstance(action_verb, str) or not action_verb.strip():
            await self._error(
                websocket,
                correlation_id,
                "actionVerb is required.",
            )
            return

        if not isinstance(parameters, dict):
            await self._error(
                websocket,
                correlation_id,
                "parameters must be an object.",
            )
            return
        if supplied_correlation_id is not None and not isinstance(
            supplied_correlation_id, str
        ):
            await self._error(
                websocket,
                None,
                "correlationId must be a string.",
            )
            return

        verb = action_verb.strip().lower()
        if verb == "execute_task":
            if self.task_service is None:
                await self._send(
                    websocket,
                    {
                        "action": "COMMAND_RESULT",
                        "correlationId": correlation_id,
                        "success": False,
                        "status": "TASK_EXECUTION_UNAVAILABLE",
                        "error": "The durable trusted task service is not configured.",
                        "provenance": PROVENANCE,
                    },
                )
                return
            try:
                self.task_service.validate_authorized_plan(parameters)
            except ValueError as exc:
                await self._send(
                    websocket,
                    {
                        "action": "COMMAND_RESULT",
                        "correlationId": correlation_id,
                        "success": False,
                        "status": "INVALID_TASK_PLAN",
                        "error": str(exc),
                        "provenance": PROVENANCE,
                    },
                )
                return
        fingerprint = self._request_fingerprint(
            verb,
            command_text,
            parameters,
        )
        if verb in _ULTRON_VERBS:
            await self._send(
                websocket,
                {
                    "action": "COMMAND_RESULT",
                    "correlationId": correlation_id,
                    "success": False,
                    "status": "PLAN_APPROVAL_UNAVAILABLE",
                    "error": (
                        "Cognitive plans cannot execute from a broad operation "
                        "approval; approve an exact supported action instead."
                    ),
                    "provenance": PROVENANCE,
                },
            )
            return
        existing = self._find_request(correlation_id)
        if existing is not None:
            if existing.get("requestFingerprint") != fingerprint:
                await self._send(
                    websocket,
                    {
                        "action": "COMMAND_RESULT",
                        "correlationId": correlation_id,
                        "success": False,
                        "status": "IDEMPOTENCY_CONFLICT",
                        "error": (
                            "This correlationId was already used for a "
                            "different command."
                        ),
                        "provenance": PROVENANCE,
                    },
                )
                return
            await self._respond_to_duplicate(
                websocket,
                correlation_id,
                existing,
            )
            return

        if verb in _OMEGA_VERBS:
            await self._dispatch_omega(
                websocket,
                correlation_id,
                verb,
                parameters,
                fingerprint,
            )
            return

        if verb in _CONSEQUENTIAL_VERBS:
            if self.approval_authorizer is None:
                await self._send(
                    websocket,
                    {
                        "action": "COMMAND_RESULT",
                        "correlationId": correlation_id,
                        "success": False,
                        "status": "AUTHENTICATION_UNAVAILABLE",
                        "error": (
                            "Consequential operations are disabled until a "
                            "trusted interface authorizer is configured."
                        ),
                        "provenance": PROVENANCE,
                    },
                )
                return
            if not self._is_authenticated_connection(websocket):
                await self._authorization_error(
                    websocket,
                    correlation_id,
                    "UNAUTHORIZED",
                    "An authenticated interface session is required to propose consequential work.",
                )
                return
            await self._propose_consequential(
                websocket,
                correlation_id,
                verb,
                command_text,
                parameters,
                fingerprint,
            )
            return

        await self._send(
            websocket,
            {
                "action": "COMMAND_RESULT",
                "correlationId": correlation_id,
                "success": False,
                "status": "UNSUPPORTED",
                "error": (
                    f"No mapped production capability for actionVerb "
                    f"'{action_verb}'. Supported verbs: "
                    f"{', '.join(sorted(set(_OMEGA_VERBS) | set(_CONSEQUENTIAL_VERBS)))}."
                ),
                "provenance": PROVENANCE,
            },
        )

    async def _dispatch_omega(
        self,
        websocket: Any,
        correlation_id: str,
        verb: str,
        parameters: dict[str, Any],
        request_fingerprint: str,
    ) -> None:
        omega_verb = _OMEGA_VERBS[verb]
        ok, payload, latency_ms = self._omega_request(
            "POST",
            "/command",
            {"verb": omega_verb, "parameters": parameters},
        )
        self._omega_last_attempt_at = _utc()
        self._omega_latency_ms = latency_ms
        response_valid = isinstance(payload, dict) and isinstance(payload.get("success"), bool)
        self._record_omega_exchange(
            "/command",
            method="POST",
            ok=ok,
            valid=response_valid,
            payload=payload,
            latency_ms=latency_ms,
        )
        if ok and response_valid:
            self._omega_last_success_at = self._omega_last_attempt_at

        if ok and isinstance(payload, dict) and payload.get("success"):
            success = True
            status = "COMPLETED"
            result = payload.get("result")
            op = {
                "title": f"{verb}: OMEGA {omega_verb}",
                "intent": f"Real OMEGA {omega_verb} via the integration service.",
                "expectedEffect": "Real read-only OMEGA result returned.",
                "lifecycleState": "COMPLETED",
                "verb": verb,
                "commandText": f"{verb} {omega_verb}",
                "parameters": parameters,
                "correlationId": correlation_id,
                "requestFingerprint": request_fingerprint,
                "executionResult": {
                    "success": True,
                    "latencyMs": latency_ms,
                    "peer": "OMEGA",
                    "result": result,
                },
            }
            self._record_operation(op)
            await self._send(
                websocket,
                {
                    "action": "COMMAND_RESULT",
                    "correlationId": correlation_id,
                    "success": True,
                    "status": status,
                    "result": {
                        "peer": "OMEGA",
                        "verb": omega_verb,
                        "latencyMs": latency_ms,
                        "operationId": op["operationId"],
                        "result": result,
                    },
                    "provenance": PROVENANCE,
                },
            )
            return

        error_text = "OMEGA integration service unreachable"
        if isinstance(payload, dict):
            error_text = str(payload.get("error") or payload.get("detail") or error_text)

        op = {
            "title": f"{verb}: OMEGA {omega_verb}",
            "intent": f"Real OMEGA {omega_verb} via the integration service.",
            "expectedEffect": "Real read-only OMEGA result returned.",
            "lifecycleState": "FAILED",
            "verb": verb,
            "commandText": f"{verb} {omega_verb}",
            "parameters": parameters,
            "correlationId": correlation_id,
            "requestFingerprint": request_fingerprint,
            "executionResult": {
                "success": False,
                "latencyMs": latency_ms,
                "error": error_text,
            },
        }
        self._record_operation(op)
        await self._send(
            websocket,
            {
                "action": "COMMAND_RESULT",
                "correlationId": correlation_id,
                "success": False,
                "status": "FAILED",
                "error": error_text,
                "provenance": PROVENANCE,
            },
        )

    async def _propose_consequential(
        self,
        websocket: Any,
        correlation_id: str,
        verb: str,
        command_text: str,
        parameters: dict[str, Any],
        request_fingerprint: str,
    ) -> None:
        subject = parameters.get("subject")
        detail = subject.strip() if isinstance(subject, str) and subject.strip() \
            else command_text.strip()
        op = {
            "title": f"{verb}: {detail[:80]}",
            "intent": f"Consequential {verb} operation proposed by the dashboard.",
            "expectedEffect": (
                "Return the current working directory without changing state."
                if verb == "execute"
                and parameters.get("subject") == "pwd"
                else TASK_EXPECTED_EFFECT
                if verb == "execute_task"
                else "Executes a real capability after authenticated operator approval."
            ),
            "lifecycleState": "SUGGESTED",
            "verb": verb,
            "commandText": command_text,
            "parameters": parameters,
            "correlationId": correlation_id,
            "requestFingerprint": request_fingerprint,
        }
        if isinstance(self.approval_authorizer, WebSocketAuthenticator):
            proposal_identity = self.approval_authorizer.identity_for(websocket)
            if proposal_identity is None:
                await self._authorization_error(
                    websocket,
                    correlation_id,
                    "UNAUTHORIZED",
                    "The authenticated session is no longer active.",
                )
                return
            op["proposalAuthorization"] = {
                **proposal_identity,
                "proposedAt": _utc(),
            }
        self._record_operation(op)
        await self._broadcast(
            {
                "channel": "command",
                "payload": self._build_command_center(),
                "provenance": PROVENANCE,
            }
        )
        await self._broadcast(
            {"channel": "operation", "payload": list(self._operations), "provenance": PROVENANCE}
        )
        await self._send(
            websocket,
            {
                "action": "COMMAND_RESULT",
                "correlationId": correlation_id,
                "success": True,
                "status": "PENDING_APPROVAL",
                "result": {
                    "operationId": op["operationId"],
                    "correlationId": op["correlationId"],
                    "requestFingerprint": op["requestFingerprint"],
                    "lifecycleState": "SUGGESTED",
                    "message": (
                        "Consequential operation proposed; it will only execute "
                        "after an explicit APPROVE_OPERATION."
                    ),
                },
                "provenance": PROVENANCE,
            },
        )

    # ------------------------------------------------------------------
    # Operation approval (real two-phase authorization)
    # ------------------------------------------------------------------

    async def _handle_approve(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        correlation_id = message.get("correlationId") or str(uuid4())
        operation = message.get("payload")

        if not isinstance(operation, dict):
            await self._error(
                websocket,
                correlation_id,
                "Operation payload must be an object.",
            )
            return

        operation_id = operation.get("operationId") or operation.get("id")
        if not isinstance(operation_id, str) or not operation_id:
            await self._error(
                websocket,
                correlation_id,
                "operationId is required for approval.",
            )
            return
        if self.approval_authorizer is None:
            await self._authorization_error(
                websocket,
                correlation_id,
                "AUTHENTICATION_UNAVAILABLE",
                "Consequential approval is disabled because no trusted interface authorizer is configured.",
            )
            return
        if not self._is_authenticated_connection(websocket):
            await self._authorization_error(
                websocket,
                correlation_id,
                "UNAUTHORIZED",
                "An authenticated interface session is required to approve this operation.",
            )
            return

        op = self._find_operation(operation_id)
        if op is None:
            await self._send(
                websocket,
                {
                    "action": "OPERATION_RESULT",
                    "correlationId": correlation_id,
                    "success": False,
                    "status": "NOT_FOUND",
                    "error": f"No pending operation with id {operation_id}.",
                    "provenance": PROVENANCE,
                },
            )
            return

        if op.get("verb") in _ULTRON_VERBS:
            await self._authorization_error(
                websocket,
                correlation_id,
                "PLAN_APPROVAL_UNAVAILABLE",
                "Cognitive plans cannot execute from a broad operation approval.",
            )
            return

        if op.get("verb") not in _CONSEQUENTIAL_VERBS:
            await self._execute_approved_operation(websocket, correlation_id, op)
            return

        if op.get("lifecycleState") != "SUGGESTED":
            await self._send(
                websocket,
                {
                    "action": "OPERATION_RESULT",
                    "correlationId": correlation_id,
                    "success": False,
                    "status": "INVALID_STATE",
                    "error": (
                        f"Operation {operation_id} is in state "
                        f"{op.get('lifecycleState')}; only SUGGESTED operations "
                        "can be approved."
                    ),
                    "provenance": PROVENANCE,
                },
            )
            return

        if isinstance(self.approval_authorizer, WebSocketAuthenticator):
            proposer = op.get("proposalAuthorization")
            approver = self.approval_authorizer.identity_for(websocket)
            if (
                not isinstance(proposer, dict)
                or approver is None
                or proposer.get("principal") != approver["principal"]
                or proposer.get("sessionId") != approver["sessionId"]
            ):
                await self._authorization_error(
                    websocket,
                    correlation_id,
                    "IDENTITY_MISMATCH",
                    "Only the authenticated session that proposed this operation may approve it.",
                )
                return

        supplied_correlation_id = operation.get("correlationId")
        supplied_fingerprint = operation.get("requestFingerprint")
        if (
            supplied_correlation_id != op.get("correlationId")
            or supplied_fingerprint != op.get("requestFingerprint")
            or not self._operation_fingerprint_matches(op)
        ):
            await self._authorization_error(
                websocket,
                correlation_id,
                "APPROVAL_MISMATCH",
                "Approval does not match the persisted operation and reviewed request fingerprint.",
            )
            return
        grant = await self._authorize_operation(
            websocket,
            op,
            purpose="execute",
            request_correlation_id=correlation_id,
        )
        if grant is None:
            await self._authorization_error(
                websocket,
                correlation_id,
                "UNAUTHORIZED",
                "The active authenticated principal is not authorized for this operation.",
            )
            return
        if not self._grant_is_valid(
            grant,
            op,
            purpose="execute",
            request_correlation_id=correlation_id,
        ):
            await self._authorization_error(
                websocket,
                correlation_id,
                "APPROVAL_INVALID",
                "The authorizer returned a malformed, expired, or mismatched approval.",
            )
            return
        approved_operation = {
            **op,
            "lifecycleState": "APPROVED",
            "updatedAt": _utc(),
            "authorization": {
                "policy": "TRUSTED_INTERFACE_AUTHORIZER",
                "required": True,
                "granted": True,
                "authorizer": grant["principal"],
                "authenticated": True,
                "grantId": grant["grantId"],
                "operationId": op["operationId"],
                "operationCorrelationId": op["correlationId"],
                "requestCorrelationId": grant["requestCorrelationId"],
                "requestFingerprint": op["requestFingerprint"],
                "action": grant["action"],
                "resourceFingerprint": grant["resourceFingerprint"],
                "grantedAt": grant["issuedAt"],
                "expiresAt": grant["expiresAt"],
                "signature": grant["signature"],
                "authorizationContext": {
                    "method": grant["authorizationContext"]["method"],
                    "sessionId": grant["authorizationContext"]["sessionId"],
                },
            },
        }
        self._validate_operation_evidence(approved_operation)
        if not self.operation_store.claim_authorization_grant_and_approve_operation(
            {
                "grantId": grant["grantId"],
                "operationId": grant["operationId"],
                "correlationId": grant["requestCorrelationId"],
                "requestFingerprint": grant["requestFingerprint"],
                "purpose": grant["purpose"],
                "principal": grant["principal"],
                "expiresAt": grant["expiresAt"],
                "issuedAt": grant["issuedAt"],
                "action": grant["action"],
                "resourceFingerprint": grant["resourceFingerprint"],
            },
            approved_operation,
        ):
            await self._authorization_error(
                websocket,
                correlation_id,
                "AUTHORIZATION_REPLAY",
                (
                    "The approval grant was already consumed or the operation "
                    "is no longer pending; no execution was started."
                ),
            )
            return
        op.clear()
        op.update(approved_operation)
        await self._broadcast(
            {"channel": "command", "payload": self._build_command_center(),
             "provenance": PROVENANCE}
        )

        await self._execute_approved_operation(websocket, correlation_id, op)

    async def _authorize_operation(
        self,
        websocket: Any,
        operation: dict[str, Any],
        *,
        purpose: str,
        request_correlation_id: str,
    ) -> dict[str, Any] | None:
        authorizer = self.approval_authorizer
        if authorizer is None:
            return None
        scope = self._authorization_scope(operation, purpose)
        if scope is None:
            return None
        return await asyncio.to_thread(
            authorizer.authorize,
            websocket=websocket,
            operation=copy.deepcopy(operation),
            purpose=purpose,
            request_correlation_id=request_correlation_id,
            action=scope["action"],
            resource_fingerprint=scope["resourceFingerprint"],
        )

    def _grant_is_valid(
        self,
        grant: Any,
        operation: dict[str, Any],
        *,
        purpose: str,
        request_correlation_id: str,
    ) -> bool:
        if not isinstance(grant, dict):
            return False
        scope = self._authorization_scope(operation, purpose)
        if scope is None:
            return False
        expected = {
            "authenticated": True,
            "operationId": operation.get("operationId"),
            "operationCorrelationId": operation.get("correlationId"),
            "requestCorrelationId": request_correlation_id,
            "requestFingerprint": operation.get("requestFingerprint"),
            "purpose": purpose,
            "action": scope["action"],
            "resourceFingerprint": scope["resourceFingerprint"],
        }
        if any(grant.get(key) != value for key, value in expected.items()):
            return False
        for key in (
            "grantId",
            "principal",
            "issuedAt",
            "expiresAt",
        ):
            if not isinstance(grant.get(key), str) or not grant[key].strip():
                return False
        context = grant.get("authorizationContext")
        if (
            not isinstance(context, dict)
            or not isinstance(context.get("method"), str)
            or not context["method"].strip()
            or not isinstance(context.get("sessionId"), str)
            or not context["sessionId"].strip()
        ):
            return False
        try:
            issued_at = datetime.fromisoformat(
                grant["issuedAt"].replace("Z", "+00:00")
            )
            expires_at = datetime.fromisoformat(
                grant["expiresAt"].replace("Z", "+00:00")
            )
        except ValueError:
            return False
        now = datetime.now(UTC)
        if (
            issued_at.tzinfo is None
            or expires_at.tzinfo is None
            or issued_at.timestamp() > now.timestamp() + 30
            or expires_at <= now
            or expires_at <= issued_at
            or (expires_at - issued_at).total_seconds() > 300
        ):
            return False
        validator = getattr(self.approval_authorizer, "validate", None)
        if not callable(validator):
            return False
        return bool(
            validator(
                grant=copy.deepcopy(grant),
                operation=copy.deepcopy(operation),
                purpose=purpose,
                action=scope["action"],
                resource_fingerprint=scope["resourceFingerprint"],
            )
        )

    @staticmethod
    def _authorization_scope(
        operation: dict[str, Any],
        purpose: str,
    ) -> dict[str, str] | None:
        if purpose == "execute":
            verb = operation.get("verb")
            mapping = _EXECUTION_MAPPINGS.get(verb)
            parameters = operation.get("parameters")
            if mapping is None or not isinstance(parameters, dict):
                return None
            action, parameter_keys = mapping
            resource: dict[str, Any] = {
                key: parameters[key]
                for key in parameter_keys
                if key in parameters and parameters[key] is not None
            }
            if verb == "execute":
                resource.setdefault("command", parameters.get("subject"))
            if verb == "execute_task":
                if (
                    not isinstance(resource.get("goal"), str)
                    or not isinstance(resource.get("steps"), list)
                ):
                    return None
            elif not isinstance(resource.get("command"), str):
                return None
        elif purpose == "reconcile":
            action = "reconcile_operation"
            resource = {
                "operationId": operation.get("operationId"),
                "requestFingerprint": operation.get("requestFingerprint"),
            }
        else:
            return None

        return {
            "action": action,
            "resourceFingerprint": resource_fingerprint(action, resource),
        }

    @staticmethod
    def _operation_fingerprint_matches(operation: dict[str, Any]) -> bool:
        parameters = operation.get("parameters")
        return (
            isinstance(parameters, dict)
            and operation.get("requestFingerprint")
            == InterfaceGateway._request_fingerprint(
                str(operation.get("verb", "")),
                str(operation.get("commandText", "")),
                parameters,
            )
        )

    async def _authorization_error(
        self,
        websocket: Any,
        correlation_id: str,
        status: str,
        error: str,
    ) -> None:
        await self._send(
            websocket,
            {
                "action": "OPERATION_RESULT",
                "correlationId": correlation_id,
                "success": False,
                "status": status,
                "error": error,
                "provenance": PROVENANCE,
            },
        )

    @staticmethod
    def _valid_verification_evidence(
        operation: dict[str, Any],
        evidence: Any,
    ) -> bool:
        return valid_verification_evidence(operation, evidence)

    async def _handle_reconcile(
        self,
        websocket: Any,
        message: dict[str, Any],
    ) -> None:
        correlation_id = message.get("correlationId")
        payload = message.get("payload")
        if (
            not isinstance(correlation_id, str)
            or not correlation_id.strip()
            or not isinstance(payload, dict)
        ):
            await self._error(
                websocket,
                correlation_id if isinstance(correlation_id, str) else None,
                "Reconciliation requires a correlationId and object payload.",
            )
            return
        operation_id = payload.get("operationId")
        supplied_fingerprint = payload.get("requestFingerprint")
        if (
            not isinstance(operation_id, str)
            or not operation_id.strip()
            or not isinstance(supplied_fingerprint, str)
            or not supplied_fingerprint.strip()
        ):
            await self._authorization_error(
                websocket,
                correlation_id,
                "MALFORMED_REQUEST",
                "operationId and requestFingerprint are required.",
            )
            return
        if self.approval_authorizer is None:
            await self._authorization_error(
                websocket,
                correlation_id,
                "AUTHENTICATION_UNAVAILABLE",
                "Reconciliation is disabled until a trusted interface authorizer is configured.",
            )
            return
        if not self._is_authenticated_connection(websocket):
            await self._authorization_error(
                websocket,
                correlation_id,
                "UNAUTHORIZED",
                "An authenticated interface session is required to reconcile this operation.",
            )
            return
        operation = self._find_operation(operation_id)
        if operation is None:
            await self._authorization_error(
                websocket,
                correlation_id,
                "NOT_FOUND",
                f"No operation with id {operation_id}.",
            )
            return
        if (
            supplied_fingerprint != operation.get("requestFingerprint")
            or not self._operation_fingerprint_matches(operation)
        ):
            await self._authorization_error(
                websocket,
                correlation_id,
                "RECONCILIATION_MISMATCH",
                "Reconciliation does not match the persisted operation fingerprint.",
            )
            return
        grant = await self._authorize_operation(
            websocket,
            operation,
            purpose="reconcile",
            request_correlation_id=correlation_id,
        )
        if grant is None or not self._grant_is_valid(
            grant,
            operation,
            purpose="reconcile",
            request_correlation_id=correlation_id,
        ):
            await self._authorization_error(
                websocket,
                correlation_id,
                "UNAUTHORIZED",
                "A current authenticated principal is required to reconcile this operation.",
            )
            return
        reconciliation_fingerprint = self._request_fingerprint(
            "reconcile",
            operation_id,
            {"requestFingerprint": supplied_fingerprint},
        )
        prior = self.operation_store.get_reconciliation(correlation_id)
        if prior is not None:
            if (
                prior["operationId"] != operation_id
                or prior["requestFingerprint"] != reconciliation_fingerprint
            ):
                await self._authorization_error(
                    websocket,
                    correlation_id,
                    "IDEMPOTENCY_CONFLICT",
                    "Reconciliation correlationId was reused for a different request.",
                )
                return
            response = prior.get("result") or {
                "success": False,
                "status": "RECONCILIATION_REQUIRED",
                "operationId": operation_id,
                "reason": "A prior reconciliation is still pending; no retry was run.",
            }
            await self._send(
                websocket,
                {
                    "action": "OPERATION_RECONCILIATION",
                    "correlationId": correlation_id,
                    **response,
                    "provenance": PROVENANCE,
                },
            )
            return
        if operation.get("lifecycleState") not in {"UNVERIFIED", "UNCERTAIN"}:
            await self._authorization_error(
                websocket,
                correlation_id,
                "INVALID_STATE",
                "Only UNVERIFIED or UNCERTAIN operations can be reconciled.",
            )
            return
        if not self.operation_store.claim_authorization_grant({
            "grantId": grant["grantId"],
            "operationId": grant["operationId"],
            "correlationId": grant["requestCorrelationId"],
            "requestFingerprint": grant["requestFingerprint"],
            "purpose": grant["purpose"],
            "principal": grant["principal"],
            "expiresAt": grant["expiresAt"],
            "issuedAt": grant["issuedAt"],
        }):
            await self._authorization_error(
                websocket,
                correlation_id,
                "AUTHORIZATION_REPLAY",
                "This reconciliation authorization grant has already been used.",
            )
            return
        try:
            previous = self.operation_store.begin_reconciliation(
                correlation_id,
                operation_id,
                reconciliation_fingerprint,
            )
        except ValueError as exc:
            await self._authorization_error(
                websocket,
                correlation_id,
                "IDEMPOTENCY_CONFLICT",
                str(exc),
            )
            return
        if previous is not None:
            response = previous.get("result") or {
                "success": False,
                "status": "RECONCILIATION_REQUIRED",
                "operationId": operation_id,
                "reason": "A prior reconciliation is still pending; no retry was run.",
            }
            await self._send(
                websocket,
                {
                    "action": "OPERATION_RECONCILIATION",
                    "correlationId": correlation_id,
                    **response,
                    "provenance": PROVENANCE,
                },
            )
            return
        try:
            evidence = self.operation_verifier.verify(copy.deepcopy(operation))
            verification_error = None
        except Exception as exc:
            evidence = None
            verification_error = str(exc)
        if evidence is not None and not self._valid_verification_evidence(
            operation,
            evidence,
        ):
            evidence = None
            verification_error = (
                "Verifier evidence did not bind to the operation identity, "
                "request fingerprint, and expected effect."
            )

        lifecycle = operation["lifecycleState"]
        execution_result = operation.get("executionResult")
        if evidence is not None and evidence["outcome"] == "CONFIRMED_SUCCESS":
            lifecycle = "VERIFIED"
            if not isinstance(execution_result, dict) or execution_result.get(
                "success"
            ) is not True:
                execution_result = {
                    "success": True,
                    "status": "RECONCILED_SUCCESS",
                    "reconciled": True,
                    "priorExecutionResult": execution_result,
                }
        elif evidence is not None and evidence["outcome"] == "CONFIRMED_FAILURE":
            lifecycle = "FAILED"
            execution_result = {
                "success": False,
                "status": "RECONCILED_FAILURE",
                "reconciled": True,
                "priorExecutionResult": execution_result,
            }
        elif evidence is not None:
            lifecycle = "UNCERTAIN"

        attempted_at = _utc()
        attempt = {
            "correlationId": correlation_id,
            "operationId": operation_id,
            "requestFingerprint": supplied_fingerprint,
            "principal": grant["principal"],
            "grantId": grant["grantId"],
            "authorizationContext": {
                "method": grant["authorizationContext"]["method"],
                "sessionId": grant["authorizationContext"]["sessionId"],
            },
            "attemptedAt": attempted_at,
            "outcome": (
                evidence.get("outcome")
                if evidence is not None
                else "UNRESOLVED"
            ),
            **(
                {"verificationEvidence": evidence}
                if evidence is not None
                else {}
            ),
            **(
                {"error": verification_error}
                if verification_error
                else {}
            ),
        }
        history = list(operation.get("reconciliationHistory") or [])
        history.append(attempt)
        updated = {
            **operation,
            "lifecycleState": lifecycle,
            "executionResult": execution_result,
            "reconciliationHistory": history,
            "updatedAt": _utc(),
        }
        if evidence is not None:
            updated["verificationAudit"] = evidence
        self._validate_operation_evidence(updated)
        status = (
            "VERIFIED"
            if lifecycle == "VERIFIED"
            else "FAILED"
            if lifecycle == "FAILED"
            else "RECONCILIATION_REQUIRED"
        )
        response = {
            "success": lifecycle == "VERIFIED",
            "status": status,
            "operationId": operation_id,
            "lifecycleState": lifecycle,
            "reconciliation": attempt,
        }
        self.operation_store.finish_reconciliation(
            updated,
            correlation_id,
            reconciliation_fingerprint,
            response,
        )
        operation.clear()
        operation.update(updated)
        await self._broadcast(
            {
                "channel": "operation",
                "payload": list(self._operations),
                "provenance": PROVENANCE,
            }
        )
        await self._send(
            websocket,
            {
                "action": "OPERATION_RECONCILIATION",
                "correlationId": correlation_id,
                **response,
                "provenance": PROVENANCE,
            },
        )

    async def _execute_approved_task(
        self,
        websocket: Any,
        correlation_id: str,
        operation: dict[str, Any],
        grant: dict[str, Any],
    ) -> None:
        context = AuthorizationContext.from_grant(grant)
        if not self.operation_store.claim_authorization_dispatch(
            grant_id=context.grant_id,
            operation_id=context.operation_id,
            correlation_id=context.request_correlation_id,
            request_fingerprint=context.request_fingerprint,
            purpose=context.purpose,
            principal=context.principal,
            action=context.action,
            resource_fingerprint=context.resource_fingerprint,
            expires_at=context.expires_at,
        ):
            self._op_update(
                operation,
                lifecycleState="FAILED",
                executionResult={
                    "success": False,
                    "status": "AUTHORIZATION_REPLAY",
                    "error": "The task authorization grant was already claimed.",
                },
            )
            await self._authorization_error(
                websocket,
                correlation_id,
                "AUTHORIZATION_REPLAY",
                "The task authorization was already used; no task was started.",
            )
            return

        started = _now_epoch()
        try:
            result = self.task_service.execute_authorized_plan(
                operation,
                context,
            )
            execution = result.get("execution")
            evidence = result.get("verification")
            task = result.get("task")
            task_status = getattr(getattr(task, "status", None), "value", None)
            verification_valid = (
                isinstance(evidence, dict)
                and self._valid_verification_evidence(operation, evidence)
            )
            if (
                isinstance(execution, dict)
                and execution.get("success") is True
                and verification_valid
                and evidence.get("outcome") == "CONFIRMED_SUCCESS"
            ):
                lifecycle = "VERIFIED"
            elif (
                task_status == "failed"
                or (
                    verification_valid
                    and evidence.get("outcome") == "CONFIRMED_FAILURE"
                )
            ):
                lifecycle = "FAILED"
            else:
                lifecycle = "UNCERTAIN"

            operation_result = {
                "success": lifecycle == "VERIFIED",
                "status": lifecycle,
                "action": "run_task",
                "latencyMs": round((_now_epoch() - started) * 1000.0, 1),
                "result": execution,
            }
            changes: dict[str, Any] = {
                "lifecycleState": lifecycle,
                "executionResult": operation_result,
            }
            if verification_valid:
                changes["verificationAudit"] = evidence
            self._op_update(operation, **changes)

            if lifecycle == "VERIFIED":
                try:
                    self.task_service.task_manager.resolve_uncertain_task(
                        result["task_id"],
                        task.attempt_id,
                        operation_id=operation["operationId"],
                    )
                except Exception:
                    logger.exception(
                        "Verified task could not complete its durable task transition"
                    )
                    self._op_update(
                        operation,
                        lifecycleState="UNCERTAIN",
                        executionResult={
                            **operation_result,
                            "success": False,
                            "status": "UNCERTAIN",
                            "error": "Task-state persistence did not confirm completion.",
                        },
                    )
                    lifecycle = "UNCERTAIN"
        except Exception as exc:
            logger.exception("Authorized task execution ended without a verified result")
            lifecycle = "UNCERTAIN"
            self._op_update(
                operation,
                lifecycleState=lifecycle,
                executionResult={
                    "success": False,
                    "status": lifecycle,
                    "action": "run_task",
                    "error": f"Task execution could not be verified ({type(exc).__name__}).",
                },
            )

        await self._broadcast(
            {
                "channel": "operation",
                "payload": list(self._operations),
                "provenance": PROVENANCE,
            }
        )
        await self._send(
            websocket,
            {
                "action": "OPERATION_RESULT",
                "correlationId": correlation_id,
                "success": lifecycle == "VERIFIED",
                "status": lifecycle,
                "result": {
                    "operationId": operation["operationId"],
                    "lifecycleState": lifecycle,
                    "executionResult": operation.get("executionResult"),
                    "verification": operation.get("verificationAudit"),
                },
                "provenance": PROVENANCE,
            },
        )

    async def _execute_approved_operation(
        self,
        websocket: Any,
        correlation_id: str,
        op: dict[str, Any],
    ) -> None:
        verb = op.get("verb")
        if verb in _ULTRON_VERBS:
            self._op_update(
                op,
                lifecycleState="FAILED",
                executionResult={
                    "success": False,
                    "status": "PLAN_APPROVAL_UNAVAILABLE",
                    "error": (
                        "Cognitive plans cannot execute from a broad operation "
                        "approval."
                    ),
                },
            )
            await self._send(
                websocket,
                {
                    "action": "OPERATION_RESULT",
                    "correlationId": correlation_id,
                    "success": False,
                    "status": "PLAN_APPROVAL_UNAVAILABLE",
                    "result": {
                        "operationId": op["operationId"],
                        "lifecycleState": op["lifecycleState"],
                    },
                    "provenance": PROVENANCE,
                },
            )
            return
        parameters = op.get("parameters") or {}
        mapping = _EXECUTION_MAPPINGS.get(verb)
        grant = None
        if verb in _CONSEQUENTIAL_VERBS:
            authorization = op.get("authorization")
            grant = {
                "grantId": authorization.get("grantId"),
                "principal": authorization.get("authorizer"),
                "authenticated": authorization.get("authenticated"),
                "operationId": authorization.get("operationId"),
                "operationCorrelationId": authorization.get(
                    "operationCorrelationId"
                ),
                "requestCorrelationId": authorization.get(
                    "requestCorrelationId"
                ),
                "requestFingerprint": authorization.get(
                    "requestFingerprint"
                ),
                "purpose": "execute",
                "action": authorization.get("action"),
                "resourceFingerprint": authorization.get(
                    "resourceFingerprint"
                ),
                "issuedAt": authorization.get("grantedAt"),
                "expiresAt": authorization.get("expiresAt"),
                "signature": authorization.get("signature"),
                "authorizationContext": authorization.get(
                    "authorizationContext"
                ),
            } if isinstance(authorization, dict) else None
            if not self._grant_is_valid(
                grant,
                op,
                purpose="execute",
                request_correlation_id=(
                    grant.get("requestCorrelationId")
                    if isinstance(grant, dict)
                    else ""
                ),
            ):
                self._op_update(
                    op,
                    lifecycleState="FAILED",
                    executionResult={
                        "success": False,
                        "status": "APPROVAL_EXPIRED",
                        "error": (
                            "Authorization was no longer valid when execution "
                            "was about to begin."
                        ),
                    },
                )
                await self._broadcast(
                    {
                        "channel": "operation",
                        "payload": list(self._operations),
                        "provenance": PROVENANCE,
                    }
                )
                await self._authorization_error(
                    websocket,
                    correlation_id,
                    "APPROVAL_EXPIRED",
                    "Authorization expired or was revoked before execution.",
                )
                return
        if mapping is None:
            error = f"No executable capability is mapped for consequential action '{verb}'."
            self._op_update(
                op,
                lifecycleState="FAILED",
                executionResult={
                    "success": False,
                    "status": "UNSUPPORTED",
                    "error": error,
                },
            )
            await self._broadcast(
                {"channel": "operation", "payload": list(self._operations), "provenance": PROVENANCE}
            )
            await self._send(
                websocket,
                {
                    "action": "OPERATION_RESULT",
                    "correlationId": correlation_id,
                    "success": False,
                    "status": "UNSUPPORTED",
                    "error": error,
                    "result": {
                        "operationId": op["operationId"],
                        "lifecycleState": op["lifecycleState"],
                    },
                    "provenance": PROVENANCE,
                },
            )
            return

        self._op_update(op, lifecycleState="EXECUTING")
        await self._broadcast(
            {"channel": "operation", "payload": list(self._operations), "provenance": PROVENANCE}
        )

        if verb == "execute_task":
            if not isinstance(grant, dict):
                self._op_update(
                    op,
                    lifecycleState="FAILED",
                    executionResult={
                        "success": False,
                        "status": "APPROVAL_INVALID",
                        "error": "Task authorization context is unavailable.",
                    },
                )
                await self._authorization_error(
                    websocket,
                    correlation_id,
                    "APPROVAL_INVALID",
                    "Task authorization context is unavailable.",
                )
                return
            await self._execute_approved_task(
                websocket,
                correlation_id,
                op,
                grant,
            )
            return

        started = _now_epoch()
        action, param_keys = mapping
        real_params = {}
        for key in param_keys:
            if key in parameters and parameters[key] is not None:
                real_params[key] = parameters[key]
        if verb == "execute":
            real_params.setdefault("command", parameters.get("subject"))

        try:
            plan = Plan(
                goal=f"Interface command: {verb}",
                steps=[
                    PlanStep(order=1, action=action, parameters=real_params)
                ],
            )
            authorization_context = AuthorizationContext.from_grant(grant)
            if verb in _CONSEQUENTIAL_VERBS and not self._grant_is_valid(
                grant,
                op,
                purpose="execute",
                request_correlation_id=grant["requestCorrelationId"],
            ):
                self._op_update(
                    op,
                    lifecycleState="FAILED",
                    executionResult={
                        "success": False,
                        "status": "APPROVAL_EXPIRED",
                        "error": (
                            "Authorization was no longer valid at the final "
                            "execution boundary."
                        ),
                    },
                )
                await self._broadcast(
                    {
                        "channel": "operation",
                        "payload": list(self._operations),
                        "provenance": PROVENANCE,
                    }
                )
                await self._authorization_error(
                    websocket,
                    correlation_id,
                    "APPROVAL_EXPIRED",
                    "Authorization expired or was revoked before dispatch.",
                )
                return
            result = self.executor.execute(
                plan,
                operation_id=authorization_context.operation_id,
                correlation_id=(
                    authorization_context.operation_correlation_id
                ),
                authorization_context=authorization_context,
            )
            step_results = result.get("results") if isinstance(result, dict) else None
            step_result = (
                step_results[0]
                if isinstance(step_results, list) and len(step_results) == 1
                and isinstance(step_results[0], dict)
                else None
            )
            skill_result = (
                step_result.get("result")
                if isinstance(step_result, dict)
                and isinstance(step_result.get("result"), dict)
                else None
            )
            execution_confirmed = (
                isinstance(result, dict)
                and result.get("success") is True
                and result.get("completed") == 1
                and step_result is not None
                and step_result.get("status") == "COMPLETED"
                and skill_result is not None
                and skill_result.get("success") is True
            )
            blocked_before_execution = (
                isinstance(step_result, dict)
                and step_result.get("status") in {"BLOCKED", "REQUIRES_CONFIRMATION"}
            ) or (
                isinstance(skill_result, dict)
                and (
                    skill_result.get("blocked") is True
                    or skill_result.get("requires_confirmation") is True
                    or skill_result.get("not_dispatched") is True
                    or (
                        skill_result.get("returncode") == 127
                        and "not found"
                        in str(skill_result.get("stderr", "")).lower()
                    )
                )
            )
            lifecycle = (
                "UNVERIFIED" if execution_confirmed
                else "FAILED" if blocked_before_execution
                else "UNCERTAIN"
            )
            verification_evidence = None
            verification_error = None
            if execution_confirmed:
                try:
                    verification_evidence = self.operation_verifier.verify(
                        copy.deepcopy({
                            **op,
                            "executionResult": {
                                "success": True,
                                "status": "UNVERIFIED",
                                "action": action,
                                "result": result,
                            },
                        })
                    )
                except Exception as exc:
                    verification_error = str(exc)

                if verification_evidence is not None:
                    if not self._valid_verification_evidence(
                        op,
                        verification_evidence,
                    ):
                        verification_error = (
                            "Verifier evidence did not bind to this operation "
                            "identity and request fingerprint."
                        )
                        verification_evidence = None
                        lifecycle = "UNCERTAIN"
                    elif verification_evidence["outcome"] == "CONFIRMED_SUCCESS":
                        lifecycle = "VERIFIED"
                    elif verification_evidence["outcome"] == "CONFIRMED_FAILURE":
                        lifecycle = "FAILED"
                    else:
                        lifecycle = "UNCERTAIN"
            self._op_update(
                op,
                lifecycleState=lifecycle,
                executionResult={
                    "success": (
                        execution_confirmed
                        and lifecycle in {"UNVERIFIED", "VERIFIED"}
                    ),
                    "executorReportedSuccess": (
                        result.get("success") is True
                        if isinstance(result, dict)
                        else False
                    ),
                    "status": lifecycle,
                    "action": action,
                    "latencyMs": round((_now_epoch() - started) * 1000.0, 1),
                    "result": result,
                    **(
                        {"priorStatus": "EXECUTION_REPORTED_SUCCESS"}
                        if lifecycle == "FAILED"
                        else {}
                    ),
                },
                **(
                    {"verificationAudit": verification_evidence}
                    if verification_evidence is not None
                    else {}
                ),
                **(
                    {"verificationError": verification_error}
                    if verification_error
                    else {}
                ),
            )
        except Exception as exc:
            self._op_update(
                op,
                lifecycleState="UNCERTAIN",
                executionResult={
                    "success": False,
                    "status": "UNCERTAIN",
                    "action": action,
                    "error": str(exc),
                },
            )

        await self._broadcast(
            {"channel": "operation", "payload": list(self._operations), "provenance": PROVENANCE}
        )
        await self._send(
            websocket,
            {
                "action": "OPERATION_RESULT",
                "correlationId": correlation_id,
                "success": op.get("lifecycleState") in {"COMPLETED", "VERIFIED"},
                "status": op.get("lifecycleState"),
                "result": {
                    "operationId": op["operationId"],
                    "lifecycleState": op.get("lifecycleState"),
                    "executionResult": op.get("executionResult"),
                },
                "provenance": PROVENANCE,
            },
        )

    async def _error(
        self,
        websocket: Any,
        correlation_id: str | None,
        error: str,
    ) -> None:
        await self._send(
            websocket,
            {
                "action": "ERROR",
                "correlationId": correlation_id,
                "success": False,
                "error": error,
                "provenance": PROVENANCE,
            },
        )


async def serve_interface(
    container,
    host: str = "127.0.0.1",
    port: int = 8080,
):
    """
    Run the production ULTRON interface WebSocket endpoint.
    """

    import websockets

    if not _is_loopback_host(host):
        raise ValueError(
            "The interface listener is restricted to loopback; remote access "
            "requires a separately configured TLS-terminating deployment."
        )

    server_container = copy.copy(container)
    authenticator = WebSocketAuthenticator.from_environment()
    server_container.interface_approval_authorizer = authenticator
    content_runtime = None
    if authenticator is not None:
        configured_path = resolve_content_database_path()
        try:
            content_runtime = create_content_runtime(
                authenticator,
                database_path=configured_path,
            )
        except ContentSchemaVersionMismatch as error:
            server_container.cre_unavailable_reason = "SCHEMA_VERSION_MISMATCH"
            logger.warning(
                "CRE initialization unavailable: runtime schema version %s "
                "does not match existing schema version %s.",
                error.expected_version,
                error.actual_version,
            )
        else:
            server_container.cre_status_service = content_runtime.status
            server_container.cre_script_preview_service = content_runtime.script_previews
            server_container.cre_intelligence_service = content_runtime.intelligence
            server_container.cre_clip_rendering_service = content_runtime.clip_rendering
    gateway = None
    try:
        gateway = InterfaceGateway(server_container)
        gateway.start()
    except Exception:
        if gateway is not None:
            gateway.stop()
            gateway.operation_store.close()
        if content_runtime is not None:
            content_runtime.close()
        raise
    assert gateway is not None

    async def handler(websocket):
        request = getattr(websocket, "request", None)
        path = getattr(request, "path", None)

        if path != ENDPOINT_PATH:
            await websocket.close(
                code=1008,
                reason="Invalid endpoint.",
            )
            return

        gateway.add_client(websocket)
        expiry_watcher = None
        if authenticator is not None:
            expiry_watcher = asyncio.create_task(
                _watch_authenticated_session(websocket, authenticator)
            )

        try:
            await gateway._send(
                websocket,
                {
                    "type": "connected",
                    "channel": "runtimeEvents",
                    "authenticationRequired": authenticator is not None,
                    "subsystems": {
                        "core": {"status": "AVAILABLE"},
                        "cre": {
                            "status": (
                                "UNAVAILABLE"
                                if gateway.cre_unavailable_reason is not None
                                else "AVAILABLE"
                                if gateway.cre_status_service is not None
                                else "NOT_CONFIGURED"
                            ),
                            **(
                                {
                                    "reason": gateway.cre_unavailable_reason,
                                    "execution": "DISABLED",
                                }
                                if gateway.cre_unavailable_reason is not None
                                else {}
                            ),
                        },
                    },
                    "provenance": PROVENANCE,
                },
            )

            async for raw in websocket:
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    await gateway._error(
                        websocket,
                        None,
                        "Invalid JSON.",
                    )
                    continue

                if not isinstance(message, dict):
                    await gateway._error(
                        websocket,
                        None,
                        "Message must be an object.",
                    )
                    continue

                await gateway.handle(websocket, message)

        finally:
            if expiry_watcher is not None:
                expiry_watcher.cancel()
                try:
                    await expiry_watcher
                except asyncio.CancelledError:
                    pass
            gateway.remove_client(websocket)

    try:
        async with websockets.serve(
            handler,
            host,
            port,
            max_size=48 * 1024 * 1024,
            max_queue=1,
        ):
            print(
                "ULTRON interface WebSocket listening on "
                f"ws://{host}:{port}{ENDPOINT_PATH}"
            )
            await asyncio.Future()
    finally:
        gateway.stop()
        gateway.operation_store.close()
        if content_runtime is not None:
            content_runtime.close()


def _is_loopback_host(host: str) -> bool:
    if host.strip().lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


async def _watch_authenticated_session(
    websocket: Any,
    authenticator: WebSocketAuthenticator,
) -> None:
    while True:
        remaining = authenticator.seconds_until_expiry(websocket)
        if remaining is None:
            await asyncio.sleep(0.25)
            continue
        if remaining <= 0:
            await websocket.close(code=1008, reason="Authentication expired.")
            return
        await asyncio.sleep(min(remaining, 1.0))