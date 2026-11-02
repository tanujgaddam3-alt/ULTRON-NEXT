from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from ultron.domain.content import (
    ContentClipRender,
    ContentPackageVersion,
    ProcessingJobState,
    PublishingApprovalState,
    PublishingAttempt,
    PublishingAttemptCode,
    PublishingAttemptState,
    PublishingJob,
    PublishingJobState,
    PublishingReleaseApproval,
    PublishingVisibility,
    RightsAction,
    RightsEvaluation,
    RightsState,
    RightsUse,
)
from ultron.security.identity.authorization import AuthorizationContext

from .rendering import ContentClipRenderingService
from .service import ContentRightsService
from .store import IdempotencyConflictError, PublishingAttemptConflictError


class MockAdapterOutcome(StrEnum):
    ACCEPTED = "accepted"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class MockDispatchFailure(RuntimeError):
    """A deterministic mock-adapter rejection."""


class MockDispatchUncertain(RuntimeError):
    """A deterministic mock-adapter outcome that cannot be established."""


class PublishingAdapter(Protocol):
    def dispatch(self, job: PublishingJob) -> None: ...


class MockPublishingAdapter:
    """Deterministic local-only adapter; it never performs network I/O."""

    def __init__(
        self,
        outcome: MockAdapterOutcome = MockAdapterOutcome.ACCEPTED,
    ) -> None:
        self.outcome = outcome
        self.dispatch_count = 0
        self.dispatched_job_ids: list[str] = []
        self._lock = threading.Lock()

    def dispatch(self, job: PublishingJob) -> None:
        with self._lock:
            self.dispatch_count += 1
            self.dispatched_job_ids.append(job.job_id)
            outcome = self.outcome
        if outcome == MockAdapterOutcome.FAILED:
            raise MockDispatchFailure("Mock adapter rejected the dispatch.")
        if outcome == MockAdapterOutcome.UNCERTAIN:
            raise MockDispatchUncertain("Mock adapter outcome is uncertain.")


