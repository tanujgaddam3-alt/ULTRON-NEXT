from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from scripts.cre_approval_verifier import (
    SIGNED_FIELDS,
    ReplayStore,
    SignatureVerifier,
    TrustLookup,
    TrustProvider,
    TrustStatus,
    TrustedSigner,
    VerificationPolicy,
    VerificationStatus,
    VerifierUnavailable,
    canonical_signature_payload,
    verify_approval,
)


NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
CATEGORY = "migration_v7_to_v9_approval"
SCOPE = "cre-v7-to-v9-migration"
DATABASE_ID = "fixture-database"
WINDOW_ID = "fixture-window"
SIGNER_ID = "fixture-signer"
AUTHORITY_ID = "fixture-authority"
POLICY = VerificationPolicy(
    max_approval_age=timedelta(hours=1),
    max_revocation_age=timedelta(minutes=5),
)


class FixtureTrustProvider(TrustProvider):
    def __init__(self, signer: TrustedSigner | None = None, *, unavailable=False):
        self.signer = signer
        self.unavailable = unavailable

    def resolve(self, signer_id: str, now: datetime) -> TrustLookup:
        if self.unavailable:
            raise VerifierUnavailable
        if self.signer is None or signer_id != self.signer.signer_id:
            return TrustLookup(TrustStatus.UNKNOWN)
        status = TrustStatus.REVOKED if self.signer.revoked else TrustStatus.TRUSTED
        return TrustLookup(status, self.signer)


class FixtureSignatureVerifier(SignatureVerifier):
    """Test-only signature oracle; it is not an implemented crypto algorithm."""

    def verify(self, key_reference: object, payload: bytes, signature: str) -> bool:
        return (
            key_reference == "explicit-test-trust-fixture"
            and signature == "fixture-signature:" + hashlib.sha256(payload).hexdigest()
        )


class FixtureReplayStore(ReplayStore):
    def __init__(self, *, unavailable=False):
        self.claimed_approval_ids: set[str] = set()
        self.claimed_nonces: set[str] = set()
        self.unavailable = unavailable

    def claim(self, approval_id: str, nonce: str) -> bool:
        if self.unavailable:
            raise VerifierUnavailable
        if approval_id in self.claimed_approval_ids or nonce in self.claimed_nonces:
            return False
        self.claimed_approval_ids.add(approval_id)
        self.claimed_nonces.add(nonce)
        return True


def _signer(**changes) -> TrustedSigner:
    signer = TrustedSigner(
        signer_id=SIGNER_ID,
        authority_id=AUTHORITY_ID,
        key_reference="explicit-test-trust-fixture",
        allowed_actions=frozenset({(CATEGORY, SCOPE)}),
        valid_from=NOW - timedelta(days=1),
        valid_until=NOW + timedelta(days=1),
        revoked=False,
        revocation_checked_at=NOW - timedelta(minutes=1),
    )
    return replace(signer, **changes)


