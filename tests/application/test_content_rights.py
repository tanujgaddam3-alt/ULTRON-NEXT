from __future__ import annotations

import hashlib
from io import BytesIO
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import time
from uuid import uuid4

import pytest

from tests.application.schema_test_utils import set_legacy_schema_version
from ultron.application.content.policy import (
    ContentRightsPolicy,
    PlatformPolicyInputs,
    PlatformPolicyRule,
    YouTubeRightsAdapter,
)
from ultron.application.content.service import ContentRightsService
from ultron.application.content.store import ContentStore, IdempotencyConflictError
from ultron.domain.content import (
    AcquisitionMethod,
    AgreementStatus,
    AssetCategory,
    AssetLicenseVerificationState,
    ContentAssetUse,
    ContentPackage,
    ContentPackageVersion,
    ContentPlatform,
    ContentTargetContext,
    EvidenceDecision,
    GrantorAuthority,
    GrantorIdentity,
    OwnershipClaim,
    OwnershipClaimState,
    PlatformDecisionState,
    PublicationState,
    ReviewState,
    RightsAction,
    RightsEvidence,
    RightsGrant,
    RightsAgreement,
    RightsState,
    RightsTerms,
    RightsUse,
    OwnerRightsAttestation,
    SourceAccessPermissionState,
    SourceAsset,
    SourceProvenance,
    SourceReferenceRecord,
)
from ultron.security.identity.authorization import (
    AuthorizationContext,
    resource_fingerprint,
)
from ultron.security.identity.authenticator import WebSocketAuthenticator


TOKEN = "T" * 43
SOURCE_BYTES = b"synthetic authorized source media"
SOURCE_HASH = hashlib.sha256(SOURCE_BYTES).hexdigest()
ALL_RIGHTS = frozenset(RightsAction)


class RightsHarness:
    def __init__(
        self,
        database,
        *,
        principal: str = "alice",
        reviewers: frozenset[str] = frozenset({"alice"}),
        store: ContentStore | None = None,
        authority: WebSocketAuthenticator | None = None,
    ):
        self.store = store or ContentStore(database)
        self.authority = authority or WebSocketAuthenticator(TOKEN, principal)
        self.websocket = object()
        assert self.authority.authenticate(self.websocket, TOKEN) is not None
        self.service = ContentRightsService(
            store=self.store,
            authority=self.authority,
            reviewer_principals=reviewers | frozenset({"reviewer"}),
        )

    def context(self, action: str, payload: dict) -> AuthorizationContext:
        operation = {
            "operationId": str(uuid4()),
            "correlationId": str(uuid4()),
            "requestFingerprint": hashlib.sha256(str(uuid4()).encode()).hexdigest(),
        }
        grant = self.authority.authorize(
            websocket=self.websocket,
            operation=operation,
            purpose="content_rights",
            request_correlation_id=str(uuid4()),
            action=action,
            resource_fingerprint=resource_fingerprint(action, payload),
        )
        assert grant is not None
        return AuthorizationContext.from_grant(grant)

    def invoke(self, action: str, payload: dict, function, **kwargs):
        return function(
            context=self.context(action, payload),
            command_id=kwargs.pop("command_id", str(uuid4())),
            **kwargs,
        )

    def review_invoke(self, action: str, payload: dict, function, **kwargs):
        authority = WebSocketAuthenticator(TOKEN, "reviewer")
        websocket = object()
        assert authority.authenticate(websocket, TOKEN) is not None
        reviewer_service = ContentRightsService(
            store=self.store,
            authority=authority,
            reviewer_principals=self.service.reviewer_principals,
        )
        operation = {
            "operationId": str(uuid4()),
            "correlationId": str(uuid4()),
            "requestFingerprint": hashlib.sha256(str(uuid4()).encode()).hexdigest(),
        }
        grant = authority.authorize(
            websocket=websocket,
            operation=operation,
            purpose="content_rights_review",
            request_correlation_id=str(uuid4()),
            action=action,
            resource_fingerprint=resource_fingerprint(action, payload),
        )
        assert grant is not None
        reviewer_function = getattr(reviewer_service, function.__name__)
        return reviewer_function(
            context=AuthorizationContext.from_grant(grant),
            command_id=kwargs.pop("command_id", str(uuid4())),
            **kwargs,
        )


def make_asset(
    asset_id: str = "asset-footage",
    *,
    owner_id: str = "alice",
    category: AssetCategory = AssetCategory.FOOTAGE,
    raw: bytes = SOURCE_BYTES,
) -> SourceAsset:
    return SourceAsset(
        asset_id=asset_id,
        owner_id=owner_id,
        category=category,
        content_sha256=hashlib.sha256(raw).hexdigest(),
        provenance=SourceProvenance(
            acquisition_method=AcquisitionMethod.OWNER_UPLOAD,
            acquired_at=datetime.now(UTC),
            source_reference="upload:synthetic-fixture",
        ),
        storage_key=f"asset:{asset_id}",
    )


def make_terms(
    platform: ContentPlatform = ContentPlatform.YOUTUBE,
    *,
    actions: frozenset[RightsAction] = ALL_RIGHTS,
    account_scope: str = "channel-1",
    territories: tuple[str, ...] = ("US",),
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    attribution_required: bool = False,
    attribution_text: str | None = None,
    max_duration_seconds: int = 60,
) -> RightsTerms:
    now = datetime.now(UTC)
    return RightsTerms(
        platform=platform,
        account_scope=account_scope,
        territories=territories,
        permitted_actions=actions,
        valid_from=valid_from or now - timedelta(hours=1),
        valid_until=valid_until or now + timedelta(days=2),
        attribution_required=attribution_required,
        attribution_text=attribution_text,
        max_duration_seconds=max_duration_seconds,
    )


def add_verified_agreement(
    h: RightsHarness,
    asset: SourceAsset,
    claim: OwnershipClaim,
    terms: RightsTerms,
    *,
    agreement_id: str | None = None,
    version: int = 1,
    approve: bool = True,
) -> RightsAgreement:
    identity_proof = b"synthetic independently reviewed identity"
    identity = GrantorIdentity(
        grantor_id=f"grantor-{asset.asset_id}",
        owner_id=asset.owner_id,
        claimed_identity=claim.rights_holder,
        verification_method="independent synthetic reviewer",
        evidence_reference="synthetic:identity-review",
        evidence_sha256=hashlib.sha256(identity_proof).hexdigest(),
    )
    if h.store.get_grantor_identity(identity.grantor_id) is None:
        h.invoke(
            "content_register_grantor_identity",
            identity.model_dump(mode="json"),
            h.service.register_grantor_identity,
            identity=identity,
        )
        identity_review_payload = {
            "grantor_id": identity.grantor_id,
            "decision": EvidenceDecision.VERIFIED.value,
            "evidence_sha256": identity.evidence_sha256,
            "reason": "Synthetic test identity reviewed independently.",
        }
        h.review_invoke(
            "content_review_grantor_identity",
            identity_review_payload,
            h.service.review_grantor_identity,
            grantor_id=identity.grantor_id,
            decision=EvidenceDecision.VERIFIED,
            evidence_sha256=identity.evidence_sha256,
            reason=identity_review_payload["reason"],
        )

    authority_proof = b"synthetic independently reviewed authority"
    authority = GrantorAuthority(
        authority_id=f"authority-{asset.asset_id}",
        grantor_id=identity.grantor_id,
        asset_id=asset.asset_id,
        rights_holder=claim.rights_holder,
        relationship="authorized representative",
        scope_description=f"Rights for {asset.asset_id}",
        evidence_reference="synthetic:authority-review",
        evidence_sha256=hashlib.sha256(authority_proof).hexdigest(),
    )
    if h.store.get_grantor_authority(authority.authority_id) is None:
        h.invoke(
            "content_register_grantor_authority",
            authority.model_dump(mode="json"),
            h.service.register_grantor_authority,
            authority=authority,
        )
        authority_review_payload = {
            "authority_id": authority.authority_id,
            "decision": EvidenceDecision.VERIFIED.value,
            "evidence_sha256": authority.evidence_sha256,
            "reason": "Synthetic test authority reviewed independently.",
        }
        h.review_invoke(
            "content_review_grantor_authority",
            authority_review_payload,
            h.service.review_grantor_authority,
            authority_id=authority.authority_id,
            decision=EvidenceDecision.VERIFIED,
            evidence_sha256=authority.evidence_sha256,
            reason=authority_review_payload["reason"],
        )

    agreement_proof = b"synthetic rights agreement evidence"
    agreement = RightsAgreement(
        agreement_id=agreement_id or (
            f"agreement-{asset.asset_id}-{terms.platform.value}-"
            f"{terms.fingerprint()[:12]}"
        ),
        version=version,
        grantor_id=identity.grantor_id,
        authority_id=authority.authority_id,
        rights_holder=claim.rights_holder,
        grantee_owner_id=asset.owner_id,
        asset_ids=(asset.asset_id,),
        terms=terms,
        evidence_reference="synthetic:agreement",
        evidence_sha256=hashlib.sha256(agreement_proof).hexdigest(),
    )
    h.invoke(
        "content_create_rights_agreement",
        agreement.model_dump(mode="json", exclude={"status"}),
        h.service.create_rights_agreement,
        agreement=agreement,
    )
    if approve:
        review_payload = {
            "agreement_id": agreement.agreement_id,
            "agreement_version": agreement.version,
            "decision": EvidenceDecision.VERIFIED.value,
            "agreement_sha256": agreement.fingerprint(),
            "evidence_sha256": agreement.evidence_sha256,
            "reason": "Synthetic test agreement reviewed independently.",
        }
        h.review_invoke(
            "content_review_rights_agreement",
            review_payload,
            h.service.review_rights_agreement,
            agreement_id=agreement.agreement_id,
            agreement_version=agreement.version,
            decision=EvidenceDecision.VERIFIED,
            agreement_sha256=agreement.fingerprint(),
            evidence_sha256=agreement.evidence_sha256,
            reason=review_payload["reason"],
        )
    stored = h.store.get_rights_agreement(agreement.agreement_id, agreement.version)
    assert stored is not None
    expected_statuses = (
        {AgreementStatus.PENDING_REVIEW}
        if not approve
        else {AgreementStatus.APPROVED, AgreementStatus.EXPIRED}
    )
    assert stored.status in expected_statuses
    return stored


def make_use(
    asset: SourceAsset,
    platform: ContentPlatform = ContentPlatform.YOUTUBE,
    *,
    account_id: str = "channel-1",
    territory: str = "US",
    actions: frozenset[RightsAction] = frozenset({RightsAction.PUBLISH}),
    commercial: bool = False,
    monetization: bool = False,
    duration_seconds: int = 15,
    attribution_text: str | None = None,
    policy_facts: dict[str, bool] | None = None,
) -> RightsUse:
    return RightsUse(
        asset_id=asset.asset_id,
        expected_asset_sha256=asset.content_sha256,
        platform=platform,
        account_id=account_id,
        territory=territory,
        actions=actions,
        commercial_intent=commercial,
        monetization_intent=monetization,
        duration_seconds=duration_seconds,
        attribution_text=attribution_text,
        platform_policy_facts=policy_facts or {},
    )


