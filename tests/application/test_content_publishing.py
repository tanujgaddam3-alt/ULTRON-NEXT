from __future__ import annotations

import hashlib
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import ultron.application.content.publishing as publishing_module

from tests.application.test_content_rights import (
    TOKEN,
    create_package_with_version,
    make_use,
)
from tests.application.test_content_rendering import (
    _authorized_pipeline,
    _create_render,
    _derivative_use,
    _run_render,
)
from ultron.application.content.publishing import (
    MockAdapterOutcome,
    MockPublishingAdapter,
    PublishingService,
)
from ultron.application.content.service import ContentRightsService
from ultron.application.content.store import ContentStore, IdempotencyConflictError
from tests.application.schema_test_utils import set_legacy_schema_version
from ultron.domain.content import (
    ContentPackageVersion,
    ProcessingJobState,
    PublishingApprovalState,
    PublishingJobState,
    PublishingReleaseApproval,
    PublishingVisibility,
    RightsAction,
    RightsUse,
)
from ultron.security.identity.authorization import AuthorizationContext, resource_fingerprint
from ultron.security.identity.authenticator import WebSocketAuthenticator


def _reviewer_setup(harness):
    authority = WebSocketAuthenticator(TOKEN, "reviewer")
    websocket = object()
    assert authority.authenticate(websocket, TOKEN) is not None
    service = ContentRightsService(
        store=harness.store,
        authority=authority,
        reviewer_principals=harness.service.reviewer_principals,
    )
    return authority, websocket, service


def _context(authority, websocket, action, payload) -> AuthorizationContext:
    operation = {
        "operationId": str(uuid4()),
        "correlationId": str(uuid4()),
        "requestFingerprint": hashlib.sha256(f"{action}:{id(payload)}".encode()).hexdigest(),
    }
    grant = authority.authorize(
        websocket=websocket,
        operation=operation,
        purpose="content_publishing",
        request_correlation_id=operation["correlationId"],
        action=action,
        resource_fingerprint=resource_fingerprint(action, payload),
    )
    assert grant is not None
    return AuthorizationContext.from_grant(grant)


def _prepared(tmp_path, monkeypatch, *, outcome=MockAdapterOutcome.ACCEPTED):
    rights_actions = frozenset(
        {RightsAction.ANALYZE, RightsAction.CREATE_DERIVATIVE, RightsAction.PUBLISH}
    )
    harness, asset, grant, storage, _, candidate, rendering = _authorized_pipeline(
        tmp_path,
        monkeypatch,
        rights_actions=rights_actions,
    )
    render_use = _derivative_use(asset, candidate)
    queued = _create_render(harness, rendering, candidate, render_use)
    render = _run_render(harness, rendering, queued.render_id)
    assert render.state == ProcessingJobState.SUCCEEDED
    render_context = harness.context(
        "content_read_clip_render_output",
        {"render_id": render.render_id},
    )
    output = rendering.get_clip_output_bytes(
        render.render_id,
        context=render_context,
    )
    publish_use = make_use(
        asset,
        actions=frozenset({RightsAction.PUBLISH}),
        duration_seconds=30,
    )
    version = create_package_with_version(
        harness,
        "publishing-package",
        asset,
        publish_use,
        artifact=output,
    )
    reviewer_authority, reviewer_websocket, reviewer_rights = _reviewer_setup(harness)
    adapter = MockPublishingAdapter(outcome)
    publishing = PublishingService(
        content_rights=harness.service,
        reviewer_rights=reviewer_rights,
        operator_rights=harness.service,
        rendering=rendering,
        adapter=adapter,
        operator_principals=frozenset({"alice"}),
    )
    return (
        harness,
        asset,
        grant,
        storage,
        rendering,
        render,
        version,
        publishing,
        adapter,
        reviewer_authority,
        reviewer_websocket,
    )


