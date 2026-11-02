from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ultron.application.content.service import ContentRightsService
from ultron.application.content.store import ContentStore
from ultron.domain.content import (
    AcquisitionMethod,
    AgreementStatus,
    AssetCategory,
    ContentLifecycleState,
    ContentAssetUse,
    ContentPackage,
    ContentPackageVersion,
    ContentPlatform,
    ContentEditProject,
    ContentTargetContext,
    EvidenceDecision,
    GrantorAuthority,
    GrantorIdentity,
    OwnershipClaim,
    RightsAction,
    RightsEvidence,
    RightsGrant,
    RightsAgreement,
    RightsState,
    RightsTerms,
    RightsUse,
    SourceAsset,
    SourceProvenance,
    SourceReferenceRecord,
)
from ultron.security.identity.authorization import (
    AuthorizationContext,
    resource_fingerprint,
)
from ultron.security.identity.authenticator import WebSocketAuthenticator


TOKEN = "L" * 43
MEDIA = b"synthetic source bytes for lifecycle tests"
MEDIA_SHA256 = hashlib.sha256(MEDIA).hexdigest()
SOURCE_URL = "https://source.example/media/asset-1"


class Harness:
    def __init__(self, database, principal: str = "alice"):
        self.store = ContentStore(database)
        self.authority = WebSocketAuthenticator(TOKEN, principal)
        self.websocket = object()
        assert self.authority.authenticate(self.websocket, TOKEN) is not None
        self.service = ContentRightsService(
            store=self.store,
            authority=self.authority,
            reviewer_principals=frozenset({"alice", "reviewer"}),
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
            purpose="content_lifecycle",
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
            purpose="content_lifecycle_review",
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

    def close(self):
        self.store.close()


def _asset() -> SourceAsset:
    return SourceAsset(
        asset_id="asset-1",
        owner_id="alice",
        category=AssetCategory.FOOTAGE,
        content_sha256=MEDIA_SHA256,
        provenance=SourceProvenance(
            acquisition_method=AcquisitionMethod.OWNER_UPLOAD,
            acquired_at=datetime.now(UTC),
            source_reference="upload:synthetic",
        ),
        storage_key="asset:1",
    )


def _rights_use(*, actions=frozenset({RightsAction.ANALYZE})) -> RightsUse:
    return RightsUse(
        asset_id="asset-1",
        expected_asset_sha256=MEDIA_SHA256,
        platform=ContentPlatform.YOUTUBE,
        account_id="channel-1",
        territory="US",
        actions=actions,
        commercial_intent=False,
        monetization_intent=False,
        duration_seconds=15,
    )


def _source(
    *,
    target_context=ContentTargetContext.YOUTUBE_SHORTS,
    rights_use: RightsUse | None = None,
    claimed_hash: str | None = None,
    source_url: str = SOURCE_URL,
) -> SourceReferenceRecord:
    return SourceReferenceRecord(
        intake_id=f"intake-{uuid4()}",
        asset_id="asset-1",
        owner_id="alice",
        source_reference=source_url,
        acquisition_method=AcquisitionMethod.AUTHORIZED_PROVIDER,
        provenance=SourceProvenance(
            acquisition_method=AcquisitionMethod.AUTHORIZED_PROVIDER,
            acquired_at=datetime.now(UTC),
            source_reference=source_url,
            provider="Synthetic provider",
            provider_record_id="synthetic-record-1",
        ),
        evidence_reference="https://evidence.example/license/record-1",
        claimed_source_sha256=claimed_hash,
        target_context=target_context,
        rights_use=rights_use or (
            None
            if target_context == ContentTargetContext.SNAPCHAT_SPOTLIGHT
            else _rights_use()
        ),
    )


def _register_asset(h: Harness):
    asset = _asset()
    h.invoke(
        "content_register_asset",
        asset.model_dump(mode="json"),
        h.service.register_asset,
        asset=asset,
        source_stream=BytesIO(MEDIA),
    )
    return asset


def test_authorized_edit_project_creation_uses_outer_transaction(tmp_path):
    harness = Harness(tmp_path / "content.db")
    asset = _register_asset(harness)
    project = ContentEditProject(
        project_id="edit-project-1",
        owner_id="alice",
        source_asset_ids=(asset.asset_id,),
        script="Synthetic private edit.",
    )
    command_id = "create-edit-project-1"

    saved = harness.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        harness.service.create_edit_project,
        project=project,
        command_id=command_id,
    )

    assert saved == project
    assert harness.store.get_edit_project(project.project_id, "alice") == project
    command = harness.store._connection.execute(
        "SELECT result_id FROM content_commands WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    assert command["result_id"] == project.project_id
    source = harness.store._connection.execute(
        "SELECT source_asset_id, source_order FROM content_edit_project_sources WHERE project_id = ?",
        (project.project_id,),
    ).fetchone()
    assert (source["source_asset_id"], source["source_order"]) == (asset.asset_id, 1)


def _register_intake(h: Harness, source: SourceReferenceRecord | None = None):
    source = source or _source()
    return h.invoke(
        "content_register_source_reference",
        source.model_dump(mode="json"),
        h.service.register_source_reference,
        source=source,
    )


def _transition(
    h: Harness,
    intake_id: str,
    target: ContentLifecycleState,
    *,
    command_id: str | None = None,
):
    payload = {
        "intake_id": intake_id,
        "target_state": target.value,
        "reason": "synthetic test transition",
    }
    return h.invoke(
        "content_transition_lifecycle",
        payload,
        h.service.transition_content_lifecycle,
        intake_id=intake_id,
        target_state=target,
        reason=payload["reason"],
        **({"command_id": command_id} if command_id else {}),
    )


def _add_analyze_grant(
    h: Harness,
    valid_until: datetime | None = None,
    *,
    actions=frozenset({RightsAction.ANALYZE}),
):
    asset = h.store.get_asset("asset-1")
    assert asset is not None
    claim = OwnershipClaim(
        claim_id="claim-1",
        asset_id=asset.asset_id,
        claimant_id="alice",
        rights_holder="Synthetic Rights Holder",
        authority_description="Synthetic authorized licensing representative",
    )
    h.invoke(
        "content_add_ownership_claim",
        claim.model_dump(mode="json"),
        h.service.add_ownership_claim,
        claim=claim,
    )
    now = datetime.now(UTC)
    terms = RightsTerms(
        platform=ContentPlatform.YOUTUBE,
        account_scope="channel-1",
        territories=("US",),
        permitted_actions=actions,
        valid_from=now - timedelta(minutes=1),
        valid_until=valid_until or now + timedelta(hours=1),
        attribution_required=False,
        max_duration_seconds=60,
    )
    evidence_bytes = b"synthetic reviewed analyze license"
    evidence = RightsEvidence(
        evidence_id="evidence-1",
        asset_id=asset.asset_id,
        claim_id=claim.claim_id,
        submitted_by="alice",
        evidence_kind="synthetic_license",
        evidence_bytes=evidence_bytes,
        evidence_sha256=hashlib.sha256(evidence_bytes).hexdigest(),
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
    review_payload = {
        "evidence_id": evidence.evidence_id,
        "decision": EvidenceDecision.VERIFIED.value,
        "verified_terms_sha256": terms.fingerprint(),
    }
    h.review_invoke(
        "content_verify_rights_evidence",
        review_payload,
        h.service.verify_evidence,
        evidence_id=evidence.evidence_id,
        decision=EvidenceDecision.VERIFIED,
        verified_terms_sha256=terms.fingerprint(),
    )
    identity_proof = b"synthetic independently reviewed identity"
    identity = GrantorIdentity(
        grantor_id="grantor-1",
        owner_id="alice",
        claimed_identity=claim.rights_holder,
        verification_method="independent synthetic reviewer",
        evidence_reference="synthetic:identity",
        evidence_sha256=hashlib.sha256(identity_proof).hexdigest(),
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
    h.review_invoke(
        "content_review_grantor_identity",
        identity_payload,
        h.service.review_grantor_identity,
        grantor_id=identity.grantor_id,
        decision=EvidenceDecision.VERIFIED,
        evidence_sha256=identity.evidence_sha256,
        reason=identity_payload["reason"],
    )
    authority_proof = b"synthetic independently reviewed authority"
    authority = GrantorAuthority(
        authority_id="authority-1",
        grantor_id=identity.grantor_id,
        asset_id=asset.asset_id,
        rights_holder=claim.rights_holder,
        relationship="authorized representative",
        scope_description="Synthetic analyze rights for asset-1",
        evidence_reference="synthetic:authority",
        evidence_sha256=hashlib.sha256(authority_proof).hexdigest(),
    )
    h.invoke(
        "content_register_grantor_authority",
        authority.model_dump(mode="json"),
        h.service.register_grantor_authority,
        authority=authority,
    )
    authority_payload = {
        "authority_id": authority.authority_id,
        "decision": EvidenceDecision.VERIFIED.value,
        "evidence_sha256": authority.evidence_sha256,
        "reason": "Synthetic authority review.",
    }
    h.review_invoke(
        "content_review_grantor_authority",
        authority_payload,
        h.service.review_grantor_authority,
        authority_id=authority.authority_id,
        decision=EvidenceDecision.VERIFIED,
        evidence_sha256=authority.evidence_sha256,
        reason=authority_payload["reason"],
    )
    agreement_proof = b"synthetic agreement evidence"
    agreement = RightsAgreement(
        agreement_id="agreement-1",
        version=1,
        grantor_id=identity.grantor_id,
        authority_id=authority.authority_id,
        rights_holder=claim.rights_holder,
        grantee_owner_id="alice",
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
    agreement_payload = {
        "agreement_id": agreement.agreement_id,
        "agreement_version": agreement.version,
        "decision": EvidenceDecision.VERIFIED.value,
        "agreement_sha256": agreement.fingerprint(),
        "evidence_sha256": agreement.evidence_sha256,
        "reason": "Synthetic agreement review.",
    }
    h.review_invoke(
        "content_review_rights_agreement",
        agreement_payload,
        h.service.review_rights_agreement,
        agreement_id=agreement.agreement_id,
        agreement_version=agreement.version,
        decision=EvidenceDecision.VERIFIED,
        agreement_sha256=agreement.fingerprint(),
        evidence_sha256=agreement.evidence_sha256,
        reason=agreement_payload["reason"],
    )
    stored_agreement = h.store.get_rights_agreement(
        agreement.agreement_id, agreement.version
    )
    assert stored_agreement is not None
    assert stored_agreement.status == AgreementStatus.APPROVED
    grant = RightsGrant(
        grant_id="grant-1",
        asset_id=asset.asset_id,
        claim_id=claim.claim_id,
        evidence_id=evidence.evidence_id,
        grantor_identity=claim.rights_holder,
        grantor_authority=claim.authority_description,
        grantee_owner_id="alice",
        terms=terms,
        agreement_id=agreement.agreement_id,
        agreement_version=agreement.version,
    )
    h.invoke(
        "content_create_rights_grant",
        grant.model_dump(mode="json"),
        h.service.create_rights_grant,
        grant=grant,
    )
    return grant


@pytest.mark.parametrize(
    "reference",
    [
        "file:///etc/passwd",
        "/etc/passwd",
        "http://source.example/video",
        "javascript:alert(1)",
        "data:text/plain,unsafe",
        "https://user:password@source.example/video",
        "https://source.example:99999/video",
        "https://",
        "https://source.example/with whitespace",
        "https://source.example\\@attacker.example/video",
        "https://source.example/video?access_token=synthetic",
    ],
)
def test_source_reference_rejects_unsafe_or_malformed_urls(reference):
    with pytest.raises(ValidationError):
        _source(source_url=reference)


def test_source_intake_keeps_claimed_digest_unverified_and_does_not_fetch(tmp_path):
    h = Harness(tmp_path / "intake.db")
    asset = _register_asset(h)
    claimed = "f" * 64
    source = _source(claimed_hash=claimed)
    stored = _register_intake(h, source)

    assert stored.source_reference == SOURCE_URL
    assert stored.claimed_source_sha256 == claimed
    assert stored.source_hash_status.value == "unverified"
    assert stored.access_permission_state.value == "unknown"
    assert asset.content_sha256 == MEDIA_SHA256
    assert h.store.get_content_lifecycle(source.intake_id).state == (
        ContentLifecycleState.REGISTERED
    )
    h.close()


def test_intake_requires_account_owner_and_exact_analysis_scope(tmp_path):
    h = Harness(tmp_path / "intake.db")
    _register_asset(h)
    bad_use = _rights_use(actions=frozenset({RightsAction.PUBLISH}))
    with pytest.raises(ValidationError):
        _source(rights_use=bad_use)

    source = _source()
    other = Harness(tmp_path / "other.db", principal="bob")
    with pytest.raises(PermissionError):
        other.invoke(
            "content_register_source_reference",
            source.model_dump(mode="json"),
            other.service.register_source_reference,
            source=source,
        )
    assert h.store.get_source_intake(source.intake_id) is None
    h.close()
    other.close()


def test_lifecycle_requires_fresh_rights_and_persists_transition_history(tmp_path):
    h = Harness(tmp_path / "lifecycle.db")
    _register_asset(h)
    source = _register_intake(h)
    intake_id = source.intake_id

    assert _transition(h, intake_id, ContentLifecycleState.RIGHTS_PENDING).state == (
        ContentLifecycleState.RIGHTS_PENDING
    )
    denied = _transition(h, intake_id, ContentLifecycleState.RIGHTS_CLEARED)
    assert denied.state == ContentLifecycleState.RIGHTS_PENDING
    _add_analyze_grant(h)
    cleared = _transition(h, intake_id, ContentLifecycleState.RIGHTS_CLEARED)
    assert cleared.state == ContentLifecycleState.RIGHTS_CLEARED
    ready = _transition(h, intake_id, ContentLifecycleState.READY_FOR_ANALYSIS)
    assert ready.state == ContentLifecycleState.READY_FOR_ANALYSIS
    events = h.store.list_content_lifecycle_events(intake_id)
    assert [event.to_state for event in events] == [
        ContentLifecycleState.REGISTERED,
        ContentLifecycleState.RIGHTS_PENDING,
        ContentLifecycleState.RIGHTS_PENDING,
        ContentLifecycleState.RIGHTS_CLEARED,
        ContentLifecycleState.READY_FOR_ANALYSIS,
    ]
    assert events[-1].rights_decision_id == ready.rights_decision_id
    h.close()


def test_duplicate_transition_replay_and_invalid_transition_are_safe(tmp_path):
    h = Harness(tmp_path / "lifecycle.db")
    _register_asset(h)
    source = _register_intake(h)
    command_id = "same-command"
    first = _transition(
        h,
        source.intake_id,
        ContentLifecycleState.RIGHTS_PENDING,
        command_id=command_id,
    )
    replay = _transition(
        h,
        source.intake_id,
        ContentLifecycleState.RIGHTS_PENDING,
        command_id=command_id,
    )
    assert replay == first
    with pytest.raises(ValueError, match="Invalid lifecycle transition"):
        _transition(h, source.intake_id, ContentLifecycleState.READY_FOR_ANALYSIS)
    assert h.store.get_content_lifecycle(source.intake_id).state == (
        ContentLifecycleState.RIGHTS_PENDING
    )
    h.close()


def test_expiry_and_revocation_are_revalidated_before_analysis(tmp_path):
    expired_h = Harness(tmp_path / "expired.db")
    _register_asset(expired_h)
    source = _register_intake(expired_h)
    _transition(expired_h, source.intake_id, ContentLifecycleState.RIGHTS_PENDING)
    expires_at = datetime.now(UTC) + timedelta(seconds=1.5)
    _add_analyze_grant(expired_h, valid_until=expires_at)
    assert _transition(
        expired_h, source.intake_id, ContentLifecycleState.RIGHTS_CLEARED
    ).state == ContentLifecycleState.RIGHTS_CLEARED
    time.sleep(max(0, (expires_at - datetime.now(UTC)).total_seconds() + 0.05))
    expired = _transition(
        expired_h, source.intake_id, ContentLifecycleState.READY_FOR_ANALYSIS
    )
    assert expired.state == ContentLifecycleState.EXPIRED
    assert expired_h.store.list_audit_events("asset-1")
    expired_h.close()

    revoked_h = Harness(tmp_path / "revoked.db")
    _register_asset(revoked_h)
    source = _register_intake(revoked_h)
    _transition(revoked_h, source.intake_id, ContentLifecycleState.RIGHTS_PENDING)
    _add_analyze_grant(revoked_h)
    _transition(revoked_h, source.intake_id, ContentLifecycleState.RIGHTS_CLEARED)
    _transition(revoked_h, source.intake_id, ContentLifecycleState.READY_FOR_ANALYSIS)
    revoked_h.invoke(
        "content_revoke_rights_grant",
        {"grant_id": "grant-1", "reason": "synthetic withdrawal"},
        revoked_h.service.revoke_rights_grant,
        grant_id="grant-1",
        reason="synthetic withdrawal",
    )
    assert revoked_h.store.get_content_lifecycle(source.intake_id).state == (
        ContentLifecycleState.RIGHTS_PENDING
    )
    revoked = _transition(
        revoked_h, source.intake_id, ContentLifecycleState.RIGHTS_CLEARED
    )
    assert revoked.state == ContentLifecycleState.REVOKED
    revoked_h.close()


def test_expired_agreement_invalidates_ready_lifecycle_at_content_boundary(tmp_path):
    h = Harness(tmp_path / "expiry-boundary.db")
    asset = _register_asset(h)
    source = _register_intake(
        h,
        _source(
            rights_use=RightsUse(
                asset_id=asset.asset_id,
                expected_asset_sha256=asset.content_sha256,
                platform=ContentPlatform.YOUTUBE,
                account_id="channel-1",
                territory="US",
                actions=frozenset({RightsAction.ANALYZE}),
                commercial_intent=False,
                monetization_intent=False,
                duration_seconds=15,
            )
        ),
    )
    _transition(h, source.intake_id, ContentLifecycleState.RIGHTS_PENDING)
    valid_until = datetime.now(UTC) + timedelta(seconds=2)
    grant = _add_analyze_grant(h, valid_until=valid_until)
    _transition(h, source.intake_id, ContentLifecycleState.RIGHTS_CLEARED)
    _transition(h, source.intake_id, ContentLifecycleState.READY_FOR_ANALYSIS)
    assert h.store.get_content_lifecycle(source.intake_id).state == (
        ContentLifecycleState.READY_FOR_ANALYSIS
    )

    package = ContentPackage(package_id="expiry-boundary", owner_id="alice")
    h.invoke(
        "content_create_package",
        package.model_dump(mode="json"),
        h.service.create_content_package,
        package=package,
    )
    package_use = _rights_use(actions=frozenset({RightsAction.ANALYZE, RightsAction.PUBLISH}))
    package_use = package_use.model_copy(
        update={"expected_asset_sha256": asset.content_sha256}
    )
    artifact = b"synthetic package after expiry"
    metadata = {"title": "Synthetic lifecycle expiry check"}
    metadata_sha256 = hashlib.sha256(
        json.dumps(
            metadata,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    package_version = ContentPackageVersion(
        package_id=package.package_id,
        version=1,
        platform=ContentPlatform.YOUTUBE,
        account_id="channel-1",
        artifact_sha256=hashlib.sha256(artifact).hexdigest(),
        metadata_sha256=metadata_sha256,
        metadata=metadata,
        asset_uses=(ContentAssetUse(use=package_use),),
    )
    time.sleep(max(0, (valid_until - datetime.now(UTC)).total_seconds()) + 0.1)
    with pytest.raises(PermissionError, match="source rights are not cleared"):
        h.invoke(
            "content_create_package_version",
            package_version.model_dump(mode="json"),
            h.service.create_content_package_version,
            version=package_version,
            artifact_stream=BytesIO(artifact),
        )
    assert h.store.get_content_lifecycle(source.intake_id).state == (
        ContentLifecycleState.RIGHTS_PENDING
    )
    assert [event.status for event in h.store.list_rights_agreement_events(
        grant.agreement_id
    )].count(AgreementStatus.EXPIRED) == 1
    h.close()


def test_snapchat_and_youtube_kids_do_not_create_new_rights_permissions(tmp_path):
    snap_h = Harness(tmp_path / "snap.db")
    _register_asset(snap_h)
    snap = _register_intake(
        snap_h,
        _source(target_context=ContentTargetContext.SNAPCHAT_SPOTLIGHT),
    )
    _transition(snap_h, snap.intake_id, ContentLifecycleState.RIGHTS_PENDING)
    blocked = _transition(
        snap_h, snap.intake_id, ContentLifecycleState.RIGHTS_CLEARED
    )
    assert blocked.state == ContentLifecycleState.BLOCKED
    snap_h.close()

    kids_h = Harness(tmp_path / "kids.db")
    _register_asset(kids_h)
    kids = _register_intake(
        kids_h,
        _source(target_context=ContentTargetContext.YOUTUBE_KIDS_AUDIENCE),
    )
    _transition(kids_h, kids.intake_id, ContentLifecycleState.RIGHTS_PENDING)
    _add_analyze_grant(kids_h)
    assert _transition(
        kids_h, kids.intake_id, ContentLifecycleState.RIGHTS_CLEARED
    ).state == ContentLifecycleState.RIGHTS_CLEARED
    kids_h.close()


def test_audit_failure_rolls_back_lifecycle_and_authorization_claim(tmp_path):
    h = Harness(tmp_path / "audit-failure.db")
    _register_asset(h)
    source = _register_intake(h)
    connection = h.store._connection
    connection.execute(
        """
        CREATE TRIGGER fail_lifecycle_audit
        BEFORE INSERT ON content_audit_events
        WHEN NEW.action = 'content_transition_lifecycle'
        BEGIN
            SELECT RAISE(ABORT, 'injected audit failure');
        END
        """
    )
    with pytest.raises(sqlite3.IntegrityError, match="injected audit failure"):
        _transition(h, source.intake_id, ContentLifecycleState.RIGHTS_PENDING)
    assert h.store.get_content_lifecycle(source.intake_id).state == (
        ContentLifecycleState.REGISTERED
    )
    assert len(h.store.list_content_lifecycle_events(source.intake_id)) == 1
    connection.execute("DROP TRIGGER fail_lifecycle_audit")
    assert _transition(
        h, source.intake_id, ContentLifecycleState.RIGHTS_PENDING
    ).state == ContentLifecycleState.RIGHTS_PENDING
    h.close()


def test_missing_linked_asset_fails_closed_on_transition(tmp_path):
    h = Harness(tmp_path / "missing-link.db")
    _register_asset(h)
    source = _register_intake(h)
    h.store._connection.execute("PRAGMA foreign_keys = OFF")
    h.store._connection.execute(
        "DELETE FROM content_assets WHERE asset_id = ?",
        ("asset-1",),
    )

    with pytest.raises(RuntimeError, match="linkage is missing or inconsistent"):
        _transition(h, source.intake_id, ContentLifecycleState.RIGHTS_PENDING)
    assert h.store.get_content_lifecycle(source.intake_id).state == (
        ContentLifecycleState.REGISTERED
    )
    h.close()


def test_concurrent_conflicting_transitions_leave_valid_durable_state(tmp_path):
    h = Harness(tmp_path / "concurrent.db")
    _register_asset(h)
    source = _register_intake(h)
    calls = [
        (
            ContentLifecycleState.RIGHTS_PENDING,
            h.context(
                "content_transition_lifecycle",
                {
                    "intake_id": source.intake_id,
                    "target_state": ContentLifecycleState.RIGHTS_PENDING.value,
                    "reason": "concurrent test",
                },
            ),
        ),
        (
            ContentLifecycleState.ARCHIVED,
            h.context(
                "content_transition_lifecycle",
                {
                    "intake_id": source.intake_id,
                    "target_state": ContentLifecycleState.ARCHIVED.value,
                    "reason": "concurrent test",
                },
            ),
        ),
    ]

    def run(item):
        target, context = item
        try:
            return h.service.transition_content_lifecycle(
                intake_id=source.intake_id,
                target_state=target,
                reason="concurrent test",
                context=context,
                command_id=str(uuid4()),
            )
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, calls))
    record = h.store.get_content_lifecycle(source.intake_id)
    assert record.state in {
        ContentLifecycleState.RIGHTS_PENDING,
        ContentLifecycleState.ARCHIVED,
    }
    assert all(result is None or result.intake_id == source.intake_id for result in results)
    states = [event.to_state for event in h.store.list_content_lifecycle_events(
        source.intake_id
    )]
    assert states[0] == ContentLifecycleState.REGISTERED
    assert states[-1] == record.state
    h.close()


def test_reopen_preserves_lifecycle_and_v1_schema_migrates_additively(tmp_path):
    database = tmp_path / "recovery.db"
    h = Harness(database)
    asset = _register_asset(h)
    source = _register_intake(h)
    _transition(h, source.intake_id, ContentLifecycleState.RIGHTS_PENDING)
    h.close()

    reopened = ContentStore(database)
    assert reopened.get_asset(asset.asset_id) is not None
    assert reopened.get_source_intake(source.intake_id) == source
    assert reopened.get_content_lifecycle(source.intake_id).state == (
        ContentLifecycleState.RIGHTS_PENDING
    )
    reopened._connection.execute("DROP TABLE content_lifecycle_events")
    reopened._connection.execute("DROP TABLE content_lifecycle")
    reopened._connection.execute("DROP TABLE source_intakes")
    reopened._connection.execute(
        "UPDATE content_schema_meta SET schema_version = 1 WHERE singleton = 1"
    )
    reopened.close()

    migrated = ContentStore(database)
    assert migrated._connection.execute(
        "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
    ).fetchone()[0] == ContentStore.SCHEMA_VERSION
    assert migrated.get_asset(asset.asset_id) is not None
    migrated.close()
