"""Fail-closed boundary for verifying detached CRE approval signatures.

This module deliberately defines no production trust root or signature
algorithm. Callers must inject organization-approved trust, signature,
replay, and freshness-policy adapters before an approval can be verified.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Protocol

MAX_APPROVAL_BYTES = 1_048_576
SIGNED_FIELDS = (
    "format",
    "approvalId",
    "category",
    "scope",
    "decision",
    "signerId",
    "authorityId",
    "databaseId",
    "maintenanceWindowId",
    "issuedAt",
    "expiresAt",
    "nonce",
    "evidenceSha256",
)


class VerificationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    SUPPLIED_UNVERIFIED = "SUPPLIED_UNVERIFIED"
    INVALID = "INVALID"
    EXPIRED = "EXPIRED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class VerificationResult:
    status: VerificationStatus
    reason: str
    evidence_sha256: str | None
    verified_at: str | None


@dataclass(frozen=True)
class VerificationPolicy:
    max_approval_age: timedelta
    max_revocation_age: timedelta


@dataclass(frozen=True)
class TrustedSigner:
    signer_id: str
    authority_id: str
    key_reference: object
    allowed_actions: frozenset[tuple[str, str]]
    valid_from: datetime
    valid_until: datetime
    revoked: bool
    revocation_checked_at: datetime


class TrustStatus(str, Enum):
    TRUSTED = "TRUSTED"
    UNKNOWN = "UNKNOWN"
    REVOKED = "REVOKED"


@dataclass(frozen=True)
class TrustLookup:
    status: TrustStatus
    signer: TrustedSigner | None = None


class VerifierUnavailable(RuntimeError):
    """A trust, signature, or replay service is unavailable."""


class TrustProvider(Protocol):
    def resolve(self, signer_id: str, now: datetime) -> TrustLookup: ...


class SignatureVerifier(Protocol):
    def verify(
        self,
        key_reference: object,
        payload: bytes,
        signature: str,
    ) -> bool: ...


class ReplayStore(Protocol):
    def claim(self, approval_id: str, nonce: str) -> bool:
        """Atomically claim both identifiers; reject reuse of either one."""
        ...


def canonical_signature_payload(fields: dict[str, Any]) -> bytes:
    """Encode the signed claim set deterministically for injected algorithms."""
    if set(fields) != set(SIGNED_FIELDS):
        raise ValueError("Signed approval fields do not match the verifier contract.")
    try:
        return json.dumps(
            fields,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("Signed approval fields are not canonical JSON values.") from error


def _parse_aware_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        return None
    return result.astimezone(UTC)


def _loads_unique_object(raw: bytes) -> dict[str, Any] | None:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value

    try:
        parsed = json.loads(raw, object_pairs_hook=reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _result(
    status: VerificationStatus,
    reason: str,
    digest: str | None,
    now: datetime | None,
) -> VerificationResult:
    return VerificationResult(
        status=status,
        reason=reason,
        evidence_sha256=digest,
        verified_at=now.astimezone(UTC).isoformat() if now is not None else None,
    )


def verify_approval(
    evidence_bytes: bytes,
    signature_envelope: dict[str, Any] | None,
    *,
    expected_category: str,
    expected_scope: str,
    expected_database_id: str,
    expected_maintenance_window_id: str,
    now: datetime,
    trust_provider: TrustProvider | None = None,
    signature_verifier: SignatureVerifier | None = None,
    replay_store: ReplayStore | None = None,
    policy: VerificationPolicy | None = None,
) -> VerificationResult:
    """Verify one exact approval document against explicitly injected trust.

    The detached signature envelope signs the SHA-256 digest of the exact
    evidence bytes plus action, identity, database, window, freshness, and
    one-use nonce claims. Structural validity alone never returns VERIFIED.
    """
    current_time = (
        now.astimezone(UTC)
        if now.tzinfo is not None and now.utcoffset() is not None
        else None
    )
    digest = hashlib.sha256(evidence_bytes).hexdigest()

    if current_time is None:
        return _result(VerificationStatus.INVALID, "invalid_verification_time", digest, None)
    if len(evidence_bytes) > MAX_APPROVAL_BYTES:
        return _result(VerificationStatus.INVALID, "evidence_too_large", digest, current_time)
    if not all(
        isinstance(value, str) and value
        for value in (
            expected_category,
            expected_scope,
            expected_database_id,
            expected_maintenance_window_id,
        )
    ):
        return _result(
            VerificationStatus.SUPPLIED_UNVERIFIED,
            "verification_scope_configuration_invalid",
            digest,
            current_time,
        )
    if signature_envelope is None:
        return _result(VerificationStatus.INVALID, "signature_missing", digest, current_time)
    if any(
        component is None
        for component in (trust_provider, signature_verifier, replay_store, policy)
    ):
        return _result(
            VerificationStatus.SUPPLIED_UNVERIFIED,
            "trusted_verifier_configuration_unavailable",
            digest,
            current_time,
        )
    if (
        not isinstance(policy.max_approval_age, timedelta)
        or not isinstance(policy.max_revocation_age, timedelta)
        or policy.max_approval_age <= timedelta(0)
        or policy.max_revocation_age <= timedelta(0)
    ):
        return _result(
            VerificationStatus.SUPPLIED_UNVERIFIED,
            "verification_policy_invalid",
            digest,
            current_time,
        )

    claims = _loads_unique_object(evidence_bytes)
    if claims is None:
        return _result(VerificationStatus.INVALID, "malformed_evidence_json", digest, current_time)

    required_envelope_fields = set(SIGNED_FIELDS) | {"signature"}
    if (
        not isinstance(signature_envelope, dict)
        or set(signature_envelope) != required_envelope_fields
    ):
        return _result(
            VerificationStatus.INVALID,
            "malformed_signature_envelope",
            digest,
            current_time,
        )
    if (
        signature_envelope.get("format") != "cre-approval-v1"
        or signature_envelope.get("category") != expected_category
        or signature_envelope.get("scope") != expected_scope
        or signature_envelope.get("decision") != "APPROVED"
        or signature_envelope.get("decision") != claims.get("decision")
        or signature_envelope.get("scope") != claims.get("scope")
        or signature_envelope.get("evidenceSha256") != digest
        or signature_envelope.get("databaseId") != expected_database_id
        or signature_envelope.get("maintenanceWindowId")
        != expected_maintenance_window_id
        or signature_envelope.get("issuedAt") != claims.get("recordedAt")
    ):
        return _result(
            VerificationStatus.INVALID,
            "approval_claim_binding_mismatch",
            digest,
            current_time,
        )

    approval_id = signature_envelope.get("approvalId")
    signer_id = signature_envelope.get("signerId")
    authority_id = signature_envelope.get("authorityId")
    nonce = signature_envelope.get("nonce")
    signature = signature_envelope.get("signature")
    if not all(
        isinstance(value, str) and value
        for value in (approval_id, signer_id, authority_id, nonce, signature)
    ):
        return _result(
            VerificationStatus.INVALID,
            "required_signature_claim_missing",
            digest,
            current_time,
        )
    if len(signature) > MAX_APPROVAL_BYTES:
        return _result(
            VerificationStatus.INVALID,
            "signature_too_large",
            digest,
            current_time,
        )

    issued_at = _parse_aware_timestamp(signature_envelope.get("issuedAt"))
    expires_at = _parse_aware_timestamp(signature_envelope.get("expiresAt"))
    if issued_at is None or expires_at is None or expires_at <= issued_at:
        return _result(
            VerificationStatus.INVALID,
            "invalid_approval_timestamps",
            digest,
            current_time,
        )
    if issued_at > current_time:
        return _result(
            VerificationStatus.INVALID,
            "approval_is_future_dated",
            digest,
            current_time,
        )
    if expires_at <= current_time or current_time - issued_at > policy.max_approval_age:
        return _result(VerificationStatus.EXPIRED, "approval_is_expired_or_stale", digest, current_time)

    try:
        trust = trust_provider.resolve(signer_id, current_time)
    except VerifierUnavailable:
        return _result(
            VerificationStatus.SUPPLIED_UNVERIFIED,
            "trust_provider_unavailable",
            digest,
            current_time,
        )
    if trust.status == TrustStatus.UNKNOWN or trust.signer is None:
        return _result(VerificationStatus.INVALID, "unknown_signer", digest, current_time)
    if trust.status == TrustStatus.REVOKED or trust.signer.revoked:
        return _result(VerificationStatus.INVALID, "signer_revoked", digest, current_time)
    if trust.status != TrustStatus.TRUSTED:
        return _result(
            VerificationStatus.SUPPLIED_UNVERIFIED,
            "trust_status_unavailable",
            digest,
            current_time,
        )

    signer = trust.signer
    if signer.signer_id != signer_id or signer.authority_id != authority_id:
        return _result(
            VerificationStatus.INVALID,
            "signer_authority_mismatch",
            digest,
            current_time,
        )
    signer_valid_from = _parse_aware_timestamp(signer.valid_from.isoformat())
    signer_valid_until = _parse_aware_timestamp(signer.valid_until.isoformat())
    signer_revocation_time = _parse_aware_timestamp(
        signer.revocation_checked_at.isoformat()
    )
    if (
        signer_valid_from is None
        or signer_valid_until is None
        or signer_revocation_time is None
    ):
        return _result(
            VerificationStatus.SUPPLIED_UNVERIFIED,
            "trust_record_timestamps_invalid",
            digest,
            current_time,
        )
    if (
        (expected_category, expected_scope) not in signer.allowed_actions
        or current_time < signer_valid_from
        or current_time >= signer_valid_until
    ):
        return _result(
            VerificationStatus.INVALID,
            "signer_not_authorized_for_action",
            digest,
            current_time,
        )
    if (
        signer_revocation_time > current_time
        or current_time - signer_revocation_time > policy.max_revocation_age
    ):
        return _result(
            VerificationStatus.SUPPLIED_UNVERIFIED,
            "revocation_status_stale_or_future_dated",
            digest,
            current_time,
        )

    signed_fields = {name: signature_envelope[name] for name in SIGNED_FIELDS}
    try:
        payload = canonical_signature_payload(signed_fields)
        if len(payload) > MAX_APPROVAL_BYTES:
            return _result(
                VerificationStatus.INVALID,
                "signed_payload_too_large",
                digest,
                current_time,
            )
        signature_valid = signature_verifier.verify(
            signer.key_reference,
            payload,
            signature,
        )
    except VerifierUnavailable:
        return _result(
            VerificationStatus.SUPPLIED_UNVERIFIED,
            "signature_verifier_unavailable",
            digest,
            current_time,
        )
    except ValueError:
        return _result(
            VerificationStatus.INVALID,
            "malformed_signed_claims",
            digest,
            current_time,
        )
    if signature_valid is not True:
        return _result(
            VerificationStatus.INVALID,
            "signature_mismatch",
            digest,
            current_time,
        )

    try:
        claimed = replay_store.claim(approval_id, nonce)
    except VerifierUnavailable:
        return _result(
            VerificationStatus.SUPPLIED_UNVERIFIED,
            "replay_store_unavailable",
            digest,
            current_time,
        )
    if claimed is not True:
        return _result(VerificationStatus.INVALID, "approval_replayed", digest, current_time)

    return _result(VerificationStatus.VERIFIED, "verified", digest, current_time)