def _create_job(harness, publishing, render, *, key="publishing-request-1", visibility=PublishingVisibility.PRIVATE, disclosed=False):
    payload = {
        "package_id": "publishing-package",
        "package_version": 1,
        "render_id": render.render_id,
        "visibility": visibility.value,
        "synthetic_media_disclosed": disclosed,
        "idempotency_key": key,
    }
    return publishing.create_job(
        package_id="publishing-package",
        package_version=1,
        render_id=render.render_id,
        visibility=visibility,
        synthetic_media_disclosed=disclosed,
        idempotency_key=key,
        context=harness.context("content_create_publishing_job", payload),
        render_context=harness.context(
            "content_read_clip_render_output",
            {"render_id": render.render_id},
        ),
    )


def _approve(harness, publishing, job, reviewer_authority, reviewer_websocket):
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    payload = {
        "job_id": job.job_id,
        "request_fingerprint": job.request_fingerprint,
        "expires_at": expires_at.isoformat(),
    }
    return publishing.approve_release(
        job_id=job.job_id,
        expires_at=expires_at,
        context=_context(
            reviewer_authority,
            reviewer_websocket,
            "content_approve_release",
            payload,
        ),
        render_context=harness.context(
            "content_read_clip_render_output",
            {"render_id": job.render_id},
        ),
    )


def _dispatch(harness, publishing, job, *, outcome):
    target, reason = {
        MockAdapterOutcome.ACCEPTED: (
            PublishingJobState.MOCK_ACCEPTED,
            "mock_adapter_acknowledged",
        ),
        MockAdapterOutcome.FAILED: (
            PublishingJobState.FAILED,
            "mock_adapter_definitive_failure",
        ),
        MockAdapterOutcome.UNCERTAIN: (
            PublishingJobState.UNCERTAIN,
            "mock_adapter_outcome_uncertain",
        ),
    }[outcome]
    return publishing.dispatch_mock(
        job_id=job.job_id,
        context=harness.context(
            "content_dispatch_mock_publishing_job",
            {"job_id": job.job_id},
        ),
        finalize_context=harness.context(
            "content_finalize_mock_publishing_job",
            {
                "job_id": job.job_id,
                "target_state": target.value,
                "reason": reason,
            },
        ),
        render_context=harness.context(
            "content_read_clip_render_output",
            {"render_id": job.render_id},
        ),
    )


def test_publishing_job_requires_exact_rights_and_source_binding(tmp_path, monkeypatch):
    (
        h,
        _,
        _,
        _,
        _,
        render,
        version,
        publishing,
        adapter,
        _,
        _,
    ) = _prepared(tmp_path, monkeypatch)
    job = _create_job(h, publishing, render)
    assert job.state == PublishingJobState.AWAITING_APPROVAL
    assert job.package_id == version.package_id
    assert job.package_version == version.version
    assert job.output_sha256 == render.output_sha256
    assert job.intended_uses[0].actions == frozenset(
        {RightsAction.PUBLISH, RightsAction.CREATE_DERIVATIVE}
    )
    assert adapter.dispatch_count == 0
    h.store.close()


def test_concurrent_same_idempotency_identity_returns_one_durable_job(
    tmp_path,
    monkeypatch,
):
    h, _, _, _, _, render, _, publishing, _, _, _ = _prepared(
        tmp_path,
        monkeypatch,
    )
    barrier = threading.Barrier(2)

    def submit():
        barrier.wait(timeout=5)
        return _create_job(h, publishing, render, key="same-concurrent-key")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: submit(), range(2)))

    assert results[0].job_id == results[1].job_id
    assert {job.state for job in results} == {PublishingJobState.AWAITING_APPROVAL}
    assert h.store._connection.execute(
        "SELECT COUNT(*) FROM content_publishing_jobs"
    ).fetchone()[0] == 1
    decisions = [
        item["decision"]
        for item in h.store.list_audit_events(results[0].render_id)
        if item["action"] == "content_create_publishing_job"
    ]
    assert sorted(decisions) == ["idempotent_replay", "publishing_request_created"]
    h.store.close()


