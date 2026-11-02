from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from typing import Any, BinaryIO
from uuid import uuid4

from ultron.domain.content import (
    AgreementStatus,
    AssetState,
    ContentAssetUse,
    ContentLifecycleEvent,
    ContentLifecycleRecord,
    ContentLifecycleState,
    ContentPlatform,
    ContentProcessingJob,
    ContentProcessingOutput,
    ContentPackage,
    ContentPackageVersion,
    ContentTargetContext,
    EvidenceDecision,
    GrantorAuthority,
    GrantorAuthorityReview,
    GrantorIdentity,
    GrantorIdentityReview,
    OwnershipClaim,
    OwnershipClaimState,
    PlatformDecisionState,
    PublicationState,
    ReviewApproval,
    ReviewState,
    RightsAction,
    RightsAgreement,
    RightsAgreementReview,
    RightsEvidence,
    RightsEvaluation,
    RightsEvidenceReview,
    RightsGrant,
    RightsState,
    RightsTerms,
    RightsUse,
    ContentEditProject,
    ContentEditSegment,
    ProcessingJobState,
    SourceAsset,
    SourceReferenceRecord,
    VerificationStatus,
)
from ultron.security.identity.authorization import (
    AuthorizationContext,
    ValidatedAuthorization,
    _validated_authorization,
)
from ultron.security.identity.authenticator import WebSocketAuthenticator

from .policy import ContentRightsPolicy, GrantRecord
from .processing import (
    ContentBlobStore,
    LocalTemporaryContentStore,
    SyntheticContentProcessor,
)
from .store import ContentStore, IdempotencyConflictError