def create_source(h: RightsHarness, asset: SourceAsset | None = None):
    asset = asset or make_asset()
    h.invoke(
        "content_register_asset",
        asset.model_dump(mode="json"),
        h.service.register_asset,
        asset=asset,
        source_stream=BytesIO(SOURCE_BYTES),
    )
    claim = OwnershipClaim(
        claim_id=f"claim-{asset.asset_id}",
        asset_id=asset.asset_id,
        claimant_id=asset.owner_id,
        rights_holder="Studio North",
        authority_description="Documented owner and licensing authority",
    )
    h.invoke(
        "content_add_ownership_claim",
        claim.model_dump(mode="json"),
        h.service.add_ownership_claim,
        claim=claim,
    )
    return asset, claim


def register_owner_created_source(h: RightsHarness) -> SourceAsset:
    now = datetime.now(UTC)
    asset = SourceAsset(
        asset_id="owner-created-footage",
        owner_id="alice",
        category=AssetCategory.FOOTAGE,
        content_sha256=SOURCE_HASH,
        provenance=SourceProvenance(
            acquisition_method=AcquisitionMethod.OWNER_CREATED,
            acquired_at=now,
            original_filename="original-footage.mp4",
            transfer_channel="whatsapp",
            source_reference=f"urn:ultron:owner-created:{SOURCE_HASH}",
            notes="Owner-created original footage.",
        ),
        storage_key="asset:owner-created-footage",
        created_at=now,
    )
    analysis_use = make_use(
        asset,
        actions=frozenset({RightsAction.ANALYZE}),
        duration_seconds=10,
    )
    source_reference = SourceReferenceRecord(
        intake_id="owner-created-intake",
        asset_id=asset.asset_id,
        owner_id=asset.owner_id,
        source_reference=asset.provenance.source_reference,
        acquisition_method=AcquisitionMethod.OWNER_CREATED,
        provenance=asset.provenance,
        claimed_source_sha256=SOURCE_HASH,
        access_permission_state=SourceAccessPermissionState.NOT_REQUIRED,
        target_context=ContentTargetContext.YOUTUBE_SHORTS,
        rights_use=analysis_use,
        created_at=now,
    )
    payload = {
        "asset": asset.model_dump(mode="json"),
        "source_reference": source_reference.model_dump(mode="json"),
    }
    h.invoke(
        "content_register_owner_created_asset",
        payload,
        h.service.register_asset,
        asset=asset,
        source_stream=BytesIO(SOURCE_BYTES),
        source_reference=source_reference,
    )
    return asset


def owner_attestation(asset: SourceAsset) -> OwnerRightsAttestation:
    now = datetime.now(UTC)
    return OwnerRightsAttestation(
        attestation_id="owner-attestation-1",
        asset_id=asset.asset_id,
        asset_sha256=asset.content_sha256,
        owner_id=asset.owner_id,
        ownership_declaration=True,
        ownership_basis="I created the original footage and own its included material.",
        evidence_reference=None,
        platform=ContentPlatform.YOUTUBE,
        account_scope="channel-1",
        territories=("US",),
        permitted_actions=frozenset(
            {
                RightsAction.ANALYZE,
                RightsAction.EDIT,
                RightsAction.CREATE_DERIVATIVE,
            }
        ),
        max_duration_seconds=60,
        limitations="No use outside the declared platform and territory scope.",
        valid_from=now - timedelta(minutes=1),
        valid_until=now + timedelta(days=1),
        created_at=now,
        transfer_channel="whatsapp",
    )


def add_grant(
    h: RightsHarness,
    asset: SourceAsset,
    claim: OwnershipClaim,
    terms: RightsTerms,
    *,
    evidence_id: str | None = None,
    grant_id: str | None = None,
    decision: EvidenceDecision = EvidenceDecision.VERIFIED,
):
    evidence_id = evidence_id or f"evidence-{terms.platform.value}-{uuid4()}"
    proof = f"synthetic reviewed grant evidence:{terms.fingerprint()}".encode()
    evidence = RightsEvidence(
        evidence_id=evidence_id,
        asset_id=asset.asset_id,
        claim_id=claim.claim_id,
        submitted_by=claim.claimant_id,
        evidence_kind="signed_license_document",
        evidence_bytes=proof,
        evidence_sha256=hashlib.sha256(proof).hexdigest(),
        grantor_identity=claim.rights_holder,
        grantor_authority=claim.authority_description,
        claimed_terms=terms,
    )
    h.invoke(
        "content_submit_rights_evidence",
        evidence.model_dump(mode="json", exclude={"evidence_bytes"}),
        h.service.submit_evidence,
        evidence=evidence,
    )
    h.review_invoke(
        "content_read_rights_evidence",
        {"evidence_id": evidence_id},
        h.service.get_evidence_for_review,
        evidence_id=evidence_id,
    )
    verified_terms_hash = (
        terms.fingerprint() if decision == EvidenceDecision.VERIFIED else None
    )
    review_payload = {
        "evidence_id": evidence_id,
        "decision": decision.value,
        "verified_terms_sha256": verified_terms_hash,
    }
    h.review_invoke(
        "content_verify_rights_evidence",
        review_payload,
        h.service.verify_evidence,
        evidence_id=evidence_id,
        decision=decision,
        verified_terms_sha256=verified_terms_hash,
    )
    if decision != EvidenceDecision.VERIFIED:
        return evidence, None

    agreement = add_verified_agreement(h, asset, claim, terms)
    grant = h.service.store.get_evidence(evidence_id)
    assert grant is not None
    rights_grant = RightsGrant(
        grant_id=grant_id or f"grant-{terms.platform.value}-{uuid4()}",
        asset_id=asset.asset_id,
        claim_id=claim.claim_id,
        evidence_id=evidence_id,
        grantor_identity=claim.rights_holder,
        grantor_authority=claim.authority_description,
        grantee_owner_id=asset.owner_id,
        terms=terms,
        agreement_id=agreement.agreement_id,
        agreement_version=agreement.version,
    )
    h.invoke(
        "content_create_rights_grant",
        rights_grant.model_dump(mode="json"),
        h.service.create_rights_grant,
        grant=rights_grant,
    )
    return evidence, rights_grant


def evaluate(h: RightsHarness, use: RightsUse, *, command_id: str | None = None):
    return h.invoke(
        "content_evaluate_rights",
        use.model_dump(mode="json"),
        h.service.evaluate_rights,
        use=use,
        **({"command_id": command_id} if command_id else {}),
    )