def test_concurrent_same_release_identity_has_one_winner(tmp_path, monkeypatch):
    h, _, _, _, _, render, _, publishing, _, _, _ = _prepared(
        tmp_path,
        monkeypatch,
    )
    barrier = threading.Barrier(2)

    def submit(key):
        barrier.wait(timeout=5)
        try:
            return _create_job(h, publishing, render, key=key)
        except IdempotencyConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, ("release-race-a", "release-race-b")))

    jobs = [item for item in results if not isinstance(item, Exception)]
    conflicts = [item for item in results if isinstance(item, IdempotencyConflictError)]
    assert len(jobs) == 1
    assert len(conflicts) == 1
    assert jobs[0].state == PublishingJobState.AWAITING_APPROVAL
    assert h.store._connection.execute(
        "SELECT COUNT(*) FROM content_publishing_jobs"
    ).fetchone()[0] == 1
    assert h.store._connection.execute(
        "SELECT COUNT(*) FROM content_commands "
        "WHERE action = 'content_create_publishing_job'"
    ).fetchone()[0] == 1
    assert h.store._connection.execute(
        "SELECT COUNT(*) FROM authorization_claims "
        "WHERE action = 'content_create_publishing_job'"
    ).fetchone()[0] == 1
    assert len(
        [
            event
            for event in h.store.list_audit_events(render.render_id)
            if event["action"] == "content_create_publishing_job"
        ]
    ) == 1
    h.store.close()


def test_publishing_approval_duplicate_prevention_and_mock_failure_states(
    tmp_path,
    monkeypatch,
):
    (
        h,
        _,
        _,
        _,
        _,
        render,
        _,
        publishing,
        adapter,
        reviewer_authority,
        reviewer_websocket,
    ) = _prepared(tmp_path, monkeypatch, outcome=MockAdapterOutcome.FAILED)
    job = _create_job(h, publishing, render)
    approval = _approve(
        h,
        publishing,
        job,
        reviewer_authority,
        reviewer_websocket,
    )
    assert approval.state == PublishingApprovalState.APPROVED
    assert approval.approved_by == "reviewer"
    assert approval.request_principal == "alice"

    duplicate = _create_job(h, publishing, render)
    assert duplicate.job_id == job.job_id
    with pytest.raises(IdempotencyConflictError, match="release identity"):
        _create_job(h, publishing, render, key="different-idempotency-key")
    with pytest.raises(ValueError, match="Idempotency key was reused"):
        _create_job(
            h,
            publishing,
            render,
            visibility=PublishingVisibility.UNLISTED,
        )
    with pytest.raises(sqlite3.IntegrityError, match="invalid publishing job state transition"):
        h.store._connection.execute(
            "UPDATE content_publishing_jobs SET state = ? WHERE job_id = ?",
            (PublishingJobState.MOCK_ACCEPTED.value, job.job_id),
        )

    result = _dispatch(
        h,
        publishing,
        job,
        outcome=MockAdapterOutcome.FAILED,
    )
    assert result.state == PublishingJobState.FAILED
    assert adapter.dispatch_count == 1
    assert [event.to_state for event in h.store.list_publishing_job_events(job.job_id)] == [
        PublishingJobState.AWAITING_APPROVAL,
        PublishingJobState.QUEUED,
        PublishingJobState.DISPATCHING,
        PublishingJobState.FAILED,
    ]
    audit = [
        event
        for event in h.store.list_audit_events(job.job_id)
        if event["decision"] == "mock_dispatch_finalized"
    ]
    assert len(audit) == 1
    assert audit[0]["details"]["state"] == PublishingJobState.FAILED.value
    assert audit[0]["details"]["external_effect"] is False
    h.store.close()