def _evidence(*, scope: str = SCOPE, recorded_at: str | None = None) -> bytes:
    return json.dumps(
        {
            "decision": "APPROVED",
            "scope": scope,
            "recordedAt": recorded_at or (NOW - timedelta(minutes=2)).isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _envelope(
    evidence: bytes,
    *,
    category: str = CATEGORY,
    scope: str = SCOPE,
    database_id: str = DATABASE_ID,
    window_id: str = WINDOW_ID,
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
    signer_id: str = SIGNER_ID,
    authority_id: str = AUTHORITY_ID,
    approval_id: str = "fixture-approval",
    nonce: str = "fixture-nonce",
) -> dict:
    issued = issued_at or NOW - timedelta(minutes=2)
    expires = expires_at or NOW + timedelta(minutes=10)
    fields = {
        "format": "cre-approval-v1",
        "approvalId": approval_id,
        "category": category,
        "scope": scope,
        "decision": "APPROVED",
        "signerId": signer_id,
        "authorityId": authority_id,
        "databaseId": database_id,
        "maintenanceWindowId": window_id,
        "issuedAt": issued.isoformat(),
        "expiresAt": expires.isoformat(),
        "nonce": nonce,
        "evidenceSha256": hashlib.sha256(evidence).hexdigest(),
    }
    return {
        **fields,
        "signature": "fixture-signature:"
        + hashlib.sha256(canonical_signature_payload(fields)).hexdigest(),
    }


def _verify(
    evidence: bytes,
    envelope: dict | None,
    *,
    trust_provider: TrustProvider | None = None,
    signature_verifier: SignatureVerifier | None = None,
    replay_store: ReplayStore | None = None,
    policy: VerificationPolicy | None = POLICY,
    expected_category: str = CATEGORY,
    expected_scope: str = SCOPE,
    expected_database_id: str = DATABASE_ID,
    expected_window_id: str = WINDOW_ID,
):
    return verify_approval(
        evidence,
        envelope,
        expected_category=expected_category,
        expected_scope=expected_scope,
        expected_database_id=expected_database_id,
        expected_maintenance_window_id=expected_window_id,
        now=NOW,
        trust_provider=trust_provider or FixtureTrustProvider(_signer()),
        signature_verifier=signature_verifier or FixtureSignatureVerifier(),
        replay_store=replay_store or FixtureReplayStore(),
        policy=policy,
    )


def test_valid_verification_requires_explicit_test_trust_fixture():
    evidence = _evidence()
    result = verify_approval(
        evidence,
        _envelope(evidence),
        expected_category=CATEGORY,
        expected_scope=SCOPE,
        expected_database_id=DATABASE_ID,
        expected_maintenance_window_id=WINDOW_ID,
        now=NOW,
        trust_provider=FixtureTrustProvider(_signer()),
        signature_verifier=FixtureSignatureVerifier(),
        replay_store=FixtureReplayStore(),
        policy=POLICY,
    )

    assert result.status == VerificationStatus.VERIFIED
    assert result.evidence_sha256 == hashlib.sha256(evidence).hexdigest()
    assert result.verified_at == NOW.isoformat()


def test_missing_signature_is_invalid():
    evidence = _evidence()

    result = _verify(evidence, None)

    assert result.status == VerificationStatus.INVALID
    assert result.reason == "signature_missing"


def test_unknown_signer_is_invalid():
    evidence = _evidence()

    result = _verify(
        evidence,
        _envelope(evidence),
        trust_provider=FixtureTrustProvider(),
    )

    assert result.status == VerificationStatus.INVALID
    assert result.reason == "unknown_signer"


@pytest.mark.parametrize(
    ("envelope_changes", "evidence_scope", "expected_category", "expected_scope", "reason"),
    [
        (
            {"scope": "cre-v9-to-v10-migration"},
            SCOPE,
            CATEGORY,
            SCOPE,
            "approval_claim_binding_mismatch",
        ),
        (
            {
                "category": "migration_v9_to_v10_approval",
                "scope": "cre-v9-to-v10-migration",
            },
            "cre-v9-to-v10-migration",
            "migration_v9_to_v10_approval",
            "cre-v9-to-v10-migration",
            "signer_not_authorized_for_action",
        ),
    ],
)
def test_wrong_action_or_scope_is_invalid(
    envelope_changes, evidence_scope, expected_category, expected_scope, reason
):
    evidence = _evidence(scope=evidence_scope)
    envelope = _envelope(evidence, **envelope_changes)

    result = _verify(
        evidence,
        envelope,
        expected_category=expected_category,
        expected_scope=expected_scope,
    )

    assert result.status == VerificationStatus.INVALID
    assert result.reason == reason


@pytest.mark.parametrize(
    ("envelope_changes", "reason"),
    [
        ({"database_id": "different-database"}, "approval_claim_binding_mismatch"),
        ({"window_id": "different-window"}, "approval_claim_binding_mismatch"),
    ],
)
def test_wrong_database_or_maintenance_window_is_invalid(envelope_changes, reason):
    evidence = _evidence()
    envelope = _envelope(evidence, **envelope_changes)

    result = _verify(evidence, envelope)

    assert result.status == VerificationStatus.INVALID
    assert result.reason == reason


def test_stale_approval_is_expired():
    evidence = _evidence(recorded_at=(NOW - timedelta(hours=2)).isoformat())
    envelope = _envelope(
        evidence,
        issued_at=NOW - timedelta(hours=2),
        expires_at=NOW + timedelta(minutes=5),
    )

    result = _verify(evidence, envelope)

    assert result.status == VerificationStatus.EXPIRED


def test_revoked_approval_is_invalid():
    evidence = _evidence()

    result = _verify(
        evidence,
        _envelope(evidence),
        trust_provider=FixtureTrustProvider(_signer(revoked=True)),
    )

    assert result.status == VerificationStatus.INVALID
    assert result.reason == "signer_revoked"


def test_stale_revocation_status_is_unverified():
    evidence = _evidence()

    result = _verify(
        evidence,
        _envelope(evidence),
        trust_provider=FixtureTrustProvider(
            _signer(revocation_checked_at=NOW - timedelta(minutes=10))
        ),
    )

    assert result.status == VerificationStatus.SUPPLIED_UNVERIFIED
    assert result.reason == "revocation_status_stale_or_future_dated"


def test_replayed_approval_is_invalid():
    evidence = _evidence()
    envelope = _envelope(evidence)
    replay_store = FixtureReplayStore()
    assert _verify(evidence, envelope, replay_store=replay_store).status == VerificationStatus.VERIFIED

    replayed = _verify(evidence, envelope, replay_store=replay_store)

    assert replayed.status == VerificationStatus.INVALID
    assert replayed.reason == "approval_replayed"


def test_altered_evidence_bytes_are_invalid():
    evidence = _evidence()
    envelope = _envelope(evidence)

    result = _verify(evidence + b" ", envelope)

    assert result.status == VerificationStatus.INVALID
    assert result.reason == "approval_claim_binding_mismatch"


@pytest.mark.parametrize(
    "evidence",
    [
        b'{"decision":"APPROVED","decision":"DENIED","scope":"scope"}',
        b'{"decision":',
        b"[]",
    ],
)
def test_malformed_input_is_invalid(evidence):
    result = _verify(evidence, _envelope(evidence))

    assert result.status == VerificationStatus.INVALID
    assert result.reason == "malformed_evidence_json"


def test_missing_trust_configuration_fails_closed():
    evidence = _evidence()

    result = verify_approval(
        evidence,
        _envelope(evidence),
        expected_category=CATEGORY,
        expected_scope=SCOPE,
        expected_database_id=DATABASE_ID,
        expected_maintenance_window_id=WINDOW_ID,
        now=NOW,
        signature_verifier=FixtureSignatureVerifier(),
        replay_store=FixtureReplayStore(),
        policy=POLICY,
    )

    assert result.status == VerificationStatus.SUPPLIED_UNVERIFIED
    assert result.reason == "trusted_verifier_configuration_unavailable"


def test_trust_service_outage_fails_closed():
    evidence = _evidence()

    result = _verify(
        evidence,
        _envelope(evidence),
        trust_provider=FixtureTrustProvider(unavailable=True),
    )

    assert result.status == VerificationStatus.SUPPLIED_UNVERIFIED
    assert result.reason == "trust_provider_unavailable"


def test_signature_verifier_outage_fails_closed():
    class UnavailableSignatureVerifier(SignatureVerifier):
        def verify(self, key_reference, payload, signature) -> bool:
            raise VerifierUnavailable

    evidence = _evidence()

    result = _verify(
        evidence,
        _envelope(evidence),
        signature_verifier=UnavailableSignatureVerifier(),
    )

    assert result.status == VerificationStatus.SUPPLIED_UNVERIFIED
    assert result.reason == "signature_verifier_unavailable"


def test_replay_store_outage_fails_closed():
    evidence = _evidence()

    result = _verify(
        evidence,
        _envelope(evidence),
        replay_store=FixtureReplayStore(unavailable=True),
    )

    assert result.status == VerificationStatus.SUPPLIED_UNVERIFIED
    assert result.reason == "replay_store_unavailable"


def test_signature_mismatch_is_invalid():
    evidence = _evidence()
    envelope = _envelope(evidence)
    envelope["signature"] = "not-a-valid-fixture-signature"

    result = _verify(evidence, envelope)

    assert result.status == VerificationStatus.INVALID
    assert result.reason == "signature_mismatch"


def test_signature_envelope_must_have_exact_contract_fields():
    evidence = _evidence()
    envelope = _envelope(evidence)
    envelope["unrecognized"] = "extra"

    result = _verify(evidence, envelope)

    assert result.status == VerificationStatus.INVALID
    assert result.reason == "malformed_signature_envelope"


def test_signed_field_contract_is_fixed():
    assert set(SIGNED_FIELDS) == {
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
    }
