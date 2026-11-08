from __future__ import annotations

import hashlib
import json
import secrets
import time

import pytest
from ultron.application.content.distribution import (
    ContentPublicationPreparationService,
    DryRunPlatformAdapter,
    PlatformAdapterRegistry,
)
from ultron.application.content.processing import LocalTemporaryContentStore
from ultron.application.content.production import ContentMediaProductionService
from ultron.application.content.service import ContentRightsService
from ultron.application.content.status import ContentOwnerMapping, ContentStatusService
from ultron.application.content.store import ContentStore
from ultron.application.content.workflow import ContentWorkflowRuntime
from ultron.domain.content_distribution import (
    DistributionJobState,
    DistributionPlatform,
    DistributionRecord,
    PublicationApprovalState,
    PublicationMode,
)
from ultron.security.identity.authorization import AuthorizationContext, resource_fingerprint
from ultron.security.identity.authenticator import WebSocketAuthenticator
from tests.application.test_content_media_production import _DeterministicRenderer
from tests.application.test_content_package_e2e import (
    _complete_with_provider,
    _pending_package,
)


def _authorized_context(authority, token, action, payload):
    authority.authenticate(token, token)
    fingerprint = resource_fingerprint(action, payload)
    grant = authority.authorize(
        websocket=token,
        operation={
            "operationId": "publication-package-operation",
            "correlationId": "publication-package-correlation",
            "requestFingerprint": fingerprint,
        },
        purpose="execute",
        request_correlation_id="publication-package-request",
        action=action,
        resource_fingerprint=fingerprint,
    )
    return AuthorizationContext.from_grant(grant)