def test_release_binding_fields_are_immutable_in_sqlite(tmp_path, monkeypatch):
    (
        h,
        _,
        _,
        _,
        _,
        render,
        _,
        publishing,
        _,
        reviewer_authority,
        reviewer_websocket,
    ) = _prepared(tmp_path, monkeypatch)
    job = _create_job(h, publishing, render)
    approval = _approve(h, publishing, job, reviewer_authority, reviewer_websocket)

    job_columns = {
        "package_id": "changed-package",
        "package_version": 2,
        "render_id": "changed-render",
        "output_sha256": "0" * 64,
        "metadata_sha256": "1" * 64,
        "platform": "instagram",
        "account_id": "changed-account",
        "visibility": PublishingVisibility.PUBLIC.value,
        "intended_uses_json": "[]",
        "synthetic_media_disclosed": 1,
        "rights_decision_ids_json": "[]",
        "rights_snapshot_sha256": "2" * 64,
        "request_fingerprint": "3" * 64,
    }
    for column, changed in job_columns.items():
        with pytest.raises(
            sqlite3.IntegrityError,
            match="publishing job identity is immutable",
        ):
            h.store._connection.execute(
                f"UPDATE content_publishing_jobs SET {column} = ? WHERE job_id = ?",
                (changed, job.job_id),
            )

    approval_columns = {
        "request_fingerprint": "4" * 64,
        "package_id": "changed-package",
        "package_version": 2,
        "output_sha256": "5" * 64,
        "metadata_sha256": "6" * 64,
        "platform": "instagram",
        "account_id": "changed-account",
        "visibility": PublishingVisibility.PUBLIC.value,
        "intended_uses_json": "[]",
        "synthetic_media_disclosed": 1,
        "rights_decision_ids_json": "[]",
        "rights_snapshot_sha256": "7" * 64,
    }
    for column, changed in approval_columns.items():
        with pytest.raises(sqlite3.IntegrityError, match="append-only record"):
            h.store._connection.execute(
                f"UPDATE content_publishing_approvals SET {column} = ? "
                "WHERE approval_id = ?",
                (changed, approval.approval_id),
            )

    persisted_job = h.store.get_publishing_job(job.job_id)
    assert persisted_job is not None
    assert persisted_job.state == PublishingJobState.QUEUED
    for field in job_columns:
        model_field = {
            "intended_uses_json": "intended_uses",
            "rights_decision_ids_json": "rights_decision_ids",
            "synthetic_media_disclosed": "synthetic_media_disclosed",
        }.get(field, field)
        assert getattr(persisted_job, model_field) == getattr(job, model_field)
    assert h.store.get_publishing_approval(job.job_id) == approval
    h.store.close()


def test_rights_revocation_invalidates_release_and_blocks_dispatch(tmp_path, monkeypatch):
    (
        h,
        _,
        grant,
        _,
        _,
        render,
        _,
        publishing,
        adapter,
        reviewer_authority,
        reviewer_websocket,
    ) = _prepared(tmp_path, monkeypatch)
    job = _create_job(h, publishing, render)
    _approve(h, publishing, job, reviewer_authority, reviewer_websocket)
    h.invoke(
        "content_revoke_rights_grant",
        {"grant_id": grant.grant_id, "reason": "revoked before dispatch"},
        h.service.revoke_rights_grant,
        grant_id=grant.grant_id,
        reason="revoked before dispatch",
    )
    blocked = _dispatch(
        h,
        publishing,
        job,
        outcome=MockAdapterOutcome.ACCEPTED,
    )
    assert blocked.state == PublishingJobState.BLOCKED
    assert h.store.get_publishing_approval(job.job_id).state == PublishingApprovalState.INVALIDATED
    assert adapter.dispatch_count == 0
    assert any(
        event["decision"] == "publishing_blocked"
        and event["details"]["reason"] == "rights_decision_changed"
        for event in h.store.list_audit_events(job.job_id)
    )
    assert h.store.list_publishing_job_events(job.job_id)[-1].to_state == (
        PublishingJobState.BLOCKED
    )
    h.store.close()


def test_kill_switch_and_output_hash_mismatch_fail_closed(tmp_path, monkeypatch):
    (
        h,
        _,
        _,
        _,
        rendering,
        render,
        _,
        publishing,
        adapter,
        reviewer_authority,
        reviewer_websocket,
    ) = _prepared(tmp_path, monkeypatch)
    original_reader = rendering.get_clip_output_bytes
    def corrupt_output(render_id, *, context):
        assert render_id == render.render_id
        assert context.principal == "alice"
        return b"corrupted bytes"

    monkeypatch.setattr(
        rendering,
        "get_clip_output_bytes",
        corrupt_output,
    )
    with pytest.raises(PermissionError, match="integrity"):
        _create_job(h, publishing, render, key="bad-output")
    monkeypatch.setattr(rendering, "get_clip_output_bytes", original_reader)

    job = _create_job(h, publishing, render)
    _approve(h, publishing, job, reviewer_authority, reviewer_websocket)
    switch_payload = {"enabled": True, "reason": "operator pause"}
    publishing.set_kill_switch(
        enabled=True,
        reason="operator pause",
        context=h.context("content_set_publishing_kill_switch", switch_payload),
        command_id="kill-switch-on",
    )
    queued = _dispatch(
        h,
        publishing,
        job,
        outcome=MockAdapterOutcome.ACCEPTED,
    )
    assert queued.state == PublishingJobState.QUEUED
    assert h.store.is_publishing_kill_switch_enabled()
    assert adapter.dispatch_count == 0
    publishing.set_kill_switch(
        enabled=False,
        reason="operator resumed",
        context=h.context(
            "content_set_publishing_kill_switch",
            {"enabled": False, "reason": "operator resumed"},
        ),
        command_id="kill-switch-off",
    )
    resumed = _dispatch(
        h,
        publishing,
        job,
        outcome=MockAdapterOutcome.ACCEPTED,
    )
    assert resumed.state == PublishingJobState.MOCK_ACCEPTED
    assert adapter.dispatch_count == 1
    h.store.close()