class ContentRightsService:
    """Authenticated application boundary for durable content rights decisions."""

    MAX_REVIEW_APPROVAL_TTL = timedelta(hours=24)
    MAX_PROCESSING_SECONDS = 5.0

    def __init__(
        self,
        *,
        store: ContentStore,
        authority: WebSocketAuthenticator,
        reviewer_principals: frozenset[str] = frozenset(),
        policy: ContentRightsPolicy | None = None,
        content_storage: ContentBlobStore | None = None,
        processor: SyntheticContentProcessor | None = None,
    ) -> None:
        if type(store) is not ContentStore:
            raise TypeError("Content rights require the configured ContentStore.")
        if type(authority) is not WebSocketAuthenticator:
            raise TypeError("A WebSocketAuthenticator trust authority is required.")
        if policy is not None and type(policy) is not ContentRightsPolicy:
            raise TypeError("Content rights require the supported rights policy.")
        if content_storage is None and processor is not None:
            raise TypeError("Content processing requires managed storage.")
        if processor is not None and type(processor) is not SyntheticContentProcessor:
            raise TypeError("Only the deterministic synthetic processor is supported.")
        self.store = store
        self.authority = authority
        self.reviewer_principals = frozenset(
            principal.strip()
            for principal in reviewer_principals
            if isinstance(principal, str) and principal.strip()
        )
        self.policy = policy or ContentRightsPolicy()
        self.content_storage = content_storage
        self.processor = processor
        self.store._register_authorization_authority(self.authority)

    def _authorize(
        self,
        context: AuthorizationContext,
        *,
        action: str,
        payload: dict[str, Any],
    ) -> ValidatedAuthorization:
        if type(context) is not AuthorizationContext or not self.authority.validate_context(
            context,
            action=action,
            parameters=payload,
            operation_id=context.operation_id,
            correlation_id=context.operation_correlation_id,
        ):
            raise PermissionError("A valid active-session authorization is required.")
        return _validated_authorization(
            context,
            self.authority,
            action=action,
            parameters=payload,
        )

    def _run(
        self,
        context: AuthorizationContext,
        *,
        action: str,
        payload: dict[str, Any],
        command_id: str,
        subject_id: str,
        operation,
    ) -> str:
        authorization = self._authorize(context, action=action, payload=payload)
        return self.store.run_authorized(
            authorization=authorization,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=subject_id,
            operation=operation,
        )

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @staticmethod
    def _hash_stream(stream: BinaryIO, *, label: str) -> str:
        digest = hashlib.sha256()
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            if not isinstance(chunk, bytes):
                raise TypeError(f"{label} must be provided as a binary stream.")
            digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _asset_in_transaction(
        connection: sqlite3.Connection,
        asset_id: str,
    ) -> SourceAsset | None:
        row = connection.execute(
            "SELECT * FROM content_assets WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()
        return None if row is None else ContentStore._load_asset(row)

    @staticmethod
    def _grant_records(
        connection: sqlite3.Connection,
        asset_id: str,
    ) -> tuple[GrantRecord, ...]:
        return ContentStore.grant_records(connection, asset_id)

    def create_edit_project(
        self,
        project: ContentEditProject,
        *,
        context: AuthorizationContext,
        command_id: str,
    ) -> ContentEditProject:
        action = "content_create_edit_project"
        payload = project.model_dump(mode="json")
        self._authorize(context, action=action, payload=payload)
        if context.principal != project.owner_id:
            raise PermissionError("Only the project owner can create a CRE edit project.")

        def operation(connection: sqlite3.Connection):
            self.store.create_edit_project_in_transaction(connection, project)
            return project.project_id, "created", {
                "source_asset_ids": list(project.source_asset_ids),
            }

        self._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=project.project_id,
            operation=operation,
        )
        return project

    def replace_edit_timeline(
        self,
        project_id: str,
        owner_id: str,
        segments: tuple[ContentEditSegment, ...],
        *,
        context: AuthorizationContext,
        command_id: str,
    ) -> tuple[ContentEditSegment, ...]:
        action = "content_replace_edit_timeline"
        payload = {
            "project_id": project_id,
            "owner_id": owner_id,
            "segments": [segment.model_dump(mode="json") for segment in segments],
        }
        self._authorize(context, action=action, payload=payload)
        if context.principal != owner_id:
            raise PermissionError("Only the project owner can edit its timeline.")

        def operation(connection: sqlite3.Connection):
            saved = self.store.replace_edit_segments_in_transaction(
                connection,
                project_id,
                owner_id,
                segments,
            )
            return project_id, "timeline_replaced", {
                "segment_count": len(saved),
            }

        self._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=project_id,
            operation=operation,
        )
        return segments

    def get_edit_project(
        self,
        project_id: str,
        owner_id: str,
        *,
        context: AuthorizationContext,
    ) -> ContentEditProject | None:
        action = "content_read_edit_project"
        payload = {"project_id": project_id, "owner_id": owner_id}
        self._authorize(context, action=action, payload=payload)
        if context.principal != owner_id:
            raise PermissionError("Only the project owner can read this CRE edit project.")
        return self.store.get_edit_project(project_id, owner_id)

    def register_asset(
        self,
        asset: SourceAsset,
        *,
        source_stream: BinaryIO,
        context: AuthorizationContext,
        command_id: str,
    ) -> SourceAsset:
        action = "content_register_asset"
        payload = asset.model_dump(mode="json")
        self._authorize(context, action=action, payload=payload)
        if context.principal != asset.owner_id:
            raise PermissionError("Only the source asset owner can register it.")
        stored_input = False
        if self.content_storage is None:
            actual_hash = self._hash_stream(source_stream, label="Source content")
        else:
            if asset.storage_key is None:
                raise ValueError("Managed processing requires an opaque asset storage key.")
            actual_hash, _ = self.content_storage.put_input(
                asset.storage_key,
                source_stream,
            )
            stored_input = True
        if actual_hash != asset.content_sha256:
            if stored_input:
                self.content_storage.delete_input(asset.storage_key)
            raise ValueError("Source bytes do not match the declared immutable hash.")

        def operation(connection: sqlite3.Connection):
            connection.execute(
                """
                INSERT INTO content_assets (
                    asset_id, owner_id, category, content_sha256,
                    provenance_json, storage_key, asset_state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset.asset_id,
                    asset.owner_id,
                    asset.category.value,
                    asset.content_sha256,
                    self._json(asset.provenance.model_dump(mode="json")),
                    asset.storage_key,
                    asset.state.value,
                    asset.created_at.isoformat(),
                ),
            )
            return asset.asset_id, "registered", {
                "content_sha256": asset.content_sha256,
                "acquisition_method": asset.provenance.acquisition_method.value,
                "category": asset.category.value,
            }

        try:
            self._run(
                context,
                action=action,
                payload=payload,
                command_id=command_id,
                subject_id=asset.asset_id,
                operation=operation,
            )
        except Exception as primary:
            if stored_input:
                try:
                    self.content_storage.delete_input(asset.storage_key)
                except Exception as cleanup:
                    raise RuntimeError(
                        f"Asset registration failed ({primary}); managed input "
                        f"cleanup failed ({cleanup})."
                    ) from primary
            raise
        stored = self.store.get_asset(asset.asset_id)
        if stored is None:
            raise RuntimeError("Asset registration committed without a durable record.")
        return stored

    def register_source_reference(
        self,
        source: SourceReferenceRecord,
        *,
        context: AuthorizationContext,
        command_id: str,
    ) -> SourceReferenceRecord:
        action = "content_register_source_reference"
        payload = source.model_dump(mode="json")

        def operation(connection: sqlite3.Connection):
            asset = self._asset_in_transaction(connection, source.asset_id)
            if (
                asset is None
                or asset.owner_id != context.principal
                or source.owner_id != context.principal
            ):
                raise PermissionError(
                    "Source reference is outside the authenticated account."
                )
            if source.rights_use is not None and (
                source.rights_use.expected_asset_sha256 != asset.content_sha256
            ):
                raise PermissionError(
                    "Source-use hash does not match the verified registered asset."
                )
            connection.execute(
                """
                INSERT INTO source_intakes (
                    intake_id, asset_id, owner_id, source_reference,
                    acquisition_method, provenance_json, evidence_reference,
                    claimed_source_sha256, source_hash_status,
                    access_permission_state, target_context,
                    rights_use_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source.intake_id,
                    source.asset_id,
                    source.owner_id,
                    source.source_reference,
                    source.acquisition_method.value,
                    self._json(source.provenance.model_dump(mode="json")),
                    source.evidence_reference,
                    source.claimed_source_sha256,
                    source.source_hash_status.value,
                    source.access_permission_state.value,
                    source.target_context.value,
                    (
                        None
                        if source.rights_use is None
                        else self._json(source.rights_use.model_dump(mode="json"))
                    ),
                    source.created_at.isoformat(),
                ),
            )
            connection.execute(
                """
                INSERT INTO content_lifecycle (
                    intake_id, asset_id, asset_sha256, target_context,
                    state, rights_decision_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    source.intake_id,
                    source.asset_id,
                    asset.content_sha256,
                    source.target_context.value,
                    ContentLifecycleState.REGISTERED.value,
                    source.created_at.isoformat(),
                    source.created_at.isoformat(),
                ),
            )
            connection.execute(
                """
                INSERT INTO content_lifecycle_events (
                    intake_id, from_state, to_state, actor, reason,
                    rights_decision_id, created_at
                ) VALUES (?, NULL, ?, ?, ?, NULL, ?)
                """,
                (
                    source.intake_id,
                    ContentLifecycleState.REGISTERED.value,
                    context.principal,
                    "source_reference_registered",
                    source.created_at.isoformat(),
                ),
            )
            return source.intake_id, "source_reference_registered", {
                "asset_id": source.asset_id,
                "asset_sha256": asset.content_sha256,
                "source_hash_status": source.source_hash_status.value,
                "target_context": source.target_context.value,
            }

        self._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=source.intake_id,
            operation=operation,
        )
        stored = self.store.get_source_intake(source.intake_id)
        if stored is None:
            raise RuntimeError("Source intake committed without a durable record.")
        return stored

    def transition_content_lifecycle(
        self,
        *,
        intake_id: str,
        target_state: ContentLifecycleState,
        reason: str,
        context: AuthorizationContext,
        command_id: str,
    ) -> ContentLifecycleRecord:
        reason = reason.strip()
        if not reason or len(reason) > 2000:
            raise ValueError("Lifecycle transition requires a bounded reason.")
        action = "content_transition_lifecycle"
        payload = {
            "intake_id": intake_id,
            "target_state": target_state.value,
            "reason": reason,
        }
        transition_id = str(uuid4())

        def operation(connection: sqlite3.Connection):
            row = connection.execute(
                """
                SELECT l.*, s.owner_id, s.rights_use_json, s.target_context AS intake_target
                FROM content_lifecycle l
                JOIN source_intakes s ON s.intake_id = l.intake_id
                WHERE l.intake_id = ?
                """,
                (intake_id,),
            ).fetchone()
            if row is None or row["owner_id"] != context.principal:
                raise PermissionError(
                    "Content lifecycle is outside the authenticated account."
                )
            asset = self._asset_in_transaction(connection, row["asset_id"])
            if (
                asset is None
                or asset.owner_id != context.principal
                or asset.content_sha256 != row["asset_sha256"]
                or row["target_context"] != row["intake_target"]
            ):
                raise RuntimeError(
                    "Lifecycle source linkage is missing or inconsistent; refusing transition."
                )
            current = ContentLifecycleState(row["state"])
            if current == ContentLifecycleState.ARCHIVED:
                if target_state == current:
                    return intake_id, "lifecycle_idempotent_noop", {
                        "state": current.value,
                    }
                raise ValueError("Archived lifecycle records are terminal.")
            if target_state == current and current not in {
                ContentLifecycleState.RIGHTS_CLEARED,
                ContentLifecycleState.READY_FOR_ANALYSIS,
            }:
                return intake_id, "lifecycle_idempotent_noop", {
                    "state": current.value,
                }
            allowed = {
                ContentLifecycleState.REGISTERED: {
                    ContentLifecycleState.RIGHTS_PENDING,
                    ContentLifecycleState.ARCHIVED,
                },
                ContentLifecycleState.RIGHTS_PENDING: {
                    ContentLifecycleState.RIGHTS_CLEARED,
                    ContentLifecycleState.ARCHIVED,
                },
                ContentLifecycleState.RIGHTS_CLEARED: {
                    ContentLifecycleState.RIGHTS_CLEARED,
                    ContentLifecycleState.RIGHTS_PENDING,
                    ContentLifecycleState.READY_FOR_ANALYSIS,
                    ContentLifecycleState.ARCHIVED,
                },
                ContentLifecycleState.READY_FOR_ANALYSIS: {
                    ContentLifecycleState.RIGHTS_PENDING,
                    ContentLifecycleState.READY_FOR_ANALYSIS,
                    ContentLifecycleState.ARCHIVED,
                },
                ContentLifecycleState.REJECTED: {
                    ContentLifecycleState.RIGHTS_PENDING,
                    ContentLifecycleState.ARCHIVED,
                },
                ContentLifecycleState.BLOCKED: {
                    ContentLifecycleState.RIGHTS_PENDING,
                    ContentLifecycleState.ARCHIVED,
                },
                ContentLifecycleState.EXPIRED: {
                    ContentLifecycleState.RIGHTS_PENDING,
                    ContentLifecycleState.ARCHIVED,
                },
                ContentLifecycleState.REVOKED: {
                    ContentLifecycleState.RIGHTS_PENDING,
                    ContentLifecycleState.ARCHIVED,
                },
            }
            if target_state not in allowed[current]:
                raise ValueError(
                    f"Invalid lifecycle transition: {current.value} -> {target_state.value}."
                )

            decided_state = target_state
            decision_id = None
            decision = None
            if target_state in {
                ContentLifecycleState.RIGHTS_CLEARED,
                ContentLifecycleState.READY_FOR_ANALYSIS,
            }:
                if (
                    target_state == ContentLifecycleState.READY_FOR_ANALYSIS
                    and current != ContentLifecycleState.READY_FOR_ANALYSIS
                    and current != ContentLifecycleState.RIGHTS_CLEARED
                ):
                    raise ValueError(
                        "Rights must be cleared before analysis readiness."
                    )
                rights_use = (
                    None
                    if row["rights_use_json"] is None
                    else RightsUse.model_validate_json(row["rights_use_json"])
                )
                target = ContentTargetContext(row["target_context"])
                if rights_use is None or target == ContentTargetContext.SNAPCHAT_SPOTLIGHT:
                    decided_state = ContentLifecycleState.BLOCKED
                    decision_reason = "no_verified_rights_policy_for_target"
                else:
                    decision = self._evaluate_rights_in_transaction(
                        connection,
                        use=rights_use,
                        decision_id=transition_id,
                        principal=context.principal,
                    )
                    decision_id = decision.decision_id
                    if (
                        decision.copyright_state == RightsState.CLEARED
                        and decision.platform_rights_state == RightsState.CLEARED
                    ):
                        decision_reason = "rights_revalidated_for_intended_analysis"
                    elif decision.platform_rights_state == RightsState.EXPIRED:
                        decided_state = ContentLifecycleState.EXPIRED
                        decision_reason = "rights_expired"
                    elif decision.platform_rights_state == RightsState.REVOKED:
                        decided_state = ContentLifecycleState.REVOKED
                        decision_reason = "rights_revoked"
                    elif decision.platform_rights_state in {
                        RightsState.UNKNOWN,
                        RightsState.REVIEW_REQUIRED,
                    }:
                        decided_state = ContentLifecycleState.RIGHTS_PENDING
                        decision_reason = "rights_require_review"
                    elif "conflicting_or_rejected_authority_evidence" in decision.reason_codes:
                        evidence_states = ContentStore.evidence_states(
                            connection, asset.asset_id
                        )
                        decided_state = (
                            ContentLifecycleState.REJECTED
                            if EvidenceDecision.REJECTED.value in evidence_states
                            else ContentLifecycleState.BLOCKED
                        )
                        decision_reason = "authority_evidence_rejected_or_ambiguous"
                    else:
                        decided_state = ContentLifecycleState.BLOCKED
                        decision_reason = "intended_use_not_cleared"

                if decided_state not in {
                    ContentLifecycleState.RIGHTS_CLEARED,
                    ContentLifecycleState.READY_FOR_ANALYSIS,
                }:
                    self._invalidate_approvals_for_asset(
                        connection,
                        asset.asset_id,
                        actor=context.principal,
                        reason=f"lifecycle_rights_{decided_state.value}",
                    )
            else:
                decision_reason = reason

            now = datetime.now(UTC).isoformat()
            connection.execute(
                """
                UPDATE content_lifecycle
                SET state = ?, rights_decision_id = ?, updated_at = ?
                WHERE intake_id = ?
                """,
                (decided_state.value, decision_id, now, intake_id),
            )
            connection.execute(
                """
                INSERT INTO content_lifecycle_events (
                    intake_id, from_state, to_state, actor, reason,
                    rights_decision_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intake_id,
                    current.value,
                    decided_state.value,
                    context.principal,
                    decision_reason,
                    decision_id,
                    now,
                ),
            )
            return intake_id, decided_state.value, {
                "from_state": current.value,
                "to_state": decided_state.value,
                "rights_decision_id": decision_id,
                "reason": decision_reason,
            }

        self._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=intake_id,
            operation=operation,
        )
        record = self.store.get_content_lifecycle(intake_id)
        if record is None:
            raise RuntimeError("Lifecycle transition committed without a durable record.")
        return record

    def _evaluate_rights_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        use: RightsUse,
        decision_id: str,
        principal: str,
    ) -> RightsEvaluation:
        self._record_expired_agreements(connection, principal=principal)
        asset = self._asset_in_transaction(connection, use.asset_id)
        if asset is None or asset.owner_id != principal:
            raise PermissionError("Rights evaluation is outside the caller's account.")
        result = self.policy.evaluate(
            decision_id=decision_id,
            asset=asset,
            use=use,
            records=self._grant_records(connection, use.asset_id),
            evidence_states=ContentStore.evidence_states(connection, use.asset_id),
        )
        connection.execute(
            """
            INSERT INTO rights_decisions (
                decision_id, asset_id, use_json, result_json,
                evaluated_at, policy_version
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                result.decision_id,
                result.asset_id,
                self._json(use.model_dump(mode="json")),
                self._json(result.model_dump(mode="json")),
                result.evaluated_at.isoformat(),
                result.policy_version,
            ),
        )
        return result

    def _record_expired_agreements(
        self,
        connection: sqlite3.Connection,
        *,
        principal: str,
    ) -> None:
        rows = connection.execute(
            """
            SELECT a.agreement_id, a.version, a.asset_ids_json, a.terms_json
            FROM rights_agreements a
            WHERE (
                SELECT status FROM rights_agreement_events e
                WHERE e.agreement_id = a.agreement_id
                  AND e.agreement_version = a.version
                ORDER BY e.sequence DESC LIMIT 1
            ) = ?
              AND a.grantee_owner_id = ?
            """,
            (AgreementStatus.APPROVED.value, principal),
        ).fetchall()
        now = datetime.now(UTC)
        for row in rows:
            try:
                terms = RightsTerms.model_validate_json(row["terms_json"])
                asset_ids = tuple(json.loads(row["asset_ids_json"]))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    "Approved agreement has invalid persisted scope or terms."
                ) from exc
            if terms.valid_until > now:
                continue
            connection.execute(
                """
                INSERT INTO rights_agreement_events (
                    agreement_id, agreement_version, status, actor, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["agreement_id"],
                    row["version"],
                    AgreementStatus.EXPIRED.value,
                    principal,
                    "agreement_validity_period_elapsed",
                    now.isoformat(),
                ),
            )
            self._invalidate_for_agreement_assets(
                connection,
                asset_ids,
                principal,
                "agreement_expired_requires_revalidation",
            )

    def register_grantor_identity(
        self,
        identity: GrantorIdentity,
        *,
        context: AuthorizationContext,
        command_id: str,
    ) -> GrantorIdentity:
        action = "content_register_grantor_identity"
        payload = identity.model_dump(mode="json")

        def operation(connection: sqlite3.Connection):
            if identity.owner_id != context.principal:
                raise PermissionError("Grantor identity claim is outside the caller's account.")
            if identity.status != VerificationStatus.UNVERIFIED:
                raise ValueError("New grantor identity claims must start unverified.")
            connection.execute(
                """
                INSERT INTO grantor_identities (
                    grantor_id, owner_id, claimed_identity, verification_method,
                    evidence_reference, evidence_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identity.grantor_id,
                    identity.owner_id,
                    identity.claimed_identity,
                    identity.verification_method,
                    identity.evidence_reference,
                    identity.evidence_sha256,
                    identity.created_at.isoformat(),
                ),
            )
            return identity.grantor_id, VerificationStatus.PENDING_REVIEW.value, {
                "evidence_sha256": identity.evidence_sha256,
                "status": VerificationStatus.PENDING_REVIEW.value,
            }

        self._run(
            context, action=action, payload=payload, command_id=command_id,
            subject_id=identity.grantor_id, operation=operation,
        )
        result = self.store.get_grantor_identity(identity.grantor_id)
        if result is None:
            raise RuntimeError("Grantor identity claim was not durably stored.")
        return result

    def review_grantor_identity(
        self,
        *,
        grantor_id: str,
        decision: EvidenceDecision,
        evidence_sha256: str,
        reason: str,
        context: AuthorizationContext,
        command_id: str,
    ) -> GrantorIdentityReview:
        reason = reason.strip()
        if not reason or len(reason) > 2000:
            raise ValueError("Identity review requires a bounded reason.")
        action = "content_review_grantor_identity"
        payload = {
            "grantor_id": grantor_id,
            "decision": decision.value,
            "evidence_sha256": evidence_sha256,
            "reason": reason,
        }
        review_id = str(uuid4())

        def operation(connection: sqlite3.Connection):
            row = connection.execute(
                "SELECT * FROM grantor_identities WHERE grantor_id = ?",
                (grantor_id,),
            ).fetchone()
            if row is None:
                raise LookupError("Grantor identity claim does not exist.")
            if context.principal not in self.reviewer_principals:
                raise PermissionError("Principal is not an authorized grantor reviewer.")
            if context.principal == row["owner_id"]:
                raise PermissionError("A grantor identity claim cannot self-verify.")
            if evidence_sha256 != row["evidence_sha256"]:
                raise PermissionError("Identity review must bind the exact evidence hash.")
            connection.execute(
                """
                INSERT INTO grantor_identity_reviews (
                    review_id, grantor_id, decision, evidence_sha256,
                    reviewer_principal, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_id, grantor_id, decision.value, evidence_sha256,
                    context.principal, reason, datetime.now(UTC).isoformat(),
                ),
            )
            affected_assets = {
                row["asset_id"]
                for row in connection.execute(
                    "SELECT asset_id FROM grantor_authorities WHERE grantor_id = ?",
                    (grantor_id,),
                ).fetchall()
            }
            agreements = connection.execute(
                "SELECT asset_ids_json FROM rights_agreements WHERE grantor_id = ?",
                (grantor_id,),
            ).fetchall()
            for agreement_row in agreements:
                affected_assets.update(json.loads(agreement_row["asset_ids_json"]))
            self._invalidate_for_agreement_assets(
                connection,
                tuple(sorted(affected_assets)),
                context.principal,
                "grantor_identity_review_changed_requires_revalidation",
            )
            return review_id, decision.value, {
                "grantor_id": grantor_id,
                "evidence_sha256": evidence_sha256,
            }

        stored_review_id = self._run(
            context, action=action, payload=payload, command_id=command_id,
            subject_id=grantor_id, operation=operation,
        )
        with self.store._lock:
            row = self.store._connection.execute(
                "SELECT * FROM grantor_identity_reviews WHERE review_id = ?",
                (stored_review_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Grantor identity review was not durably stored.")
        return GrantorIdentityReview(
            review_id=row["review_id"],
            grantor_id=row["grantor_id"],
            decision=row["decision"],
            evidence_sha256=row["evidence_sha256"],
            reviewer_principal=row["reviewer_principal"],
            reason=row["reason"],
            created_at=row["created_at"],
        )

    def register_grantor_authority(
        self,
        authority: GrantorAuthority,
        *,
        context: AuthorizationContext,
        command_id: str,
    ) -> GrantorAuthority:
        action = "content_register_grantor_authority"
        payload = authority.model_dump(mode="json")

        def operation(connection: sqlite3.Connection):
            if authority.status != VerificationStatus.UNVERIFIED:
                raise ValueError("New authority claims must start unverified.")
            identity = connection.execute(
                """
                SELECT i.owner_id, i.evidence_sha256,
                    (SELECT decision FROM grantor_identity_reviews r
                     WHERE r.grantor_id = i.grantor_id
                     ORDER BY r.sequence DESC LIMIT 1) AS latest_decision,
                    (SELECT evidence_sha256 FROM grantor_identity_reviews r
                     WHERE r.grantor_id = i.grantor_id
                     ORDER BY r.sequence DESC LIMIT 1) AS latest_review_hash
                FROM grantor_identities i WHERE i.grantor_id = ?
                """,
                (authority.grantor_id,),
            ).fetchone()
            asset = self._asset_in_transaction(connection, authority.asset_id)
            if (
                identity is None
                or asset is None
                or identity["owner_id"] != context.principal
                or asset.owner_id != context.principal
            ):
                raise PermissionError("Authority claim is outside the caller's account.")
            if (
                identity["latest_decision"] != EvidenceDecision.VERIFIED.value
                or identity["latest_review_hash"] != identity["evidence_sha256"]
            ):
                raise PermissionError(
                    "Identity verification does not establish authority to grant rights."
                )
            connection.execute(
                """
                INSERT INTO grantor_authorities (
                    authority_id, grantor_id, asset_id, rights_holder,
                    relationship, scope_description, evidence_reference,
                    evidence_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    authority.authority_id, authority.grantor_id,
                    authority.asset_id, authority.rights_holder,
                    authority.relationship, authority.scope_description,
                    authority.evidence_reference, authority.evidence_sha256,
                    authority.created_at.isoformat(),
                ),
            )
            return authority.authority_id, VerificationStatus.PENDING_REVIEW.value, {
                "asset_id": authority.asset_id,
                "evidence_sha256": authority.evidence_sha256,
                "status": VerificationStatus.PENDING_REVIEW.value,
            }

        self._run(
            context, action=action, payload=payload, command_id=command_id,
            subject_id=authority.authority_id, operation=operation,
        )
        result = self.store.get_grantor_authority(authority.authority_id)
        if result is None:
            raise RuntimeError("Grantor authority claim was not durably stored.")
        return result

    def review_grantor_authority(
        self,
        *,
        authority_id: str,
        decision: EvidenceDecision,
        evidence_sha256: str,
        reason: str,
        context: AuthorizationContext,
        command_id: str,
    ) -> GrantorAuthorityReview:
        reason = reason.strip()
        if not reason or len(reason) > 2000:
            raise ValueError("Authority review requires a bounded reason.")
        action = "content_review_grantor_authority"
        payload = {
            "authority_id": authority_id,
            "decision": decision.value,
            "evidence_sha256": evidence_sha256,
            "reason": reason,
        }
        review_id = str(uuid4())

        def operation(connection: sqlite3.Connection):
            row = connection.execute(
                """
                SELECT a.*, i.owner_id,
                    (SELECT decision FROM grantor_identity_reviews r
                     WHERE r.grantor_id = i.grantor_id
                     ORDER BY r.sequence DESC LIMIT 1) AS identity_decision,
                    (SELECT evidence_sha256 FROM grantor_identity_reviews r
                     WHERE r.grantor_id = i.grantor_id
                     ORDER BY r.sequence DESC LIMIT 1) AS identity_review_hash,
                    i.evidence_sha256 AS identity_evidence_sha256
                FROM grantor_authorities a
                JOIN grantor_identities i ON i.grantor_id = a.grantor_id
                WHERE a.authority_id = ?
                """,
                (authority_id,),
            ).fetchone()
            if row is None or row["owner_id"] != context.principal:
                if row is None:
                    raise LookupError("Grantor authority claim does not exist.")
                if context.principal not in self.reviewer_principals:
                    raise PermissionError("Principal is not an authorized grantor reviewer.")
            if context.principal not in self.reviewer_principals:
                raise PermissionError("Principal is not an authorized grantor reviewer.")
            if context.principal == row["owner_id"]:
                raise PermissionError("A grantor authority claim cannot self-verify.")
            if (
                row["identity_decision"] != EvidenceDecision.VERIFIED.value
                or row["identity_review_hash"] != row["identity_evidence_sha256"]
            ):
                raise PermissionError("Grantor identity is not independently verified.")
            if evidence_sha256 != row["evidence_sha256"]:
                raise PermissionError("Authority review must bind the exact evidence hash.")
            connection.execute(
                """
                INSERT INTO grantor_authority_reviews (
                    review_id, authority_id, decision, evidence_sha256,
                    reviewer_principal, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_id, authority_id, decision.value, evidence_sha256,
                    context.principal, reason, datetime.now(UTC).isoformat(),
                ),
            )
            affected_assets = {row["asset_id"]}
            agreements = connection.execute(
                "SELECT asset_ids_json FROM rights_agreements WHERE authority_id = ?",
                (authority_id,),
            ).fetchall()
            for agreement_row in agreements:
                affected_assets.update(json.loads(agreement_row["asset_ids_json"]))
            self._invalidate_for_agreement_assets(
                connection,
                tuple(sorted(affected_assets)),
                context.principal,
                "grantor_authority_review_changed_requires_revalidation",
            )
            return review_id, decision.value, {
                "authority_id": authority_id,
                "asset_id": row["asset_id"],
                "evidence_sha256": evidence_sha256,
            }

        stored_review_id = self._run(
            context, action=action, payload=payload, command_id=command_id,
            subject_id=authority_id, operation=operation,
        )
        result = self.store.get_grantor_authority(authority_id)
        if result is None:
            raise RuntimeError("Grantor authority review was not durably stored.")
        with self.store._lock:
            row = self.store._connection.execute(
                "SELECT * FROM grantor_authority_reviews WHERE review_id = ?",
                (stored_review_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Grantor authority review record is missing.")
        return GrantorAuthorityReview(
            review_id=row["review_id"],
            authority_id=row["authority_id"],
            decision=row["decision"],
            evidence_sha256=row["evidence_sha256"],
            reviewer_principal=row["reviewer_principal"],
            reason=row["reason"],
            created_at=row["created_at"],
        )

    def create_rights_agreement(
        self,
        agreement: RightsAgreement,
        *,
        context: AuthorizationContext,
        command_id: str,
    ) -> RightsAgreement:
        action = "content_create_rights_agreement"
        payload = agreement.model_dump(mode="json", exclude={"status"})

        def operation(connection: sqlite3.Connection):
            if agreement.status != AgreementStatus.PENDING_REVIEW:
                raise ValueError("New agreements must start pending independent review.")
            if agreement.grantee_owner_id != context.principal:
                raise PermissionError("Agreement is outside the authenticated account.")
            identity = connection.execute(
                """
                SELECT i.*,
                    (SELECT decision FROM grantor_identity_reviews r
                     WHERE r.grantor_id = i.grantor_id
                     ORDER BY r.sequence DESC LIMIT 1) AS decision,
                    (SELECT evidence_sha256 FROM grantor_identity_reviews r
                     WHERE r.grantor_id = i.grantor_id
                     ORDER BY r.sequence DESC LIMIT 1) AS reviewed_hash
                FROM grantor_identities i WHERE i.grantor_id = ?
                """,
                (agreement.grantor_id,),
            ).fetchone()
            authority = connection.execute(
                """
                SELECT a.*,
                    (SELECT decision FROM grantor_authority_reviews r
                     WHERE r.authority_id = a.authority_id
                     ORDER BY r.sequence DESC LIMIT 1) AS decision,
                    (SELECT evidence_sha256 FROM grantor_authority_reviews r
                     WHERE r.authority_id = a.authority_id
                     ORDER BY r.sequence DESC LIMIT 1) AS reviewed_hash
                FROM grantor_authorities a WHERE a.authority_id = ?
                """,
                (agreement.authority_id,),
            ).fetchone()
            assets = connection.execute(
                f"SELECT asset_id, owner_id FROM content_assets WHERE asset_id IN ({','.join('?' for _ in agreement.asset_ids)})",
                agreement.asset_ids,
            ).fetchall()
            if (
                identity is None or authority is None
                or identity["decision"] != EvidenceDecision.VERIFIED.value
                or identity["reviewed_hash"] != identity["evidence_sha256"]
                or authority["decision"] != EvidenceDecision.VERIFIED.value
                or authority["reviewed_hash"] != authority["evidence_sha256"]
                or authority["grantor_id"] != agreement.grantor_id
                or authority["rights_holder"] != agreement.rights_holder
                or agreement.asset_ids != (authority["asset_id"],)
                or identity["owner_id"] != context.principal
                or len(assets) != len(agreement.asset_ids)
                or any(row["owner_id"] != context.principal for row in assets)
            ):
                raise PermissionError(
                    "Agreement lacks verified identity, asset-specific authority, or valid scope."
                )
            latest = connection.execute(
                """
                SELECT MAX(version) AS version FROM rights_agreements
                WHERE agreement_id = ?
                """,
                (agreement.agreement_id,),
            ).fetchone()["version"]
            expected_version = 1 if latest is None else latest + 1
            if agreement.version != expected_version:
                raise ValueError("Agreement versions must be created sequentially.")
            if latest is not None:
                previous_assets_row = connection.execute(
                    """
                    SELECT asset_ids_json FROM rights_agreements
                    WHERE agreement_id = ? AND version = ?
                    """,
                    (agreement.agreement_id, latest),
                ).fetchone()
                previous_asset_ids = (
                    tuple(json.loads(previous_assets_row["asset_ids_json"]))
                    if previous_assets_row is not None
                    else ()
                )
                previous = connection.execute(
                    """
                    SELECT status FROM rights_agreement_events
                    WHERE agreement_id = ? AND agreement_version = ?
                    ORDER BY sequence DESC LIMIT 1
                    """,
                    (agreement.agreement_id, latest),
                ).fetchone()
                if previous is None or previous["status"] != AgreementStatus.APPROVED.value:
                    raise PermissionError("Only a current approved agreement can be amended.")
                connection.execute(
                    """
                    INSERT INTO rights_agreement_events (
                        agreement_id, agreement_version, status, actor, reason, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        agreement.agreement_id, latest, AgreementStatus.AMENDED.value,
                        context.principal, "superseded_by_amendment",
                        datetime.now(UTC).isoformat(),
                    ),
                )
            connection.execute(
                """
                INSERT INTO rights_agreements (
                    agreement_id, version, grantor_id, authority_id, rights_holder,
                    grantee_owner_id, asset_ids_json, terms_json, evidence_reference,
                    evidence_sha256, agreement_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agreement.agreement_id, agreement.version, agreement.grantor_id,
                    agreement.authority_id, agreement.rights_holder,
                    agreement.grantee_owner_id, self._json(list(agreement.asset_ids)),
                    self._json(agreement.terms.model_dump(mode="json")),
                    agreement.evidence_reference, agreement.evidence_sha256,
                    agreement.fingerprint(), agreement.created_at.isoformat(),
                ),
            )
            connection.execute(
                """
                INSERT INTO rights_agreement_events (
                    agreement_id, agreement_version, status, actor, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    agreement.agreement_id, agreement.version,
                    AgreementStatus.PENDING_REVIEW.value, context.principal,
                    "agreement_submitted_for_review", datetime.now(UTC).isoformat(),
                ),
            )
            if latest is not None:
                self._invalidate_for_agreement_assets(
                    connection,
                    tuple(sorted(set(previous_asset_ids) | set(agreement.asset_ids))),
                    context.principal,
                    "agreement_amended_requires_revalidation",
                )
            return agreement.agreement_id, AgreementStatus.PENDING_REVIEW.value, {
                "agreement_version": agreement.version,
                "agreement_sha256": agreement.fingerprint(),
                "evidence_sha256": agreement.evidence_sha256,
                "asset_ids": list(agreement.asset_ids),
            }

        self._run(
            context, action=action, payload=payload, command_id=command_id,
            subject_id=agreement.agreement_id, operation=operation,
        )
        result = self.store.get_rights_agreement(
            agreement.agreement_id, agreement.version
        )
        if result is None:
            raise RuntimeError("Rights agreement was not durably stored.")
        return result

    def review_rights_agreement(
        self,
        *,
        agreement_id: str,
        agreement_version: int,
        decision: EvidenceDecision,
        agreement_sha256: str,
        evidence_sha256: str,
        reason: str,
        context: AuthorizationContext,
        command_id: str,
    ) -> RightsAgreementReview:
        reason = reason.strip()
        if not reason or len(reason) > 2000:
            raise ValueError("Agreement review requires a bounded reason.")
        action = "content_review_rights_agreement"
        payload = {
            "agreement_id": agreement_id,
            "agreement_version": agreement_version,
            "decision": decision.value,
            "agreement_sha256": agreement_sha256,
            "evidence_sha256": evidence_sha256,
            "reason": reason,
        }
        review_id = str(uuid4())

        def operation(connection: sqlite3.Connection):
            row = connection.execute(
                """
                SELECT a.*, i.owner_id AS identity_owner,
                    i.evidence_sha256 AS identity_evidence_sha256,
                    (SELECT decision FROM grantor_identity_reviews r
                     WHERE r.grantor_id = i.grantor_id
                     ORDER BY r.sequence DESC LIMIT 1) AS identity_state,
                    (SELECT evidence_sha256 FROM grantor_identity_reviews r
                     WHERE r.grantor_id = i.grantor_id
                     ORDER BY r.sequence DESC LIMIT 1) AS identity_review_hash,
                    (SELECT decision FROM grantor_authority_reviews r
                     WHERE r.authority_id = a.authority_id
                     ORDER BY r.sequence DESC LIMIT 1) AS authority_state,
                    (SELECT evidence_sha256 FROM grantor_authority_reviews r
                     WHERE r.authority_id = a.authority_id
                     ORDER BY r.sequence DESC LIMIT 1) AS authority_review_hash,
                    ga.evidence_sha256 AS authority_evidence_sha256,
                    ga.grantor_id AS authority_grantor_id,
                    ga.asset_id AS authority_asset_id,
                    ga.rights_holder AS authority_rights_holder
                FROM rights_agreements a
                JOIN grantor_identities i ON i.grantor_id = a.grantor_id
                JOIN grantor_authorities ga ON ga.authority_id = a.authority_id
                WHERE a.agreement_id = ? AND a.version = ?
                """,
                (agreement_id, agreement_version),
            ).fetchone()
            if row is None:
                raise LookupError("Rights agreement version does not exist.")
            if context.principal not in self.reviewer_principals:
                raise PermissionError("Principal is not an authorized agreement reviewer.")
            if context.principal in {row["identity_owner"], row["grantee_owner_id"]}:
                raise PermissionError("Agreement creator cannot review their own agreement.")
            latest_version = connection.execute(
                "SELECT MAX(version) FROM rights_agreements WHERE agreement_id = ?",
                (agreement_id,),
            ).fetchone()[0]
            current_status = connection.execute(
                """
                SELECT status FROM rights_agreement_events
                WHERE agreement_id = ? AND agreement_version = ?
                ORDER BY sequence DESC LIMIT 1
                """,
                (agreement_id, agreement_version),
            ).fetchone()
            if (
                latest_version != agreement_version
                or current_status is None
                or current_status["status"] != AgreementStatus.PENDING_REVIEW.value
            ):
                raise PermissionError("Only the current pending agreement version can be reviewed.")
            agreement = self.store._load_rights_agreement(row)
            if (
                agreement_sha256 != agreement.fingerprint()
                or evidence_sha256 != row["evidence_sha256"]
                or row["identity_state"] != EvidenceDecision.VERIFIED.value
                or row["identity_review_hash"] != row["identity_evidence_sha256"]
                or row["authority_state"] != EvidenceDecision.VERIFIED.value
                or row["authority_review_hash"] != row["authority_evidence_sha256"]
                or row["authority_grantor_id"] != row["grantor_id"]
                or row["authority_asset_id"] not in agreement.asset_ids
                or row["authority_rights_holder"] != agreement.rights_holder
                or row["identity_owner"] != row["grantee_owner_id"]
            ):
                raise PermissionError(
                    "Agreement review lacks intact, verified identity/authority evidence."
                )
            connection.execute(
                """
                INSERT INTO rights_agreement_reviews (
                    review_id, agreement_id, agreement_version, decision,
                    agreement_sha256, evidence_sha256, reviewer_principal,
                    reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_id, agreement_id, agreement_version, decision.value,
                    agreement_sha256, evidence_sha256, context.principal,
                    reason, datetime.now(UTC).isoformat(),
                ),
            )
            status = (
                AgreementStatus.APPROVED
                if decision == EvidenceDecision.VERIFIED
                else AgreementStatus.REJECTED
            )
            connection.execute(
                """
                INSERT INTO rights_agreement_events (
                    agreement_id, agreement_version, status, actor, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    agreement_id, agreement_version, status.value,
                    context.principal, reason, datetime.now(UTC).isoformat(),
                ),
            )
            self._invalidate_for_agreement_assets(
                connection, tuple(json.loads(row["asset_ids_json"])),
                context.principal, f"agreement_{status.value}_requires_revalidation",
            )
            return review_id, decision.value, {
                "agreement_id": agreement_id,
                "agreement_version": agreement_version,
                "agreement_sha256": agreement_sha256,
                "evidence_sha256": evidence_sha256,
                "status": status.value,
            }

        stored_review_id = self._run(
            context, action=action, payload=payload, command_id=command_id,
            subject_id=agreement_id, operation=operation,
        )
        with self.store._lock:
            row = self.store._connection.execute(
                "SELECT * FROM rights_agreement_reviews WHERE review_id = ?",
                (stored_review_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Agreement review was not durably stored.")
        return RightsAgreementReview(
            review_id=row["review_id"],
            agreement_id=row["agreement_id"],
            agreement_version=row["agreement_version"],
            decision=row["decision"],
            agreement_sha256=row["agreement_sha256"],
            evidence_sha256=row["evidence_sha256"],
            reviewer_principal=row["reviewer_principal"],
            reason=row["reason"],
            created_at=row["created_at"],
        )

    def change_rights_agreement_status(
        self,
        *,
        agreement_id: str,
        agreement_version: int,
        status: AgreementStatus,
        reason: str,
        context: AuthorizationContext,
        command_id: str,
    ) -> RightsAgreement:
        if status not in {AgreementStatus.SUSPENDED, AgreementStatus.REVOKED}:
            raise ValueError("Agreement status changes are limited to suspension/revocation.")
        reason = reason.strip()
        if not reason or len(reason) > 2000:
            raise ValueError("Agreement status change requires a bounded reason.")
        action = "content_change_rights_agreement_status"
        payload = {
            "agreement_id": agreement_id,
            "agreement_version": agreement_version,
            "status": status.value,
            "reason": reason,
        }

        def operation(connection: sqlite3.Connection):
            row = connection.execute(
                """
                SELECT * FROM rights_agreements
                WHERE agreement_id = ? AND version = ?
                """,
                (agreement_id, agreement_version),
            ).fetchone()
            if row is None:
                raise LookupError("Rights agreement version does not exist.")
            if row["grantee_owner_id"] != context.principal:
                raise PermissionError("Agreement is outside the authenticated account.")
            prior = connection.execute(
                """
                SELECT status FROM rights_agreement_events
                WHERE agreement_id = ? AND agreement_version = ?
                ORDER BY sequence DESC LIMIT 1
                """,
                (agreement_id, agreement_version),
            ).fetchone()
            if prior is None or prior["status"] != AgreementStatus.APPROVED.value:
                raise PermissionError("Only an approved agreement can be suspended or revoked.")
            connection.execute(
                """
                INSERT INTO rights_agreement_events (
                    agreement_id, agreement_version, status, actor, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    agreement_id, agreement_version, status.value,
                    context.principal, reason, datetime.now(UTC).isoformat(),
                ),
            )
            self._invalidate_for_agreement_assets(
                connection, tuple(json.loads(row["asset_ids_json"])),
                context.principal, f"agreement_{status.value}_requires_revalidation",
            )
            return agreement_id, status.value, {
                "agreement_version": agreement_version,
                "asset_ids": json.loads(row["asset_ids_json"]),
                "reason": reason,
            }

        self._run(
            context, action=action, payload=payload, command_id=command_id,
            subject_id=agreement_id, operation=operation,
        )
        agreement = self.store.get_rights_agreement(agreement_id, agreement_version)
        if agreement is None:
            raise RuntimeError("Agreement status transition was not durable.")
        return agreement

    def _invalidate_for_agreement_assets(
        self,
        connection: sqlite3.Connection,
        asset_ids: tuple[str, ...],
        actor: str,
        reason: str,
    ) -> None:
        for asset_id in asset_ids:
            self._invalidate_approvals_for_asset(
                connection, asset_id, actor=actor, reason=reason
            )
            self._invalidate_lifecycle_for_asset(
                connection, asset_id, actor=actor, reason=reason
            )

    def _insert_agreement_status(
        self,
        connection: sqlite3.Connection,
        *,
        agreement_id: str,
        version: int,
        status: AgreementStatus,
        actor: str,
        reason: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO rights_agreement_events (
                agreement_id, agreement_version, status, actor, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                agreement_id, version, status.value, actor, reason,
                datetime.now(UTC).isoformat(),
            ),
        )

    def add_ownership_claim(
        self,
        claim: OwnershipClaim,
        *,
        context: AuthorizationContext,
        command_id: str,
    ) -> OwnershipClaim:
        action = "content_add_ownership_claim"
        payload = claim.model_dump(mode="json")

        def operation(connection: sqlite3.Connection):
            asset = self._asset_in_transaction(connection, claim.asset_id)
            if asset is None or asset.owner_id != context.principal:
                raise PermissionError("Ownership claim is outside the caller's account.")
            if claim.claimant_id != context.principal:
                raise PermissionError("Claimant must be the authenticated principal.")
            connection.execute(
                """
                INSERT INTO ownership_claims (
                    claim_id, asset_id, claimant_id, rights_holder,
                    authority_description, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    claim.claim_id,
                    claim.asset_id,
                    claim.claimant_id,
                    claim.rights_holder,
                    claim.authority_description,
                    claim.created_at.isoformat(),
                ),
            )
            self._invalidate_approvals_for_asset(
                connection,
                claim.asset_id,
                actor=context.principal,
                reason="ownership_claim_changed",
            )
            self._invalidate_lifecycle_for_asset(
                connection,
                claim.asset_id,
                actor=context.principal,
                reason="ownership_claim_changed_requires_revalidation",
            )
            return claim.claim_id, "review_required", {
                "asset_id": claim.asset_id,
                "rights_holder": claim.rights_holder,
            }

        self._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=claim.claim_id,
            operation=operation,
        )
        stored = self.store.get_ownership_claim(claim.claim_id)
        if stored is None:
            raise RuntimeError("Ownership claim was not durably stored.")
        return stored

    def submit_evidence(
        self,
        evidence: RightsEvidence,
        *,
        context: AuthorizationContext,
        command_id: str,
    ) -> RightsEvidence:
        action = "content_submit_rights_evidence"
        payload = evidence.model_dump(
            mode="json",
            exclude={"evidence_bytes"},
        )

        def operation(connection: sqlite3.Connection):
            claim = connection.execute(
                """
                SELECT c.*, a.owner_id
                FROM ownership_claims c
                JOIN content_assets a ON a.asset_id = c.asset_id
                WHERE c.claim_id = ?
                """,
                (evidence.claim_id,),
            ).fetchone()
            if (
                claim is None
                or claim["asset_id"] != evidence.asset_id
                or claim["owner_id"] != context.principal
                or claim["claimant_id"] != context.principal
            ):
                raise PermissionError("Evidence does not belong to the caller's claim.")
            if (
                evidence.submitted_by != context.principal
                or evidence.grantor_identity != claim["rights_holder"]
                or evidence.grantor_authority != claim["authority_description"]
            ):
                raise PermissionError("Evidence grantor identity conflicts with the claim.")
            connection.execute(
                """
                INSERT INTO rights_evidence (
                    evidence_id, asset_id, claim_id, submitted_by, evidence_kind,
                    evidence_blob, evidence_sha256, grantor_identity,
                    grantor_authority, claimed_terms_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence.evidence_id,
                    evidence.asset_id,
                    evidence.claim_id,
                    evidence.submitted_by,
                    evidence.evidence_kind,
                    sqlite3.Binary(evidence.evidence_bytes),
                    evidence.evidence_sha256,
                    evidence.grantor_identity,
                    evidence.grantor_authority,
                    self._json(evidence.claimed_terms.model_dump(mode="json")),
                    evidence.created_at.isoformat(),
                ),
            )
            self._invalidate_approvals_for_asset(
                connection,
                evidence.asset_id,
                actor=context.principal,
                reason="rights_evidence_submitted",
            )
            self._invalidate_lifecycle_for_asset(
                connection,
                evidence.asset_id,
                actor=context.principal,
                reason="rights_evidence_submitted_requires_revalidation",
            )
            return evidence.evidence_id, "submitted", {
                "asset_id": evidence.asset_id,
                "evidence_sha256": evidence.evidence_sha256,
                "claimed_terms_sha256": evidence.claimed_terms.fingerprint(),
            }

        self._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=evidence.evidence_id,
            operation=operation,
        )
        stored = self.store.get_evidence(evidence.evidence_id)
        if stored is None:
            raise RuntimeError("Evidence submission committed without a record.")
        return stored

    def verify_evidence(
        self,
        *,
        evidence_id: str,
        decision: EvidenceDecision,
        verified_terms_sha256: str | None = None,
        context: AuthorizationContext,
        command_id: str,
    ) -> RightsEvidenceReview:
        action = "content_verify_rights_evidence"
        payload = {
            "evidence_id": evidence_id,
            "decision": decision.value,
            "verified_terms_sha256": verified_terms_sha256,
        }
        review_id = str(uuid4())

        def operation(connection: sqlite3.Connection):
            row = connection.execute(
                """
                SELECT e.*, c.claimant_id, c.asset_id AS claim_asset_id,
                       a.owner_id, a.asset_id
                FROM rights_evidence e
                JOIN ownership_claims c ON c.claim_id = e.claim_id
                JOIN content_assets a ON a.asset_id = e.asset_id
                WHERE e.evidence_id = ?
                """,
                (evidence_id,),
            ).fetchone()
            if row is None:
                raise LookupError("Rights evidence does not exist.")
            if context.principal not in self.reviewer_principals:
                raise PermissionError("Principal is not an authorized evidence reviewer.")
            if context.principal in {row["owner_id"], row["claimant_id"]}:
                raise PermissionError("A rights evidence submitter cannot review their own evidence.")
            claimed_terms = RightsTerms.model_validate(
                json.loads(row["claimed_terms_json"])
            )
            if decision == EvidenceDecision.VERIFIED:
                if verified_terms_sha256 != claimed_terms.fingerprint():
                    raise PermissionError(
                        "Verified decision must bind the exact, reviewed grant terms."
                    )
            elif verified_terms_sha256 is not None:
                raise ValueError("Non-verified decisions cannot approve grant terms.")

            connection.execute(
                """
                INSERT INTO evidence_reviews (
                    review_id, evidence_id, decision, reviewer_principal,
                    evidence_sha256, verified_terms_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_id,
                    evidence_id,
                    decision.value,
                    context.principal,
                    row["evidence_sha256"],
                    verified_terms_sha256,
                    datetime.now(UTC).isoformat(),
                ),
            )
            self._invalidate_approvals_for_asset(
                connection,
                row["asset_id"],
                actor=context.principal,
                reason="rights_evidence_changed",
            )
            self._invalidate_lifecycle_for_asset(
                connection,
                row["asset_id"],
                actor=context.principal,
                reason="rights_evidence_changed_requires_revalidation",
            )
            claim_state = {
                EvidenceDecision.VERIFIED: OwnershipClaimState.VERIFIED,
                EvidenceDecision.REJECTED: OwnershipClaimState.REJECTED,
                EvidenceDecision.AMBIGUOUS: OwnershipClaimState.AMBIGUOUS,
            }[decision]
            return row["claim_id"], decision.value, {
                "asset_id": row["asset_id"],
                "evidence_id": evidence_id,
                "review_id": review_id,
                "verified_terms_sha256": verified_terms_sha256,
                "claim_state": claim_state.value,
            }

        self._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=evidence_id,
            operation=operation,
        )
        review = self.store.get_latest_evidence_review(evidence_id)
        if review is None or review.review_id != review_id:
            raise RuntimeError("Reviewed evidence decision was not durably stored.")
        return review

    def get_evidence_for_review(
        self,
        *,
        evidence_id: str,
        context: AuthorizationContext,
        command_id: str,
    ) -> RightsEvidence:
        action = "content_read_rights_evidence"
        payload = {"evidence_id": evidence_id}

        def operation(connection: sqlite3.Connection):
            if context.principal not in self.reviewer_principals:
                raise PermissionError("Principal is not an authorized evidence reviewer.")
            row = connection.execute(
                """
                SELECT e.evidence_sha256, e.claimed_terms_json, e.asset_id,
                       c.claimant_id, a.owner_id
                FROM rights_evidence e
                JOIN ownership_claims c ON c.claim_id = e.claim_id
                JOIN content_assets a ON a.asset_id = e.asset_id
                WHERE e.evidence_id = ?
                """,
                (evidence_id,),
            ).fetchone()
            if row is None:
                raise LookupError("Rights evidence does not exist.")
            if context.principal in {row["owner_id"], row["claimant_id"]}:
                raise PermissionError("A rights evidence submitter cannot review their own evidence.")
            terms_hash = RightsTerms.model_validate(
                json.loads(row["claimed_terms_json"])
            ).fingerprint()
            return evidence_id, "evidence_presented_for_human_review", {
                "asset_id": row["asset_id"],
                "evidence_sha256": row["evidence_sha256"],
                "claimed_terms_sha256": terms_hash,
            }

        self._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=evidence_id,
            operation=operation,
        )
        evidence = self.store.get_evidence(evidence_id)
        if evidence is None:
            raise RuntimeError("Reviewed evidence disappeared after access was logged.")
        return evidence

    def create_rights_grant(
        self,
        grant: RightsGrant,
        *,
        context: AuthorizationContext,
        command_id: str,
    ) -> RightsGrant:
        action = "content_create_rights_grant"
        payload = grant.model_dump(mode="json")

        def operation(connection: sqlite3.Connection):
            if grant.agreement_id is None or grant.agreement_version is None:
                raise PermissionError("A reviewed rights agreement is required for a grant.")
            claim = connection.execute(
                """
                SELECT c.*, a.owner_id
                FROM ownership_claims c
                JOIN content_assets a ON a.asset_id = c.asset_id
                WHERE c.claim_id = ?
                """,
                (grant.claim_id,),
            ).fetchone()
            evidence = connection.execute(
                """
                SELECT e.*, (
                    SELECT decision FROM evidence_reviews
                    WHERE evidence_id = e.evidence_id
                    ORDER BY created_at DESC, review_id DESC LIMIT 1
                ) AS latest_decision,
                (
                    SELECT verified_terms_sha256 FROM evidence_reviews
                    WHERE evidence_id = e.evidence_id
                    ORDER BY created_at DESC, review_id DESC LIMIT 1
                ) AS verified_terms_sha256
                FROM rights_evidence e
                WHERE e.evidence_id = ?
                """,
                (grant.evidence_id,),
            ).fetchone()
            if (
                claim is None
                or evidence is None
                or claim["asset_id"] != grant.asset_id
                or evidence["asset_id"] != grant.asset_id
                or evidence["claim_id"] != grant.claim_id
            ):
                raise PermissionError("Grant does not match its source claim and evidence.")
            if claim["owner_id"] != context.principal:
                raise PermissionError("Only the content owner can record a rights grant.")
            if grant.grantee_owner_id != claim["owner_id"]:
                raise PermissionError("Rights grant is for a different content owner.")
            if (
                claim["rights_holder"] != grant.grantor_identity
                or claim["authority_description"] != grant.grantor_authority
                or evidence["grantor_identity"] != grant.grantor_identity
                or evidence["grantor_authority"] != grant.grantor_authority
            ):
                raise PermissionError("Grantor identity or authority is inconsistent.")
            if evidence["latest_decision"] != EvidenceDecision.VERIFIED.value:
                raise PermissionError("Rights evidence has not been explicitly verified.")
            if (
                evidence["verified_terms_sha256"] != grant.terms.fingerprint()
                or RightsTerms.model_validate(
                    json.loads(evidence["claimed_terms_json"])
                ).fingerprint()
                != grant.terms.fingerprint()
                or hashlib.sha256(bytes(evidence["evidence_blob"])).hexdigest()
                != evidence["evidence_sha256"]
            ):
                raise PermissionError(
                    "Grant terms do not match intact, verified supporting evidence."
                )
            agreement_row = connection.execute(
                """
                SELECT a.*,
                    (SELECT status FROM rights_agreement_events e
                     WHERE e.agreement_id = a.agreement_id
                       AND e.agreement_version = a.version
                     ORDER BY e.sequence DESC LIMIT 1) AS current_status,
                    (SELECT decision FROM rights_agreement_reviews r
                     WHERE r.agreement_id = a.agreement_id
                       AND r.agreement_version = a.version
                     ORDER BY r.sequence DESC LIMIT 1) AS review_decision,
                    (SELECT agreement_sha256 FROM rights_agreement_reviews r
                     WHERE r.agreement_id = a.agreement_id
                       AND r.agreement_version = a.version
                     ORDER BY r.sequence DESC LIMIT 1) AS reviewed_agreement_sha256,
                    (SELECT evidence_sha256 FROM rights_agreement_reviews r
                     WHERE r.agreement_id = a.agreement_id
                       AND r.agreement_version = a.version
                     ORDER BY r.sequence DESC LIMIT 1) AS reviewed_evidence_sha256,
                    (SELECT decision FROM grantor_identity_reviews r
                     WHERE r.grantor_id = a.grantor_id
                     ORDER BY r.sequence DESC LIMIT 1) AS identity_status,
                    (SELECT evidence_sha256 FROM grantor_identity_reviews r
                     WHERE r.grantor_id = a.grantor_id
                     ORDER BY r.sequence DESC LIMIT 1) AS reviewed_identity_sha256,
                    i.evidence_sha256 AS identity_evidence_sha256,
                    (SELECT decision FROM grantor_authority_reviews r
                     WHERE r.authority_id = a.authority_id
                     ORDER BY r.sequence DESC LIMIT 1) AS authority_status,
                    (SELECT evidence_sha256 FROM grantor_authority_reviews r
                     WHERE r.authority_id = a.authority_id
                     ORDER BY r.sequence DESC LIMIT 1) AS reviewed_authority_sha256,
                    ga.evidence_sha256 AS authority_evidence_sha256,
                    ga.asset_id AS authority_asset_id,
                    ga.rights_holder AS authority_rights_holder,
                    ga.grantor_id AS authority_grantor_id
                FROM rights_agreements a
                JOIN grantor_identities i ON i.grantor_id = a.grantor_id
                JOIN grantor_authorities ga ON ga.authority_id = a.authority_id
                WHERE a.agreement_id = ? AND a.version = ?
                """,
                (grant.agreement_id, grant.agreement_version),
            ).fetchone()
            if agreement_row is None:
                raise PermissionError("Rights agreement is missing.")
            agreement = self.store._load_rights_agreement(agreement_row)
            latest_version = connection.execute(
                "SELECT MAX(version) FROM rights_agreements WHERE agreement_id = ?",
                (grant.agreement_id,),
            ).fetchone()[0]
            if (
                latest_version != grant.agreement_version
                or agreement_row["current_status"] != AgreementStatus.APPROVED.value
                or agreement.terms.valid_until <= datetime.now(UTC)
                or agreement_row["review_decision"] != EvidenceDecision.VERIFIED.value
                or agreement.fingerprint()
                != agreement_row["reviewed_agreement_sha256"]
                or agreement.fingerprint()
                != agreement_row["agreement_sha256"]
                or agreement_row["reviewed_evidence_sha256"]
                != agreement.evidence_sha256
                or agreement_row["identity_status"] != EvidenceDecision.VERIFIED.value
                or agreement_row["reviewed_identity_sha256"]
                != agreement_row["identity_evidence_sha256"]
                or agreement_row["authority_status"] != EvidenceDecision.VERIFIED.value
                or agreement_row["reviewed_authority_sha256"]
                != agreement_row["authority_evidence_sha256"]
                or agreement_row["authority_asset_id"] != grant.asset_id
                or agreement_row["authority_rights_holder"] != grant.grantor_identity
                or agreement_row["authority_grantor_id"] != agreement.grantor_id
                or agreement.rights_holder != grant.grantor_identity
                or agreement.grantee_owner_id != grant.grantee_owner_id
                or grant.asset_id not in agreement.asset_ids
                or agreement.terms.fingerprint() != grant.terms.fingerprint()
            ):
                raise PermissionError(
                    "Rights agreement is unverified, out of scope, expired, or inconsistent."
                )
            connection.execute(
                """
                INSERT INTO rights_grants (
                    grant_id, asset_id, claim_id, evidence_id, grantor_identity,
                    grantor_authority, grantee_owner_id, terms_json, created_at,
                    agreement_id, agreement_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    grant.grant_id,
                    grant.asset_id,
                    grant.claim_id,
                    grant.evidence_id,
                    grant.grantor_identity,
                    grant.grantor_authority,
                    grant.grantee_owner_id,
                    self._json(grant.terms.model_dump(mode="json")),
                    grant.created_at.isoformat(),
                    grant.agreement_id,
                    grant.agreement_version,
                ),
            )
            self._invalidate_approvals_for_asset(
                connection,
                grant.asset_id,
                actor=context.principal,
                reason="rights_grant_changed",
            )
            self._invalidate_lifecycle_for_asset(
                connection,
                grant.asset_id,
                actor=context.principal,
                reason="rights_grant_changed_requires_revalidation",
            )
            return grant.grant_id, "verified_grant_recorded", {
                "asset_id": grant.asset_id,
                "platform": grant.terms.platform.value,
                "terms_sha256": grant.terms.fingerprint(),
                "evidence_id": grant.evidence_id,
                "agreement_id": grant.agreement_id,
                "agreement_version": grant.agreement_version,
            }

        self._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=grant.grant_id,
            operation=operation,
        )
        stored = self.store.get_grant(grant.grant_id)
        if stored is None:
            raise RuntimeError("Rights grant was not durably stored.")
        return stored

    def revoke_rights_grant(
        self,
        *,
        grant_id: str,
        reason: str,
        context: AuthorizationContext,
        command_id: str,
    ) -> RightsGrant:
        reason = reason.strip()
        if not reason or len(reason) > 2000:
            raise ValueError("A bounded revocation reason is required.")
        action = "content_revoke_rights_grant"
        payload = {"grant_id": grant_id, "reason": reason}

        def operation(connection: sqlite3.Connection):
            row = connection.execute(
                """
                SELECT g.grantee_owner_id, g.asset_id, r.grant_id AS revoked
                FROM rights_grants g
                LEFT JOIN rights_grant_revocations r ON r.grant_id = g.grant_id
                WHERE g.grant_id = ?
                """,
                (grant_id,),
            ).fetchone()
            if row is None or row["grantee_owner_id"] != context.principal:
                raise PermissionError("Grant revocation is outside the caller's account.")
            if row["revoked"] is None:
                connection.execute(
                    """
                    INSERT INTO rights_grant_revocations (
                        grant_id, revoked_by, reason, created_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        grant_id,
                        context.principal,
                        reason,
                        datetime.now(UTC).isoformat(),
                    ),
                )
                self._invalidate_approvals_for_asset(
                    connection,
                    row["asset_id"],
                    actor=context.principal,
                    reason="rights_grant_revoked",
                )
                self._invalidate_lifecycle_for_asset(
                    connection,
                    row["asset_id"],
                    actor=context.principal,
                    reason="rights_grant_revoked_requires_revalidation",
                )
            return grant_id, "revoked", {
                "asset_id": row["asset_id"],
                "reason": reason,
            }

        self._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=grant_id,
            operation=operation,
        )
        stored = self.store.get_grant(grant_id)
        if stored is None:
            raise RuntimeError("Revoked rights grant disappeared.")
        return stored

    def evaluate_rights(
        self,
        use: RightsUse,
        *,
        context: AuthorizationContext,
        command_id: str,
    ) -> RightsEvaluation:
        action = "content_evaluate_rights"
        payload = use.model_dump(mode="json")
        decision_id = str(uuid4())

        def operation(connection: sqlite3.Connection):
            result = self._evaluate_rights_in_transaction(
                connection,
                use=use,
                principal=context.principal,
                decision_id=decision_id,
            )
            return result.decision_id, result.platform_rights_state.value, {
                "asset_id": use.asset_id,
                "copyright_state": result.copyright_state.value,
                "platform_rights_state": result.platform_rights_state.value,
                "platform_compliance_state": result.platform_compliance_state.value,
                "publishing_authorization_state": (
                    result.publishing_authorization_state.value
                ),
                "monetization_eligibility_state": (
                    result.monetization_eligibility_state.value
                ),
                "reason_codes": list(result.reason_codes),
                "policy_version": result.policy_version,
            }

        self._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=use.asset_id,
            operation=operation,
        )
        with self.store._lock:
            row = self.store._connection.execute(
                "SELECT result_json FROM rights_decisions WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError(
                "Rights decision request was denied or its durable result is missing."
            )
        return RightsEvaluation.model_validate_json(row["result_json"])

    @staticmethod
    def _processing_platform(
        target_context: ContentTargetContext,
    ) -> ContentPlatform | None:
        return {
            ContentTargetContext.YOUTUBE_SHORTS: ContentPlatform.YOUTUBE,
            ContentTargetContext.YOUTUBE_KIDS_AUDIENCE: ContentPlatform.YOUTUBE,
            ContentTargetContext.INSTAGRAM_REELS: ContentPlatform.INSTAGRAM,
            ContentTargetContext.SNAPCHAT_SPOTLIGHT: None,
        }[target_context]

    @staticmethod
    def _insert_processing_event(
        connection: sqlite3.Connection,
        *,
        job_id: str,
        from_state: ProcessingJobState | None,
        to_state: ProcessingJobState,
        actor: str,
        reason: str,
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO content_processing_events (
                job_id, from_state, to_state, actor, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                None if from_state is None else from_state.value,
                to_state.value,
                actor,
                reason,
                now,
            ),
        )

    def create_processing_job(
        self,
        use: RightsUse,
        *,
        target_context: ContentTargetContext,
        context: AuthorizationContext,
        command_id: str,
    ) -> ContentProcessingJob:
        if self.content_storage is None or self.processor is None:
            raise RuntimeError("Synthetic processing is not configured.")
        platform = self._processing_platform(target_context)
        if (
            platform is None
            or use.platform != platform
            or use.actions != frozenset({RightsAction.ANALYZE})
            or use.commercial_intent
            or use.monetization_intent
        ):
            raise ValueError("Processing requires an exact non-commercial ANALYZE use.")
        action = "content_create_processing_job"
        payload = {
            "use": use.model_dump(mode="json"),
            "target_context": target_context.value,
        }
        job_id = str(uuid4())

        def operation(connection: sqlite3.Connection):
            asset = self._asset_in_transaction(connection, use.asset_id)
            if (
                asset is None
                or asset.owner_id != context.principal
                or asset.content_sha256 != use.expected_asset_sha256
                or asset.storage_key is None
            ):
                raise PermissionError(
                    "Processing request does not match a managed registered asset."
                )
            decision = self._evaluate_rights_in_transaction(
                connection,
                use=use,
                decision_id=str(uuid4()),
                principal=context.principal,
            )
            if (
                decision.copyright_state != RightsState.CLEARED
                or decision.platform_rights_state != RightsState.CLEARED
            ):
                raise PermissionError(
                    "Current rights do not authorize this processing request."
                )
            now = datetime.now(UTC).isoformat()
            connection.execute(
                """
                INSERT INTO content_processing_jobs (
                    job_id, owner_id, session_id, command_id, asset_id,
                    asset_sha256, target_context, rights_use_json, state,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    context.principal,
                    context.session_id,
                    command_id,
                    asset.asset_id,
                    asset.content_sha256,
                    target_context.value,
                    self._json(use.model_dump(mode="json")),
                    ProcessingJobState.QUEUED.value,
                    now,
                    now,
                ),
            )
            self._insert_processing_event(
                connection,
                job_id=job_id,
                from_state=None,
                to_state=ProcessingJobState.QUEUED,
                actor=context.principal,
                reason="authorized_processing_request",
                now=now,
            )
            return job_id, "queued", {
                "asset_id": asset.asset_id,
                "asset_sha256": asset.content_sha256,
                "rights_decision_id": decision.decision_id,
                "target_context": target_context.value,
            }

        self._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=job_id,
            operation=operation,
        )
        job = self.store.get_processing_job(job_id)
        if job is None:
            with self.store._lock:
                command = self.store._connection.execute(
                    """
                    SELECT result_id FROM content_commands
                    WHERE principal = ? AND action = ? AND command_id = ?
                    """,
                    (context.principal, action, command_id),
                ).fetchone()
            job = (
                None
                if command is None
                else self.store.get_processing_job(command["result_id"])
            )
        if job is None:
            raise RuntimeError("Authorized processing job was not durably recorded.")
        return job

    def _finish_processing_job(
        self,
        job_id: str,
        *,
        state: ProcessingJobState,
        reason: str,
        context: AuthorizationContext,
        output: tuple[str, str, int] | None = None,
    ) -> ProcessingJobState:
        now = datetime.now(UTC).isoformat()
        action = f"content_processing_{state.value}"
        fingerprint = ContentStore._fingerprint(
            action,
            {"job_id": job_id, "state": state.value, "reason": reason},
        )
        with self.store._transaction() as connection:
            row = connection.execute(
                "SELECT state, owner_id FROM content_processing_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None or row["owner_id"] != context.principal:
                raise PermissionError("Processing job is outside the authorized account.")
            current = ProcessingJobState(row["state"])
            if current != ProcessingJobState.RUNNING:
                return current
            if output is not None:
                output_key, output_hash, output_size = output
                connection.execute(
                    """
                    INSERT INTO content_processing_outputs (
                        job_id, output_key, content_sha256, byte_size, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (job_id, output_key, output_hash, output_size, now),
                )
            connection.execute(
                """
                UPDATE content_processing_jobs
                SET state = ?, updated_at = ?
                WHERE job_id = ? AND state = ?
                """,
                (
                    state.value,
                    now,
                    job_id,
                    ProcessingJobState.RUNNING.value,
                ),
            )
            self._insert_processing_event(
                connection,
                job_id=job_id,
                from_state=current,
                to_state=state,
                actor=context.principal,
                reason=reason,
                now=now,
            )
            self.store._insert_audit(
                connection,
                context,
                action,
                job_id,
                state.value,
                fingerprint,
                {"reason": reason},
                now,
            )
            return state

    def run_processing_job(
        self,
        job_id: str,
        *,
        context: AuthorizationContext,
        command_id: str,
    ) -> ContentProcessingJob:
        if self.content_storage is None or self.processor is None:
            raise RuntimeError("Synthetic processing is not configured.")
        action = "content_run_processing_job"
        payload = {"job_id": job_id}
        content: bytes | None = None
        did_claim = False

        def claim(connection: sqlite3.Connection):
            nonlocal content, did_claim
            row = connection.execute(
                "SELECT * FROM content_processing_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None or row["owner_id"] != context.principal:
                raise PermissionError("Processing job is outside the authorized account.")
            transcription_request = connection.execute(
                "SELECT 1 FROM content_transcription_requests WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if transcription_request is not None:
                raise PermissionError(
                    "Transcription jobs must use the authorized transcription worker."
                )
            if ProcessingJobState(row["state"]) != ProcessingJobState.QUEUED:
                raise PermissionError("Processing job is not available for dispatch.")
            job = self.store._load_processing_job(row)
            asset = self._asset_in_transaction(connection, job.asset_id)
            if (
                asset is None
                or asset.owner_id != context.principal
                or asset.content_sha256 != job.asset_sha256
                or asset.storage_key is None
                or job.rights_use.expected_asset_sha256 != asset.content_sha256
            ):
                raise RuntimeError("Processing job asset linkage is inconsistent.")
            try:
                content = self.content_storage.read_verified_input(
                    asset.storage_key,
                    asset.content_sha256,
                )
            except Exception as exc:
                now = datetime.now(UTC).isoformat()
                connection.execute(
                    """
                    UPDATE content_processing_jobs
                    SET state = ?, updated_at = ?
                    WHERE job_id = ? AND state = ?
                    """,
                    (
                        ProcessingJobState.FAILED.value,
                        now,
                        job_id,
                        ProcessingJobState.QUEUED.value,
                    ),
                )
                did_claim = True
                self._insert_processing_event(
                    connection,
                    job_id=job_id,
                    from_state=ProcessingJobState.QUEUED,
                    to_state=ProcessingJobState.FAILED,
                    actor=context.principal,
                    reason=f"managed_input_unavailable_or_invalid:{type(exc).__name__}",
                    now=now,
                )
                return job_id, "input_invalid", {
                    "failure": type(exc).__name__,
                }
            decision = self._evaluate_rights_in_transaction(
                connection,
                use=job.rights_use,
                decision_id=str(uuid4()),
                principal=context.principal,
            )
            if (
                decision.copyright_state != RightsState.CLEARED
                or decision.platform_rights_state != RightsState.CLEARED
            ):
                now = datetime.now(UTC).isoformat()
                connection.execute(
                    """
                    UPDATE content_processing_jobs
                    SET state = ?, updated_at = ?
                    WHERE job_id = ? AND state = ?
                    """,
                    (
                        ProcessingJobState.FAILED.value,
                        now,
                        job_id,
                        ProcessingJobState.QUEUED.value,
                    ),
                )
                did_claim = True
                self._insert_processing_event(
                    connection,
                    job_id=job_id,
                    from_state=ProcessingJobState.QUEUED,
                    to_state=ProcessingJobState.FAILED,
                    actor=context.principal,
                    reason="rights_revalidation_denied_before_dispatch",
                    now=now,
                )
                return job_id, "rights_denied", {
                    "rights_decision_id": decision.decision_id,
                    "reason_codes": list(decision.reason_codes),
                }
            now = datetime.now(UTC).isoformat()
            connection.execute(
                """
                UPDATE content_processing_jobs
                SET state = ?, updated_at = ?
                WHERE job_id = ? AND state = ?
                """,
                (
                    ProcessingJobState.RUNNING.value,
                    now,
                    job_id,
                    ProcessingJobState.QUEUED.value,
                ),
            )
            self._insert_processing_event(
                connection,
                job_id=job_id,
                from_state=ProcessingJobState.QUEUED,
                to_state=ProcessingJobState.RUNNING,
                actor=context.principal,
                reason="rights_revalidated_and_worker_claimed",
                now=now,
            )
            did_claim = True
            return job_id, "running", {
                "rights_decision_id": decision.decision_id,
            }

        self._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=job_id,
            operation=claim,
        )
        current = self.store.get_processing_job(job_id)
        if current is None:
            raise RuntimeError("Processing job disappeared after worker claim.")
        if not did_claim:
            return current
        if current.state != ProcessingJobState.RUNNING:
            if current.state == ProcessingJobState.FAILED:
                raise RuntimeError("Processing job failed before processor dispatch.")
            raise PermissionError("Processing job was not claimed for dispatch.")
        if content is None:
            raise RuntimeError("Verified processing input was not loaded.")

        started = time.monotonic()
        try:
            result = self.processor.process(
                content,
                max_output_bytes=LocalTemporaryContentStore.MAX_OUTPUT_BYTES,
            )
            elapsed = time.monotonic() - started
            if elapsed > self.MAX_PROCESSING_SECONDS:
                self._finish_processing_job(
                    job_id,
                    state=ProcessingJobState.UNCERTAIN,
                    reason="synthetic_processor_exceeded_time_limit",
                    context=context,
                )
                raise TimeoutError("Synthetic processor exceeded its time limit.")
        except TimeoutError:
            self._finish_processing_job(
                job_id,
                state=ProcessingJobState.UNCERTAIN,
                reason="synthetic_processor_timeout_outcome_unknown",
                context=context,
            )
            raise
        except Exception as primary:
            try:
                self._finish_processing_job(
                    job_id,
                    state=ProcessingJobState.FAILED,
                    reason="synthetic_processor_failed",
                    context=context,
                )
            except Exception as persistence:
                raise RuntimeError(
                    f"Processor failed ({primary}); failure status could not be "
                    f"durably recorded ({persistence})."
                ) from primary
            raise
        if (
            not isinstance(result, bytes)
            or len(result) > LocalTemporaryContentStore.MAX_OUTPUT_BYTES
        ):
            self._finish_processing_job(
                job_id,
                state=ProcessingJobState.FAILED,
                reason="processor_output_invalid_or_oversized",
                context=context,
            )
            raise ValueError("Synthetic processor returned invalid or oversized output.")
        output_key, output_hash, output_size = self.content_storage.put_output(
            job_id,
            result,
        )
        try:
            terminal = self._finish_processing_job(
                job_id,
                state=ProcessingJobState.SUCCEEDED,
                reason="synthetic_processing_completed",
                context=context,
                output=(output_key, output_hash, output_size),
            )
        except Exception as primary:
            try:
                self.content_storage.delete_output(output_key)
            except Exception as cleanup:
                raise RuntimeError(
                    f"Output finalization failed ({primary}); unreferenced output "
                    f"cleanup failed ({cleanup})."
                ) from primary
            raise
        if terminal != ProcessingJobState.SUCCEEDED:
            try:
                self.content_storage.delete_output(output_key)
            except Exception as cleanup:
                raise RuntimeError(
                    f"Processing was cancelled as {terminal.value}; output cleanup "
                    f"failed ({cleanup})."
                ) from cleanup
        completed = self.store.get_processing_job(job_id)
        if completed is None:
            raise RuntimeError("Processing job disappeared after execution.")
        return completed

    def cancel_processing_job(
        self,
        job_id: str,
        *,
        context: AuthorizationContext,
        command_id: str,
    ) -> ContentProcessingJob:
        action = "content_cancel_processing_job"
        payload = {"job_id": job_id}

        def operation(connection: sqlite3.Connection):
            row = connection.execute(
                "SELECT state, owner_id FROM content_processing_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None or row["owner_id"] != context.principal:
                raise PermissionError("Processing job is outside the authorized account.")
            current = ProcessingJobState(row["state"])
            target = (
                ProcessingJobState.CANCELLED
                if current == ProcessingJobState.QUEUED
                else ProcessingJobState.UNCERTAIN
                if current == ProcessingJobState.RUNNING
                else current
            )
            reason = (
                "cancelled_before_dispatch"
                if target == ProcessingJobState.CANCELLED
                else "cancellation_requested_during_execution_outcome_unknown"
                if target == ProcessingJobState.UNCERTAIN
                else "terminal_job_cancellation_noop"
            )
            if target != current:
                now = datetime.now(UTC).isoformat()
                connection.execute(
                    """
                    UPDATE content_processing_jobs
                    SET state = ?, updated_at = ?
                    WHERE job_id = ? AND state = ?
                    """,
                    (target.value, now, job_id, current.value),
                )
                self._insert_processing_event(
                    connection,
                    job_id=job_id,
                    from_state=current,
                    to_state=target,
                    actor=context.principal,
                    reason=reason,
                    now=now,
                )
            return job_id, target.value, {"reason": reason}

        self._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=job_id,
            operation=operation,
        )
        job = self.store.get_processing_job(job_id)
        if job is None:
            raise RuntimeError("Processing job disappeared after cancellation.")
        return job

    def get_processing_output_bytes(
        self,
        job_id: str,
        *,
        context: AuthorizationContext,
    ) -> bytes:
        if self.content_storage is None:
            raise RuntimeError("Managed content storage is not configured.")
        action = "content_read_processing_output"
        payload = {"job_id": job_id}

        def operation(connection: sqlite3.Connection):
            row = connection.execute(
                """
                SELECT j.owner_id, j.state, o.output_key, o.content_sha256
                FROM content_processing_jobs j
                LEFT JOIN content_processing_outputs o ON o.job_id = j.job_id
                WHERE j.job_id = ?
                """,
                (job_id,),
            ).fetchone()
            if (
                row is None
                or row["owner_id"] != context.principal
                or row["state"] != ProcessingJobState.SUCCEEDED.value
                or row["output_key"] is None
            ):
                raise PermissionError("A successful owned processing output is required.")
            return row["output_key"], "output_read_authorized", {
                "content_sha256": row["content_sha256"],
            }

        self._run(
            context,
            action=action,
            payload=payload,
            command_id=str(uuid4()),
            subject_id=job_id,
            operation=operation,
        )
        output = self.store.get_processing_output(job_id)
        if output is None:
            raise RuntimeError("Authorized output metadata disappeared.")
        return self.content_storage.read_output(
            output.output_key,
            output.content_sha256,
        )

    def recover_processing_storage(self) -> None:
        if self.content_storage is None:
            return
        with self.store._transaction() as connection:
            active_clip_renders = frozenset(
                row["render_id"]
                for row in connection.execute(
                    """
                    SELECT render_id FROM content_clip_renders
                    WHERE state IN (?, ?)
                    """,
                    (
                        ProcessingJobState.RUNNING.value,
                        ProcessingJobState.UNCERTAIN.value,
                    ),
                ).fetchall()
            )
            self.content_storage.cleanup_orphans(
                self.store.processing_output_keys(),
                active_clip_renders,
            )

    def create_content_package(
        self,
        package: ContentPackage,
        *,
        context: AuthorizationContext,
        command_id: str,
    ) -> ContentPackage:
        action = "content_create_package"
        payload = package.model_dump(mode="json")

        def operation(connection: sqlite3.Connection):
            if package.owner_id != context.principal:
                raise PermissionError("Content package owner must be the authenticated principal.")
            connection.execute(
                """
                INSERT INTO content_packages(package_id, owner_id, created_at)
                VALUES (?, ?, ?)
                """,
                (
                    package.package_id,
                    package.owner_id,
                    package.created_at.isoformat(),
                ),
            )
            return package.package_id, "created", {
                "owner_id": package.owner_id,
            }

        self._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=package.package_id,
            operation=operation,
        )
        stored = self.store.get_package(package.package_id)
        if stored is None:
            raise RuntimeError("Content package was not durably stored.")
        return stored

    def create_content_package_version(
        self,
        version: ContentPackageVersion,
        *,
        artifact_stream: BinaryIO,
        context: AuthorizationContext,
        command_id: str,
    ) -> ContentPackageVersion:
        action = "content_create_package_version"
        payload = version.model_dump(mode="json")
        self._authorize(context, action=action, payload=payload)
        package = self.store.get_package(version.package_id)
        if package is None or package.owner_id != context.principal:
            raise PermissionError("Package version is outside the caller's account.")
        if (
            self._hash_stream(artifact_stream, label="Package artifact")
            != version.artifact_sha256
        ):
            raise ValueError("Artifact bytes do not match the declared immutable hash.")

        def operation(connection: sqlite3.Connection):
            package = connection.execute(
                "SELECT owner_id FROM content_packages WHERE package_id = ?",
                (version.package_id,),
            ).fetchone()
            if package is None or package["owner_id"] != context.principal:
                raise PermissionError("Package version is outside the caller's account.")
            self._record_expired_agreements(
                connection,
                principal=context.principal,
            )
            previous = connection.execute(
                """
                SELECT MAX(version) AS max_version FROM content_package_versions
                WHERE package_id = ?
                """,
                (version.package_id,),
            ).fetchone()["max_version"]
            if version.version != (previous or 0) + 1:
                raise ValueError("Content package versions must be consecutive.")

            evaluations: list[RightsEvaluation] = []
            for asset_use in version.asset_uses:
                use = asset_use.use
                asset = self._asset_in_transaction(connection, use.asset_id)
                if asset is None or asset.owner_id != context.principal:
                    raise PermissionError("Package source asset is outside the caller's account.")
                result = self.policy.evaluate(
                    decision_id=str(uuid4()),
                    asset=asset,
                    use=use,
                    records=self._grant_records(connection, use.asset_id),
                    evidence_states=ContentStore.evidence_states(
                        connection,
                        use.asset_id,
                    ),
                )
                evaluations.append(result)
            denied = [
                result
                for result in evaluations
                if result.copyright_state != RightsState.CLEARED
                or result.platform_rights_state != RightsState.CLEARED
            ]
            if denied:
                return version.package_id, "blocked", {
                    "package_version": version.version,
                    "reason_codes": sorted(
                        {
                            reason
                            for result in denied
                            for reason in result.reason_codes
                        }
                    ),
                }

            connection.execute(
                """
                INSERT INTO content_package_versions (
                    package_id, version, platform, account_id, artifact_sha256,
                    metadata_sha256, metadata_json, asset_uses_json, review_state,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version.package_id,
                    version.version,
                    version.platform.value,
                    version.account_id,
                    version.artifact_sha256,
                    version.metadata_sha256,
                    self._json(version.metadata),
                    self._json(
                        [item.model_dump(mode="json") for item in version.asset_uses]
                    ),
                    ReviewState.PENDING.value,
                    version.created_at.isoformat(),
                ),
            )
            for asset_use in version.asset_uses:
                connection.execute(
                    """
                    INSERT INTO content_package_assets (
                        package_id, package_version, asset_id, expected_asset_sha256
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        version.package_id,
                        version.version,
                        asset_use.use.asset_id,
                        asset_use.use.expected_asset_sha256,
                    ),
                )
            now = datetime.now(UTC).isoformat()
            connection.execute(
                """
                INSERT INTO content_review_events (
                    package_id, package_version, state, actor, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    version.package_id,
                    version.version,
                    ReviewState.PENDING.value,
                    context.principal,
                    "new_immutable_content_version",
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO content_publication_states (
                    package_id, package_version, platform, account_id, state, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    version.package_id,
                    version.version,
                    version.platform.value,
                    version.account_id,
                    PublicationState.NOT_PUBLISHED.value,
                    now,
                ),
            )
            if previous is not None:
                self._invalidate_approvals_for_package_version(
                    connection,
                    version.package_id,
                    previous,
                    actor=context.principal,
                    reason="content_version_changed",
                )
            return f"{version.package_id}:{version.version}", "created", {
                "package_version": version.version,
                "artifact_sha256": version.artifact_sha256,
                "metadata_sha256": version.metadata_sha256,
                "asset_ids": [item.use.asset_id for item in version.asset_uses],
            }

        self._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=version.package_id,
            operation=operation,
        )
        stored = self.store.get_package_version(version.package_id, version.version)
        if stored is None:
            raise PermissionError(
                "Package version was blocked because source rights are not cleared."
            )
        return stored

    def approve_content_version(
        self,
        *,
        package_id: str,
        package_version: int,
        artifact_sha256: str,
        metadata_sha256: str,
        expires_at: datetime,
        context: AuthorizationContext,
        command_id: str,
    ) -> ReviewApproval:
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("Review approval expiry must include a timezone.")
        expires_at = expires_at.astimezone(UTC)
        now = datetime.now(UTC)
        if not now < expires_at <= now + self.MAX_REVIEW_APPROVAL_TTL:
            raise ValueError("Review approval must expire within 24 hours.")
        action = "content_approve_version"
        payload = {
            "package_id": package_id,
            "package_version": package_version,
            "artifact_sha256": artifact_sha256,
            "metadata_sha256": metadata_sha256,
            "permitted_action": RightsAction.PUBLISH.value,
            "expires_at": expires_at.isoformat(),
        }
        approval_id = str(uuid4())
        nonce = str(uuid4())

        def operation(connection: sqlite3.Connection):
            if context.principal not in self.reviewer_principals:
                return approval_id, "blocked", {
                    "reason": "principal_not_authorized_for_human_review",
                }
            row = connection.execute(
                """
                SELECT v.*, p.owner_id FROM content_package_versions v
                JOIN content_packages p ON p.package_id = v.package_id
                WHERE v.package_id = ? AND v.version = ?
                """,
                (package_id, package_version),
            ).fetchone()
            if (
                row is None
                or row["owner_id"] != context.principal
                or row["artifact_sha256"] != artifact_sha256
                or row["metadata_sha256"] != metadata_sha256
            ):
                return approval_id, "blocked", {
                    "reason": "content_version_or_owner_mismatch",
                }
            self._record_expired_agreements(
                connection,
                principal=context.principal,
            )
            asset_uses = json.loads(row["asset_uses_json"])
            evaluations: list[RightsEvaluation] = []
            rights_expiries: list[datetime] = []
            for item in asset_uses:
                use = RightsUse.model_validate(item["use"])
                asset = self._asset_in_transaction(connection, use.asset_id)
                if asset is None or asset.owner_id != context.principal:
                    return approval_id, "blocked", {
                        "reason": "source_asset_missing_or_cross_account",
                        "asset_id": use.asset_id,
                    }
                grant_records = self._grant_records(connection, use.asset_id)
                result = self.policy.evaluate(
                    decision_id=str(uuid4()),
                    asset=asset,
                    use=use,
                    records=grant_records,
                    evidence_states=ContentStore.evidence_states(
                        connection,
                        use.asset_id,
                    ),
                )
                evaluations.append(result)
                rights_expiries.extend(
                    record.grant.terms.valid_until
                    for record in grant_records
                    if record.grant.grant_id in result.applicable_grant_ids
                )
            if any(
                result.copyright_state != RightsState.CLEARED
                or result.platform_rights_state != RightsState.CLEARED
                for result in evaluations
            ):
                return approval_id, "blocked", {
                    "reason": "rights_not_cleared",
                    "reason_codes": sorted(
                        {
                            reason
                            for result in evaluations
                            for reason in result.reason_codes
                        }
                    ),
                }
            if not rights_expiries or expires_at > min(rights_expiries):
                return approval_id, "blocked", {
                    "reason": "approval_expiry_exceeds_verified_rights",
                }
            rights_snapshot = hashlib.sha256(
                self._json(
                    [
                        {
                            "asset_id": result.asset_id,
                            "decision_id": result.decision_id,
                            "copyright_state": result.copyright_state.value,
                            "platform_rights_state": (
                                result.platform_rights_state.value
                            ),
                            "grant_ids": list(result.applicable_grant_ids),
                            "policy_version": result.policy_version,
                        }
                        for result in evaluations
                    ]
                ).encode("utf-8")
            ).hexdigest()
            created_at = datetime.now(UTC).isoformat()
            connection.execute(
                """
                INSERT INTO review_approvals (
                    approval_id, package_id, package_version, artifact_sha256,
                    metadata_sha256, platform, account_id, permitted_action,
                    principal, session_id, rights_snapshot_sha256, expires_at,
                    nonce, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    package_id,
                    package_version,
                    artifact_sha256,
                    metadata_sha256,
                    row["platform"],
                    row["account_id"],
                    RightsAction.PUBLISH.value,
                    context.principal,
                    context.session_id,
                    rights_snapshot,
                    expires_at.isoformat(),
                    nonce,
                    created_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO approval_events (
                    approval_id, state, actor, reason, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    ReviewState.APPROVED.value,
                    context.principal,
                    "human_review_of_exact_version",
                    created_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO content_review_events (
                    package_id, package_version, state, actor, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    package_id,
                    package_version,
                    ReviewState.APPROVED.value,
                    context.principal,
                    "human_review_approved_exact_artifact",
                    created_at,
                ),
            )
            return approval_id, "review_approved_not_publish_authorized", {
                "package_version": package_version,
                "artifact_sha256": artifact_sha256,
                "metadata_sha256": metadata_sha256,
                "platform": row["platform"],
                "account_id": row["account_id"],
                "permitted_action": RightsAction.PUBLISH.value,
                "rights_snapshot_sha256": rights_snapshot,
                "expires_at": expires_at.isoformat(),
                "publishing_authorization": PlatformDecisionState.NOT_AUTHORIZED.value,
                "platform_compliance": PlatformDecisionState.UNKNOWN.value,
            }

        self._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=approval_id,
            operation=operation,
        )
        approval = self.store.get_approval(approval_id)
        if approval is None:
            raise PermissionError("Review approval was denied by current rights checks.")
        return approval

    @staticmethod
    def _invalidate_lifecycle_for_asset(
        connection: sqlite3.Connection,
        asset_id: str,
        *,
        actor: str,
        reason: str,
    ) -> None:
        rows = connection.execute(
            """
            SELECT intake_id, state
            FROM content_lifecycle
            WHERE asset_id = ? AND state IN (?, ?)
            """,
            (
                asset_id,
                ContentLifecycleState.RIGHTS_CLEARED.value,
                ContentLifecycleState.READY_FOR_ANALYSIS.value,
            ),
        ).fetchall()
        now = datetime.now(UTC).isoformat()
        for row in rows:
            connection.execute(
                """
                UPDATE content_lifecycle
                SET state = ?, rights_decision_id = NULL, updated_at = ?
                WHERE intake_id = ?
                """,
                (
                    ContentLifecycleState.RIGHTS_PENDING.value,
                    now,
                    row["intake_id"],
                ),
            )
            connection.execute(
                """
                INSERT INTO content_lifecycle_events (
                    intake_id, from_state, to_state, actor, reason,
                    rights_decision_id, created_at
                ) VALUES (?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    row["intake_id"],
                    row["state"],
                    ContentLifecycleState.RIGHTS_PENDING.value,
                    actor,
                    reason,
                    now,
                ),
            )

    def _invalidate_approvals_for_asset(
        self,
        connection: sqlite3.Connection,
        asset_id: str,
        *,
        actor: str,
        reason: str,
    ) -> None:
        rows = connection.execute(
            """
            SELECT a.approval_id, a.package_id, a.package_version
            FROM review_approvals a
            JOIN content_package_assets p
              ON p.package_id = a.package_id
             AND p.package_version = a.package_version
            WHERE p.asset_id = ?
              AND (
                SELECT state FROM approval_events e
                WHERE e.approval_id = a.approval_id
                ORDER BY e.event_id DESC LIMIT 1
              ) = ?
            """,
            (asset_id, ReviewState.APPROVED.value),
        ).fetchall()
        now = datetime.now(UTC).isoformat()
        for row in rows:
            connection.execute(
                """
                INSERT INTO approval_events (
                    approval_id, state, actor, reason, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["approval_id"],
                    ReviewState.INVALIDATED.value,
                    actor,
                    reason,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO content_review_events (
                    package_id, package_version, state, actor, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["package_id"],
                    row["package_version"],
                    ReviewState.INVALIDATED.value,
                    actor,
                    reason,
                    now,
                ),
            )

    def _invalidate_approvals_for_package_version(
        self,
        connection: sqlite3.Connection,
        package_id: str,
        version: int,
        *,
        actor: str,
        reason: str,
    ) -> None:
        rows = connection.execute(
            """
            SELECT approval_id FROM review_approvals
            WHERE package_id = ? AND package_version = ?
              AND (
                SELECT state FROM approval_events e
                WHERE e.approval_id = review_approvals.approval_id
                ORDER BY e.event_id DESC LIMIT 1
              ) = ?
            """,
            (package_id, version, ReviewState.APPROVED.value),
        ).fetchall()
        now = datetime.now(UTC).isoformat()
        for row in rows:
            connection.execute(
                """
                INSERT INTO approval_events (
                    approval_id, state, actor, reason, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["approval_id"],
                    ReviewState.INVALIDATED.value,
                    actor,
                    reason,
                    now,
                ),
            )
        if rows:
            connection.execute(
                """
                INSERT INTO content_review_events (
                    package_id, package_version, state, actor, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    package_id,
                    version,
                    ReviewState.INVALIDATED.value,
                    actor,
                    reason,
                    now,
                ),
            )


__all__ = [
    "ContentRightsService",
    "ContentStore",
    "IdempotencyConflictError",
]