def make_package_version(
    package_id: str,
    asset: SourceAsset,
    use: RightsUse,
    *,
    version: int = 1,
    artifact: bytes = b"synthetic rendered artifact",
    metadata: dict | None = None,
) -> ContentPackageVersion:
    metadata = metadata or {"title": "Synthetic test clip"}
    metadata_hash = hashlib.sha256(
        json.dumps(
            metadata,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    return ContentPackageVersion(
        package_id=package_id,
        version=version,
        platform=use.platform,
        account_id=use.account_id,
        artifact_sha256=hashlib.sha256(artifact).hexdigest(),
        metadata_sha256=metadata_hash,
        metadata=metadata,
        asset_uses=(ContentAssetUse(use=use),),
    )


def create_package_with_version(
    h: RightsHarness,
    package_id: str,
    asset: SourceAsset,
    use: RightsUse,
    *,
    version: int = 1,
    artifact: bytes = b"synthetic rendered artifact",
    metadata: dict | None = None,
):
    package = ContentPackage(package_id=package_id, owner_id=asset.owner_id)
    h.invoke(
        "content_create_package",
        package.model_dump(mode="json"),
        h.service.create_content_package,
        package=package,
    )
    content_version = make_package_version(
        package_id,
        asset,
        use,
        version=version,
        artifact=artifact,
        metadata=metadata,
    )
    h.invoke(
        "content_create_package_version",
        content_version.model_dump(mode="json"),
        h.service.create_content_package_version,
        version=content_version,
        artifact_stream=BytesIO(artifact),
    )
    return content_version


def approve_version(h: RightsHarness, version: ContentPackageVersion):
    expiry = datetime.now(UTC) + timedelta(hours=1)
    payload = {
        "package_id": version.package_id,
        "package_version": version.version,
        "artifact_sha256": version.artifact_sha256,
        "metadata_sha256": version.metadata_sha256,
        "permitted_action": RightsAction.PUBLISH.value,
        "expires_at": expiry.isoformat(),
    }
    return h.invoke(
        "content_approve_version",
        payload,
        h.service.approve_content_version,
        package_id=version.package_id,
        package_version=version.version,
        artifact_sha256=version.artifact_sha256,
        metadata_sha256=version.metadata_sha256,
        expires_at=expiry,
    )


def test_youtube_and_instagram_grants_are_independent(tmp_path):
    h = RightsHarness(tmp_path / "rights.db")
    asset, claim = create_source(h)
    add_grant(h, asset, claim, make_terms(ContentPlatform.YOUTUBE))

    youtube = evaluate(h, make_use(asset, ContentPlatform.YOUTUBE))
    instagram = evaluate(h, make_use(asset, ContentPlatform.INSTAGRAM))
    wrong_channel = evaluate(
        h,
        make_use(asset, ContentPlatform.YOUTUBE, account_id="channel-2"),
    )

    assert youtube.copyright_state == RightsState.CLEARED
    assert youtube.platform_rights_state == RightsState.CLEARED
    assert instagram.platform_rights_state == RightsState.BLOCKED
    assert "grant_does_not_cover_requested_platform" in instagram.reason_codes
    assert wrong_channel.platform_rights_state == RightsState.BLOCKED
    assert h.service.store.get_publication_record(
        "missing", 1, ContentPlatform.YOUTUBE, "channel-1"
    ) is None
    h.store.close()


@pytest.mark.parametrize(
    ("platforms", "expected"),
    [
        ((ContentPlatform.INSTAGRAM,), (RightsState.BLOCKED, RightsState.CLEARED)),
        (
            (ContentPlatform.YOUTUBE, ContentPlatform.INSTAGRAM),
            (RightsState.CLEARED, RightsState.CLEARED),
        ),
    ],
)
def test_instagram_and_dual_platform_grants_are_independently_evaluated(
    tmp_path,
    platforms,
    expected,
):
    h = RightsHarness(tmp_path / "rights.db")
    asset, claim = create_source(h)
    for platform in platforms:
        add_grant(h, asset, claim, make_terms(platform))

    decisions = tuple(
        evaluate(h, make_use(asset, platform)).platform_rights_state
        for platform in (ContentPlatform.YOUTUBE, ContentPlatform.INSTAGRAM)
    )
    assert decisions == expected
    h.store.close()


def test_unknown_pending_ambiguous_and_rejected_evidence_fail_closed(tmp_path):
    h = RightsHarness(tmp_path / "rights.db")
    asset, claim = create_source(h)
    assert evaluate(h, make_use(asset)).platform_rights_state == RightsState.UNKNOWN

    terms = make_terms()
    proof = b"pending synthetic license"
    evidence = RightsEvidence(
        evidence_id="evidence-pending",
        asset_id=asset.asset_id,
        claim_id=claim.claim_id,
        submitted_by="alice",
        evidence_kind="license",
        evidence_bytes=proof,
        evidence_sha256=hashlib.sha256(proof).hexdigest(),
        grantor_identity=claim.rights_holder,
        grantor_authority=claim.authority_description,
        claimed_terms=terms,
    )
    h.invoke(
        "content_submit_rights_evidence",
        evidence.model_dump(mode="json", exclude={"evidence_bytes"}),
        h.service.submit_evidence,
        evidence=evidence,
    )
    h.review_invoke(
        "content_read_rights_evidence",
        {"evidence_id": evidence.evidence_id},
        h.service.get_evidence_for_review,
        evidence_id=evidence.evidence_id,
    )
    assert (
        evaluate(h, make_use(asset)).platform_rights_state
        == RightsState.REVIEW_REQUIRED
    )
    h.review_invoke(
        "content_verify_rights_evidence",
        {
            "evidence_id": evidence.evidence_id,
            "decision": EvidenceDecision.AMBIGUOUS.value,
            "verified_terms_sha256": None,
        },
        h.service.verify_evidence,
        evidence_id=evidence.evidence_id,
        decision=EvidenceDecision.AMBIGUOUS,
        verified_terms_sha256=None,
    )
    assert evaluate(h, make_use(asset)).platform_rights_state == RightsState.BLOCKED
    h.store.close()

    second = RightsHarness(tmp_path / "rejected.db")
    asset, claim = create_source(second, make_asset("rejected-asset"))
    add_grant(
        second,
        asset,
        claim,
        make_terms(),
        decision=EvidenceDecision.REJECTED,
    )
    assert evaluate(second, make_use(asset)).platform_rights_state == RightsState.BLOCKED
    second.store.close()


def test_expired_and_revoked_grants_block_use(tmp_path):
    h = RightsHarness(tmp_path / "rights.db")
    asset, claim = create_source(h)
    with pytest.raises(PermissionError, match="Rights agreement is unverified"):
        add_grant(
            h,
            asset,
            claim,
            make_terms(
                valid_from=datetime.now(UTC) - timedelta(days=3),
                valid_until=datetime.now(UTC) - timedelta(days=1),
            ),
        )
    assert evaluate(h, make_use(asset)).platform_rights_state == RightsState.UNKNOWN

    # A fresh grant is required for a valid decision before exercising revocation.
    _, current_grant = add_grant(
        h,
        asset,
        claim,
        make_terms(),
    )
    assert current_grant is not None
    assert evaluate(h, make_use(asset)).platform_rights_state == RightsState.CLEARED
    h.invoke(
        "content_revoke_rights_grant",
        {"grant_id": current_grant.grant_id, "reason": "license withdrawn"},
        h.service.revoke_rights_grant,
        grant_id=current_grant.grant_id,
        reason="license withdrawn",
    )
    assert evaluate(h, make_use(asset)).platform_rights_state == RightsState.REVOKED
    h.store.close()


@pytest.mark.parametrize(
    ("grant_actions", "use_actions", "commercial", "monetization", "duration", "attribution"),
    [
        (
            frozenset({RightsAction.PUBLISH}),
            frozenset({RightsAction.PUBLISH, RightsAction.CREATE_DERIVATIVE}),
            False,
            False,
            15,
            None,
        ),
        (
            frozenset({RightsAction.PUBLISH}),
            frozenset({RightsAction.PUBLISH, RightsAction.COMMERCIAL_USE}),
            True,
            False,
            15,
            None,
        ),
        (
            frozenset({RightsAction.PUBLISH, RightsAction.COMMERCIAL_USE}),
            frozenset(
                {RightsAction.PUBLISH, RightsAction.COMMERCIAL_USE, RightsAction.MONETIZE}
            ),
            True,
            True,
            15,
            None,
        ),
        (
            frozenset({RightsAction.PUBLISH}),
            frozenset({RightsAction.PUBLISH}),
            False,
            False,
            61,
            None,
        ),
        (
            frozenset({RightsAction.PUBLISH}),
            frozenset({RightsAction.PUBLISH}),
            False,
            False,
            15,
            "required",
        ),
        (
            frozenset({RightsAction.ANALYZE}),
            frozenset({RightsAction.PUBLISH}),
            False,
            False,
            15,
            None,
        ),
    ],
)
def test_action_commercial_monetization_duration_and_attribution_limits(
    tmp_path,
    grant_actions,
    use_actions,
    commercial,
    monetization,
    duration,
    attribution,
):
    h = RightsHarness(tmp_path / f"{uuid4()}.db")
    asset, claim = create_source(h)
    grant_attribution = attribution == "required"
    terms = make_terms(
        actions=grant_actions,
        max_duration_seconds=60,
        attribution_required=grant_attribution,
        attribution_text="Credit Studio North" if grant_attribution else None,
    )
    add_grant(h, asset, claim, terms)
    use = make_use(
        asset,
        actions=use_actions,
        commercial=commercial,
        monetization=monetization,
        duration_seconds=duration,
        attribution_text=attribution,
    )
    assert evaluate(h, use).platform_rights_state == RightsState.BLOCKED
    h.store.close()


def test_territory_and_account_scope_are_explicit(tmp_path):
    h = RightsHarness(tmp_path / "rights.db")
    asset, claim = create_source(h)
    add_grant(
        h,
        asset,
        claim,
        make_terms(territories=("GB",), account_scope="channel-1"),
    )
    assert (
        evaluate(h, make_use(asset, territory="US")).platform_rights_state
        == RightsState.BLOCKED
    )
    assert (
        evaluate(h, make_use(asset, territory="GB")).platform_rights_state
        == RightsState.CLEARED
    )
    assert (
        evaluate(
            h,
            make_use(asset, territory="GB", account_id="another-channel"),
        ).platform_rights_state
        == RightsState.BLOCKED
    )
    h.store.close()


def test_music_rights_are_independent_from_footage_rights(tmp_path):
    h = RightsHarness(tmp_path / "rights.db")
    footage, footage_claim = create_source(h, make_asset("footage"))
    music, _music_claim = create_source(
        h,
        make_asset("music", category=AssetCategory.MUSIC),
    )
    add_grant(h, footage, footage_claim, make_terms())
    package = ContentPackage(package_id="music-test", owner_id="alice")
    h.invoke(
        "content_create_package",
        package.model_dump(mode="json"),
        h.service.create_content_package,
        package=package,
    )
    footage_use = make_use(footage)
    music_use = make_use(music)
    version = make_package_version(
        package.package_id,
        footage,
        footage_use,
    ).model_copy(
        update={"asset_uses": (ContentAssetUse(use=footage_use), ContentAssetUse(use=music_use))}
    )
    with pytest.raises(PermissionError, match="source rights are not cleared"):
        h.invoke(
            "content_create_package_version",
            version.model_dump(mode="json"),
            h.service.create_content_package_version,
            version=version,
            artifact_stream=BytesIO(b"synthetic rendered artifact"),
        )
    assert h.service.store.get_package_version(package.package_id, 1) is None
    h.store.close()


def test_asset_hash_and_storage_key_boundaries_and_evidence_tampering(tmp_path):
    with pytest.raises(ValueError, match="path-safe"):
        SourceAsset.model_validate(
            {
                **make_asset("unsafe").model_dump(),
                "storage_key": "../outside",
            }
        )
    with pytest.raises(ValueError, match="filesystem paths"):
        SourceProvenance(
            acquisition_method=AcquisitionMethod.OWNER_UPLOAD,
            acquired_at=datetime.now(UTC),
            source_reference="file:///etc/passwd",
        )

    h = RightsHarness(tmp_path / "rights.db")
    mismatched_asset = make_asset("hash-mismatch")
    with pytest.raises(ValueError, match="do not match"):
        h.invoke(
            "content_register_asset",
            mismatched_asset.model_dump(mode="json"),
            h.service.register_asset,
            asset=mismatched_asset,
            source_stream=BytesIO(b"not the declared source"),
        )
    assert h.store.get_asset(mismatched_asset.asset_id) is None
    asset, claim = create_source(h)
    evidence, grant = add_grant(h, asset, claim, make_terms())
    assert grant is not None
    with h.store._lock:
        h.store._connection.execute("DROP TRIGGER rights_evidence_no_update")
        h.store._connection.execute(
            "UPDATE rights_evidence SET evidence_blob = ? WHERE evidence_id = ?",
            (sqlite3.Binary(b"forged evidence"), evidence.evidence_id),
        )
    decision = evaluate(h, make_use(asset))
    assert decision.copyright_state == RightsState.BLOCKED
    assert decision.platform_rights_state == RightsState.BLOCKED
    assert "evidence_integrity_failure" in decision.reason_codes
    h.store.close()


def test_artifact_hash_is_verified_before_package_version_is_persisted(tmp_path):
    h = RightsHarness(tmp_path / "rights.db")
    asset, claim = create_source(h)
    add_grant(h, asset, claim, make_terms())
    use = make_use(asset)
    package = ContentPackage(package_id="artifact-hash-package", owner_id="alice")
    h.invoke(
        "content_create_package",
        package.model_dump(mode="json"),
        h.service.create_content_package,
        package=package,
    )
    version = make_package_version(package.package_id, asset, use)
    payload = version.model_dump(mode="json")
    with pytest.raises(ValueError, match="Artifact bytes do not match"):
        h.invoke(
            "content_create_package_version",
            payload,
            h.service.create_content_package_version,
            version=version,
            artifact_stream=BytesIO(b"different rendered artifact"),
        )
    assert h.store.get_package_version(package.package_id, 1) is None
    created = h.invoke(
        "content_create_package_version",
        payload,
        h.service.create_content_package_version,
        version=version,
        artifact_stream=BytesIO(b"synthetic rendered artifact"),
    )
    assert created.artifact_sha256 == version.artifact_sha256
    h.store.close()


def test_package_approval_binds_hash_and_rights_changes_invalidate_it(tmp_path):
    h = RightsHarness(tmp_path / "rights.db")
    asset, claim = create_source(h)
    _, grant = add_grant(h, asset, claim, make_terms())
    assert grant is not None
    use = make_use(
        asset,
        actions=frozenset(
            {
                RightsAction.ANALYZE,
                RightsAction.EDIT,
                RightsAction.CREATE_DERIVATIVE,
                RightsAction.PUBLISH,
            }
        ),
    )
    version = create_package_with_version(h, "package-1", asset, use)
    approval = approve_version(h, version)
    assert approval.review_state == ReviewState.APPROVED
    assert approval.artifact_sha256 == version.artifact_sha256
    assert approval.metadata_sha256 == version.metadata_sha256
    assert approval.platform == ContentPlatform.YOUTUBE
    assert approval.account_id == "channel-1"
    assert approval.permitted_action == RightsAction.PUBLISH
    assert approval.principal == "alice"
    assert approval.session_id
    assert (
        h.store.get_package_version("package-1", 1).review_state
        == ReviewState.APPROVED
    )
    assert h.store.get_publication_record(
        "package-1", 1, ContentPlatform.YOUTUBE, "channel-1"
    ).state == PublicationState.NOT_PUBLISHED

    h.invoke(
        "content_revoke_rights_grant",
        {"grant_id": grant.grant_id, "reason": "rights withdrawn"},
        h.service.revoke_rights_grant,
        grant_id=grant.grant_id,
        reason="rights withdrawn",
    )
    assert h.store.get_approval(approval.approval_id).review_state == ReviewState.INVALIDATED
    assert h.store.get_package_version("package-1", 1).review_state == ReviewState.INVALIDATED
    h.store.close()


def test_package_version_changes_invalidate_old_approval(tmp_path):
    h = RightsHarness(tmp_path / "rights.db")
    asset, claim = create_source(h)
    add_grant(h, asset, claim, make_terms())
    use = make_use(asset)
    first = create_package_with_version(h, "package-v", asset, use)
    approval = approve_version(h, first)

    second = make_package_version(
        "package-v",
        asset,
        use,
        version=2,
        artifact=b"second synthetic artifact",
        metadata={"title": "Updated synthetic clip"},
    )
    h.invoke(
        "content_create_package_version",
        second.model_dump(mode="json"),
        h.service.create_content_package_version,
        version=second,
        artifact_stream=BytesIO(b"second synthetic artifact"),
    )
    assert h.store.get_approval(approval.approval_id).review_state == ReviewState.INVALIDATED
    assert h.store.get_package_version("package-v", 2).review_state == ReviewState.PENDING
    h.store.close()


def test_missing_tampered_and_expired_approval_cannot_be_reused(tmp_path):
    h = RightsHarness(tmp_path / "rights.db")
    asset, claim = create_source(h)
    _, grant = add_grant(h, asset, claim, make_terms())
    use = make_use(asset)
    version = create_package_with_version(h, "package-exp", asset, use)
    expiry = datetime.now(UTC) + timedelta(milliseconds=30)
    payload = {
        "package_id": version.package_id,
        "package_version": version.version,
        "artifact_sha256": version.artifact_sha256,
        "metadata_sha256": version.metadata_sha256,
        "permitted_action": RightsAction.PUBLISH.value,
        "expires_at": expiry.isoformat(),
    }
    approval = h.invoke(
        "content_approve_version",
        payload,
        h.service.approve_content_version,
        package_id=version.package_id,
        package_version=version.version,
        artifact_sha256=version.artifact_sha256,
        metadata_sha256=version.metadata_sha256,
        expires_at=expiry,
    )
    import time

    time.sleep(0.05)
    assert h.store.get_approval(approval.approval_id).review_state == ReviewState.EXPIRED

    with pytest.raises(PermissionError):
        h.invoke(
            "content_approve_version",
            {
                **payload,
                "artifact_sha256": "0" * 64,
            },
            h.service.approve_content_version,
            package_id=version.package_id,
            package_version=version.version,
            artifact_sha256="0" * 64,
            metadata_sha256=version.metadata_sha256,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    assert grant is not None
    h.store.close()


def test_approval_cannot_outlive_its_current_rights_grant(tmp_path):
    h = RightsHarness(tmp_path / "rights-expiry.db")
    asset, claim = create_source(h)
    _, grant = add_grant(
        h,
        asset,
        claim,
        make_terms(valid_until=datetime.now(UTC) + timedelta(minutes=10)),
    )
    assert grant is not None
    version = create_package_with_version(h, "package-rights-expiry", asset, make_use(asset))
    expiry = datetime.now(UTC) + timedelta(hours=1)
    payload = {
        "package_id": version.package_id,
        "package_version": version.version,
        "artifact_sha256": version.artifact_sha256,
        "metadata_sha256": version.metadata_sha256,
        "permitted_action": RightsAction.PUBLISH.value,
        "expires_at": expiry.isoformat(),
    }
    with pytest.raises(PermissionError):
        h.invoke(
            "content_approve_version",
            payload,
            h.service.approve_content_version,
            package_id=version.package_id,
            package_version=version.version,
            artifact_sha256=version.artifact_sha256,
            metadata_sha256=version.metadata_sha256,
            expires_at=expiry,
        )
    assert h.store.list_audit_events(version.package_id)
    h.store.close()


def test_authorization_is_session_bound_one_use_and_idempotent(tmp_path):
    h = RightsHarness(tmp_path / "rights.db")
    asset = make_asset()
    payload = asset.model_dump(mode="json")
    action = "content_register_asset"
    context = h.context(action, payload)
    h.service.register_asset(
        asset,
        source_stream=BytesIO(SOURCE_BYTES),
        context=context,
        command_id="create-once",
    )
    with pytest.raises(PermissionError, match="already consumed"):
        h.service.register_asset(
            asset,
            source_stream=BytesIO(SOURCE_BYTES),
            context=context,
            command_id="create-once",
        )

    replay_asset = make_asset("asset-replay")
    replay_payload = replay_asset.model_dump(mode="json")
    command_id = "same-idempotency-key"
    first = h.invoke(
        action,
        replay_payload,
        h.service.register_asset,
        asset=replay_asset,
        source_stream=BytesIO(SOURCE_BYTES),
        command_id=command_id,
    )
    duplicate = h.invoke(
        action,
        replay_payload,
        h.service.register_asset,
        asset=replay_asset,
        source_stream=BytesIO(SOURCE_BYTES),
        command_id=command_id,
    )
    assert first.asset_id == duplicate.asset_id == "asset-replay"

    forged_payload = make_asset("forged").model_dump(mode="json")
    forged_context = h.context(action, forged_payload)
    with pytest.raises(PermissionError, match="valid active-session"):
        h.service.register_asset(
            asset,
            source_stream=BytesIO(SOURCE_BYTES),
            context=forged_context,
            command_id="forged-command",
        )
    assert h.store.get_asset("forged") is None

    expired_asset = make_asset("expired-context")
    expired_payload = expired_asset.model_dump(mode="json")
    expired_context = h.context(action, expired_payload)
    expired_grant = expired_context.to_grant()
    expired_grant["expiresAt"] = (
        datetime.now(UTC) - timedelta(seconds=1)
    ).isoformat()
    expired_grant["signature"] = h.authority._sign_grant(expired_grant)
    with pytest.raises(PermissionError, match="valid active-session"):
        h.service.register_asset(
            expired_asset,
            source_stream=BytesIO(SOURCE_BYTES),
            context=AuthorizationContext.from_grant(expired_grant),
            command_id="expired-context",
        )

    revoked_asset = make_asset("revoked-context")
    revoked_payload = revoked_asset.model_dump(mode="json")
    revoked_context = h.context(action, revoked_payload)
    h.authority.revoke(h.websocket)
    with pytest.raises(PermissionError, match="valid active-session"):
        h.service.register_asset(
            revoked_asset,
            source_stream=BytesIO(SOURCE_BYTES),
            context=revoked_context,
            command_id="revoked-context",
        )
    h.store.close()


def test_cross_account_reads_and_writes_are_rejected(tmp_path):
    alice = RightsHarness(tmp_path / "rights.db")
    asset, _claim = create_source(alice)

    bob = RightsHarness(
        tmp_path / "rights.db",
        principal="bob",
        reviewers=frozenset({"bob"}),
        store=alice.store,
    )
    with pytest.raises(PermissionError, match="outside the caller's account"):
        evaluate(bob, make_use(asset))
    other_asset = make_asset("bob-asset", owner_id="alice")
    with pytest.raises(PermissionError, match="owner"):
        bob.invoke(
            "content_register_asset",
            other_asset.model_dump(mode="json"),
            bob.service.register_asset,
            asset=other_asset,
            source_stream=BytesIO(SOURCE_BYTES),
        )
    alice.store.close()


def test_concurrent_duplicate_commands_create_one_asset_and_audit_both_requests(
    tmp_path,
):
    h = RightsHarness(tmp_path / "rights.db")
    asset = make_asset()
    payload = asset.model_dump(mode="json")
    command_id = "concurrent-create"

    def register():
        return h.service.register_asset(
            asset,
            source_stream=BytesIO(SOURCE_BYTES),
            context=h.context("content_register_asset", payload),
            command_id=command_id,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: register(), range(2)))

    assert [result.asset_id for result in results] == [asset.asset_id] * 2
    with h.store._lock:
        row = h.store._connection.execute(
            "SELECT COUNT(*) AS count FROM content_assets WHERE asset_id = ?",
            (asset.asset_id,),
        ).fetchone()
        command_count = h.store._connection.execute(
            "SELECT COUNT(*) AS count FROM content_commands "
            "WHERE command_id = ?",
            (command_id,),
        ).fetchone()
    assert row["count"] == 1
    assert command_count["count"] == 1
    assert sum(
        event["decision"] == "idempotent_replay"
        for event in h.store.list_audit_events(asset.asset_id)
    ) == 1
    h.store.close()


def test_audit_failure_rolls_back_state_and_authorization_consumption(tmp_path):
    h = RightsHarness(tmp_path / "rights.db")
    asset = make_asset()
    payload = asset.model_dump(mode="json")
    context = h.context("content_register_asset", payload)
    with h.store._lock:
        h.store._connection.execute(
            """
            CREATE TRIGGER fail_content_audit
            BEFORE INSERT ON content_audit_events
            BEGIN
                SELECT RAISE(ABORT, 'injected audit failure');
            END
            """
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected audit failure"):
        h.service.register_asset(
            asset,
            source_stream=BytesIO(SOURCE_BYTES),
            context=context,
            command_id="atomic-create",
        )
    assert h.store.get_asset(asset.asset_id) is None
    assert h.store.list_audit_events(asset.asset_id) == []
    with h.store._lock:
        h.store._connection.execute("DROP TRIGGER fail_content_audit")
    # The authorization claim rolled back with the failed content and audit write.
    registered = h.service.register_asset(
        asset,
        source_stream=BytesIO(SOURCE_BYTES),
        context=context,
        command_id="atomic-create",
    )
    assert registered.asset_id == asset.asset_id
    assert len(h.store.list_audit_events(asset.asset_id)) == 1
    h.store.close()


def test_rights_review_and_audit_event_commit_or_rollback_together(tmp_path):
    h = RightsHarness(tmp_path / "rights.db")
    asset, claim = create_source(h)
    terms = make_terms()
    proof = b"synthetic evidence for atomic verification"
    evidence = RightsEvidence(
        evidence_id="atomic-evidence",
        asset_id=asset.asset_id,
        claim_id=claim.claim_id,
        submitted_by="alice",
        evidence_kind="license",
        evidence_bytes=proof,
        evidence_sha256=hashlib.sha256(proof).hexdigest(),
        grantor_identity=claim.rights_holder,
        grantor_authority=claim.authority_description,
        claimed_terms=terms,
    )
    h.invoke(
        "content_submit_rights_evidence",
        evidence.model_dump(mode="json", exclude={"evidence_bytes"}),
        h.service.submit_evidence,
        evidence=evidence,
    )
    presented = h.review_invoke(
        "content_read_rights_evidence",
        {"evidence_id": evidence.evidence_id},
        h.service.get_evidence_for_review,
        evidence_id=evidence.evidence_id,
    )
    assert presented.evidence_sha256 == evidence.evidence_sha256
    payload = {
        "evidence_id": evidence.evidence_id,
        "decision": EvidenceDecision.VERIFIED.value,
        "verified_terms_sha256": terms.fingerprint(),
    }
    with h.store._lock:
        h.store._connection.execute(
            """
            CREATE TRIGGER fail_rights_audit
            BEFORE INSERT ON content_audit_events
            WHEN NEW.action = 'content_verify_rights_evidence'
            BEGIN
                SELECT RAISE(ABORT, 'injected rights audit failure');
            END
            """
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected rights audit failure"):
        h.review_invoke(
            "content_verify_rights_evidence",
            payload,
            h.service.verify_evidence,
            evidence_id=evidence.evidence_id,
            decision=EvidenceDecision.VERIFIED,
            verified_terms_sha256=terms.fingerprint(),
        )
    assert h.store.evidence_decision(evidence.evidence_id) is None
    assert h.store.get_ownership_claim(claim.claim_id).state == OwnershipClaimState.REVIEW_REQUIRED
    with h.store._lock:
        h.store._connection.execute("DROP TRIGGER fail_rights_audit")
    review = h.review_invoke(
        "content_verify_rights_evidence",
        payload,
        h.service.verify_evidence,
        evidence_id=evidence.evidence_id,
        decision=EvidenceDecision.VERIFIED,
        verified_terms_sha256=terms.fingerprint(),
        command_id="atomic-evidence-review",
    )
    assert review.decision == EvidenceDecision.VERIFIED
    assert h.store.evidence_decision(evidence.evidence_id) == EvidenceDecision.VERIFIED
    h.store.close()


def test_allowlisted_asset_owner_cannot_read_or_review_own_evidence(tmp_path):
    h = RightsHarness(tmp_path / "self-review.db", reviewers=frozenset({"alice"}))
    asset, claim = create_source(h)
    terms = make_terms()
    proof = b"owner-submitted evidence"
    evidence = RightsEvidence(
        evidence_id="self-review-evidence",
        asset_id=asset.asset_id,
        claim_id=claim.claim_id,
        submitted_by="alice",
        evidence_kind="license",
        evidence_bytes=proof,
        evidence_sha256=hashlib.sha256(proof).hexdigest(),
        grantor_identity=claim.rights_holder,
        grantor_authority=claim.authority_description,
        claimed_terms=terms,
    )
    h.invoke(
        "content_submit_rights_evidence",
        evidence.model_dump(mode="json", exclude={"evidence_bytes"}),
        h.service.submit_evidence,
        evidence=evidence,
    )

    with pytest.raises(PermissionError, match="cannot review their own evidence"):
        h.invoke(
            "content_read_rights_evidence",
            {"evidence_id": evidence.evidence_id},
            h.service.get_evidence_for_review,
            evidence_id=evidence.evidence_id,
        )
    with pytest.raises(PermissionError, match="cannot review their own evidence"):
        h.invoke(
            "content_verify_rights_evidence",
            {
                "evidence_id": evidence.evidence_id,
                "decision": EvidenceDecision.VERIFIED.value,
                "verified_terms_sha256": terms.fingerprint(),
            },
            h.service.verify_evidence,
            evidence_id=evidence.evidence_id,
            decision=EvidenceDecision.VERIFIED,
            verified_terms_sha256=terms.fingerprint(),
        )
    h.store.close()


def test_missing_evidence_blocks_grants_and_is_detected(tmp_path):
    h = RightsHarness(tmp_path / "rights.db")
    asset, claim = create_source(h)
    evidence, grant = add_grant(h, asset, claim, make_terms())
    assert grant is not None
    with h.store._lock:
        h.store._connection.execute("DROP TRIGGER rights_evidence_no_delete")
        h.store._connection.execute("PRAGMA foreign_keys = OFF")
        h.store._connection.execute(
            "DELETE FROM rights_evidence WHERE evidence_id = ?",
            (evidence.evidence_id,),
        )
    decision = evaluate(h, make_use(asset))
    assert decision.copyright_state == RightsState.BLOCKED
    assert decision.platform_rights_state == RightsState.BLOCKED
    assert decision.reason_codes == ("evidence_integrity_failure",)
    h.store.close()


def test_conflicting_evidence_invalidates_approval_and_blocks_existing_grant(
    tmp_path,
):
    h = RightsHarness(tmp_path / "rights.db")
    asset, claim = create_source(h)
    add_grant(h, asset, claim, make_terms())
    use = make_use(asset)
    version = create_package_with_version(h, "conflict-package", asset, use)
    approval = approve_version(h, version)

    add_grant(
        h,
        asset,
        claim,
        make_terms(ContentPlatform.INSTAGRAM),
        decision=EvidenceDecision.AMBIGUOUS,
    )
    assert evaluate(h, use).platform_rights_state == RightsState.BLOCKED
    assert h.store.get_approval(approval.approval_id).review_state == ReviewState.INVALIDATED
    h.store.close()


def test_idempotency_key_cannot_be_reused_for_different_content(tmp_path):
    h = RightsHarness(tmp_path / "rights.db")
    first = make_asset("idempotency-first")
    h.invoke(
        "content_register_asset",
        first.model_dump(mode="json"),
        h.service.register_asset,
        asset=first,
        source_stream=BytesIO(SOURCE_BYTES),
        command_id="reused-key",
    )
    second = make_asset("idempotency-second")
    with pytest.raises(IdempotencyConflictError, match="different content"):
        h.invoke(
            "content_register_asset",
            second.model_dump(mode="json"),
            h.service.register_asset,
            asset=second,
            source_stream=BytesIO(SOURCE_BYTES),
            command_id="reused-key",
        )
    assert h.store.get_asset(second.asset_id) is None
    h.store.close()


def test_restart_recovery_preserves_rights_decisions_and_audit(tmp_path):
    database = tmp_path / "rights.db"
    h = RightsHarness(database)
    asset, claim = create_source(h)
    add_grant(h, asset, claim, make_terms())
    expected = evaluate(h, make_use(asset))
    events_before_restart = h.store.list_audit_events()
    h.store.close()

    store = ContentStore(database)
    service = ContentRightsService(
        store=store,
        authority=h.authority,
        reviewer_principals=frozenset({"alice"}),
    )
    payload = make_use(asset).model_dump(mode="json")
    context = h.context("content_evaluate_rights", payload)
    replayed = service.evaluate_rights(
        make_use(asset),
        context=context,
        command_id=str(uuid4()),
    )
    assert replayed.platform_rights_state == RightsState.CLEARED
    assert store.get_grant(expected.applicable_grant_ids[0]) is not None
    assert len(store.list_audit_events()) == len(events_before_restart) + 1
    store.close()


def test_simultaneous_revoke_and_evaluate_are_serialized(tmp_path):
    h = RightsHarness(tmp_path / "rights.db")
    asset, claim = create_source(h)
    _, grant = add_grant(h, asset, claim, make_terms())
    assert grant is not None
    use = make_use(asset)
    evaluation_payload = use.model_dump(mode="json")
    revoke_payload = {"grant_id": grant.grant_id, "reason": "withdrawn concurrently"}

    def evaluate_once():
        return h.service.evaluate_rights(
            use,
            context=h.context("content_evaluate_rights", evaluation_payload),
            command_id=str(uuid4()),
        )

    def revoke_once():
        return h.service.revoke_rights_grant(
            grant_id=grant.grant_id,
            reason=revoke_payload["reason"],
            context=h.context("content_revoke_rights_grant", revoke_payload),
            command_id="concurrent-revoke",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(evaluate_once), pool.submit(revoke_once)]
        [future.result() for future in futures]
    assert evaluate(h, use).platform_rights_state == RightsState.REVOKED
    h.store.close()


def test_policy_compliance_publishing_and_monetization_are_separate(tmp_path):
    inputs = PlatformPolicyInputs(
        platform=ContentPlatform.YOUTUBE,
        version="reviewed-rules-7",
        rules=(
            PlatformPolicyRule(
                rule_id="reviewed-disclosure",
                description="Reviewer-maintained disclosure requirement",
                official_source="https://official.example/policy",
            ),
        ),
        official_requirements_verified=True,
        verified_by="policy-reviewer",
        verified_at=datetime.now(UTC),
    )
    policy = ContentRightsPolicy(
        youtube=YouTubeRightsAdapter(inputs),
    )
    h = RightsHarness(tmp_path / "rights.db")
    h.service.policy = policy
    asset, claim = create_source(h)
    add_grant(h, asset, claim, make_terms())

    missing_fact = evaluate(h, make_use(asset))
    false_fact = evaluate(
        h,
        make_use(asset, policy_facts={"reviewed-disclosure": False}),
    )
    passed_fact = evaluate(
        h,
        make_use(asset, policy_facts={"reviewed-disclosure": True}),
    )
    assert missing_fact.platform_compliance_state == PlatformDecisionState.UNKNOWN
    assert false_fact.platform_compliance_state == PlatformDecisionState.NON_COMPLIANT
    assert passed_fact.platform_compliance_state == PlatformDecisionState.COMPLIANT
    assert passed_fact.publishing_authorization_state == PlatformDecisionState.NOT_AUTHORIZED
    assert passed_fact.monetization_eligibility_state == PlatformDecisionState.UNKNOWN
    assert passed_fact.monetization_permission is False
    h.store.close()


def test_unverified_platform_policy_inputs_cannot_claim_compliance():
    inputs = PlatformPolicyInputs(
        platform=ContentPlatform.INSTAGRAM,
        version="not-yet-reviewed",
    )
    adapter = YouTubeRightsAdapter(
        PlatformPolicyInputs(
            platform=ContentPlatform.YOUTUBE,
            version="unverified",
        )
    )
    assert inputs.official_requirements_verified is False
    assert adapter.policy_inputs.official_requirements_verified is False

    with pytest.raises(ValueError, match="reviewer, time, official sources"):
        PlatformPolicyInputs(
            platform=ContentPlatform.INSTAGRAM,
            version="bad-enabled",
            rules=(
                PlatformPolicyRule(
                    rule_id="unchecked",
                    description="Unverified",
                    official_source="https://official.example/policy",
                ),
            ),
            official_requirements_verified=True,
        )


def test_sqlite_append_only_and_schema_version_checks(tmp_path):
    h = RightsHarness(tmp_path / "rights.db")
    asset, _claim = create_source(h)
    with h.store._lock:
        with pytest.raises(sqlite3.IntegrityError, match="asset identity is immutable"):
            h.store._connection.execute(
                "UPDATE content_assets SET content_sha256 = ? WHERE asset_id = ?",
                ("0" * 64, asset.asset_id),
            )
        h.store._connection.execute(
            "UPDATE content_schema_meta SET schema_version = 99 WHERE singleton = 1"
        )
    h.store.close()
    with pytest.raises(RuntimeError, match="newer than this application supports"):
        ContentStore(tmp_path / "rights.db")


def test_schema_v2_migration_adds_agreement_links_without_losing_grants(tmp_path):
    database = tmp_path / "rights-v2.db"
    h = RightsHarness(database)
    asset, claim = create_source(h)
    _evidence, grant = add_grant(h, asset, claim, make_terms())
    assert grant is not None
    set_legacy_schema_version(h.store, 7)
    with h.store._lock:
        h.store._connection.execute("DROP TRIGGER rights_grants_no_update")
        h.store._connection.execute(
            "ALTER TABLE rights_grants DROP COLUMN agreement_version"
        )
        h.store._connection.execute(
            "ALTER TABLE rights_grants DROP COLUMN agreement_id"
        )
        h.store._connection.execute(
            "UPDATE content_schema_meta SET schema_version = 2 WHERE singleton = 1"
        )
    h.store.close()

    migrated = ContentStore(database)
    preserved = migrated.get_grant(grant.grant_id)
    assert preserved is not None
    assert preserved.asset_id == asset.asset_id
    assert preserved.agreement_id is None
    assert preserved.agreement_version is None
    assert migrated._connection.execute(
        "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
    ).fetchone()[0] == ContentStore.LEGACY_BASE_SCHEMA_VERSION
    migrated_harness = RightsHarness(database, store=migrated)
    legacy_decision = evaluate(migrated_harness, make_use(asset))
    assert legacy_decision.platform_rights_state == RightsState.BLOCKED
    assert "agreement_missing_or_integrity_failure" in legacy_decision.reason_codes
    migrated_harness.store.close()


def test_package_service_cannot_bypass_rights_or_publish(tmp_path):
    h = RightsHarness(tmp_path / "rights.db")
    asset, _claim = create_source(h)
    package = ContentPackage(package_id="blocked-package", owner_id="alice")
    h.invoke(
        "content_create_package",
        package.model_dump(mode="json"),
        h.service.create_content_package,
        package=package,
    )
    use = make_use(asset)
    version = make_package_version(package.package_id, asset, use)
    with pytest.raises(PermissionError, match="source rights are not cleared"):
        h.invoke(
            "content_create_package_version",
            version.model_dump(mode="json"),
            h.service.create_content_package_version,
            version=version,
            artifact_stream=BytesIO(b"synthetic rendered artifact"),
        )
    assert h.store.get_package_version(package.package_id, 1) is None
    assert not hasattr(h.service, "publish")
    assert not hasattr(h.service, "publish_to_youtube")
    assert not hasattr(h.service, "publish_to_instagram")
    h.store.close()


def test_grantor_identity_cannot_self_verify_or_establish_authority(tmp_path):
    h = RightsHarness(tmp_path / "rights.db")
    asset, claim = create_source(h)
    proof = b"synthetic grantor identity"
    identity = GrantorIdentity(
        grantor_id="grantor-unverified",
        owner_id=asset.owner_id,
        claimed_identity=claim.rights_holder,
        verification_method="synthetic independent review",
        evidence_reference="synthetic:identity",
        evidence_sha256=hashlib.sha256(proof).hexdigest(),
    )
    h.invoke(
        "content_register_grantor_identity",
        identity.model_dump(mode="json"),
        h.service.register_grantor_identity,
        identity=identity,
    )
    identity_payload = {
        "grantor_id": identity.grantor_id,
        "decision": EvidenceDecision.VERIFIED.value,
        "evidence_sha256": identity.evidence_sha256,
        "reason": "Synthetic identity review.",
    }
    with pytest.raises(PermissionError, match="cannot self-verify"):
        h.invoke(
            "content_review_grantor_identity",
            identity_payload,
            h.service.review_grantor_identity,
            grantor_id=identity.grantor_id,
            decision=EvidenceDecision.VERIFIED,
            evidence_sha256=identity.evidence_sha256,
            reason=identity_payload["reason"],
        )
    h.review_invoke(
        "content_review_grantor_identity",
        identity_payload,
        h.service.review_grantor_identity,
        grantor_id=identity.grantor_id,
        decision=EvidenceDecision.VERIFIED,
        evidence_sha256=identity.evidence_sha256,
        reason=identity_payload["reason"],
    )
    authority_proof = b"synthetic authority evidence"
    authority = GrantorAuthority(
        authority_id="authority-unverified",
        grantor_id=identity.grantor_id,
        asset_id=asset.asset_id,
        rights_holder=claim.rights_holder,
        relationship="claimed representative",
        scope_description="Synthetic asset scope",
        evidence_reference="synthetic:authority",
        evidence_sha256=hashlib.sha256(authority_proof).hexdigest(),
    )
    h.invoke(
        "content_register_grantor_authority",
        authority.model_dump(mode="json"),
        h.service.register_grantor_authority,
        authority=authority,
    )
    terms = make_terms(actions=frozenset({RightsAction.ANALYZE}))
    agreement = RightsAgreement(
        agreement_id="agreement-unverified-authority",
        version=1,
        grantor_id=identity.grantor_id,
        authority_id=authority.authority_id,
        rights_holder=claim.rights_holder,
        grantee_owner_id=asset.owner_id,
        asset_ids=(asset.asset_id,),
        terms=terms,
        evidence_reference="synthetic:agreement",
        evidence_sha256=hashlib.sha256(b"synthetic agreement").hexdigest(),
    )
    with pytest.raises(PermissionError, match="verified identity, asset-specific authority"):
        h.invoke(
            "content_create_rights_agreement",
            agreement.model_dump(mode="json", exclude={"status"}),
            h.service.create_rights_agreement,
            agreement=agreement,
        )
    h.store.close()


def test_agreement_revocation_is_durable_and_duplicate_safe(tmp_path):
    h = RightsHarness(tmp_path / "rights.db")
    asset, claim = create_source(h)
    _evidence, grant = add_grant(h, asset, claim, make_terms())
    assert grant is not None and grant.agreement_id is not None
    agreement = h.store.get_rights_agreement(
        grant.agreement_id, grant.agreement_version
    )
    assert agreement is not None
    payload = {
        "agreement_id": agreement.agreement_id,
        "agreement_version": agreement.version,
        "status": AgreementStatus.REVOKED.value,
        "reason": "synthetic revocation",
    }
    h.invoke(
        "content_change_rights_agreement_status",
        payload,
        h.service.change_rights_agreement_status,
        agreement_id=agreement.agreement_id,
        agreement_version=agreement.version,
        status=AgreementStatus.REVOKED,
        reason=payload["reason"],
        command_id="revoke-agreement-once",
    )
    h.invoke(
        "content_change_rights_agreement_status",
        payload,
        h.service.change_rights_agreement_status,
        agreement_id=agreement.agreement_id,
        agreement_version=agreement.version,
        status=AgreementStatus.REVOKED,
        reason=payload["reason"],
        command_id="revoke-agreement-once",
    )
    history = h.store.list_rights_agreement_events(agreement.agreement_id)
    assert [event.status for event in history].count(AgreementStatus.REVOKED) == 1
    assert evaluate(h, make_use(asset)).platform_rights_state == RightsState.REVOKED
    h.store.close()


@pytest.mark.parametrize("review_kind", ["identity", "authority"])
def test_grantor_review_changes_invalidate_rights_and_approvals(tmp_path, review_kind):
    h = RightsHarness(tmp_path / f"{review_kind}.db")
    asset, claim = create_source(h)
    _evidence, grant = add_grant(h, asset, claim, make_terms())
    assert grant is not None and grant.agreement_id is not None
    version = create_package_with_version(
        h, f"{review_kind}-review-package", asset, make_use(asset)
    )
    approval = approve_version(h, version)
    agreement = h.store.get_rights_agreement(
        grant.agreement_id, grant.agreement_version
    )
    assert agreement is not None
    if review_kind == "identity":
        subject_id = agreement.grantor_id
        action = "content_review_grantor_identity"
        function = h.service.review_grantor_identity
        kwargs = {
            "grantor_id": subject_id,
            "evidence_sha256": h.store.get_grantor_identity(subject_id).evidence_sha256,
        }
    else:
        subject_id = agreement.authority_id
        action = "content_review_grantor_authority"
        function = h.service.review_grantor_authority
        kwargs = {
            "authority_id": subject_id,
            "evidence_sha256": h.store.get_grantor_authority(subject_id).evidence_sha256,
        }
    payload = {
        **kwargs,
        "decision": EvidenceDecision.REJECTED.value,
        "reason": "Synthetic re-review rejected the authority evidence.",
    }
    h.review_invoke(
        action,
        payload,
        function,
        **kwargs,
        decision=EvidenceDecision.REJECTED,
        reason=payload["reason"],
    )
    assert h.store.get_approval(approval.approval_id).review_state == (
        ReviewState.INVALIDATED
    )
    assert evaluate(h, make_use(asset)).platform_rights_state != RightsState.CLEARED
    h.store.close()


def test_agreement_amendment_invalidates_approval_and_old_scope(tmp_path):
    h = RightsHarness(tmp_path / "rights.db")
    asset, claim = create_source(h)
    _evidence, grant = add_grant(h, asset, claim, make_terms())
    assert grant is not None and grant.agreement_id is not None
    use = make_use(asset)
    version = create_package_with_version(h, "agreement-amendment-package", asset, use)
    approval = approve_version(h, version)

    amended_terms = make_terms(
        actions=frozenset({RightsAction.PUBLISH, RightsAction.ANALYZE})
    )
    amended = add_verified_agreement(
        h,
        asset,
        claim,
        amended_terms,
        agreement_id=grant.agreement_id,
        version=2,
    )
    assert amended.version == 2
    assert h.store.get_approval(approval.approval_id).review_state == ReviewState.INVALIDATED
    assert evaluate(h, use).platform_rights_state != RightsState.CLEARED
    with pytest.raises(PermissionError, match="current pending agreement version"):
        h.review_invoke(
            "content_review_rights_agreement",
            {
                "agreement_id": grant.agreement_id,
                "agreement_version": 1,
                "decision": EvidenceDecision.VERIFIED.value,
                "agreement_sha256": "0" * 64,
                "evidence_sha256": "0" * 64,
                "reason": "stale review",
            },
            h.service.review_rights_agreement,
            agreement_id=grant.agreement_id,
            agreement_version=1,
            decision=EvidenceDecision.VERIFIED,
            agreement_sha256="0" * 64,
            evidence_sha256="0" * 64,
            reason="stale review",
        )
    h.store.close()


def test_rights_grant_without_agreement_is_rejected(tmp_path):
    h = RightsHarness(tmp_path / "rights.db")
    asset, claim = create_source(h)
    _evidence, grant = add_grant(h, asset, claim, make_terms())
    assert grant is not None
    unbound = grant.model_copy(
        update={
            "grant_id": "grant-without-agreement",
            "agreement_id": None,
            "agreement_version": None,
        }
    )
    with pytest.raises(PermissionError, match="reviewed rights agreement"):
        h.invoke(
            "content_create_rights_grant",
            unbound.model_dump(mode="json"),
            h.service.create_rights_grant,
            grant=unbound,
        )
    h.store.close()


def test_agreement_event_and_audit_rollback_together(tmp_path):
    h = RightsHarness(tmp_path / "rights.db")
    asset, claim = create_source(h)
    _evidence, grant = add_grant(h, asset, claim, make_terms())
    assert grant is not None and grant.agreement_id is not None
    status_payload = {
        "agreement_id": grant.agreement_id,
        "agreement_version": grant.agreement_version,
        "status": AgreementStatus.SUSPENDED.value,
        "reason": "injected failure",
    }
    context = h.context("content_change_rights_agreement_status", status_payload)
    with h.store._lock:
        h.store._connection.execute(
            """
            CREATE TRIGGER fail_agreement_event
            BEFORE INSERT ON rights_agreement_events
            WHEN NEW.status = 'suspended'
            BEGIN
                SELECT RAISE(ABORT, 'injected agreement event failure');
            END
            """
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected agreement event failure"):
        h.service.change_rights_agreement_status(
            agreement_id=grant.agreement_id,
            agreement_version=grant.agreement_version,
            status=AgreementStatus.SUSPENDED,
            reason=status_payload["reason"],
            context=context,
            command_id="suspend-with-fault",
        )
    assert h.store.get_rights_agreement(
        grant.agreement_id, grant.agreement_version
    ).status == AgreementStatus.APPROVED
    assert not [
        event
        for event in h.store.list_audit_events(grant.agreement_id)
        if event["action"] == "content_change_rights_agreement_status"
    ]
    with h.store._lock:
        h.store._connection.execute("DROP TRIGGER fail_agreement_event")
    h.store.close()


def test_agreement_expiry_is_recorded_on_revalidation(tmp_path):
    h = RightsHarness(tmp_path / "rights.db")
    asset, claim = create_source(h)
    expired_terms = make_terms(
        valid_from=datetime.now(UTC) - timedelta(days=2),
        valid_until=datetime.now(UTC) - timedelta(days=1),
    )
    agreement = add_verified_agreement(h, asset, claim, expired_terms)
    assert agreement.status == AgreementStatus.EXPIRED
    evaluate(h, make_use(asset))
    history = h.store.list_rights_agreement_events(agreement.agreement_id)
    assert [event.status for event in history].count(AgreementStatus.EXPIRED) == 1
    h.store.close()


def test_package_version_rights_boundary_expires_agreement_and_invalidates_approval(
    tmp_path,
):
    h = RightsHarness(tmp_path / "rights.db")
    asset, claim = create_source(h)
    terms = make_terms(valid_until=datetime.now(UTC) + timedelta(seconds=2))
    _evidence, grant = add_grant(h, asset, claim, terms)
    assert grant is not None
    use = make_use(asset)
    version = create_package_with_version(
        h, "expiry-boundary-package", asset, use
    )
    approval_expiry = terms.valid_until - timedelta(milliseconds=50)
    assert approval_expiry > datetime.now(UTC)
    approval_payload = {
        "package_id": version.package_id,
        "package_version": version.version,
        "artifact_sha256": version.artifact_sha256,
        "metadata_sha256": version.metadata_sha256,
        "permitted_action": RightsAction.PUBLISH.value,
        "expires_at": approval_expiry.isoformat(),
    }
    approval = h.invoke(
        "content_approve_version",
        approval_payload,
        h.service.approve_content_version,
        package_id=version.package_id,
        package_version=version.version,
        artifact_sha256=version.artifact_sha256,
        metadata_sha256=version.metadata_sha256,
        expires_at=approval_expiry,
    )

    time.sleep(max(0, (terms.valid_until - datetime.now(UTC)).total_seconds()) + 0.1)
    next_version = make_package_version(
        version.package_id,
        asset,
        use,
        version=2,
        artifact=b"synthetic artifact after agreement expiry",
    )
    with pytest.raises(PermissionError, match="source rights are not cleared"):
        h.invoke(
            "content_create_package_version",
            next_version.model_dump(mode="json"),
            h.service.create_content_package_version,
            version=next_version,
            artifact_stream=BytesIO(b"synthetic artifact after agreement expiry"),
        )
    assert h.store.get_approval(approval.approval_id).review_state == (
        ReviewState.INVALIDATED
    )
    history = h.store.list_rights_agreement_events(grant.agreement_id)
    assert [event.status for event in history].count(AgreementStatus.EXPIRED) == 1
    h.store.close()


def test_missing_grantor_record_fails_closed_after_restart(tmp_path):
    database = tmp_path / "rights.db"
    h = RightsHarness(database)
    asset, claim = create_source(h)
    _evidence, grant = add_grant(h, asset, claim, make_terms())
    assert grant is not None and grant.agreement_id is not None
    agreement = h.store.get_rights_agreement(
        grant.agreement_id, grant.agreement_version
    )
    assert agreement is not None
    with h.store._lock:
        h.store._connection.execute("DROP TRIGGER grantor_identities_no_delete")
        h.store._connection.execute("PRAGMA foreign_keys = OFF")
        h.store._connection.execute(
            "DELETE FROM grantor_identities WHERE grantor_id = ?",
            (agreement.grantor_id,),
        )
        h.store._connection.execute("PRAGMA foreign_keys = ON")
    h.store.close()

    with pytest.raises(RuntimeError, match="incomplete or inconsistent"):
        ContentStore(database)


def test_agreement_review_binds_exact_version_and_evidence(tmp_path):
    h = RightsHarness(tmp_path / "rights.db")
    asset, claim = create_source(h)
    terms = make_terms()
    agreement = add_verified_agreement(h, asset, claim, terms, approve=False)
    payload = {
        "agreement_id": agreement.agreement_id,
        "agreement_version": agreement.version,
        "decision": EvidenceDecision.VERIFIED.value,
        "agreement_sha256": "0" * 64,
        "evidence_sha256": agreement.evidence_sha256,
        "reason": "Synthetic mismatched review.",
    }
    with pytest.raises(PermissionError, match="intact, verified identity/authority"):
        h.review_invoke(
            "content_review_rights_agreement",
            payload,
            h.service.review_rights_agreement,
            agreement_id=agreement.agreement_id,
            agreement_version=agreement.version,
            decision=EvidenceDecision.VERIFIED,
            agreement_sha256=payload["agreement_sha256"],
            evidence_sha256=agreement.evidence_sha256,
            reason=payload["reason"],
        )
    with h.store._lock:
        review_count = h.store._connection.execute(
            "SELECT COUNT(*) FROM rights_agreement_reviews WHERE agreement_id = ?",
            (agreement.agreement_id,),
        ).fetchone()[0]
    assert review_count == 0
    valid_payload = {
        "agreement_id": agreement.agreement_id,
        "agreement_version": agreement.version,
        "decision": EvidenceDecision.VERIFIED.value,
        "agreement_sha256": agreement.fingerprint(),
        "evidence_sha256": agreement.evidence_sha256,
        "reason": "Synthetic valid review.",
    }
    first_review = h.review_invoke(
        "content_review_rights_agreement",
        valid_payload,
        h.service.review_rights_agreement,
        agreement_id=agreement.agreement_id,
        agreement_version=agreement.version,
        decision=EvidenceDecision.VERIFIED,
        agreement_sha256=agreement.fingerprint(),
        evidence_sha256=agreement.evidence_sha256,
        reason=valid_payload["reason"],
        command_id="idempotent-agreement-review",
    )
    repeated_review = h.review_invoke(
        "content_review_rights_agreement",
        valid_payload,
        h.service.review_rights_agreement,
        agreement_id=agreement.agreement_id,
        agreement_version=agreement.version,
        decision=EvidenceDecision.VERIFIED,
        agreement_sha256=agreement.fingerprint(),
        evidence_sha256=agreement.evidence_sha256,
        reason=valid_payload["reason"],
        command_id="idempotent-agreement-review",
    )
    assert repeated_review.review_id == first_review.review_id
    with h.store._lock:
        review_count = h.store._connection.execute(
            "SELECT COUNT(*) FROM rights_agreement_reviews WHERE agreement_id = ?",
            (agreement.agreement_id,),
        ).fetchone()[0]
    assert review_count == 1
    h.store.close()


def test_agreement_amendment_does_not_invalidate_unrelated_asset(tmp_path):
    h = RightsHarness(tmp_path / "rights.db")
    asset, claim = create_source(h)
    other_asset, other_claim = create_source(h, make_asset("asset-other"))
    _first_evidence, first_grant = add_grant(h, asset, claim, make_terms())
    _other_evidence, other_grant = add_grant(
        h, other_asset, other_claim, make_terms()
    )
    assert first_grant is not None and other_grant is not None
    first_version = create_package_with_version(
        h, "first-agreement-package", asset, make_use(asset)
    )
    other_version = create_package_with_version(
        h, "other-agreement-package", other_asset, make_use(other_asset)
    )
    first_approval = approve_version(h, first_version)
    other_approval = approve_version(h, other_version)

    add_verified_agreement(
        h,
        asset,
        claim,
        make_terms(actions=frozenset({RightsAction.PUBLISH})),
        agreement_id=first_grant.agreement_id,
        version=2,
    )
    assert h.store.get_approval(first_approval.approval_id).review_state == (
        ReviewState.INVALIDATED
    )
    assert h.store.get_approval(other_approval.approval_id).review_state == (
        ReviewState.APPROVED
    )
    assert evaluate(h, make_use(other_asset)).platform_rights_state == (
        RightsState.CLEARED
    )
    h.store.close()


def test_concurrent_agreement_status_changes_serialize(tmp_path):
    h = RightsHarness(tmp_path / "rights.db")
    asset, claim = create_source(h)
    _evidence, grant = add_grant(h, asset, claim, make_terms())
    assert grant is not None and grant.agreement_id is not None

    def change(status: AgreementStatus):
        payload = {
            "agreement_id": grant.agreement_id,
            "agreement_version": grant.agreement_version,
            "status": status.value,
            "reason": f"concurrent {status.value}",
        }
        try:
            h.invoke(
                "content_change_rights_agreement_status",
                payload,
                h.service.change_rights_agreement_status,
                agreement_id=grant.agreement_id,
                agreement_version=grant.agreement_version,
                status=status,
                reason=payload["reason"],
            )
            return "changed"
        except PermissionError:
            return "already_changed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                change,
                (AgreementStatus.SUSPENDED, AgreementStatus.REVOKED),
            )
        )
    assert sorted(results) == ["already_changed", "changed"]
    current = h.store.get_rights_agreement(
        grant.agreement_id, grant.agreement_version
    )
    assert current is not None
    assert current.status in {AgreementStatus.SUSPENDED, AgreementStatus.REVOKED}
    assert evaluate(h, make_use(asset)).platform_rights_state != RightsState.CLEARED
    h.store.close()


def test_owner_self_attestation_is_scoped_audited_and_revocable(tmp_path):
    h = RightsHarness(tmp_path / "owner-rights.db", reviewers=frozenset())
    asset = register_owner_created_source(h)
    attestation = owner_attestation(asset)
    stored = h.invoke(
        "content_attest_owner_created_rights",
        attestation.model_dump(mode="json"),
        h.service.attest_owner_created_rights,
        attestation=attestation,
    )
    assert stored.status.value == "active"
    assert stored.transfer_channel == "whatsapp"

    analyze = make_use(
        asset,
        actions=frozenset({RightsAction.ANALYZE}),
        duration_seconds=10,
    )
    analysis_payload = analyze.model_dump(mode="json")
    analysis = h.invoke(
        "content_evaluate_rights",
        analysis_payload,
        h.service.evaluate_rights,
        use=analyze,
    )
    assert analysis.copyright_state is RightsState.SELF_ATTESTED
    assert analysis.platform_rights_state is RightsState.SELF_ATTESTED
    assert analysis.permits_requested_use
    assert analysis.applicable_owner_attestation_ids == (stored.attestation_id,)
    assert analysis.applicable_grant_ids == ()
    assert analysis.publishing_authorization_state is PlatformDecisionState.NOT_AUTHORIZED

    for action in (RightsAction.EDIT, RightsAction.CREATE_DERIVATIVE):
        use = make_use(asset, actions=frozenset({action}), duration_seconds=10)
        decision = h.invoke(
            "content_evaluate_rights",
            use.model_dump(mode="json"),
            h.service.evaluate_rights,
            use=use,
        )
        assert decision.platform_rights_state is RightsState.SELF_ATTESTED
        assert decision.permits_requested_use

    out_of_scope_uses = (
        make_use(
            asset,
            platform=ContentPlatform.INSTAGRAM,
            actions=frozenset({RightsAction.ANALYZE}),
        ),
        make_use(
            asset,
            account_id="another-channel",
            actions=frozenset({RightsAction.ANALYZE}),
        ),
        make_use(
            asset,
            territory="CA",
            actions=frozenset({RightsAction.ANALYZE}),
        ),
        make_use(asset, actions=frozenset({RightsAction.PUBLISH})),
        make_use(
            asset,
            actions=frozenset(
                {RightsAction.ANALYZE, RightsAction.COMMERCIAL_USE}
            ),
            commercial=True,
        ),
    )
    for use in out_of_scope_uses:
        decision = h.invoke(
            "content_evaluate_rights",
            use.model_dump(mode="json"),
            h.service.evaluate_rights,
            use=use,
        )
        assert not decision.permits_requested_use
        assert decision.platform_rights_state is not RightsState.SELF_ATTESTED

    changed_hash_use = analyze.model_copy(
        update={"expected_asset_sha256": "0" * 64}
    )
    changed_hash_decision = h.invoke(
        "content_evaluate_rights",
        changed_hash_use.model_dump(mode="json"),
        h.service.evaluate_rights,
        use=changed_hash_use,
    )
    assert changed_hash_decision.platform_rights_state is RightsState.BLOCKED

    reason = "Owner withdrew the self-attestation."
    revoked = h.invoke(
        "content_revoke_owner_rights_attestation",
        {"attestation_id": stored.attestation_id, "reason": reason},
        h.service.revoke_owner_created_rights,
        attestation_id=stored.attestation_id,
        reason=reason,
    )
    assert revoked.status.value == "revoked"
    after_revoke = h.invoke(
        "content_evaluate_rights",
        analysis_payload,
        h.service.evaluate_rights,
        use=analyze,
    )
    assert after_revoke.platform_rights_state is RightsState.REVOKED
    assert not after_revoke.permits_requested_use
    with h.store._lock:
        events = h.store._connection.execute(
            """
            SELECT event_type FROM owner_rights_attestation_events
            WHERE attestation_id = ? ORDER BY sequence
            """,
            (stored.attestation_id,),
        ).fetchall()
    assert [row["event_type"] for row in events] == ["attested", "revoked"]
    with h.store._lock, pytest.raises(sqlite3.IntegrityError, match="append-only"):
        h.store._connection.execute(
            """
            UPDATE owner_rights_attestation_events
            SET reason = 'rewritten'
            WHERE attestation_id = ?
            """,
            (stored.attestation_id,),
        )
    h.store.close()


def test_owner_self_attestation_rejects_non_owner_created_assets(tmp_path):
    h = RightsHarness(tmp_path / "third-party-rights.db", reviewers=frozenset())
    asset = make_asset()
    h.invoke(
        "content_register_asset",
        asset.model_dump(mode="json"),
        h.service.register_asset,
        asset=asset,
        source_stream=BytesIO(SOURCE_BYTES),
    )
    attestation = owner_attestation(asset)
    with pytest.raises(PermissionError, match="owner-created asset"):
        h.invoke(
            "content_attest_owner_created_rights",
            attestation.model_dump(mode="json"),
            h.service.attest_owner_created_rights,
            attestation=attestation,
        )
    assert h.store.get_owner_rights_attestation(
        attestation.attestation_id,
        asset.owner_id,
    ) is None
    h.store.close()


def test_restricted_technical_test_keeps_unknown_rights_and_is_revocable(tmp_path):
    h = RightsHarness(tmp_path / "restricted-test.db", reviewers=frozenset())
    asset = make_asset()
    h.invoke(
        "content_register_asset",
        asset.model_dump(mode="json"),
        h.service.register_asset,
        asset=asset,
        source_stream=BytesIO(SOURCE_BYTES),
    )
    acknowledgement = h.service.RESTRICTED_TEST_ACKNOWLEDGEMENT
    payload = {
        "asset_id": asset.asset_id,
        "asset_sha256": asset.content_sha256,
        "acknowledgement": acknowledgement,
        "license_verification_state": AssetLicenseVerificationState.UNKNOWN.value,
        "allowed_operations": sorted(
            {
                "transcription",
                "transcript_read",
                "highlight",
                "source_planning",
                "private_render",
                "project_render",
            }
        ),
        "commercial_use": False,
        "external_distribution": False,
    }
    permit = h.invoke(
        "content_create_restricted_technical_test",
        payload,
        h.service.create_restricted_technical_test_permit,
        asset_id=asset.asset_id,
        asset_sha256=asset.content_sha256,
        acknowledgement=acknowledgement,
    )
    license_record = h.store.get_latest_asset_license_record(
        asset.asset_id,
        asset.owner_id,
    )
    assert license_record is not None
    assert license_record.verification_state is AssetLicenseVerificationState.UNKNOWN
    assert license_record.original_source_url is None
    assert license_record.license_identifier is None
    assert permit.asset_sha256 == asset.content_sha256
    assert permit.commercial_use is False
    assert permit.external_distribution is False
    analyze_use = make_use(
        asset,
        actions=frozenset({RightsAction.ANALYZE}),
    )
    with h.store._transaction() as connection:
        decision = h.service.require_rights_or_restricted_test_in_transaction(
            connection,
            use=analyze_use,
            principal=asset.owner_id,
            operation="transcription",
        )
    assert not decision.permits_requested_use
    assert decision.platform_rights_state is RightsState.UNKNOWN
    with h.store._transaction() as connection, pytest.raises(
        PermissionError,
        match="project_render operation requires its explicit rights actions",
    ):
        h.service.require_rights_or_restricted_test_in_transaction(
            connection,
            use=analyze_use,
            principal=asset.owner_id,
            operation="project_render",
        )
    publish_use = make_use(
        asset,
        actions=frozenset({RightsAction.ANALYZE, RightsAction.PUBLISH}),
    )
    with h.store._transaction() as connection, pytest.raises(
        PermissionError,
        match="private_render operation requires its explicit rights actions",
    ):
        h.service.require_rights_or_restricted_test_in_transaction(
            connection,
            use=publish_use,
            principal=asset.owner_id,
            operation="private_render",
        )

    revoked = h.invoke(
        "content_revoke_restricted_technical_test",
        {"permit_id": permit.permit_id},
        h.service.revoke_restricted_technical_test_permit,
        permit_id=permit.permit_id,
    )
    assert revoked is None
    stored_permit = h.store.get_restricted_technical_test(
        permit.permit_id,
        asset.owner_id,
    )
    assert stored_permit is not None
    assert stored_permit.status.value == "revoked"
    with h.store._transaction() as connection, pytest.raises(
        PermissionError,
        match="no matching restricted technical-test permit",
    ):
        h.service.require_rights_or_restricted_test_in_transaction(
            connection,
            use=analyze_use,
            principal=asset.owner_id,
            operation="transcription",
        )
    with h.store._lock:
        events = h.store._connection.execute(
            """
            SELECT event_type FROM content_restricted_technical_test_events
            WHERE permit_id = ? ORDER BY sequence
            """,
            (permit.permit_id,),
        ).fetchall()
    assert [row["event_type"] for row in events] == [
        "permit_created",
        "restricted_local_operation",
        "permit_revoked",
    ]
    h.store.close()