class PublishingService:
    MAX_APPROVAL_TTL = timedelta(hours=24)

    def __init__(
        self,
        *,
        content_rights: ContentRightsService,
        reviewer_rights: ContentRightsService,
        operator_rights: ContentRightsService,
        rendering: ContentClipRenderingService,
        adapter: MockPublishingAdapter,
        operator_principals: frozenset[str],
    ) -> None:
        if type(content_rights) is not ContentRightsService:
            raise TypeError("Publishing requires the configured content-rights service.")
        if (
            type(reviewer_rights) is not ContentRightsService
            or type(operator_rights) is not ContentRightsService
        ):
            raise TypeError("Release reviewers and operators require authenticated services.")
        if (
            reviewer_rights.store is not content_rights.store
            or operator_rights.store is not content_rights.store
        ):
            raise ValueError("Publishing authorization services must share one content store.")
        if type(rendering) is not ContentClipRenderingService:
            raise TypeError("Publishing requires the authorized clip-rendering service.")
        if type(adapter) is not MockPublishingAdapter:
            raise TypeError("Mission 12 permits only the local mock publishing adapter.")
        operators = frozenset(
            principal.strip()
            for principal in operator_principals
            if isinstance(principal, str) and principal.strip()
        )
        if not operators:
            raise ValueError("At least one publishing kill-switch operator is required.")
        self.content_rights = content_rights
        self.reviewer_rights = reviewer_rights
        self.operator_rights = operator_rights
        self.store = content_rights.store
        self.rendering = rendering
        self.adapter = adapter
        self.operator_principals = operators
        self.store._seal_authorization_authorities()

    @staticmethod
    def _canonical(value: object) -> str:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @classmethod
    def _sha256(cls, value: object) -> str:
        return hashlib.sha256(cls._canonical(value).encode("utf-8")).hexdigest()

    @staticmethod
    def _normalized_use(use: RightsUse) -> dict[str, object]:
        value = use.model_dump(mode="json")
        value["actions"] = sorted(action.value for action in use.actions)
        return value

    @classmethod
    def _uses_json(cls, uses: tuple[RightsUse, ...]) -> str:
        return cls._canonical([cls._normalized_use(use) for use in uses])

    @staticmethod
    def _validate_reason(reason: str) -> str:
        reason = reason.strip()
        if not reason or len(reason) > 1000:
            raise ValueError("A bounded publishing audit reason is required.")
        return reason

    def _load_bound_content(
        self,
        *,
        package_id: str,
        package_version: int,
        render_id: str,
        owner_id: str,
        render_context: AuthorizationContext,
    ) -> tuple[ContentPackageVersion, ContentClipRender, bytes]:
        if render_context.principal != owner_id:
            raise PermissionError("Rendered output must be read by its owning principal.")
        version = self.store.get_package_version(package_id, package_version)
        render = self.store.get_clip_render(render_id)
        if (
            version is None
            or render is None
            or render.owner_id != owner_id
            or render.state != ProcessingJobState.SUCCEEDED
            or render.output_key is None
            or render.output_sha256 is None
            or render.output_byte_size is None
            or version.platform != render.rights_use.platform
            or version.account_id != render.rights_use.account_id
            or version.artifact_sha256 != render.output_sha256
        ):
            raise PermissionError(
                "A successful, matching package version and rendered output are required."
            )
        source_uses = {item.use.asset_id: item.use for item in version.asset_uses}
        package_source_use = source_uses.get(render.asset_id)
        if (
            package_source_use is None
            or package_source_use.expected_asset_sha256 != render.asset_sha256
            or render.rights_use.asset_id != render.asset_id
            or render.rights_use.expected_asset_sha256 != render.asset_sha256
            or RightsAction.CREATE_DERIVATIVE not in render.rights_use.actions
        ):
            raise PermissionError(
                "Package and render source provenance do not match the registered asset."
            )
        output = self.rendering.get_clip_output_bytes(
            render_id,
            context=render_context,
        )
        if (
            hashlib.sha256(output).hexdigest() != render.output_sha256
            or len(output) != render.output_byte_size
        ):
            raise PermissionError("Rendered output integrity verification failed.")
        return version, render, output

    def create_attempt(
        self,
        *,
        job_id: str,
        context: AuthorizationContext,
        render_context: AuthorizationContext,
        command_id: str,
    ) -> PublishingAttempt:
        job = self.store.get_publishing_job(job_id)
        if job is None or context.principal != job.owner_id:
            raise PermissionError("Publishing job is outside the authenticated account.")
        self._load_bound_content(
            package_id=job.package_id,
            package_version=job.package_version,
            render_id=job.render_id,
            owner_id=job.owner_id,
            render_context=render_context,
        )
        action = "content_create_publishing_attempt"
        payload = {"job_id": job_id}

        def operation(connection: sqlite3.Connection):
            row = connection.execute(
                "SELECT * FROM content_publishing_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if (
                row is None
                or row["owner_id"] != context.principal
                or row["state"] != PublishingJobState.QUEUED.value
            ):
                raise PermissionError("Only an owned, queued job can start an attempt.")
            approval = connection.execute(
                """
                SELECT a.*,
                    (SELECT e.state FROM content_publishing_approval_events e
                     WHERE e.approval_id = a.approval_id
                     ORDER BY e.sequence DESC LIMIT 1) AS latest_state
                FROM content_publishing_approvals a
                WHERE a.job_id = ?
                ORDER BY a.created_at DESC, a.rowid DESC LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            if (
                approval is None
                or approval["latest_state"] != PublishingApprovalState.APPROVED.value
                or approval["request_fingerprint"] != row["request_fingerprint"]
                or approval["approved_by"] == approval["request_principal"]
                or approval["package_id"] != row["package_id"]
                or approval["package_version"] != row["package_version"]
                or approval["output_sha256"] != row["output_sha256"]
                or approval["metadata_sha256"] != row["metadata_sha256"]
                or approval["platform"] != row["platform"]
                or approval["account_id"] != row["account_id"]
                or approval["visibility"] != row["visibility"]
                or approval["intended_uses_json"] != row["intended_uses_json"]
                or approval["synthetic_media_disclosed"]
                != row["synthetic_media_disclosed"]
                or datetime.fromisoformat(approval["expires_at"]) <= datetime.now(UTC)
            ):
                raise PermissionError("A current exact release approval is required.")
            if self.store.is_publishing_kill_switch_enabled():
                raise PermissionError("Publishing is blocked by the kill switch.")
            uses = tuple(
                RightsUse.model_validate(item)
                for item in json.loads(row["intended_uses_json"])
            )
            decisions, snapshot = self._evaluate_uses(
                connection,
                uses=uses,
                principal=row["owner_id"],
            )
            if snapshot != approval["rights_snapshot_sha256"]:
                raise PermissionError("Rights changed after release approval.")
            attempt = self.store._create_publishing_attempt_in_transaction(
                job_id,
                command_id=command_id,
                actor=context.principal,
                connection=connection,
            )
            return attempt.identity.attempt_id, "publishing_attempt_created", {
                "attempt_id": attempt.identity.attempt_id,
                "attempt_number": attempt.identity.attempt_number,
                "rights_decision_ids": [decision.decision_id for decision in decisions],
            }

        attempt_id = self.content_rights._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=job_id,
            operation=operation,
        )
        attempt = self.store.get_publishing_attempt(attempt_id)
        if attempt is None:
            raise RuntimeError("Publishing attempt committed without a durable record.")
        return attempt

    def record_attempt_initiation_intent(
        self,
        *,
        attempt_id: str,
        expected_revision: int,
        context: AuthorizationContext,
        render_context: AuthorizationContext,
        command_id: str,
    ) -> PublishingAttempt:
        attempt = self.store.get_publishing_attempt(attempt_id)
        if attempt is None or context.principal != attempt.identity.owner_id:
            raise PermissionError("Publishing attempt is outside the authenticated account.")
        job = self.store.get_publishing_job(attempt.identity.job_id)
        if (
            job is None
            or job.owner_id != attempt.identity.owner_id
            or job.account_id != attempt.identity.account_id
            or job.package_id != attempt.identity.package_id
            or job.package_version != attempt.identity.package_version
            or job.output_sha256 != attempt.identity.output_sha256
        ):
            raise PermissionError("Publishing attempt has no matching persisted job.")
        self._load_bound_content(
            package_id=job.package_id,
            package_version=job.package_version,
            render_id=job.render_id,
            owner_id=job.owner_id,
            render_context=render_context,
        )
        action = "content_start_publishing_attempt"
        payload = {
            "attempt_id": attempt_id,
            "expected_revision": expected_revision,
        }

        def operation(connection: sqlite3.Connection):
            row = connection.execute(
                """
                SELECT a.attempt_id AS current_attempt_id,
                    a.owner_id AS attempt_owner_id,
                    a.account_id AS attempt_account_id,
                    a.release_identity AS attempt_release_identity,
                    a.package_id AS attempt_package_id,
                    a.package_version AS attempt_package_version,
                    a.output_sha256 AS attempt_output_sha256,
                    a.revision AS attempt_revision,
                    j.*
                FROM content_publishing_attempts a
                JOIN content_publishing_jobs j ON j.job_id = a.job_id
                WHERE a.attempt_id = ?
                """,
                (attempt_id,),
            ).fetchone()
            if (
                row is None
                or row["attempt_owner_id"] != context.principal
                or row["attempt_owner_id"] != row["owner_id"]
                or row["attempt_account_id"] != row["account_id"]
                or row["attempt_release_identity"] != row["release_identity"]
                or row["attempt_package_id"] != row["package_id"]
                or row["attempt_package_version"] != row["package_version"]
                or row["attempt_output_sha256"] != row["output_sha256"]
            ):
                raise PermissionError("Attempt, owner, job, or release binding changed.")
            if row["attempt_revision"] != expected_revision:
                raise PublishingAttemptConflictError(
                    "Publishing attempt revision is stale."
                )
            if row["state"] != PublishingJobState.QUEUED.value:
                raise PublishingAttemptConflictError(
                    "Publishing job is not queued for initiation."
                )

            approval = connection.execute(
                """
                SELECT a.*,
                    (SELECT e.state FROM content_publishing_approval_events e
                     WHERE e.approval_id = a.approval_id
                     ORDER BY e.sequence DESC LIMIT 1) AS latest_state
                FROM content_publishing_approvals a
                WHERE a.job_id = ?
                ORDER BY a.created_at DESC, a.rowid DESC LIMIT 1
                """,
                (attempt.identity.job_id,),
            ).fetchone()
            if (
                approval is None
                or approval["latest_state"] != PublishingApprovalState.APPROVED.value
                or approval["approved_by"] == approval["request_principal"]
                or approval["request_fingerprint"] != row["request_fingerprint"]
                or approval["package_id"] != row["package_id"]
                or approval["package_version"] != row["package_version"]
                or approval["output_sha256"] != row["output_sha256"]
                or approval["metadata_sha256"] != row["metadata_sha256"]
                or approval["platform"] != row["platform"]
                or approval["account_id"] != row["account_id"]
                or approval["visibility"] != row["visibility"]
                or approval["intended_uses_json"] != row["intended_uses_json"]
                or approval["synthetic_media_disclosed"]
                != row["synthetic_media_disclosed"]
                or datetime.fromisoformat(approval["expires_at"]) <= datetime.now(UTC)
            ):
                raise PermissionError("A current exact independent release approval is required.")
            if self.store.is_publishing_kill_switch_enabled():
                raise PermissionError("Publishing is blocked by the kill switch.")
            uses = tuple(
                RightsUse.model_validate(item)
                for item in json.loads(row["intended_uses_json"])
            )
            decisions, snapshot = self._evaluate_uses(
                connection,
                uses=uses,
                principal=row["owner_id"],
            )
            if snapshot != approval["rights_snapshot_sha256"]:
                raise PermissionError("Rights changed after release approval.")
            updated = self.store._transition_publishing_attempt(
                attempt_id,
                expected_revision=expected_revision,
                to_state=PublishingAttemptState.INITIATION_INTENT,
                code=PublishingAttemptCode.INITIATION_AUTHORIZED,
                command_id=command_id,
                actor=context.principal,
                connection=connection,
            )
            return updated.identity.attempt_id, "publishing_attempt_initiation_intent", {
                "attempt_id": updated.identity.attempt_id,
                "revision": updated.revision,
                "rights_decision_ids": [decision.decision_id for decision in decisions],
            }

        result_id = self.content_rights._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=attempt.identity.job_id,
            operation=operation,
        )
        updated_attempt = self.store.get_publishing_attempt(result_id)
        if updated_attempt is None:
            raise RuntimeError("Publishing attempt disappeared after intent recording.")
        return updated_attempt

    @staticmethod
    def _publishing_uses(
        version: ContentPackageVersion,
    ) -> tuple[RightsUse, ...]:
        uses: list[RightsUse] = []
        for item in version.asset_uses:
            use = item.use
            if RightsAction.PUBLISH not in use.actions:
                raise PermissionError("Every source requires explicit publish rights.")
            uses.append(
                use.model_copy(
                    update={
                        "actions": frozenset(
                            {*use.actions, RightsAction.CREATE_DERIVATIVE}
                        )
                    }
                )
            )
        return tuple(uses)

    def _evaluate_uses(
        self,
        connection: sqlite3.Connection,
        *,
        uses: tuple[RightsUse, ...],
        principal: str,
    ) -> tuple[tuple[RightsEvaluation, ...], str]:
        decisions = tuple(
            self.content_rights._evaluate_rights_in_transaction(
                connection,
                use=use,
                principal=principal,
                decision_id=str(uuid4()),
            )
            for use in uses
        )
        if any(
            item.copyright_state != RightsState.CLEARED
            or item.platform_rights_state != RightsState.CLEARED
            for item in decisions
        ):
            raise PermissionError("Current source and derivative rights are not cleared.")
        snapshot = self._sha256(
            [
                {
                    "use": self._normalized_use(decision.use),
                    "copyright_state": decision.copyright_state.value,
                    "platform_rights_state": decision.platform_rights_state.value,
                    "grant_ids": list(decision.applicable_grant_ids),
                    "policy_version": decision.policy_version,
                }
                for decision in decisions
            ]
        )
        return decisions, snapshot

    @staticmethod
    def _insert_job_event(
        connection: sqlite3.Connection,
        *,
        job_id: str,
        previous: PublishingJobState | None,
        target: PublishingJobState,
        actor: str,
        reason: str,
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO content_publishing_job_events (
                job_id, from_state, to_state, actor, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                None if previous is None else previous.value,
                target.value,
                actor,
                reason,
                now,
            ),
        )

    def create_job(
        self,
        *,
        package_id: str,
        package_version: int,
        render_id: str,
        visibility: PublishingVisibility,
        synthetic_media_disclosed: bool,
        idempotency_key: str,
        context: AuthorizationContext,
        render_context: AuthorizationContext,
    ) -> PublishingJob:
        idempotency_key = idempotency_key.strip()
        if not idempotency_key or len(idempotency_key) > 200:
            raise ValueError("A bounded publishing idempotency key is required.")
        payload = {
            "package_id": package_id,
            "package_version": package_version,
            "render_id": render_id,
            "visibility": visibility.value,
            "synthetic_media_disclosed": synthetic_media_disclosed,
            "idempotency_key": idempotency_key,
        }
        self._load_bound_content(
            package_id=package_id,
            package_version=package_version,
            render_id=render_id,
            owner_id=context.principal,
            render_context=render_context,
        )
        version = self.store.get_package_version(package_id, package_version)
        render = self.store.get_clip_render(render_id)
        if version is None or render is None:
            raise PermissionError("Bound content disappeared before publishing request.")
        uses = self._publishing_uses(version)
        now = datetime.now(UTC).isoformat()
        job_id = str(uuid4())

        def operation(connection: sqlite3.Connection):
            decisions, rights_snapshot = self._evaluate_uses(
                connection,
                uses=uses,
                principal=context.principal,
            )
            immutable_binding = {
                "package_id": package_id,
                "package_version": package_version,
                "render_id": render_id,
                "output_sha256": render.output_sha256,
                "metadata_sha256": version.metadata_sha256,
                "platform": version.platform.value,
                "account_id": version.account_id,
                "visibility": visibility.value,
                "synthetic_media_disclosed": synthetic_media_disclosed,
                "intended_uses": [
                    self._normalized_use(use) for use in uses
                ],
                "rights_snapshot_sha256": rights_snapshot,
            }
            request_fingerprint = self._sha256(immutable_binding)
            release_identity = request_fingerprint
            connection.execute(
                """
                INSERT INTO content_publishing_jobs (
                    job_id, owner_id, package_id, package_version, render_id,
                    output_sha256, metadata_sha256, platform, account_id,
                    visibility, intended_uses_json, synthetic_media_disclosed,
                    rights_decision_ids_json, rights_snapshot_sha256,
                    idempotency_key, request_fingerprint, release_identity,
                    state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    context.principal,
                    package_id,
                    package_version,
                    render_id,
                    render.output_sha256,
                    version.metadata_sha256,
                    version.platform.value,
                    version.account_id,
                    visibility.value,
                    self._canonical(
                        [self._normalized_use(use) for use in uses]
                    ),
                    int(synthetic_media_disclosed),
                    self._canonical([item.decision_id for item in decisions]),
                    rights_snapshot,
                    idempotency_key,
                    request_fingerprint,
                    release_identity,
                    PublishingJobState.AWAITING_APPROVAL.value,
                    now,
                    now,
                ),
            )
            self._insert_job_event(
                connection,
                job_id=job_id,
                previous=None,
                target=PublishingJobState.AWAITING_APPROVAL,
                actor=context.principal,
                reason="immutable_publish_request_created",
                now=now,
            )
            return job_id, "publishing_request_created", {
                "package_id": package_id,
                "package_version": package_version,
                "render_id": render_id,
                "output_sha256": render.output_sha256,
                "rights_decision_ids": [item.decision_id for item in decisions],
            }

        try:
            committed_job_id = self.content_rights._run(
                context,
                action="content_create_publishing_job",
                payload=payload,
                command_id=idempotency_key,
                subject_id=render_id,
                operation=operation,
            )
        except sqlite3.IntegrityError as exc:
            raise IdempotencyConflictError(
                "Publishing request duplicates an existing idempotency or release identity."
            ) from exc
        job = self.store.get_publishing_job(committed_job_id)
        if job is None:
            raise RuntimeError("Publishing request committed without a durable job.")
        return job

    def approve_release(
        self,
        *,
        job_id: str,
        expires_at: datetime,
        context: AuthorizationContext,
        render_context: AuthorizationContext,
    ) -> PublishingReleaseApproval:
        if context.principal not in self.reviewer_rights.reviewer_principals:
            raise PermissionError("Principal is not authorized to approve releases.")
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("Release approval expiry must include a timezone.")
        expires_at = expires_at.astimezone(UTC)
        now_dt = datetime.now(UTC)
        if not now_dt < expires_at <= now_dt + self.MAX_APPROVAL_TTL:
            raise ValueError("Release approval must expire within 24 hours.")
        job = self.store.get_publishing_job(job_id)
        if job is None:
            raise PermissionError("Publishing job does not exist.")
        if job.owner_id == context.principal:
            raise PermissionError("Release approval must be independent of the requester.")
        if render_context.principal != job.owner_id:
            raise PermissionError("Only the content owner may authorize output verification.")
        version, render, _ = self._load_bound_content(
            package_id=job.package_id,
            package_version=job.package_version,
            render_id=job.render_id,
            owner_id=job.owner_id,
            render_context=render_context,
        )
        if (
            render.output_sha256 != job.output_sha256
            or version.metadata_sha256 != job.metadata_sha256
            or version.platform != job.platform
            or version.account_id != job.account_id
        ):
            raise PermissionError("Publishing approval binding no longer matches content.")
        payload = {
            "job_id": job_id,
            "request_fingerprint": job.request_fingerprint,
            "expires_at": expires_at.isoformat(),
        }
        approval_id = str(uuid4())
        now = now_dt.isoformat()

        def operation(connection: sqlite3.Connection):
            row = connection.execute(
                "SELECT state FROM content_publishing_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None or row["state"] not in {
                PublishingJobState.AWAITING_APPROVAL.value,
                PublishingJobState.QUEUED.value,
            }:
                raise PermissionError("Only an unblocked publishing request can be approved.")
            if row["state"] == PublishingJobState.QUEUED.value:
                previous_approval = connection.execute(
                    """
                    SELECT a.expires_at,
                        (SELECT state FROM content_publishing_approval_events e
                         WHERE e.approval_id = a.approval_id
                         ORDER BY sequence DESC LIMIT 1) AS latest_state
                    FROM content_publishing_approvals a
                    WHERE a.job_id = ?
                    ORDER BY a.created_at DESC, a.rowid DESC LIMIT 1
                    """,
                    (job_id,),
                ).fetchone()
                if (
                    previous_approval is not None
                    and previous_approval["latest_state"]
                    == PublishingApprovalState.APPROVED.value
                    and datetime.fromisoformat(previous_approval["expires_at"])
                    > datetime.now(UTC)
                ):
                    raise PermissionError("A current release approval already exists.")
            decisions, snapshot = self._evaluate_uses(
                connection,
                uses=job.intended_uses,
                principal=job.owner_id,
            )
            if snapshot != job.rights_snapshot_sha256:
                raise PermissionError("Rights changed after the publishing request.")
            connection.execute(
                """
                INSERT INTO content_publishing_approvals (
                    approval_id, job_id, request_fingerprint, package_id,
                    package_version, output_sha256, metadata_sha256, platform,
                    account_id, visibility, intended_uses_json,
                    synthetic_media_disclosed, rights_decision_ids_json,
                    rights_snapshot_sha256, request_principal, approved_by,
                    expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    job_id,
                    job.request_fingerprint,
                    job.package_id,
                    job.package_version,
                    job.output_sha256,
                    job.metadata_sha256,
                    job.platform.value,
                    job.account_id,
                    job.visibility.value,
                    self._uses_json(job.intended_uses),
                    int(job.synthetic_media_disclosed),
                    self._canonical([item.decision_id for item in decisions]),
                    snapshot,
                    job.owner_id,
                    context.principal,
                    expires_at.isoformat(),
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO content_publishing_approval_events (
                    approval_id, state, actor, reason, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    PublishingApprovalState.APPROVED.value,
                    context.principal,
                    "exact_release_tuple_approved",
                    now,
                ),
            )
            if row["state"] == PublishingJobState.AWAITING_APPROVAL.value:
                connection.execute(
                    """
                    UPDATE content_publishing_jobs
                    SET state = ?, updated_at = ? WHERE job_id = ?
                    """,
                    (PublishingJobState.QUEUED.value, now, job_id),
                )
                self._insert_job_event(
                    connection,
                    job_id=job_id,
                    previous=PublishingJobState.AWAITING_APPROVAL,
                    target=PublishingJobState.QUEUED,
                    actor=context.principal,
                    reason="independent_release_approval_recorded",
                    now=now,
                )
            return approval_id, "release_approved", {
                "job_id": job_id,
                "approval_id": approval_id,
                "rights_decision_ids": [item.decision_id for item in decisions],
            }

        self.reviewer_rights._run(
            context,
            action="content_approve_release",
            payload=payload,
            command_id=str(uuid4()),
            subject_id=job_id,
            operation=operation,
        )
        approval = self.store.get_publishing_approval(job_id)
        if approval is None or approval.approval_id != approval_id:
            raise RuntimeError("Release approval committed without a durable record.")
        return approval

    def set_kill_switch(
        self,
        *,
        enabled: bool,
        reason: str,
        context: AuthorizationContext,
        command_id: str,
    ) -> None:
        if context.principal not in self.operator_principals:
            raise PermissionError("Principal is not authorized to change the kill switch.")
        reason = self._validate_reason(reason)
        payload = {"enabled": enabled, "reason": reason}
        now = datetime.now(UTC).isoformat()

        def operation(connection: sqlite3.Connection):
            connection.execute(
                """
                UPDATE content_publishing_controls
                SET kill_switch_enabled = ?, updated_by = ?, updated_at = ?
                WHERE singleton = 1
                """,
                (int(enabled), context.principal, now),
            )
            connection.execute(
                """
                INSERT INTO content_publishing_control_events (
                    kill_switch_enabled, actor, reason, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (int(enabled), context.principal, reason, now),
            )
            return "publishing-kill-switch", "kill_switch_updated", {
                "enabled": enabled,
            }

        self.operator_rights._run(
            context,
            action="content_set_publishing_kill_switch",
            payload=payload,
            command_id=command_id,
            subject_id="publishing-kill-switch",
            operation=operation,
        )

    def resolve_blocked_attempt_for_safe_replacement(
        self,
        *,
        attempt_id: str,
        expected_revision: int,
        reason: str,
        context: AuthorizationContext,
        command_id: str,
    ) -> PublishingAttempt:
        if context.principal not in self.operator_principals:
            raise PermissionError(
                "Principal is not authorized to resolve a publishing attempt."
            )
        action = "content_resolve_publishing_attempt_safe_to_replace"
        reason = self._validate_reason(reason)
        payload = {
            "attempt_id": attempt_id,
            "expected_revision": expected_revision,
            "resolution_code": "safe_to_replace",
            "reason": reason,
        }
        authorization = self.operator_rights._authorize(
            context,
            action=action,
            payload=payload,
        )
        return self.store._resolve_blocked_publishing_attempt_for_safe_replacement(
            attempt_id,
            expected_revision=expected_revision,
            reason=reason,
            authorization=authorization,
            command_id=command_id,
        )

    def dispatch_mock(
        self,
        *,
        job_id: str,
        context: AuthorizationContext,
        finalize_context: AuthorizationContext,
        render_context: AuthorizationContext,
    ) -> PublishingJob:
        job = self.store.get_publishing_job(job_id)
        if job is None or context.principal != job.owner_id:
            raise PermissionError("Publishing job is outside the authenticated account.")
        if finalize_context.principal != job.owner_id:
            raise PermissionError("Dispatch finalization must remain in the job owner account.")
        self._load_bound_content(
            package_id=job.package_id,
            package_version=job.package_version,
            render_id=job.render_id,
            owner_id=job.owner_id,
            render_context=render_context,
        )
        payload = {"job_id": job_id}
        now = datetime.now(UTC).isoformat()

        def operation(connection: sqlite3.Connection):
            job_row = connection.execute(
                "SELECT * FROM content_publishing_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if (
                job_row is None
                or job_row["owner_id"] != context.principal
                or job_row["state"] != PublishingJobState.QUEUED.value
            ):
                raise PermissionError("Only a queued owned publishing job can dispatch.")
            approval_row = connection.execute(
                """
                SELECT a.*,
                    (SELECT state FROM content_publishing_approval_events e
                     WHERE e.approval_id = a.approval_id
                     ORDER BY sequence DESC LIMIT 1) AS latest_state
                FROM content_publishing_approvals a
                WHERE a.job_id = ?
                ORDER BY a.created_at DESC, a.rowid DESC LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            if (
                approval_row is None
                or approval_row["latest_state"] != PublishingApprovalState.APPROVED.value
                or approval_row["request_fingerprint"] != job_row["request_fingerprint"]
                or approval_row["approved_by"] == approval_row["request_principal"]
                or approval_row["package_id"] != job_row["package_id"]
                or approval_row["package_version"] != job_row["package_version"]
                or approval_row["output_sha256"] != job_row["output_sha256"]
                or approval_row["metadata_sha256"] != job_row["metadata_sha256"]
                or approval_row["platform"] != job_row["platform"]
                or approval_row["account_id"] != job_row["account_id"]
                or approval_row["visibility"] != job_row["visibility"]
                or approval_row["intended_uses_json"] != job_row["intended_uses_json"]
                or approval_row["synthetic_media_disclosed"]
                != job_row["synthetic_media_disclosed"]
            ):
                raise PermissionError("A current exact release approval is required.")
            if datetime.fromisoformat(approval_row["expires_at"]) <= datetime.now(UTC):
                connection.execute(
                    """
                    INSERT INTO content_publishing_approval_events (
                        approval_id, state, actor, reason, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        approval_row["approval_id"],
                        PublishingApprovalState.EXPIRED.value,
                        context.principal,
                        "release_approval_expired_before_dispatch",
                        now,
                    ),
                )
                self._insert_job_event(
                    connection,
                    job_id=job_id,
                    previous=PublishingJobState.QUEUED,
                    target=PublishingJobState.QUEUED,
                    actor=context.principal,
                    reason="release_approval_expired_before_dispatch",
                    now=now,
                )
                return job_id, "publishing_blocked", {
                    "reason": "release_approval_expired",
                    "state": PublishingJobState.QUEUED.value,
                }
            if self.store.is_publishing_kill_switch_enabled():
                self._insert_job_event(
                    connection,
                    job_id=job_id,
                    previous=PublishingJobState.QUEUED,
                    target=PublishingJobState.QUEUED,
                    actor=context.principal,
                    reason="publishing_kill_switch_engaged",
                    now=now,
                )
                return job_id, "publishing_blocked", {
                    "reason": "publishing_kill_switch_engaged",
                    "state": PublishingJobState.QUEUED.value,
                }
            uses = tuple(
                RightsUse.model_validate(item)
                for item in json.loads(job_row["intended_uses_json"])
            )
            try:
                decisions, snapshot = self._evaluate_uses(
                    connection,
                    uses=uses,
                    principal=job_row["owner_id"],
                )
            except PermissionError:
                snapshot = ""
                decisions = ()
            if snapshot != approval_row["rights_snapshot_sha256"]:
                connection.execute(
                    """
                    INSERT INTO content_publishing_approval_events (
                        approval_id, state, actor, reason, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        approval_row["approval_id"],
                        PublishingApprovalState.INVALIDATED.value,
                        context.principal,
                        "rights_decision_changed_before_dispatch",
                        now,
                    ),
                )
                connection.execute(
                    """
                    UPDATE content_publishing_jobs SET state = ?, updated_at = ?
                    WHERE job_id = ?
                    """,
                    (
                        PublishingJobState.BLOCKED.value,
                        now,
                        job_id,
                    ),
                )
                self._insert_job_event(
                    connection,
                    job_id=job_id,
                    previous=PublishingJobState.QUEUED,
                    target=PublishingJobState.BLOCKED,
                    actor=context.principal,
                    reason="rights_decision_changed_before_dispatch",
                    now=now,
                )
                return job_id, "publishing_blocked", {
                    "reason": "rights_decision_changed",
                }
            connection.execute(
                """
                UPDATE content_publishing_jobs SET state = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (PublishingJobState.DISPATCHING.value, now, job_id),
            )
            self._insert_job_event(
                connection,
                job_id=job_id,
                previous=PublishingJobState.QUEUED,
                target=PublishingJobState.DISPATCHING,
                actor=context.principal,
                reason="mock_dispatch_intent_committed",
                now=now,
            )
            return job_id, "mock_dispatch_started", {
                "approval_id": approval_row["approval_id"],
                "rights_decision_ids": [item.decision_id for item in decisions],
            }

        self.content_rights._run(
            context,
            action="content_dispatch_mock_publishing_job",
            payload=payload,
            command_id=str(uuid4()),
            subject_id=job_id,
            operation=operation,
        )
        current = self.store.get_publishing_job(job_id)
        if current is None:
            raise RuntimeError("Publishing job disappeared after dispatch authorization.")
        if current.state != PublishingJobState.DISPATCHING:
            return current

        target_state = PublishingJobState.MOCK_ACCEPTED
        reason = "mock_adapter_acknowledged"
        try:
            self.adapter.dispatch(current)
        except MockDispatchFailure:
            target_state = PublishingJobState.FAILED
            reason = "mock_adapter_definitive_failure"
        except MockDispatchUncertain:
            target_state = PublishingJobState.UNCERTAIN
            reason = "mock_adapter_outcome_uncertain"

        transition_payload = {
            "job_id": job_id,
            "target_state": target_state.value,
            "reason": reason,
        }

        def finalize(connection: sqlite3.Connection):
            row = connection.execute(
                "SELECT state FROM content_publishing_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None or row["state"] != PublishingJobState.DISPATCHING.value:
                raise RuntimeError("Publishing dispatch state changed unexpectedly.")
            connection.execute(
                """
                UPDATE content_publishing_jobs SET state = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (target_state.value, datetime.now(UTC).isoformat(), job_id),
            )
            self._insert_job_event(
                connection,
                job_id=job_id,
                previous=PublishingJobState.DISPATCHING,
                target=target_state,
                actor=context.principal,
                reason=reason,
                now=datetime.now(UTC).isoformat(),
            )
            return job_id, "mock_dispatch_finalized", {
                "state": target_state.value,
                "external_effect": False,
            }

        self.content_rights._run(
            finalize_context,
            action="content_finalize_mock_publishing_job",
            payload=transition_payload,
            command_id=str(uuid4()),
            subject_id=job_id,
            operation=finalize,
        )
        finalized = self.store.get_publishing_job(job_id)
        if finalized is None:
            raise RuntimeError("Publishing job disappeared after finalization.")
        return finalized
