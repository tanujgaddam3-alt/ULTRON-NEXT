from __future__ import annotations

import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ultron.application.content.evidence_trust_policy import (
    EvidenceTrustPolicy,
    EvidenceTrustPolicyStore,
    RiskOperation,
    RightsDecisionModel,
)
from ultron.application.content.wikimedia_commons import CommonsCandidate, WikimediaCommonsAdapter
from ultron.application.content.rights_verification import (
    EvidenceAssuranceLevel,
    AutomatedRightsEvidence,
    RightsInvestigationResult,
    RightsVerificationDecision,
)
from ultron.domain.content import RightsAction


def _policy() -> EvidenceTrustPolicy:
    return EvidenceTrustPolicy(
        policy_id="owner-evidence-policy-v1",
        accepted_issuer_categories=frozenset({"government_media_usage_authority"}),
        accepted_license_types=frozenset({"CC BY 4.0", "CC BY-SA 4.0", "CC0 1.0", "Public domain", "NASA_OFFICIAL_USAGE_GUIDANCE"}),
        accepted_license_versions=frozenset({"4.0", "1.0", "public-domain", "official-guidance-current"}),
        permitted_intended_actions=frozenset({
            RightsAction.EDIT,
            RightsAction.CREATE_DERIVATIVE,
            RightsAction.PUBLISH,
            RightsAction.COMMERCIAL_USE,
        }),
        require_commercial_permission=True,
        require_derivative_permission=True,
        attribution_requirements=("NASA", "CC attribution"),
        territories=("*",),
        max_asset_duration_seconds=1800,
        reject_third_party_content=True,
        evidence_max_age=timedelta(days=30),
        require_unrevoked_evidence=True,
        manual_intervention_conditions=("missing_rights_holder_identity", "missing_authority_evidence", "third_party_material_assessment_missing"),
        accepted_assurance_thresholds={
            RiskOperation.LOCAL_PRIVATE_PROCESSING: EvidenceAssuranceLevel.A1_PROVIDER_DECLARATION,
            RiskOperation.EDITORIAL_PREPARATION: EvidenceAssuranceLevel.A2_PLATFORM_ENFORCED,
            RiskOperation.DERIVATIVE_CREATION: EvidenceAssuranceLevel.A3_INSTITUTIONAL,
            RiskOperation.COMMERCIAL_PUBLICATION: EvidenceAssuranceLevel.A3_INSTITUTIONAL,
            RiskOperation.MONETIZATION: EvidenceAssuranceLevel.A4_INDEPENDENT_AUTHORIZATION,
        },
        residual_risk_allowed=frozenset({RiskOperation.EDITORIAL_PREPARATION}),
    )


def test_owner_policy_persists_and_loads() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "owner_policy.json"
        store = EvidenceTrustPolicyStore(path)
        configured = store.configure(_policy())
        loaded = EvidenceTrustPolicyStore(path).get()
        assert loaded == configured


def test_owner_policy_cannot_be_configured_twice_without_explicit_update() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = EvidenceTrustPolicyStore(Path(directory) / "owner_policy.json")
        store.configure(_policy())
        with pytest.raises(ValueError, match="already configured"):
            store.configure(_policy())


def test_commons_artist_metadata_is_not_treated_as_owner_authority() -> None:
    candidate = CommonsCandidate(
        pageid=123,
        title="File:Test.webm",
        canonical_url="https://commons.wikimedia.org/wiki/File:Test.webm",
        download_url="https://upload.wikimedia.org/wikipedia/commons/test/Test.webm",
        size_bytes=1000,
        mime_type="video/webm",
        sha1="a" * 40,
        license_name="CC BY-SA 4.0",
        license_url="https://creativecommons.org/licenses/by-sa/4.0/",
        attribution_required=True,
        artist="Possible uploader artist",
        copyrighted=True,
        credit=None,
        metadata_sha256="b" * 64,
    )
    result = WikimediaCommonsAdapter().investigate(candidate, retrieved_at=datetime.now(UTC))
    assert result.decision == RightsVerificationDecision.CONDITIONAL
    assert "license_requires_independent_verification" in result.reasons


def test_commons_missing_license_is_rejected() -> None:
    candidate = CommonsCandidate(
        pageid=124,
        title="File:Missing.webm",
        canonical_url="https://commons.wikimedia.org/wiki/File:Missing.webm",
        download_url="https://upload.wikimedia.org/wikipedia/commons/test/Missing.webm",
        size_bytes=1000,
        mime_type="video/webm",
        sha1="a" * 40,
        license_name=None,
        license_url=None,
        attribution_required=False,
        artist=None,
        copyrighted=True,
        credit=None,
        metadata_sha256="c" * 64,
    )
    result = WikimediaCommonsAdapter().investigate(candidate, retrieved_at=datetime.now(UTC))
    assert result.decision == RightsVerificationDecision.REJECTED
    assert "unsupported_license_or_third_party_restriction" in result.reasons


def test_commons_bound_asset_request_does_not_override_license_verification() -> None:
    adapter = WikimediaCommonsAdapter()
    candidate = CommonsCandidate(
        pageid=125,
        title="File:Mismatch.webm",
        canonical_url="https://commons.wikimedia.org/wiki/File:Mismatch.webm",
        download_url="https://upload.wikimedia.org/wikipedia/commons/test/Mismatch.webm",
        size_bytes=1000,
        mime_type="video/webm",
        sha1="d" * 40,
        license_name="CC0 1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        attribution_required=False,
        artist="Uploader",
        copyrighted=False,
        credit=None,
        metadata_sha256="e" * 64,
    )
    result = adapter.investigate(
        candidate,
        retrieved_at=datetime.now(UTC),
        asset_id="different-asset",
        asset_sha256="f" * 64,
    )
    assert result.decision == RightsVerificationDecision.CONDITIONAL


