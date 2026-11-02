from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from ultron.security.identity.authorization import (
    AuthorizationContext,
    resource_fingerprint as fingerprint_resource,
)


def _is_sqlite_operation_store(value: object) -> bool:
    from ultron.application.runtime.operation_store import SQLiteOperationStore

    return type(value) is SQLiteOperationStore


class UserIdentity(BaseModel):
    """
    Represents an authenticated user.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    username: str

    authenticated: bool = False

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AuthenticationEngine:
    """
    Verifies user identity.
    """

    def __init__(self):

        self.users = {}

    def register_user(
        self,
        username: str,
        secret_hash: str,
    ):

        self.users[username] = secret_hash

    def authenticate(
        self,
        username: str,
        secret_hash: str,
    ) -> UserIdentity:

        stored = self.users.get(username)

        if stored and stored == secret_hash:
            return UserIdentity(
                username=username,
                authenticated=True,
            )

        return UserIdentity(
            username=username,
            authenticated=False,
        )


class WebSocketAuthenticator:
    """Authenticates explicitly configured local principals and binds sessions to sockets."""

    SESSION_TTL_ENV = "ULTRON_INTERFACE_SESSION_TTL_SECONDS"
    TOKEN_ENV = "ULTRON_INTERFACE_AUTH_TOKEN"
    PRINCIPAL_ENV = "ULTRON_INTERFACE_AUTH_PRINCIPAL"
    CRE_REVIEWER_TOKEN_ENV = "ULTRON_INTERFACE_CRE_REVIEWER_TOKEN"
    CRE_REVIEWER_PRINCIPAL_ENV = "ULTRON_INTERFACE_CRE_REVIEWER_PRINCIPAL"
    DEFAULT_SESSION_TTL_SECONDS = 900
    IDLE_TTL_SECONDS = 300
    MAX_AUTH_FAILURES = 5
    _TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43,}$")

    def __init__(
        self,
        token: str,
        principal: str = "local-operator",
        *,
        additional_credentials: dict[str, str] | None = None,
        session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
    ):
        if not isinstance(token, str) or not self._TOKEN_PATTERN.fullmatch(token):
            raise ValueError(
                "Interface token must be a high-entropy URL-safe token "
                "of at least 43 characters."
            )
        if not isinstance(principal, str) or not principal.strip():
            raise ValueError("Interface principal must be configured.")
        if not 1 <= session_ttl_seconds <= self.DEFAULT_SESSION_TTL_SECONDS:
            raise ValueError("Interface session lifetime must be between 1 and 900 seconds.")

        self.principal = principal.strip()
        credentials = {self.principal: token}
        for additional_principal, additional_token in (additional_credentials or {}).items():
            if (
                not isinstance(additional_principal, str)
                or not additional_principal.strip()
                or additional_principal.strip() in credentials
                or not isinstance(additional_token, str)
                or not self._TOKEN_PATTERN.fullmatch(additional_token)
                or any(
                    hmac.compare_digest(additional_token, configured_token)
                    for configured_token in credentials.values()
                )
            ):
                raise ValueError("Additional authenticated principals must have unique credentials.")
            credentials[additional_principal.strip()] = additional_token
        self._credential_digests = tuple(
            (identity, hashlib.sha256(secret.encode("utf-8")).digest())
            for identity, secret in credentials.items()
        )
        self.session_ttl_seconds = session_ttl_seconds
        self.additional_principals = tuple(
            principal for principal in credentials if principal != self.principal
        )
        self._token_digest = hashlib.sha256(token.encode("utf-8")).digest()
        self._sessions: dict[Any, dict[str, Any]] = {}
        self._failed_attempts: dict[Any, int] = {}
        self._lock = threading.RLock()

    @classmethod
    def from_environment(cls) -> WebSocketAuthenticator | None:
        token = os.environ.get(cls.TOKEN_ENV)
        if token is None:
            return None

        principal = os.environ.get(cls.PRINCIPAL_ENV, "local-operator")
        ttl_setting = os.environ.get(
            cls.SESSION_TTL_ENV,
            str(cls.DEFAULT_SESSION_TTL_SECONDS),
        )
        try:
            session_ttl_seconds = int(ttl_setting)
        except ValueError as exc:
            raise ValueError(
                f"{cls.SESSION_TTL_ENV} must be an integer from 1 to 900."
            ) from exc

        reviewer_token = os.environ.get(cls.CRE_REVIEWER_TOKEN_ENV)
        reviewer_principal = os.environ.get(cls.CRE_REVIEWER_PRINCIPAL_ENV)
        if (reviewer_token is None) != (reviewer_principal is None):
            raise ValueError(
                f"{cls.CRE_REVIEWER_TOKEN_ENV} and "
                f"{cls.CRE_REVIEWER_PRINCIPAL_ENV} must be configured together."
            )
        additional_credentials = (
            {reviewer_principal: reviewer_token}
            if reviewer_token is not None and reviewer_principal is not None
            else None
        )
        return cls(
            token,
            principal,
            additional_credentials=additional_credentials,
            session_ttl_seconds=session_ttl_seconds,
        )

    def authenticate(self, websocket: Any, credential: Any) -> dict[str, str] | None:
        with self._lock:
            self._sessions.pop(websocket, None)
            attempts = self._failed_attempts.get(websocket, 0)
            if attempts >= self.MAX_AUTH_FAILURES:
                return None

            candidate = (
                credential.encode("utf-8")
                if isinstance(credential, str)
                else b""
            )
            supplied_digest = hashlib.sha256(candidate).digest()
            matched_principal = None
            for principal, expected_digest in self._credential_digests:
                if hmac.compare_digest(expected_digest, supplied_digest):
                    matched_principal = principal
            if matched_principal is None:
                self._failed_attempts[websocket] = attempts + 1
                return None

            now = time.monotonic()
            session = {
                "sessionId": str(uuid4()),
                "principal": matched_principal,
                "createdAt": now,
                "lastActivity": now,
                "expiresAt": now + self.session_ttl_seconds,
            }
            self._sessions[websocket] = session
            self._failed_attempts.pop(websocket, None)
            return {
                "principal": session["principal"],
                "sessionId": session["sessionId"],
                "expiresAt": (
                    datetime.now(UTC)
                    + timedelta(seconds=self.session_ttl_seconds)
                ).isoformat(timespec="milliseconds"),
            }

    def is_authenticated(self, websocket: Any) -> bool:
        with self._lock:
            return self._active_session(websocket) is not None

    def identity_for(self, websocket: Any) -> dict[str, str] | None:
        with self._lock:
            session = self._active_session(websocket)
            if session is None:
                return None
            return {
                "principal": session["principal"],
                "sessionId": session["sessionId"],
            }

    def touch(self, websocket: Any) -> bool:
        with self._lock:
            session = self._active_session(websocket)
            if session is None:
                return False
            session["lastActivity"] = time.monotonic()
            return True

    def seconds_until_expiry(self, websocket: Any) -> float | None:
        with self._lock:
            session = self._sessions.get(websocket)
            if session is None:
                return None
            now = time.monotonic()
            absolute_remaining = session["expiresAt"] - now
            idle_remaining = (
                session["lastActivity"] + self.IDLE_TTL_SECONDS - now
            )
            return min(absolute_remaining, idle_remaining)

    def revoke(self, websocket: Any) -> None:
        with self._lock:
            self._sessions.pop(websocket, None)
            self._failed_attempts.pop(websocket, None)

    def failed_attempts(self, websocket: Any) -> int:
        with self._lock:
            return self._failed_attempts.get(websocket, 0)

    def shares_configured_credential(self, other: object) -> bool:
        return (
            type(other) is WebSocketAuthenticator
            and any(
                hmac.compare_digest(left, right)
                for _, left in self._credential_digests
                for _, right in other._credential_digests
            )
        )

    def authorize(
        self,
        *,
        websocket: Any,
        operation: dict[str, Any],
        purpose: str,
        request_correlation_id: str,
        action: str,
        resource_fingerprint: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            session = self._active_session(websocket)
            if session is None:
                return None
            issued_at = datetime.now(UTC)
            expires_at = min(
                issued_at + timedelta(minutes=5),
                issued_at + timedelta(
                    seconds=max(
                        0,
                        int(session["expiresAt"] - time.monotonic()),
                    )
                ),
            )
            if expires_at <= issued_at:
                return None
            grant = {
                "authenticated": True,
                "principal": session["principal"],
                "grantId": str(uuid4()),
                "operationId": operation["operationId"],
                "operationCorrelationId": operation["correlationId"],
                "requestCorrelationId": request_correlation_id,
                "requestFingerprint": operation["requestFingerprint"],
                "purpose": purpose,
                "action": action,
                "resourceFingerprint": resource_fingerprint,
                "issuedAt": issued_at.isoformat(),
                "expiresAt": expires_at.isoformat(),
                "authorizationContext": {
                    "method": "websocket-bearer-session",
                    "sessionId": session["sessionId"],
                },
            }
            grant["signature"] = self._sign_grant(grant)
            return grant

    def validate(
        self,
        *,
        grant: dict[str, Any],
        operation: dict[str, Any],
        purpose: str,
        action: str,
        resource_fingerprint: str,
    ) -> bool:
        if not self._valid_grant_signature(grant):
            return False
        try:
            expires_at = datetime.fromisoformat(
                grant["expiresAt"].replace("Z", "+00:00")
            )
        except (KeyError, AttributeError, ValueError):
            return False
        if expires_at.tzinfo is None or expires_at <= datetime.now(UTC):
            return False
        context = grant.get("authorizationContext")
        if not isinstance(context, dict):
            return False
        session_id = context.get("sessionId")
        with self._lock:
            for websocket in tuple(self._sessions):
                session = self._active_session(websocket)
                if (
                    session is not None
                    and session["sessionId"] == session_id
                    and session["principal"] == grant.get("principal")
                    and operation.get("operationId") == grant.get("operationId")
                    and operation.get("correlationId")
                    == grant.get("operationCorrelationId")
                    and operation.get("requestFingerprint")
                    == grant.get("requestFingerprint")
                    and purpose == grant.get("purpose")
                    and action == grant.get("action")
                    and resource_fingerprint
                    == grant.get("resourceFingerprint")
                ):
                    return True
        return False

    def validate_context(
        self,
        context: AuthorizationContext,
        *,
        action: str,
        parameters: dict[str, Any],
        operation_id: str,
        correlation_id: str,
    ) -> bool:
        if type(context) is not AuthorizationContext:
            return False
        if (
            context.operation_id != operation_id
            or context.operation_correlation_id != correlation_id
            or context.action != action
            or context.resource_fingerprint
            != fingerprint_resource(action, parameters)
        ):
            return False
        grant = context.to_grant()
        operation = {
            "operationId": context.operation_id,
            "correlationId": context.operation_correlation_id,
            "requestFingerprint": context.request_fingerprint,
        }
        return self.validate(
            grant=grant,
            operation=operation,
            purpose=context.purpose,
            action=action,
            resource_fingerprint=context.resource_fingerprint,
        )

    def validate_and_claim_dispatch(
        self,
        context: AuthorizationContext,
        operation_store: SQLiteOperationStore,
        *,
        action: str,
        parameters: dict[str, Any],
        operation_id: str,
        correlation_id: str,
    ) -> bool:
        """Serialize revocation with the persisted one-use dispatch claim."""
        if not _is_sqlite_operation_store(operation_store):
            return False
        with self._lock:
            if not self.validate_context(
                context,
                action=action,
                parameters=parameters,
                operation_id=operation_id,
                correlation_id=correlation_id,
            ):
                return False
            return operation_store.claim_authorization_dispatch(
                grant_id=context.grant_id,
                operation_id=context.operation_id,
                correlation_id=context.request_correlation_id,
                request_fingerprint=context.request_fingerprint,
                purpose=context.purpose,
                principal=context.principal,
                action=context.action,
                resource_fingerprint=context.resource_fingerprint,
                expires_at=context.expires_at,
            )

    def validate_and_cancel_task(
        self,
        websocket: Any,
        grant: dict[str, Any],
        operation_store: SQLiteOperationStore,
        *,
        task_id: str,
    ) -> dict[str, str] | None:
        """Validate the exact active socket session while atomically cancelling."""
        if not _is_sqlite_operation_store(operation_store):
            return None
        with self._lock:
            session = self._active_session(websocket)
            context = grant.get("authorizationContext")
            if (
                session is None
                or not isinstance(context, dict)
                or context.get("sessionId") != session["sessionId"]
                or grant.get("principal") != session["principal"]
                or grant.get("purpose") != "cancel_task"
                or grant.get("action") != "cancel_task_before_dispatch"
                or not self._valid_grant_signature(grant)
            ):
                return None
            try:
                expires_at = datetime.fromisoformat(
                    grant["expiresAt"].replace("Z", "+00:00")
                )
            except (KeyError, AttributeError, ValueError):
                return None
            if expires_at.tzinfo is None or expires_at <= datetime.now(UTC):
                return None
            resource = {"taskId": task_id}
            if (
                grant.get("resourceFingerprint")
                != fingerprint_resource("cancel_task_before_dispatch", resource)
            ):
                return None
            operation = operation_store.get_by_id(
                grant.get("operationId", "")
            )
            if operation is None or not self.validate(
                grant=grant,
                operation=operation,
                purpose="cancel_task",
                action="cancel_task_before_dispatch",
                resource_fingerprint=grant["resourceFingerprint"],
            ):
                return None
            return operation_store.cancel_task_with_grant(
                grant,
                task_id=task_id,
            )

    def authorize_child_operation(
        self,
        *,
        parent_context: AuthorizationContext,
        parent_operation: dict[str, Any],
        operation_store: SQLiteOperationStore,
        child_operation: dict[str, Any],
        action: str,
        resource_fingerprint: str,
    ) -> dict[str, Any] | None:
        """Derive a narrower signed grant from an approved exact task plan."""
        parameters = parent_operation.get("parameters")
        if (
            not _is_sqlite_operation_store(operation_store)
            or parent_operation.get("lifecycleState") != "EXECUTING"
            or parent_operation.get("verb") != "execute_task"
            or not isinstance(parameters, dict)
            or parent_context.operation_id != parent_operation.get("operationId")
            or parent_context.operation_correlation_id
            != parent_operation.get("correlationId")
            or parent_context.request_fingerprint
            != parent_operation.get("requestFingerprint")
            or not operation_store.authorization_dispatch_claimed(
                grant_id=parent_context.grant_id,
                operation_id=parent_context.operation_id,
                correlation_id=parent_context.request_correlation_id,
                request_fingerprint=parent_context.request_fingerprint,
                principal=parent_context.principal,
                action=parent_context.action,
                resource_fingerprint=parent_context.resource_fingerprint,
            )
            or not self.validate_context(
                parent_context,
                action="run_task",
                parameters=parameters,
                operation_id=str(parent_operation.get("operationId", "")),
                correlation_id=str(parent_operation.get("correlationId", "")),
            )
        ):
            return None

        child_operation_id = child_operation.get("operationId")
        child_correlation_id = child_operation.get("correlationId")
        child_fingerprint = child_operation.get("requestFingerprint")
        if not all(
            isinstance(value, str) and value
            for value in (
                child_operation_id,
                child_correlation_id,
                child_fingerprint,
            )
        ):
            return None
        child_parameters = child_operation.get("parameters")
        command = (
            child_parameters.get("subject")
            if isinstance(child_parameters, dict)
            and set(child_parameters) == {"subject"}
            else None
        )
        approved_steps = parameters.get("steps")
        if (
            action != "run_command"
            or child_operation.get("verb") != "execute"
            or command not in {"pwd", "whoami"}
            or not isinstance(approved_steps, list)
            or not any(
                isinstance(step, dict)
                and step.get("action") == "run_command"
                and isinstance(step.get("parameters"), dict)
                and step["parameters"] == {"command": command}
                and child_operation_id
                == f"{parent_operation.get('operationId')}:step:{step.get('order')}"
                for step in approved_steps
            )
            or fingerprint_resource(
                action,
                {"command": command},
            ) != resource_fingerprint
        ):
            return None

        with self._lock:
            session = next(
                (
                    active
                    for websocket in tuple(self._sessions)
                    if (active := self._active_session(websocket)) is not None
                    and active["sessionId"] == parent_context.session_id
                    and active["principal"] == parent_context.principal
                ),
                None,
            )
            if session is None:
                return None
            issued_at = datetime.now(UTC)
            parent_expiry = datetime.fromisoformat(
                parent_context.expires_at.replace("Z", "+00:00")
            )
            expires_at = min(
                parent_expiry,
                issued_at
                + timedelta(
                    seconds=max(
                        0,
                        int(session["expiresAt"] - time.monotonic()),
                    )
                ),
            )
            if expires_at <= issued_at:
                return None
            grant = {
                "authenticated": True,
                "principal": parent_context.principal,
                "grantId": str(uuid4()),
                "operationId": child_operation_id,
                "operationCorrelationId": child_correlation_id,
                "requestCorrelationId": child_correlation_id,
                "requestFingerprint": child_fingerprint,
                "purpose": "execute",
                "action": action,
                "resourceFingerprint": resource_fingerprint,
                "issuedAt": issued_at.isoformat(),
                "expiresAt": expires_at.isoformat(),
                "authorizationContext": {
                    "method": parent_context.method,
                    "sessionId": parent_context.session_id,
                },
            }
            grant["signature"] = self._sign_grant(grant)
            return grant

    def _sign_grant(self, grant: dict[str, Any]) -> str:
        payload = {
            key: grant[key]
            for key in (
                "principal",
                "grantId",
                "operationId",
                "operationCorrelationId",
                "requestCorrelationId",
                "requestFingerprint",
                "purpose",
                "action",
                "resourceFingerprint",
                "issuedAt",
                "expiresAt",
            )
        }
        payload["authorizationContext"] = grant["authorizationContext"]
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hmac.new(self._token_digest, canonical, hashlib.sha256).hexdigest()

    def _valid_grant_signature(self, grant: dict[str, Any]) -> bool:
        signature = grant.get("signature")
        if not isinstance(signature, str):
            return False
        try:
            expected = self._sign_grant(grant)
        except (KeyError, TypeError, ValueError):
            return False
        return hmac.compare_digest(signature, expected)

    def _active_session(self, websocket: Any) -> dict[str, Any] | None:
        session = self._sessions.get(websocket)
        if session is None:
            return None
        now = time.monotonic()
        if (
            now >= session["expiresAt"]
            or now - session["lastActivity"] >= self.IDLE_TTL_SECONDS
        ):
            self._sessions.pop(websocket, None)
            return None
        return session
