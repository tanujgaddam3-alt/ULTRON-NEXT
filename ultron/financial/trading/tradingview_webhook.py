from __future__ import annotations

import hmac
import ipaddress
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable


MAX_REQUEST_BYTES = 4096
MAX_SIGNAL_AGE_SECONDS = 120.0
WEBHOOK_PATH = "/webhook/tradingview"
REQUIRED_FIELDS = {
    "schema",
    "secret",
    "event_id",
    "strategy_id",
    "symbol",
    "action",
    "price",
    "timestamp",
}
EVENT_ID_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")


class TradingViewAlertError(ValueError):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class TradingViewAlert:
    event_id: str
    strategy_id: str
    symbol: str
    action: str
    reference_price: float
    timestamp: datetime

    def fingerprint(self) -> str:
        return json.dumps(
            {
                "event_id": self.event_id,
                "strategy_id": self.strategy_id,
                "symbol": self.symbol,
                "action": self.action,
                "reference_price": self.reference_price,
                "timestamp": self.timestamp.astimezone(UTC).isoformat(),
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )


class TradingViewWebhookProcessor:
    """Authenticate and validate TradingView triggers before ULTRON processing."""

    def __init__(
        self,
        *,
        secret: str,
        allowed_symbols: set[str],
        strategy_id: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(secret, str) or len(secret.encode("utf-8")) < 32:
            raise ValueError("TradingView webhook secret must be at least 32 bytes.")
        if not allowed_symbols:
            raise ValueError("At least one TradingView symbol must be allowlisted.")
        normalized_symbols = {str(symbol).strip().upper() for symbol in allowed_symbols}
        if any(not symbol for symbol in normalized_symbols):
            raise ValueError("TradingView symbol allowlist contains an empty symbol.")
        if not isinstance(strategy_id, str) or not strategy_id.strip():
            raise ValueError("TradingView strategy ID is required.")
        self._secret = secret
        self.allowed_symbols = frozenset(normalized_symbols)
        self.strategy_id = strategy_id.strip()
        self._clock = clock or (lambda: datetime.now(UTC))

    @classmethod
    def from_environment(cls, *, clock: Callable[[], datetime] | None = None):
        backend = os.getenv("ULTRON_EXECUTION_BACKEND", "simulation").strip().lower()
        if backend != "simulation":
            raise RuntimeError("TradingView webhook requires ULTRON_EXECUTION_BACKEND=simulation.")
        symbols = {
            symbol.strip().upper()
            for symbol in os.getenv("ULTRON_TRADINGVIEW_ALLOWED_SYMBOLS", "").split(",")
            if symbol.strip()
        }
        return cls(
            secret=os.getenv("ULTRON_TRADINGVIEW_WEBHOOK_SECRET", ""),
            allowed_symbols=symbols,
            strategy_id=os.getenv("ULTRON_TRADINGVIEW_STRATEGY_ID", ""),
            clock=clock,
        )

    @staticmethod
    def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field.")
            result[key] = value
        return result

    @staticmethod
    def _reject_constant(_value: str) -> None:
        raise ValueError("Non-finite JSON number.")

    def validate(self, body: bytes) -> TradingViewAlert:
        if not isinstance(body, bytes) or not body or len(body) > MAX_REQUEST_BYTES:
            raise TradingViewAlertError(
                "invalid_body", "Request body is empty or too large.", HTTPStatus.BAD_REQUEST
            )
        try:
            payload = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=self._unique_object,
                parse_constant=self._reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise TradingViewAlertError(
                "invalid_json", "Request body must be valid JSON.", HTTPStatus.BAD_REQUEST
            ) from None
        if not isinstance(payload, dict):
            raise TradingViewAlertError(
                "invalid_schema", "Alert must be a JSON object.", HTTPStatus.BAD_REQUEST
            )

        supplied_secret = payload.get("secret")
        if not isinstance(supplied_secret, str) or not hmac.compare_digest(
            supplied_secret.encode("utf-8"), self._secret.encode("utf-8")
        ):
            raise TradingViewAlertError(
                "unauthorized", "Webhook authentication failed.", HTTPStatus.UNAUTHORIZED
            )
        if set(payload) != REQUIRED_FIELDS:
            raise TradingViewAlertError(
                "invalid_schema", "Alert fields do not match the configured schema.",
                HTTPStatus.BAD_REQUEST,
            )
        if payload.get("schema") != "ultron.tradingview.v1":
            raise TradingViewAlertError(
                "unsupported_schema", "Alert schema is not supported.", HTTPStatus.BAD_REQUEST
            )
        event_id = payload.get("event_id")
        if not isinstance(event_id, str) or not EVENT_ID_PATTERN.fullmatch(event_id):
            raise TradingViewAlertError(
                "invalid_event_id", "Alert event ID is invalid.", HTTPStatus.BAD_REQUEST
            )
        if payload.get("strategy_id") != self.strategy_id:
            raise TradingViewAlertError(
                "unsupported_strategy", "Alert strategy is not configured.", HTTPStatus.BAD_REQUEST
            )
        symbol = payload.get("symbol")
        if not isinstance(symbol, str):
            raise TradingViewAlertError(
                "invalid_symbol", "Alert symbol is invalid.", HTTPStatus.BAD_REQUEST
            )
        symbol = symbol.strip().upper()
        if symbol not in self.allowed_symbols:
            raise TradingViewAlertError(
                "unsupported_symbol", "Alert symbol is not allowlisted.", HTTPStatus.BAD_REQUEST
            )
        action = payload.get("action")
        if not isinstance(action, str) or action.strip().lower() not in {"buy", "sell"}:
            raise TradingViewAlertError(
                "unsupported_action", "Alert action must be buy or sell.", HTTPStatus.BAD_REQUEST
            )

        raw_price = payload.get("price")
        if isinstance(raw_price, bool) or not isinstance(raw_price, (int, float, str)):
            raise TradingViewAlertError(
                "invalid_price", "Alert reference price must be numeric and positive.",
                HTTPStatus.BAD_REQUEST,
            )
        try:
            reference_price = float(raw_price)
        except (TypeError, ValueError, OverflowError):
            reference_price = math.nan
        if not math.isfinite(reference_price) or reference_price <= 0:
            raise TradingViewAlertError(
                "invalid_price", "Alert reference price must be numeric and positive.",
                HTTPStatus.BAD_REQUEST,
            )

        raw_timestamp = payload.get("timestamp")
        if not isinstance(raw_timestamp, str):
            raise TradingViewAlertError(
                "invalid_timestamp", "Alert timestamp must be timezone-aware ISO 8601.",
                HTTPStatus.BAD_REQUEST,
            )
        try:
            timestamp = datetime.fromisoformat(
                raw_timestamp[:-1] + "+00:00" if raw_timestamp.endswith("Z") else raw_timestamp
            )
        except ValueError:
            timestamp = None
        if timestamp is None or timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise TradingViewAlertError(
                "invalid_timestamp", "Alert timestamp must be timezone-aware ISO 8601.",
                HTTPStatus.BAD_REQUEST,
            )
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise RuntimeError("Webhook clock must return a timezone-aware timestamp.")
        age = (now.astimezone(UTC) - timestamp.astimezone(UTC)).total_seconds()
        if age < 0:
            raise TradingViewAlertError(
                "future_timestamp", "Alert timestamp cannot be in the future.",
                HTTPStatus.BAD_REQUEST,
            )
        if age > MAX_SIGNAL_AGE_SECONDS:
            raise TradingViewAlertError(
                "stale_alert", "Alert timestamp is outside the accepted freshness window.",
                HTTPStatus.BAD_REQUEST,
            )

        return TradingViewAlert(
            event_id=event_id,
            strategy_id=self.strategy_id,
            symbol=symbol,
            action=action.strip().lower(),
            reference_price=reference_price,
            timestamp=timestamp.astimezone(UTC),
        )

    def handle(self, body: bytes, runtime: Any) -> tuple[int, dict[str, Any]]:
        try:
            alert = self.validate(body)
        except TradingViewAlertError as error:
            return error.status, {"accepted": False, "error": error.code, "message": str(error)}
        try:
            result = runtime.process_tradingview_alert(alert)
        except Exception:
            return HTTPStatus.INTERNAL_SERVER_ERROR, {
                "accepted": False,
                "event_id": alert.event_id,
                "status": "reconciliation_required",
                "error": "processing_uncertain",
            }
        status = result.get("status", "rejected")
        http_status = HTTPStatus.OK if status in {"completed", "rejected", "duplicate"} else HTTPStatus.CONFLICT
        return http_status, result


class _TradingViewRequestHandler(BaseHTTPRequestHandler):
    server_version = "ULTRONTradingView/1"
    sys_version = ""

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != WEBHOOK_PATH:
            self._write_json(HTTPStatus.NOT_FOUND, {"accepted": False, "error": "not_found"})
            return
        if self.headers.get("Transfer-Encoding"):
            self._write_json(HTTPStatus.LENGTH_REQUIRED, {"accepted": False, "error": "content_length_required"})
            return
        if self.headers.get_content_type() != "application/json":
            self._write_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"accepted": False, "error": "json_required"})
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = -1
        if length < 0:
            self._write_json(HTTPStatus.LENGTH_REQUIRED, {"accepted": False, "error": "content_length_required"})
            return
        if length > MAX_REQUEST_BYTES:
            self._write_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"accepted": False, "error": "request_too_large"})
            return
        body = self.rfile.read(length)
        if len(body) != length:
            self._write_json(HTTPStatus.BAD_REQUEST, {"accepted": False, "error": "incomplete_request"})
            return
        processor = self.server.processor  # type: ignore[attr-defined]
        status, payload = processor.handle(body, self.server.runtime)  # type: ignore[attr-defined]
        self._write_json(status, payload)

    def do_GET(self) -> None:
        self._write_json(HTTPStatus.METHOD_NOT_ALLOWED, {"accepted": False, "error": "method_not_allowed"})

    def log_message(self, _format: str, *args: Any) -> None:
        return


class TradingViewWebhookServer:
    """Loopback-only HTTP ingress; terminate public HTTPS at a trusted proxy."""

    def __init__(self, processor: TradingViewWebhookProcessor, runtime: Any, port: int = 8765) -> None:
        if not 0 <= port <= 65535:
            raise ValueError("Webhook port must be between 0 and 65535.")
        address = "127.0.0.1"
        if not ipaddress.ip_address(address).is_loopback:
            raise RuntimeError("TradingView webhook server must bind to loopback.")
        self._server = ThreadingHTTPServer((address, port), _TradingViewRequestHandler)
        self._server.daemon_threads = True
        self._server.processor = processor  # type: ignore[attr-defined]
        self._server.runtime = runtime  # type: ignore[attr-defined]

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def serve_forever(self) -> None:
        self._server.serve_forever()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()