def test_kill_switch_racing_with_dispatch_preserves_in_flight_state(
    tmp_path,
    monkeypatch,
):
    (
        h,
        _,
        _,
        _,
        _,
        render,
        _,
        publishing,
        adapter,
        reviewer_authority,
        reviewer_websocket,
    ) = _prepared(tmp_path, monkeypatch)
    job = _create_job(h, publishing, render)
    _approve(h, publishing, job, reviewer_authority, reviewer_websocket)

    dispatch_entered = threading.Event()
    continue_dispatch = threading.Event()
    original_dispatch = adapter.dispatch

    def paused_dispatch(dispatched_job):
        assert dispatched_job.job_id == job.job_id
        dispatch_entered.set()
        if not continue_dispatch.wait(timeout=5):
            raise AssertionError("dispatch release event was not signaled")
        original_dispatch(dispatched_job)

    monkeypatch.setattr(adapter, "dispatch", paused_dispatch)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            _dispatch,
            h,
            publishing,
            job,
            outcome=MockAdapterOutcome.ACCEPTED,
        )
        assert dispatch_entered.wait(timeout=5)
        assert h.store.get_publishing_job(job.job_id).state == (
            PublishingJobState.DISPATCHING
        )
        publishing.set_kill_switch(
            enabled=True,
            reason="pause while dispatch is in flight",
            context=h.context(
                "content_set_publishing_kill_switch",
                {
                    "enabled": True,
                    "reason": "pause while dispatch is in flight",
                },
            ),
            command_id="kill-switch-racing",
        )
        assert h.store.is_publishing_kill_switch_enabled()
        assert h.store.get_publishing_job(job.job_id).state == (
            PublishingJobState.DISPATCHING
        )
        continue_dispatch.set()
        completed = future.result(timeout=5)

    assert completed.state == PublishingJobState.MOCK_ACCEPTED
    assert adapter.dispatch_count == 1
    assert [event.to_state for event in h.store.list_publishing_job_events(job.job_id)][-2:] == [
        PublishingJobState.DISPATCHING,
        PublishingJobState.MOCK_ACCEPTED,
    ]
    h.store.close()


def test_dispatch_error_details_do_not_leak_adapter_exception(tmp_path, monkeypatch, caplog):
    (
        h,
        _,
        _,
        _,
        _,
        render,
        _,
        publishing,
        adapter,
        reviewer_authority,
        reviewer_websocket,
    ) = _prepared(tmp_path, monkeypatch)
    job = _create_job(h, publishing, render)
    _approve(h, publishing, job, reviewer_authority, reviewer_websocket)
    secret_canary = "oauth_token_secret_canary"

    def rejected_dispatch(_job):
        assert _job.job_id == job.job_id
        raise publishing_module.MockDispatchFailure(
            f"adapter rejected request with credential {secret_canary}"
        )

    monkeypatch.setattr(adapter, "dispatch", rejected_dispatch)
    failed = _dispatch(
        h,
        publishing,
        job,
        outcome=MockAdapterOutcome.FAILED,
    )

    persisted = h.store.list_audit_events(job.job_id)
    assert failed.state == PublishingJobState.FAILED
    assert "credential" not in repr(failed.model_dump(mode="json"))
    assert secret_canary not in caplog.text
    assert secret_canary not in repr(persisted)
    assert persisted[-1]["decision"] == "mock_dispatch_finalized"
    assert h.store.list_publishing_job_events(job.job_id)[-1].reason == (
        "mock_adapter_definitive_failure"
    )
    h.store.close()


