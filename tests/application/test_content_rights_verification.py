from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ultron.application.content.internet_archive import (
    InternetArchiveAcquisitionAdapter,
    InternetArchiveCandidate,
)
from ultron.application.content.rights_verification import (
    AutomatedRightsEvidence,
    FailClosedRightsVerifier,
    RightsVerificationDecision,
    TrustedIssuer,
    TrustedIssuerRegistry,
)
from ultron.domain.content import RightsAction


NOW = datetime(2026, 11, 7, tzinfo=UTC)
WORK = "internet_archive:test-item"
ASSET_HASH = "a" * 64
COMMERCIAL = frozenset(
    {
        RightsAction.EDIT,
        RightsAction.CREATE_DERIVATIVE,
        RightsAction.PUBLISH,
        RightsAction.COMMERCIAL_USE,
        RightsAction.MONETIZE,
    }
)


def evidence(
    evidence_id: str,
    kind: str,
    *,
    issuer: str = "trusted-rights-issuer.example",
    revoked: bool = False,
    expires_at: datetime | None = None,
    contradictory: frozenset[str] = frozenset(),
    asset_id: str | None = "asset-1",
    asset_sha256: str | None = ASSET_HASH,
    permitted_actions: frozenset[RightsAction] = COMMERCIAL,
    third_party_restrictions: tuple[str, ...] = (),
    retrieval_at: datetime = NOW - timedelta(days=2),
) -> AutomatedRightsEvidence:
    return AutomatedRightsEvidence(
        evidence_id=evidence_id,
        kind=kind,
        issuer=issuer,
        subject="rights-holder",
        work_identity=WORK,
        asset_id=asset_id,
        asset_sha256=asset_sha256,
        authority_scope="commercial editing and monetization" if kind == "authority" else None,
        permitted_actions=permitted_actions,
        source_reference=f"https://issuer.example/{evidence_id}",
        retrieval_at=retrieval_at,
        verification_method="independent_verifier_snapshot",
        expires_at=expires_at,
        revoked=revoked,
        evidence_sha256="b" * 64,
        contradictory_evidence_ids=contradictory,
        third_party_restrictions=third_party_restrictions,
    )


def complete_evidence() -> tuple[AutomatedRightsEvidence, ...]:
    return (
        evidence("license-1", "license_declaration"),
        evidence("holder-1", "rights_holder_identity"),
        evidence("authority-1", "authority"),
        evidence("third-party-1", "third_party_assessment"),
    )


def investigate(
    records: tuple[AutomatedRightsEvidence, ...],
    *,
    trusted: frozenset[str] = frozenset({"trusted-rights-issuer.example"}),
):
    return FailClosedRightsVerifier(trusted_issuers=trusted).investigate(
        work_identity=WORK,
        provider_identity="https://archive.org/details/test-item",
        evidence=records,
        required_actions=COMMERCIAL,
        asset_id="asset-1",
        asset_sha256=ASSET_HASH,
        now=NOW,
    )


def test_complete_independent_evidence_is_approved():
    result = investigate(complete_evidence())
    assert result.decision is RightsVerificationDecision.APPROVED


def test_ia_provider_metadata_never_self_approves():
    candidate = InternetArchiveCandidate(
        identifier="test-item",
        title="Test Item",
        creator="Forged Creator Claim",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        metadata_url="https://archive.org/metadata/test-item",
        canonical_url="https://archive.org/details/test-item",
    )
    result = InternetArchiveAcquisitionAdapter().automated_investigation(
        candidate,
        retrieved_at=NOW,
        provider_metadata_sha256="c" * 64,
    )
    assert result.decision is RightsVerificationDecision.CONDITIONAL
    assert "license_requires_independent_verification" in result.reasons


@pytest.mark.parametrize(
    ("records", "expected_reason"),
    [
        ((evidence("holder-1", "rights_holder_identity"),), "missing_license_evidence"),
        ((evidence("license-1", "license_declaration"),), "missing_rights_holder_identity"),
        ((evidence("license-1", "license_declaration"), evidence("holder-1", "rights_holder_identity")), "missing_authority_evidence"),
    ],
)
def test_missing_authority_or_identity_is_not_approved(records, expected_reason):
    result = investigate(records, trusted=frozenset({"trusted-rights-issuer.example"}))
    assert result.decision in {
        RightsVerificationDecision.UNKNOWN,
        RightsVerificationDecision.CONDITIONAL,
    }
    assert expected_reason in result.reasons


def test_revoked_expired_and_contradictory_evidence_fail_closed():
    revoked = tuple(
        item for item in complete_evidence()
        if item.kind != "authority"
    ) + (evidence("authority-1", "authority", revoked=True),)
    assert investigate(revoked).decision is RightsVerificationDecision.REJECTED

    expired = tuple(
        evidence("authority-1", "authority", expires_at=NOW - timedelta(seconds=1))
        if item.kind == "authority"
        else item
        for item in complete_evidence()
    )
    assert investigate(expired).decision is RightsVerificationDecision.REJECTED

    contradictory = tuple(
        evidence("authority-1", "authority", contradictory=frozenset({"license-1"}))
        if item.kind == "authority"
        else item
        for item in complete_evidence()
    )
    assert investigate(contradictory).decision is RightsVerificationDecision.REJECTED