def test_publication_package_binds_validated_media_and_recovers(tmp_path):
    database = tmp_path / "distribution.db"
    store, opportunity, workflow, pending, angle = _pending_package(database)
    content_package = _complete_with_provider(
        store,
        opportunity,
        workflow,
        pending,
        angle,
        classification="fictional",
    )
    script = store.get_script_artifact(content_package.script_artifact_id)
    assert script is not None
    store.create_approval_decision(
        decision_id="script-approval-1",
        workflow_id=script.workflow_id,
        artifact_type="script",
        artifact_id=script.artifact_id,
        artifact_version=script.version,
        artifact_sha256=script.content_sha256,
        decision="approved",
        actor="independent-reviewer",
    )
    storage = LocalTemporaryContentStore(tmp_path / "media")
    media_service = ContentMediaProductionService(
        store,
        storage,
        _DeterministicRenderer(),
    )
    plan = media_service.create_plan(content_package.package_id)
    media_artifact = media_service.execute(plan.job_id)

    metadata = {
        "title": "Accepted title",
        "description": "Approved description",
        "tags": ("cre", "offline"),
        "category": None,
        "thumbnail_reference": None,
        "subtitle_artifact_ids": tuple(
            item.artifact_id
            for item in store.list_media_subtitle_artifacts(plan.job_id)
        ),
        "visibility": "private",
        "mode": PublicationMode.DRY_RUN.value,
        "scheduled_at": None,
    }
    binding = {
        "content_package_id": content_package.package_id,
        "script_artifact_id": script.artifact_id,
        "script_version": script.version,
        "script_sha256": script.content_sha256,
        "media_artifact_id": media_artifact.artifact_id,
        "media_sha256": media_artifact.output_sha256,
        "platform": DistributionPlatform.YOUTUBE.value,
        "account_id": "test-channel",
        "requested_by": "operator",
        "metadata": metadata,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            binding,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    package_id = f"publication-{fingerprint[:32]}"
    authorization_payload = {
        "publication_package_id": package_id,
        "approval_fingerprint": fingerprint,
        "platform": DistributionPlatform.YOUTUBE.value,
        "account_id": "test-channel",
        "requested_by": "operator",
        "mode": PublicationMode.DRY_RUN.value,
    }
    token = secrets.token_urlsafe(48)
    authority = WebSocketAuthenticator(token, principal="operator")
    context = _authorized_context(
        authority,
        token,
        ContentPublicationPreparationService.ACTION,
        authorization_payload,
    )
    rights = ContentRightsService(
        store=store,
        authority=authority,
        content_storage=storage,
    )
    service = ContentPublicationPreparationService(
        content_rights=rights,
        storage=storage,
    )
    publication_package = service.create_package(
        context=context,
        command_id="prepare-publication-1",
        content_package_id=content_package.package_id,
        media_artifact_id=media_artifact.artifact_id,
        platform=DistributionPlatform.YOUTUBE,
        account_id="test-channel",
        title="Accepted title",
        description="Approved description",
        tags=("cre", "offline"),
        mode=PublicationMode.DRY_RUN,
    )

    assert publication_package.publication_package_id == package_id
    assert publication_package.approval_fingerprint == fingerprint
    assert publication_package.script_artifact_id == script.artifact_id
    assert publication_package.script_version == script.version
    assert publication_package.script_sha256 == script.content_sha256
    assert publication_package.requested_by == "operator"
    assert publication_package.media_artifact_id == media_artifact.artifact_id
    assert publication_package.media_sha256 == media_artifact.output_sha256
    assert publication_package.validation_passed is True
    assert publication_package.mode == PublicationMode.DRY_RUN
    assert publication_package.subtitle_artifact_ids == metadata["subtitle_artifact_ids"]
    assert hashlib.sha256(
        storage.read_output_reference(
            media_artifact.storage_reference,
            media_artifact.output_sha256,
            max_bytes=LocalTemporaryContentStore.MAX_CLIP_OUTPUT_BYTES,
        )
    ).hexdigest() == media_artifact.output_sha256

    reviewer_token = token
    reviewer_authority = WebSocketAuthenticator(
        reviewer_token,
        principal="reviewer",
    )
    approval_payload = {
        "publication_package_id": package_id,
        "approval_fingerprint": fingerprint,
        "approved_by": "reviewer",
    }
    reviewer_context = _authorized_context(
        reviewer_authority,
        reviewer_token,
        ContentPublicationPreparationService.APPROVAL_ACTION,
        approval_payload,
    )
    reviewer_rights = ContentRightsService(
        store=store,
        authority=reviewer_authority,
        content_storage=storage,
    )
    reviewer_service = ContentPublicationPreparationService(
        content_rights=reviewer_rights,
        storage=storage,
    )
    approval, job = reviewer_service.approve_package(
        context=reviewer_context,
        command_id="approve-publication-1",
        publication_package_id=package_id,
    )
    assert approval.approved_by == "reviewer"
    assert approval.requested_by == "operator"
    assert approval.approval_fingerprint == fingerprint
    assert job.state.value == "approved"
    assert job.approval_id == approval.approval_id
    status = ContentStatusService(
        content_rights=rights,
        owner_mapping=ContentOwnerMapping("operator", "operator"),
    ).for_principal("operator")
    package_projection = next(
        item
        for item in status["services"]["contentPackages"]
        if item["packageId"] == content_package.package_id
    )
    publication_projection = package_projection["publicationPackages"][0]
    assert publication_projection["approvalState"] == "APPROVED"
    assert publication_projection["jobState"] == "APPROVED"
    assert publication_projection["publicationUrl"] is None

    dry_run_registry = PlatformAdapterRegistry()
    dry_run_registry.register(DryRunPlatformAdapter(DistributionPlatform.YOUTUBE))
    dry_run_token = token
    dry_run_authority = WebSocketAuthenticator(
        dry_run_token,
        principal="operator",
    )
    dry_run_payload = {
        "job_id": job.job_id,
        "publication_package_id": package_id,
        "approval_fingerprint": fingerprint,
        "mode": PublicationMode.DRY_RUN.value,
    }
    dry_run_context = _authorized_context(
        dry_run_authority,
        dry_run_token,
        "content.dry_run_publication",
        dry_run_payload,
    )
    dry_run_rights = ContentRightsService(
        store=store,
        authority=dry_run_authority,
        content_storage=storage,
    )
    dry_run_service = ContentPublicationPreparationService(
        content_rights=dry_run_rights,
        storage=storage,
        adapters=dry_run_registry,
    )
    result = dry_run_service.dry_run(
        context=dry_run_context,
        command_id="dry-run-publication-1",
        job_id=job.job_id,
    )
    assert result.accepted is True
    assert result.confirmed is False
    assert result.remote_publication_id is None
    assert store.get_distribution_job(job.job_id) == job
    unauthorized_task = store.create_content_workflow_task(
        task_id="dry-run-unauthorized",
        workflow_id=workflow.workflow_id,
        task_type="dry_run_publication",
        idempotency_key="dry-run-unauthorized-key",
        input_data={"job_id": job.job_id},
    )
    unauthorized_worker = ContentWorkflowRuntime(
        store,
        worker_id="publishing-worker-denied",
        permissions=frozenset(),
        poll_interval_seconds=0.01,
        publication_preparation_service=dry_run_service,
    )
    unauthorized_worker.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            unauthorized_task = store.get_content_workflow_task(
                unauthorized_task.task_id
            )
            if unauthorized_task is not None and unauthorized_task.state == "failed":
                break
            time.sleep(0.01)
    finally:
        unauthorized_worker.stop()
    assert unauthorized_task is not None and unauthorized_task.state == "failed"
    assert unauthorized_task.error_code == "PERMISSION_DENIED"

    authorized_task = store.create_content_workflow_task(
        task_id="dry-run-authorized",
        workflow_id=workflow.workflow_id,
        task_type="dry_run_publication",
        idempotency_key="dry-run-authorized-key",
        input_data={"job_id": job.job_id},
    )
    authorized_worker = ContentWorkflowRuntime(
        store,
        worker_id="publishing-worker-authorized",
        permissions=frozenset({"content.publish"}),
        poll_interval_seconds=0.01,
        publication_preparation_service=dry_run_service,
    )
    authorized_worker.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            authorized_task = store.get_content_workflow_task(authorized_task.task_id)
            if authorized_task is not None and authorized_task.state == "completed":
                break
            time.sleep(0.01)
    finally:
        authorized_worker.stop()
    assert authorized_task is not None and authorized_task.state == "completed"
    task_result = json.loads(authorized_task.result_json)
    assert task_result["accepted"] is True
    assert task_result["confirmed"] is False
    assert task_result["remote_publication_id"] is None
    store.transition_distribution_job(
        job_id=job.job_id,
        expected_state=DistributionJobState.APPROVED,
        target_state=DistributionJobState.UPLOADING,
        actor="test-worker",
        reason="test-only simulated adapter upload",
    )
    with pytest.raises(ValueError, match="Remote processing states"):
        store.transition_distribution_job(
            job_id=job.job_id,
            expected_state=DistributionJobState.UPLOADING,
            target_state=DistributionJobState.PROCESSING,
            actor="test-worker",
            reason="reject status without remote identity",
        )
    store.transition_distribution_job(
        job_id=job.job_id,
        expected_state=DistributionJobState.UPLOADING,
        target_state=DistributionJobState.PROCESSING,
        actor="test-worker",
        reason="test-only simulated platform acceptance",
        remote_publication_id="fake-remote-video",
    )
    store.transition_distribution_job(
        job_id=job.job_id,
        expected_state=DistributionJobState.PROCESSING,
        target_state=DistributionJobState.PUBLISHED,
        actor="test-reconciler",
        reason="test-only simulated remote processing completion",
    )
    record = DistributionRecord(
        publication_id="publication-record-1",
        job_id=job.job_id,
        publication_package_id=package_id,
        platform=publication_package.platform,
        account_id=publication_package.account_id,
        remote_publication_id="fake-remote-video",
        remote_url="https://youtube.example/watch?v=fake-remote-video",
        media_artifact_id=media_artifact.artifact_id,
        media_sha256=media_artifact.output_sha256,
        metadata_snapshot={
            "title": publication_package.title,
            "description": publication_package.description,
            "tags": list(publication_package.tags),
            "visibility": publication_package.visibility.value,
        },
        published_at=publication_package.created_at,
        remote_status="published",
    )
    assert store.create_distribution_record(record) == record
    assert store.create_distribution_record(record) == record
    assert [
        event["to_state"] for event in store.list_distribution_job_events(job.job_id)
    ] == ["approved", "uploading", "processing", "published"]

    store.close()
    reopened = ContentStore(database)
    try:
        restored = reopened.get_publication_package(package_id)
        assert restored == publication_package
        assert reopened.list_publication_packages(
            content_package_id=content_package.package_id
        ) == (publication_package,)
        assert reopened.get_publication_approval(package_id) == approval
        recovered_job = reopened.get_distribution_job(job.job_id)
        assert recovered_job is not None
        assert recovered_job.state == DistributionJobState.PUBLISHED
        assert reopened.get_distribution_record_for_job(job.job_id) == record
        reopened.create_approval_decision(
            decision_id="script-approval-rejected",
            workflow_id=script.workflow_id,
            artifact_type="script",
            artifact_id=script.artifact_id,
            artifact_version=script.version,
            artifact_sha256=script.content_sha256,
            decision="rejected",
            actor="independent-reviewer",
        )
        stale_approval = reopened.get_publication_approval(package_id)
        assert stale_approval is not None
        assert stale_approval.state == PublicationApprovalState.INVALIDATED
    finally:
        reopened.close()


def test_managed_output_reference_rejects_external_path(tmp_path):
    storage = LocalTemporaryContentStore(tmp_path / "managed")
    external = tmp_path / "outside.mp4"
    external.write_bytes(b"not managed")
    digest = hashlib.sha256(external.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="outside managed content storage"):
        storage.read_output_reference(str(external), digest)