def test_self_approval_is_rejected_and_expired_approval_can_be_replaced(
    tmp_path,
    monkeypatch,
):
    (
        h,
        _,
        _,
        _,
        _,
        render,
        _,
        publishing,
        adapter,
        reviewer_authority,
        reviewer_websocket,
    ) = _prepared(tmp_path, monkeypatch)
    job = _create_job(h, publishing, render)

    owner_authority = WebSocketAuthenticator(TOKEN, "alice")
    owner_websocket = object()
    assert owner_authority.authenticate(owner_websocket, TOKEN) is not None
    owner_reviewer = ContentRightsService(
        store=h.store,
        authority=owner_authority,
        reviewer_principals=frozenset({"alice"}),
    )
    self_approving_service = PublishingService(
        content_rights=h.service,
        reviewer_rights=owner_reviewer,
        operator_rights=h.service,
        rendering=publishing.rendering,
        adapter=adapter,
        operator_principals=frozenset({"alice"}),
    )
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    self_payload = {
        "job_id": job.job_id,
        "request_fingerprint": job.request_fingerprint,
        "expires_at": expires_at.isoformat(),
    }
    with pytest.raises(PermissionError, match="independent"):
        self_approving_service.approve_release(
            job_id=job.job_id,
            expires_at=expires_at,
            context=_context(
                owner_authority,
                owner_websocket,
                "content_approve_release",
                self_payload,
            ),
            render_context=h.context(
                "content_read_clip_render_output",
                {"render_id": job.render_id},
            ),
        )

    class FrozenDateTime(datetime):
        current = datetime.now(UTC)

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr(publishing_module, "datetime", FrozenDateTime)
    expires_at = FrozenDateTime.current + timedelta(seconds=1)
    approval_payload = {
        "job_id": job.job_id,
        "request_fingerprint": job.request_fingerprint,
        "expires_at": expires_at.isoformat(),
    }
    approval = publishing.approve_release(
        job_id=job.job_id,
        expires_at=expires_at,
        context=_context(
            reviewer_authority,
            reviewer_websocket,
            "content_approve_release",
            approval_payload,
        ),
        render_context=h.context(
            "content_read_clip_render_output",
            {"render_id": job.render_id},
        ),
    )
    FrozenDateTime.current += timedelta(seconds=2)
    expired = _dispatch(
        h,
        publishing,
        job,
        outcome=MockAdapterOutcome.ACCEPTED,
    )
    assert expired.state == PublishingJobState.QUEUED
    assert adapter.dispatch_count == 0
    replacement = _approve(
        h,
        publishing,
        job,
        reviewer_authority,
        reviewer_websocket,
    )
    assert replacement.approval_id != approval.approval_id
    assert replacement.state == PublishingApprovalState.APPROVED
    accepted = _dispatch(
        h,
        publishing,
        job,
        outcome=MockAdapterOutcome.ACCEPTED,
    )
    assert accepted.state == PublishingJobState.MOCK_ACCEPTED
    assert adapter.dispatch_count == 1
    h.store.close()