def test_asset_hash_mismatch_replay_and_restrictions_are_rejected():
    mismatch = tuple(
        evidence("authority-1", "authority", asset_sha256="d" * 64)
        if item.kind == "authority"
        else item
        for item in complete_evidence()
    )
    assert investigate(mismatch).decision is RightsVerificationDecision.REJECTED

    replay = FailClosedRightsVerifier(
        trusted_issuers=frozenset({"trusted-rights-issuer.example"}),
        replay_evidence_ids=frozenset({"license-1"}),
    ).investigate(
        work_identity=WORK,
        provider_identity="https://archive.org/details/test-item",
        evidence=complete_evidence(),
        required_actions=COMMERCIAL,
        asset_id="asset-1",
        asset_sha256=ASSET_HASH,
        now=NOW,
    )
    assert replay.decision is RightsVerificationDecision.REJECTED
    assert "evidence_replay_detected" in replay.reasons

    restricted = tuple(
        evidence("third-party-1", "third_party_assessment", third_party_restrictions=("embeddedsong",))
        if item.kind == "third_party_assessment"
        else item
        for item in complete_evidence()
    )
    assert investigate(restricted).decision is RightsVerificationDecision.REJECTED


def trusted_issuer(tmp_path: Path, *, revoked: bool = False) -> TrustedIssuer:
    return TrustedIssuer(
        issuer_id="trusted-government.example",
        canonical_identity="https://trusted-government.example/identity",
        authoritative_endpoints=("https://trusted-government.example/rights",),
        issuer_category="government_authority",
        trust_basis="explicit_system_policy",
        authority_scope=("exact-work-license-and-holder-authority",),
        supported_evidence_types=frozenset(
            {"license_declaration", "rights_holder_identity", "authority", "third_party_assessment"}
        ),
        supported_license_types=frozenset({"CC0-1.0"}),
        permitted_intended_uses=COMMERCIAL,
        territories=("worldwide",),
        attribution_requirements=(),
        verification_methods=("independent_verifier_snapshot",),
        valid_from=NOW - timedelta(days=30),
        expires_at=NOW + timedelta(days=30),
        revoked=revoked,
    )


def test_trusted_issuer_registry_persists_and_reloads(tmp_path: Path):
    registry = TrustedIssuerRegistry(persistence_path=tmp_path / "trusted-issuers.json")
    issuer = trusted_issuer(tmp_path)
    registry.register(issuer, reason="system_policy_approval", now=NOW)

    reloaded = TrustedIssuerRegistry(persistence_path=tmp_path / "trusted-issuers.json")
    assert reloaded.get("trusted-government.example", now=NOW) == issuer
    assert reloaded.audit_history()[0].action == "registered"


def test_revoked_or_expired_registry_issuer_blocks_verification(tmp_path: Path):
    registry = TrustedIssuerRegistry(persistence_path=tmp_path / "trusted-issuers.json")
    registry.register(trusted_issuer(tmp_path), reason="system_policy_approval", now=NOW)
    registry.revoke("trusted-government.example", reason="authority_revoked", now=NOW)

    records = tuple(
        evidence(
            item_id,
            item_kind,
            issuer="trusted-government.example",
        )
        for item_id, item_kind in (
            ("license-1", "license_declaration"),
            ("holder-1", "rights_holder_identity"),
            ("authority-1", "authority"),
            ("third-party-1", "third_party_assessment"),
        )
    )
    result = FailClosedRightsVerifier(
        issuer_registry=registry,
        replay_evidence_ids=frozenset(),
    ).investigate(
        work_identity=WORK,
        provider_identity="https://archive.org/details/test-item",
        evidence=records,
        required_actions=COMMERCIAL,
        asset_id="asset-1",
        asset_sha256=ASSET_HASH,
        now=NOW,
    )
    assert result.decision is RightsVerificationDecision.CONDITIONAL
    assert "license_requires_independent_verification" in result.reasons


def test_impersonated_issuer_and_wrong_authority_scope_are_rejected(tmp_path: Path):
    registry = TrustedIssuerRegistry(persistence_path=tmp_path / "trusted-issuers.json")
    registry.register(trusted_issuer(tmp_path), reason="system_policy_approval", now=NOW)

    records = tuple(
        evidence(
            item_id,
            item_kind,
            issuer="trusted-government.example.evil.invalid",
        )
        for item_id, item_kind in (
            ("license-1", "license_declaration"),
            ("holder-1", "rights_holder_identity"),
            ("authority-1", "authority"),
            ("third-party-1", "third_party_assessment"),
        )
    )
    result = FailClosedRightsVerifier(
        issuer_registry=registry,
    ).investigate(
        work_identity=WORK,
        provider_identity="https://archive.org/details/test-item",
        evidence=records,
        required_actions=COMMERCIAL,
        asset_id="asset-1",
        asset_sha256=ASSET_HASH,
        now=NOW,
    )
    assert result.decision is RightsVerificationDecision.CONDITIONAL
