from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


class AuthorizationRejectedError(PermissionError):
    """Trusted authorization failed before an operation reached its effect."""


@dataclass(frozen=True, slots=True)
class AuthorizationContext:
    """Immutable, authority-signed scope for one operation dispatch."""

    principal: str
    session_id: str
    grant_id: str
    operation_id: str
    operation_correlation_id: str
    request_correlation_id: str
    request_fingerprint: str
    purpose: str
    action: str
    resource_fingerprint: str
    issued_at: str
    expires_at: str
    method: str
    signature: str

    @classmethod
    def from_grant(cls, grant: dict[str, Any]) -> AuthorizationContext:
        context = grant.get("authorizationContext")
        if not isinstance(context, dict):
            raise ValueError("Authorization grant has no session context.")

        values = {
            "principal": grant.get("principal"),
            "session_id": context.get("sessionId"),
            "grant_id": grant.get("grantId"),
            "operation_id": grant.get("operationId"),
            "operation_correlation_id": grant.get("operationCorrelationId"),
            "request_correlation_id": grant.get("requestCorrelationId"),
            "request_fingerprint": grant.get("requestFingerprint"),
            "purpose": grant.get("purpose"),
            "action": grant.get("action"),
            "resource_fingerprint": grant.get("resourceFingerprint"),
            "issued_at": grant.get("issuedAt"),
            "expires_at": grant.get("expiresAt"),
            "method": context.get("method"),
            "signature": grant.get("signature"),
        }
        if any(not isinstance(value, str) or not value for value in values.values()):
            raise ValueError("Authorization grant is missing required fields.")
        return cls(**values)

    def to_grant(self) -> dict[str, Any]:
        return {
            "authenticated": True,
            "principal": self.principal,
            "grantId": self.grant_id,
            "operationId": self.operation_id,
            "operationCorrelationId": self.operation_correlation_id,
            "requestCorrelationId": self.request_correlation_id,
            "requestFingerprint": self.request_fingerprint,
            "purpose": self.purpose,
            "action": self.action,
            "resourceFingerprint": self.resource_fingerprint,
            "issuedAt": self.issued_at,
            "expiresAt": self.expires_at,
            "authorizationContext": {
                "method": self.method,
                "sessionId": self.session_id,
            },
            "signature": self.signature,
        }


_VALIDATED_AUTHORIZATION_SEAL = object()


@dataclass(frozen=True, slots=True, init=False)
class ValidatedAuthorization:
    """Opaque capability produced only after active-session validation."""

    context: AuthorizationContext
    _authority: Any = field(repr=False, compare=False)

    def __init__(
        self,
        context: AuthorizationContext,
        authority: Any,
        *,
        _seal: object,
    ) -> None:
        if _seal is not _VALIDATED_AUTHORIZATION_SEAL:
            raise TypeError("Validated authorization capabilities cannot be created directly.")
        object.__setattr__(self, "context", context)
        object.__setattr__(self, "_authority", authority)

    def is_valid_for(self, *, action: str, parameters: dict[str, Any]) -> bool:
        context = self.context
        return bool(
            self._authority.validate_context(
                context,
                action=action,
                parameters=parameters,
                operation_id=context.operation_id,
                correlation_id=context.operation_correlation_id,
            )
        )


def _validated_authorization(
    context: AuthorizationContext,
    authority: Any,
    *,
    action: str,
    parameters: dict[str, Any],
) -> ValidatedAuthorization:
    from ultron.security.identity.authenticator import WebSocketAuthenticator

    if (
        type(context) is not AuthorizationContext
        or type(authority) is not WebSocketAuthenticator
        or not authority.validate_context(
            context,
            action=action,
            parameters=parameters,
            operation_id=context.operation_id,
            correlation_id=context.operation_correlation_id,
        )
    ):
        raise PermissionError("A valid active-session authorization is required.")
    return ValidatedAuthorization(
        context,
        authority,
        _seal=_VALIDATED_AUTHORIZATION_SEAL,
    )


def resource_fingerprint(action: str, parameters: dict[str, Any]) -> str:
    """Fingerprint the exact action parameters at a dispatch boundary."""
    canonical = json.dumps(
        {"action": action, "parameters": parameters},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