def test_uncertain_mock_result_is_terminal_and_restart_recovers_dispatching(
    tmp_path,
    monkeypatch,
):
    (
        h,
        _,
        _,
        _,
        _,
        render,
        _,
        publishing,
        adapter,
        reviewer_authority,
        reviewer_websocket,
    ) = _prepared(tmp_path, monkeypatch, outcome=MockAdapterOutcome.UNCERTAIN)
    job = _create_job(h, publishing, render)
    _approve(h, publishing, job, reviewer_authority, reviewer_websocket)
    uncertain = _dispatch(
        h,
        publishing,
        job,
        outcome=MockAdapterOutcome.UNCERTAIN,
    )
    assert uncertain.state == PublishingJobState.UNCERTAIN
    with pytest.raises(PermissionError):
        publishing.dispatch_mock(
            job_id=job.job_id,
            context=h.context(
                "content_dispatch_mock_publishing_job",
                {"job_id": job.job_id},
            ),
            finalize_context=h.context(
                "content_finalize_mock_publishing_job",
                {
                    "job_id": job.job_id,
                    "target_state": PublishingJobState.UNCERTAIN.value,
                    "reason": "mock_adapter_outcome_uncertain",
                },
            ),
            render_context=h.context(
                "content_read_clip_render_output",
                {"render_id": job.render_id},
            ),
        )
    assert adapter.dispatch_count == 1

    second_job = _create_job(
        h,
        publishing,
        render,
        key="restart-request",
        visibility=PublishingVisibility.UNLISTED,
    )
    _approve(h, publishing, second_job, reviewer_authority, reviewer_websocket)
    queued_job = _create_job(
        h,
        publishing,
        render,
        key="queued-recovery-request",
        visibility=PublishingVisibility.PUBLIC,
    )
    _approve(h, publishing, queued_job, reviewer_authority, reviewer_websocket)
    with h.store._transaction() as connection:
        connection.execute(
            "UPDATE content_publishing_jobs SET state = ?, updated_at = ? WHERE job_id = ?",
            (
                PublishingJobState.DISPATCHING.value,
                datetime.now(UTC).isoformat(),
                second_job.job_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO content_publishing_job_events (
                job_id, from_state, to_state, actor, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                second_job.job_id,
                PublishingJobState.QUEUED.value,
                PublishingJobState.DISPATCHING.value,
                "test",
                "simulate process interruption",
                datetime.now(UTC).isoformat(),
            ),
        )
    database = h.store.database_identity
    h.store.close()
    reopened = ContentStore(database)
    recovered = reopened.get_publishing_job(second_job.job_id)
    assert recovered is not None and recovered.state == PublishingJobState.UNCERTAIN
    assert reopened.get_publishing_job(job.job_id).state == PublishingJobState.UNCERTAIN
    assert reopened.get_publishing_job(queued_job.job_id).state == PublishingJobState.QUEUED
    assert reopened.list_publishing_job_events(second_job.job_id)[-1].to_state == (
        PublishingJobState.UNCERTAIN
    )
    recovery_audit = [
        event
        for event in reopened.list_audit_events(second_job.job_id)
        if event["action"] == "content_recover_publishing_dispatch"
    ]
    assert len(recovery_audit) == 1
    assert recovery_audit[0]["decision"] == "publishing_recovered_uncertain"
    assert recovery_audit[0]["principal"] == "system:startup_recovery"
    assert adapter.dispatch_count == 1
    reopened.close()


def test_interruption_after_dispatch_commit_recovers_uncertain_without_retry(
    tmp_path,
    monkeypatch,
):
    (
        h,
        _,
        _,
        _,
        _,
        render,
        _,
        publishing,
        adapter,
        reviewer_authority,
        reviewer_websocket,
    ) = _prepared(tmp_path, monkeypatch)
    job = _create_job(h, publishing, render)
    _approve(h, publishing, job, reviewer_authority, reviewer_websocket)

    def interrupted(_job):
        assert _job.job_id == job.job_id
        adapter.dispatch_count += 1
        raise KeyboardInterrupt("simulated interruption at adapter boundary")

    monkeypatch.setattr(adapter, "dispatch", interrupted)
    with pytest.raises(KeyboardInterrupt, match="simulated interruption"):
        _dispatch(
            h,
            publishing,
            job,
            outcome=MockAdapterOutcome.ACCEPTED,
        )
    assert h.store.get_publishing_job(job.job_id).state == PublishingJobState.DISPATCHING
    assert h.store.list_publishing_job_events(job.job_id)[-1].to_state == (
        PublishingJobState.DISPATCHING
    )
    assert adapter.dispatch_count == 1

    database = h.store.database_identity
    h.store.close()
    reopened = ContentStore(database)
    recovered = reopened.get_publishing_job(job.job_id)
    assert recovered is not None and recovered.state == PublishingJobState.UNCERTAIN
    assert reopened.list_publishing_job_events(job.job_id)[-1].to_state == (
        PublishingJobState.UNCERTAIN
    )
    recovery_audit = [
        event
        for event in reopened.list_audit_events(job.job_id)
        if event["decision"] == "publishing_recovered_uncertain"
    ]
    assert len(recovery_audit) == 1
    assert adapter.dispatch_count == 1
    reopened.close()


def test_schema_six_database_migrates_additively_and_preserves_content(
    tmp_path,
    monkeypatch,
):
    (
        h,
        asset,
        _,
        _,
        _,
        render,
        version,
        _,
        _,
        _,
        _,
    ) = _prepared(tmp_path, monkeypatch)
    with h.store._transaction() as connection:
        for table in (
            "content_publishing_approval_events",
            "content_publishing_approvals",
            "content_publishing_job_events",
            "content_publishing_control_events",
            "content_publishing_controls",
            "content_publishing_jobs",
        ):
            connection.execute(f"DROP TABLE {table}")
    set_legacy_schema_version(h.store, 7)
    h.store._connection.execute(
        "UPDATE content_schema_meta SET schema_version = 6 WHERE singleton = 1"
    )
    database = h.store.database_identity
    h.store.close()

    migrated = ContentStore(database)
    assert migrated.get_asset(asset.asset_id) == asset
    assert migrated.get_package_version(version.package_id, version.version) == version
    assert migrated.get_clip_render(render.render_id) == render
    assert migrated._connection.execute(
        "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
    ).fetchone()[0] == ContentStore.LEGACY_BASE_SCHEMA_VERSION
    assert migrated._connection.execute(
        "SELECT COUNT(*) FROM content_publishing_jobs"
    ).fetchone()[0] == 0
    assert migrated._connection.execute("PRAGMA foreign_key_check").fetchall() == []
    migrated.close()


def test_publishing_models_reject_malformed_hashes_and_self_approval():
    with pytest.raises(ValueError, match="must be SHA-256"):
        ContentPackageVersion.model_validate(
            {
                "package_id": "p",
                "version": 1,
                "platform": "youtube",
                "account_id": "channel",
                "artifact_sha256": "bad",
                "metadata_sha256": "a" * 64,
                "metadata": {},
                "asset_uses": [],
            }
        )
    with pytest.raises(ValueError, match="independent"):
        PublishingReleaseApproval(
            approval_id="approval",
            job_id="job",
            request_fingerprint="a" * 64,
            package_id="package",
            package_version=1,
            output_sha256="b" * 64,
            metadata_sha256="c" * 64,
            platform="youtube",
            account_id="channel",
            visibility=PublishingVisibility.PRIVATE,
            intended_uses=(
                RightsUse(
                    asset_id="asset",
                    expected_asset_sha256="d" * 64,
                    platform="youtube",
                    account_id="channel",
                    territory="US",
                    actions=frozenset({RightsAction.PUBLISH}),
                    commercial_intent=False,
                    monetization_intent=False,
                    duration_seconds=10,
                ),
            ),
            synthetic_media_disclosed=False,
            rights_decision_ids=("decision",),
            rights_snapshot_sha256="e" * 64,
            request_principal="alice",
            approved_by="alice",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            state=PublishingApprovalState.APPROVED,
            created_at=datetime.now(UTC),
        )


def test_publishing_service_rejects_offline_youtube_adapter(tmp_path, monkeypatch):
    from ultron.application.content.youtube_adapter import YouTubeAdapter

    h, _, _, _, rendering, _, _, publishing, *_ = _prepared(
        tmp_path,
        monkeypatch,
    )

    def authorize(_job):
        del _job

    def output(_job):
        del _job
        return b""

    def metadata(_job):
        del _job
        return {}

    youtube_adapter = YouTubeAdapter(
        transport=object(),
        credentials=object(),
        progress_store=object(),
        session_handle_provider=lambda _job: None,
        authorization_guard=authorize,
        output_provider=output,
        metadata_provider=metadata,
    )
    with pytest.raises(TypeError, match="Mission 12 permits only"):
        PublishingService(
            content_rights=publishing.content_rights,
            reviewer_rights=publishing.reviewer_rights,
            operator_rights=publishing.operator_rights,
            rendering=rendering,
            adapter=youtube_adapter,
            operator_principals=publishing.operator_principals,
        )
    assert h.store.list_publishing_job_events("nonexistent-job") == []
    h.store.close()