def test_commons_license_is_platform_evidence_not_verified_authority() -> None:
    candidate = CommonsCandidate(
        pageid=126,
        title="File:Platform.webm",
        canonical_url="https://commons.wikimedia.org/wiki/File:Platform.webm",
        download_url="https://upload.wikimedia.org/wikipedia/commons/p/Platform.webm",
        size_bytes=1000,
        mime_type="video/webm",
        sha1="a" * 40,
        license_name="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution_required=True,
        artist=None,
        copyrighted=True,
        credit=None,
        metadata_sha256="a" * 64,
    )
    result = WikimediaCommonsAdapter().investigate(candidate, retrieved_at=datetime.now(UTC))
    for evidence in result.license_evidence:
        assert evidence.assurance_level == EvidenceAssuranceLevel.A2_PLATFORM_ENFORCED
        assert "rights_holder_authority_not_independently_verified" in evidence.source_limitations


def test_owner_policy_assigns_residual_risk_for_only_explicit_operations() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = EvidenceTrustPolicyStore(Path(directory) / "policy.json")
        configured = store.configure(_policy())
        loaded = EvidenceTrustPolicyStore(Path(directory) / "policy.json").get()
        assert loaded == configured
        assert loaded.accepted_assurance_thresholds[RiskOperation.MONETIZATION] == EvidenceAssuranceLevel.A4_INDEPENDENT_AUTHORIZATION
        assert RiskOperation.EDITORIAL_PREPARATION in loaded.residual_risk_allowed


def test_operation_decision_distinguishes_verified_from_residual_risk() -> None:
    now = datetime.now(UTC)
    evidence = AutomatedRightsEvidence(
        evidence_id="test-evidence",
        kind="license_declaration",
        issuer="institution",
        subject="asset",
        work_identity="institution:asset",
        asset_id=None,
        asset_sha256=None,
        authority_scope="institutional",
        permitted_actions=frozenset({
            RightsAction.EDIT,
            RightsAction.CREATE_DERIVATIVE,
            RightsAction.PUBLISH,
            RightsAction.COMMERCIAL_USE,
        }),
        source_reference="https://institution.example/rights",
        retrieval_at=now,
        verification_method="official_https",
        license_identifier="CC BY 4.0",
        license_version="4.0",
        assurance_level=EvidenceAssuranceLevel.A3_INSTITUTIONAL,
    )
    result = RightsInvestigationResult(
        decision=RightsVerificationDecision.APPROVED,
        work_identity="institution:asset",
        provider_identity="institution.example",
        license_evidence=(evidence,),
        rights_holder_evidence=(),
        authority_evidence=(evidence,),
        third_party_assessment=(),
        reasons=(),
    )
    policy = _policy()
    verified, _ = policy.evaluate_operation(
        result,
        operation=RiskOperation.EDITORIAL_PREPARATION,
        intended_actions=frozenset({
            RightsAction.EDIT,
            RightsAction.CREATE_DERIVATIVE,
            RightsAction.PUBLISH,
            RightsAction.COMMERCIAL_USE,
        }),
        duration_seconds=30,
        territory="US",
        trusted_issuer_categories={
            "institution": "government_media_usage_authority",
        },
        now=now,
    )
    assert verified == RightsDecisionModel.VERIFIED_APPROVED
    untrusted_issuer, untrusted_reasons = policy.evaluate_operation(
        result,
        operation=RiskOperation.EDITORIAL_PREPARATION,
        intended_actions=frozenset({
            RightsAction.EDIT,
            RightsAction.CREATE_DERIVATIVE,
            RightsAction.PUBLISH,
            RightsAction.COMMERCIAL_USE,
        }),
        duration_seconds=30,
        territory="US",
        trusted_issuer_categories={"institution": "unaccepted_category"},
        now=now,
    )
    assert untrusted_issuer == RightsDecisionModel.CONDITIONAL
    assert "issuer_category_not_owner_accepted_or_unverified" in untrusted_reasons

    residual_evidence = replace(
        evidence,
        evidence_id="residual-evidence",
        assurance_level=EvidenceAssuranceLevel.A2_PLATFORM_ENFORCED,
        source_limitations=("platform_declaration_without_holder_authority",),
    )
    residual_result = replace(
        result,
        license_evidence=(residual_evidence,),
        authority_evidence=(residual_evidence,),
    )
    residual, residual_reasons = policy.evaluate_operation(
        residual_result,
        operation=RiskOperation.EDITORIAL_PREPARATION,
        intended_actions=frozenset({
            RightsAction.EDIT,
            RightsAction.CREATE_DERIVATIVE,
            RightsAction.PUBLISH,
            RightsAction.COMMERCIAL_USE,
        }),
        duration_seconds=30,
        territory="US",
        trusted_issuer_categories={
            "institution": "government_media_usage_authority",
        },
        now=now,
    )
    assert residual == RightsDecisionModel.POLICY_ACCEPTED_WITH_RESIDUAL_RISK
    assert "owner_risk_policy_accepts_residual_risk" in residual_reasons
