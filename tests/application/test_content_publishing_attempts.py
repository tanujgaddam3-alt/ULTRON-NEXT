from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
import ultron.application.content.publishing as publishing_module

from tests.application.test_content_publishing import (
    _approve,
    _context,
    _create_job,
    _prepared,
    _reviewer_setup,
)
from tests.application.schema_test_utils import set_legacy_schema_version
from ultron.application.content.rendering import ContentClipRenderingService
from ultron.application.content.service import ContentRightsService
from ultron.application.content.store import (
    ContentStore,
    IdempotencyConflictError,
    PublishingAttemptConflictError,
)
from ultron.domain.content import (
    PublishingAttemptCode,
    PublishingAttemptSecretState,
    PublishingAttemptState,
)
from ultron.security.identity.authorization import (
    AuthorizationContext,
    ValidatedAuthorization,
    _validated_authorization,
    resource_fingerprint,
)
from ultron.security.identity.authenticator import WebSocketAuthenticator


def _approved_job(tmp_path, monkeypatch):
    prepared = _prepared(tmp_path, monkeypatch)
    harness, render, publishing = prepared[0], prepared[5], prepared[7]
    job = _create_job(harness, publishing, render)
    _approve(harness, publishing, job, prepared[9], prepared[10])
    return harness, job, publishing


def _create_attempt(
    harness,
    publishing,
    job_id: str,
    command_id: str | None = None,
):
    action = "content_create_publishing_attempt"
    job = harness.store.get_publishing_job(job_id)
    return publishing.create_attempt(
        job_id=job_id,
        context=harness.context(action, {"job_id": job_id}),
        render_context=harness.context(
            "content_read_clip_render_output",
            {"render_id": job.render_id},
        ),
        command_id=command_id or str(uuid4()),
    )


def _transition(
    store,
    attempt,
    state: PublishingAttemptState,
    code: PublishingAttemptCode,
    *,
    command_id: str | None = None,
):
    return store._transition_publishing_attempt(
        attempt.identity.attempt_id,
        expected_revision=attempt.revision,
        to_state=state,
        code=code,
        command_id=command_id or str(uuid4()),
        actor="system:publishing_service",
    )


def _migrate(store):
    assert store._connection.execute(
        "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
    ).fetchone()[0] == ContentStore.SCHEMA_VERSION == 10
    assert store._connection.execute(
        """
        SELECT COUNT(*) FROM sqlite_master
        WHERE type = 'table' AND name = 'content_publishing_attempts'
        """
    ).fetchone()[0] == 1
    assert store.migrate_publishing_attempt_schema() == 10


def _publishing_for_store(publishing, store):
    def rights_for_store(service):
        return ContentRightsService(
            store=store,
            authority=service.authority,
            reviewer_principals=service.reviewer_principals,
            policy=service.policy,
            content_storage=service.content_storage,
            processor=service.processor,
        )

    content_rights = rights_for_store(publishing.content_rights)
    reviewer_rights = rights_for_store(publishing.reviewer_rights)
    operator_rights = rights_for_store(publishing.operator_rights)
    return type(publishing)(
        content_rights=content_rights,
        reviewer_rights=reviewer_rights,
        operator_rights=operator_rights,
        rendering=ContentClipRenderingService(content_rights=content_rights),
        adapter=publishing.adapter,
        operator_principals=publishing.operator_principals,
    )


def _create_attempt_in_process(database, job_id, barrier, results, command_id):
    store = ContentStore(database)
    try:
        barrier.wait(timeout=10)
        with store._transaction() as connection:
            attempt = store._create_publishing_attempt_in_transaction(
                job_id,
                command_id=command_id,
                actor="system:publishing_service",
                connection=connection,
            )
        results.put(("created", attempt.identity.attempt_id))
    except PublishingAttemptConflictError:
        results.put(("conflict", None))
    except BaseException as exc:
        results.put(("error", type(exc).__name__))
        raise
    finally:
        store.close()


def test_fresh_attempt_schema_is_complete_and_idempotent(tmp_path, monkeypatch):
    harness, job, _ = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    before = {
        table: store._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "content_publishing_jobs",
            "content_publishing_approvals",
            "content_publishing_approval_events",
            "rights_decisions",
            "content_audit_events",
        )
    }

    _migrate(store)
    assert store.migrate_publishing_attempt_schema() == 10
    assert store._connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert store._connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    after = {
        table: store._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in before
    }
    assert after == before
    assert store.get_publishing_job(job.job_id) is not None

    database = store.database_identity
    store.close()
    reopened = ContentStore(database)
    assert reopened._connection.execute(
        "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
    ).fetchone()[0] == 10
    assert reopened.get_latest_publishing_attempt(job.job_id) is None
    reopened._connection.execute(
        "DROP TRIGGER content_publishing_attempt_state_transition"
    )
    with pytest.raises(RuntimeError, match="inconsistent"):
        ContentStore(database)
    with pytest.raises(RuntimeError, match="inconsistent"):
        reopened.migrate_publishing_attempt_schema()
    reopened.close()


def test_explicit_v7_to_v10_migration_command(tmp_path):
    database = tmp_path / "content.db"
    backup = tmp_path / "content.db.pre-v10"
    store = ContentStore(database)
    set_legacy_schema_version(store, 7)
    store.close()
    shutil.copyfile(database, backup)

    repository = Path(__file__).resolve().parents[2]
    migrated = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.migrate_cre_private_render_operations",
            str(database),
            "--backup",
            str(backup),
        ],
        cwd=repository,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert migrated.returncode == 0, migrated.stderr.decode()
    assert b"now v10" in migrated.stdout

    reopened = ContentStore(database, recover_interrupted_jobs=False)
    assert reopened.private_render_operations_enabled
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
        ).fetchone()[0] == 10
    reopened.close()


def test_explicit_schema_v8_to_v9_upgrade_tightens_retry_eligibility(
    tmp_path,
    monkeypatch,
):
    harness, _, _ = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    set_legacy_schema_version(store, 8)
    database = store.database_identity
    store.close()

    reopened = ContentStore(database)
    assert reopened._connection.execute(
        "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
    ).fetchone()[0] == 8
    assert reopened.migrate_publishing_attempt_schema() == 9
    index_sql = reopened._connection.execute(
        """
        SELECT sql FROM sqlite_master
        WHERE type = 'index'
          AND name = 'content_publishing_attempt_one_unresolved_per_job'
        """
    ).fetchone()[0]
    assert "error_code = 'provider_rejected'" in index_sql
    assert reopened._connection.execute(
        """
        SELECT COUNT(*) FROM sqlite_master
        WHERE type = 'trigger'
          AND name = 'content_publishing_attempt_failure_source_valid'
        """
    ).fetchone()[0] == 1
    reopened.close()


def test_migration_rejects_legacy_failure_without_preupload_evidence(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    attempt = _create_attempt(harness, publishing, job.job_id)
    intent = _transition(
        store,
        attempt,
        PublishingAttemptState.INITIATION_INTENT,
        PublishingAttemptCode.INITIATION_AUTHORIZED,
    )
    failed = _transition(
        store,
        intent,
        PublishingAttemptState.FAILED,
        PublishingAttemptCode.PROVIDER_REJECTED,
    )
    store._connection.execute(
        "DROP TRIGGER content_publishing_attempt_events_append_only_delete"
    )
    store._connection.execute(
        """
        DELETE FROM content_publishing_attempt_events
        WHERE attempt_id = ? AND to_state = 'failed'
        """,
        (failed.identity.attempt_id,),
    )
    store._connection.execute(
        "UPDATE content_schema_meta SET schema_version = 8 WHERE singleton = 1"
    )

    with pytest.raises(
        RuntimeError,
        match="without safe pre-upload rejection evidence",
    ):
        store.migrate_publishing_attempt_schema()
    assert store._connection.execute(
        "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
    ).fetchone()[0] == 8
    legacy_row = store._connection.execute(
        "SELECT state, error_code FROM content_publishing_attempts WHERE attempt_id = ?",
        (failed.identity.attempt_id,),
    ).fetchone()
    assert legacy_row["state"] == PublishingAttemptState.FAILED.value
    assert legacy_row["error_code"] == PublishingAttemptCode.PROVIDER_REJECTED.value


def test_attempt_create_is_service_generated_stable_and_idempotent(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    command_id = str(uuid4())

    first = _create_attempt(harness, publishing, job.job_id, command_id)
    replay = _create_attempt(harness, publishing, job.job_id, command_id)
    assert first == replay
    assert first.identity.attempt_id != job.job_id
    assert UUID(first.identity.attempt_id).version == 4
    assert first.identity.attempt_number == 1
    assert first.identity.owner_id == job.owner_id
    assert first.identity.account_id == job.account_id
    assert first.identity.package_id == job.package_id
    assert first.identity.package_version == job.package_version
    assert first.identity.output_sha256 == job.output_sha256
    assert first.identity.release_identity == store._connection.execute(
        "SELECT release_identity FROM content_publishing_jobs WHERE job_id = ?",
        (job.job_id,),
    ).fetchone()[0]
    assert first.state == PublishingAttemptState.CREATED
    assert first.confirmed_offset == 0
    assert first.total_bytes > 0
    assert len(store.list_publishing_attempt_events(first.identity.attempt_id)) == 1

    with pytest.raises(IdempotencyConflictError):
        store._transition_publishing_attempt(
            first.identity.attempt_id,
            expected_revision=first.revision,
            to_state=PublishingAttemptState.INITIATION_INTENT,
            code=PublishingAttemptCode.INITIATION_AUTHORIZED,
            command_id=command_id,
            actor="system:publishing_service",
        )

    database = store.database_identity
    store.close()
    reopened = ContentStore(database)
    recovered = reopened.get_publishing_attempt(first.identity.attempt_id)
    assert recovered is not None
    assert recovered.identity.attempt_id == first.identity.attempt_id
    assert recovered.identity == first.identity
    assert recovered.state == PublishingAttemptState.CREATED
    reopened.close()


def test_new_attempt_requires_prior_attempt_to_be_confirmed_terminal(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    first = _create_attempt(harness, publishing, job.job_id)

    with pytest.raises(PublishingAttemptConflictError):
        _create_attempt(harness, publishing, job.job_id)
    intent = _transition(
        store,
        first,
        PublishingAttemptState.INITIATION_INTENT,
        PublishingAttemptCode.INITIATION_AUTHORIZED,
    )
    failed = _transition(
        store,
        intent,
        PublishingAttemptState.FAILED,
        PublishingAttemptCode.PROVIDER_REJECTED,
    )
    assert failed.state == PublishingAttemptState.FAILED
    second = _create_attempt(harness, publishing, job.job_id)
    assert second.identity.attempt_number == 2
    assert second.identity.attempt_id != first.identity.attempt_id


def test_attempt_creation_authorization_grant_cannot_be_reused(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    action = "content_create_publishing_attempt"
    payload = {"job_id": job.job_id}
    context = harness.context(action, payload)
    render_context = harness.context(
        "content_read_clip_render_output",
        {"render_id": job.render_id},
    )
    created = publishing.create_attempt(
        job_id=job.job_id,
        context=context,
        render_context=render_context,
        command_id=str(uuid4()),
    )

    with pytest.raises(PermissionError, match="already consumed"):
        publishing.create_attempt(
            job_id=job.job_id,
            context=context,
            render_context=render_context,
            command_id=str(uuid4()),
        )

    assert store.get_latest_publishing_attempt(job.job_id) == created
    assert len(store.list_publishing_attempt_events(created.identity.attempt_id)) == 1
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_audit_events WHERE action = ? AND subject_id = ?",
        (action, job.job_id),
    ).fetchone()[0] == 1


def test_processing_failure_after_remote_upload_requires_safe_disposition(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    attempt = _create_attempt(harness, publishing, job.job_id)
    intent = _transition(
        store,
        attempt,
        PublishingAttemptState.INITIATION_INTENT,
        PublishingAttemptCode.INITIATION_AUTHORIZED,
    )
    reference = store._prepare_publishing_attempt_secret_reference(
        intent.identity.attempt_id,
        expected_revision=intent.revision,
        command_id=str(uuid4()),
        actor="system:publishing_service",
    )
    prepared = store.get_publishing_attempt(attempt.identity.attempt_id)
    active_reference = store._update_publishing_attempt_secret_reference(
        attempt.identity.attempt_id,
        reference.reference.reference_id,
        expected_revision=prepared.revision,
        new_status=PublishingAttemptSecretState.ACTIVE,
        command_id=str(uuid4()),
        actor="system:publishing_service",
    )
    session_ready = _transition(
        store,
        store.get_publishing_attempt(attempt.identity.attempt_id),
        PublishingAttemptState.SESSION_READY,
        PublishingAttemptCode.SESSION_STORED,
    )
    uploading = _transition(
        store,
        session_ready,
        PublishingAttemptState.UPLOADING,
        PublishingAttemptCode.UPLOAD_STARTED,
    )
    remote_uploaded = _transition(
        store,
        uploading,
        PublishingAttemptState.REMOTE_UPLOADED,
        PublishingAttemptCode.REMOTE_UPLOAD_CONFIRMED,
    )
    processing = _transition(
        store,
        remote_uploaded,
        PublishingAttemptState.PROCESSING,
        PublishingAttemptCode.PROCESSING_STARTED,
    )
    assert active_reference.status == PublishingAttemptSecretState.ACTIVE

    event_count = len(store.list_publishing_attempt_events(attempt.identity.attempt_id))
    with pytest.raises(PublishingAttemptConflictError, match="safe terminal outcome"):
        _transition(
            store,
            processing,
            PublishingAttemptState.FAILED,
            PublishingAttemptCode.PROCESSING_FAILED_CONFIRMED,
        )
    assert store.get_publishing_attempt(attempt.identity.attempt_id).state == (
        PublishingAttemptState.PROCESSING
    )
    assert len(store.list_publishing_attempt_events(attempt.identity.attempt_id)) == (
        event_count
    )

    unresolved = _transition(
        store,
        processing,
        PublishingAttemptState.RECONCILIATION_REQUIRED,
        PublishingAttemptCode.REMOTE_OUTCOME_UNKNOWN,
    )
    before = {
        "attempts": store._connection.execute(
            "SELECT COUNT(*) FROM content_publishing_attempts WHERE job_id = ?",
            (job.job_id,),
        ).fetchone()[0],
        "events": len(store.list_publishing_attempt_events(attempt.identity.attempt_id)),
        "attempt_audit": store._connection.execute(
            """
            SELECT COUNT(*) FROM content_audit_events
            WHERE action = 'content_create_publishing_attempt'
            """
        ).fetchone()[0],
    }
    with pytest.raises(PublishingAttemptConflictError):
        _create_attempt(harness, publishing, job.job_id)
    assert store.get_publishing_attempt(attempt.identity.attempt_id) == unresolved
    assert before == {
        "attempts": store._connection.execute(
            "SELECT COUNT(*) FROM content_publishing_attempts WHERE job_id = ?",
            (job.job_id,),
        ).fetchone()[0],
        "events": len(store.list_publishing_attempt_events(attempt.identity.attempt_id)),
        "attempt_audit": store._connection.execute(
            """
            SELECT COUNT(*) FROM content_audit_events
            WHERE action = 'content_create_publishing_attempt'
            """
        ).fetchone()[0],
    }

    blocked = _transition(
        store,
        unresolved,
        PublishingAttemptState.BLOCKED,
        PublishingAttemptCode.RECONCILIATION_MISMATCH,
    )
    action = "content_resolve_publishing_attempt_safe_to_replace"
    reason = "operator verified no duplicate remote release exists"
    payload = {
        "attempt_id": blocked.identity.attempt_id,
        "expected_revision": blocked.revision,
        "resolution_code": "safe_to_replace",
        "reason": reason,
    }
    valid_context = harness.context(action, payload)
    with pytest.raises(PermissionError):
        publishing.resolve_blocked_attempt_for_safe_replacement(
            attempt_id=blocked.identity.attempt_id,
            expected_revision=blocked.revision,
            reason=reason,
            command_id=str(uuid4()),
            context=replace(valid_context, signature="invalid"),
        )
    assert store.get_publishing_attempt(attempt.identity.attempt_id) == blocked

    resolved = publishing.resolve_blocked_attempt_for_safe_replacement(
        attempt_id=blocked.identity.attempt_id,
        expected_revision=blocked.revision,
        reason=reason,
        command_id=str(uuid4()),
        context=harness.context(action, payload),
    )
    assert resolved.operator_resolution_code == "safe_to_replace"
    assert _create_attempt(harness, publishing, job.job_id).identity.attempt_number == 2


def test_uncertain_initiation_cannot_be_cleared_by_generic_failure(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    attempt = _create_attempt(harness, publishing, job.job_id)
    intent = _transition(
        store,
        attempt,
        PublishingAttemptState.INITIATION_INTENT,
        PublishingAttemptCode.INITIATION_AUTHORIZED,
    )
    uncertain = _transition(
        store,
        intent,
        PublishingAttemptState.INITIATION_UNCERTAIN,
        PublishingAttemptCode.INITIATION_OUTCOME_UNKNOWN,
    )
    event_count = len(store.list_publishing_attempt_events(attempt.identity.attempt_id))
    for failure_code in (
        PublishingAttemptCode.PROCESSING_FAILED_CONFIRMED,
        PublishingAttemptCode.PROVIDER_REJECTED,
    ):
        with pytest.raises(PublishingAttemptConflictError):
            _transition(
                store,
                uncertain,
                PublishingAttemptState.FAILED,
                failure_code,
            )
    assert store.get_publishing_attempt(attempt.identity.attempt_id) == uncertain
    assert len(store.list_publishing_attempt_events(attempt.identity.attempt_id)) == (
        event_count
    )
    with pytest.raises(PublishingAttemptConflictError):
        _create_attempt(harness, publishing, job.job_id)


def test_publishing_attempt_mutations_require_authenticated_service_boundary(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    attempt = _create_attempt(harness, publishing, job.job_id)

    assert not hasattr(store, "transition_publishing_attempt")
    assert not hasattr(store, "record_server_confirmed_publishing_offset")
    assert not hasattr(store, "prepare_publishing_attempt_secret_reference")
    assert not hasattr(store, "update_publishing_attempt_secret_reference")
    assert not hasattr(publishing, "record_server_confirmed_publishing_offset")
    assert not hasattr(publishing, "prepare_publishing_attempt_secret_reference")
    assert not hasattr(publishing, "update_publishing_attempt_secret_reference")

    action = "content_start_publishing_attempt"
    payload = {
        "attempt_id": attempt.identity.attempt_id,
        "expected_revision": attempt.revision,
    }
    valid_context = harness.context(action, payload)
    event_count = len(store.list_publishing_attempt_events(attempt.identity.attempt_id))
    with pytest.raises(PermissionError):
        publishing.record_attempt_initiation_intent(
            attempt_id=attempt.identity.attempt_id,
            expected_revision=attempt.revision,
            context=replace(valid_context, signature="invalid"),
            render_context=harness.context(
                "content_read_clip_render_output",
                {"render_id": job.render_id},
            ),
            command_id=str(uuid4()),
        )
    assert store.get_publishing_attempt(attempt.identity.attempt_id) == attempt
    assert len(store.list_publishing_attempt_events(attempt.identity.attempt_id)) == (
        event_count
    )

    wrong_attempt_payload = {
        "attempt_id": str(uuid4()),
        "expected_revision": attempt.revision,
    }
    with pytest.raises(PermissionError):
        publishing.record_attempt_initiation_intent(
            attempt_id=attempt.identity.attempt_id,
            expected_revision=attempt.revision,
            context=harness.context(action, wrong_attempt_payload),
            render_context=harness.context(
                "content_read_clip_render_output",
                {"render_id": job.render_id},
            ),
            command_id=str(uuid4()),
        )
    assert store.get_publishing_attempt(attempt.identity.attempt_id) == attempt
    assert len(store.list_publishing_attempt_events(attempt.identity.attempt_id)) == (
        event_count
    )

    stale_payload = {
        "attempt_id": attempt.identity.attempt_id,
        "expected_revision": attempt.revision - 1,
    }
    with pytest.raises(PublishingAttemptConflictError):
        publishing.record_attempt_initiation_intent(
            attempt_id=attempt.identity.attempt_id,
            expected_revision=attempt.revision - 1,
            context=harness.context(action, stale_payload),
            render_context=harness.context(
                "content_read_clip_render_output",
                {"render_id": job.render_id},
            ),
            command_id=str(uuid4()),
        )
    assert store.get_publishing_attempt(attempt.identity.attempt_id) == attempt
    assert len(store.list_publishing_attempt_events(attempt.identity.attempt_id)) == (
        event_count
    )

    started = publishing.record_attempt_initiation_intent(
        attempt_id=attempt.identity.attempt_id,
        expected_revision=attempt.revision,
        context=harness.context(action, payload),
        render_context=harness.context(
            "content_read_clip_render_output",
            {"render_id": job.render_id},
        ),
        command_id=str(uuid4()),
    )
    assert started.state == PublishingAttemptState.INITIATION_INTENT
    assert started.revision == attempt.revision + 1
    assert store.list_publishing_attempt_events(attempt.identity.attempt_id)[-1].actor == (
        "alice"
    )


def test_store_transaction_runner_rejects_fabricated_authorization(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    attempt = _create_attempt(harness, publishing, job.job_id)
    action = "content_start_publishing_attempt"
    payload = {
        "attempt_id": attempt.identity.attempt_id,
        "expected_revision": attempt.revision,
    }
    forged_context = replace(
        harness.context(action, payload),
        signature="forged",
    )
    with pytest.raises(TypeError):
        ValidatedAuthorization(forged_context, harness.authority)
    fake_authority = type(
        "PermissiveAuthority",
        (),
        {"validate_context": lambda self, *args, **kwargs: True},
    )()
    with pytest.raises(PermissionError):
        _validated_authorization(
            forged_context,
            fake_authority,
            action=action,
            parameters=payload,
        )

    rogue_authority = WebSocketAuthenticator("R" * 43, "alice")
    rogue_socket = object()
    assert rogue_authority.authenticate(rogue_socket, "R" * 43) is not None
    operation = {
        "operationId": str(uuid4()),
        "correlationId": str(uuid4()),
        "requestFingerprint": str(uuid4()),
    }
    rogue_grant = rogue_authority.authorize(
        websocket=rogue_socket,
        operation=operation,
        purpose="content_publishing",
        request_correlation_id=operation["correlationId"],
        action=action,
        resource_fingerprint=resource_fingerprint(action, payload),
    )
    assert rogue_grant is not None
    rogue_context = AuthorizationContext.from_grant(rogue_grant)
    rogue_capability = _validated_authorization(
        rogue_context,
        rogue_authority,
        action=action,
        parameters=payload,
    )
    wrong_resource = _validated_authorization(
        harness.context("content_read_clip_render_output", {"render_id": job.render_id}),
        harness.authority,
        action="content_read_clip_render_output",
        parameters={"render_id": job.render_id},
    )

    event_count = len(store.list_publishing_attempt_events(attempt.identity.attempt_id))
    audit_count = store._connection.execute(
        """
        SELECT COUNT(*) FROM content_audit_events
        WHERE action = ? AND subject_id = ?
        """,
        (action, attempt.identity.attempt_id),
    ).fetchone()[0]
    callback_executed = False

    def operation(connection):
        nonlocal callback_executed
        callback_executed = True
        connection.execute(
            """
            INSERT INTO content_commands (
                principal, action, command_id, request_fingerprint,
                result_id, created_at
            ) VALUES ('forged', 'forged', 'forged', 'forged', 'forged', 'forged')
            """
        )
        return "forged", "success", {}

    with pytest.raises(PermissionError, match="authorization capability"):
        store.run_authorized(
            authorization=cast(ValidatedAuthorization, forged_context),
            action=action,
            payload=payload,
            command_id=str(uuid4()),
            subject_id=attempt.identity.attempt_id,
            operation=operation,
        )
    with pytest.raises(PermissionError, match="authorization capability"):
        store.run_authorized(
            authorization=rogue_capability,
            action=action,
            payload=payload,
            command_id=str(uuid4()),
            subject_id=attempt.identity.attempt_id,
            operation=operation,
        )
    with pytest.raises(PermissionError, match="authorization capability"):
        store.run_authorized(
            authorization=wrong_resource,
            action=action,
            payload=payload,
            command_id=str(uuid4()),
            subject_id=attempt.identity.attempt_id,
            operation=operation,
        )
    assert not callback_executed
    assert store.get_publishing_attempt(attempt.identity.attempt_id) == attempt
    assert len(store.list_publishing_attempt_events(attempt.identity.attempt_id)) == (
        event_count
    )
    assert store._connection.execute(
        """
        SELECT COUNT(*) FROM content_audit_events
        WHERE action = ? AND subject_id = ?
        """,
        (action, attempt.identity.attempt_id),
    ).fetchone()[0] == audit_count


def test_initiation_intent_rechecks_kill_switch_and_approval_expiry(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    attempt = _create_attempt(harness, publishing, job.job_id)

    publishing.set_kill_switch(
        enabled=True,
        reason="operator safety hold",
        context=harness.context(
            "content_set_publishing_kill_switch",
            {"enabled": True, "reason": "operator safety hold"},
        ),
        command_id=str(uuid4()),
    )
    action = "content_start_publishing_attempt"
    payload = {
        "attempt_id": attempt.identity.attempt_id,
        "expected_revision": attempt.revision,
    }
    event_count = len(store.list_publishing_attempt_events(attempt.identity.attempt_id))
    with pytest.raises(PermissionError, match="kill switch"):
        publishing.record_attempt_initiation_intent(
            attempt_id=attempt.identity.attempt_id,
            expected_revision=attempt.revision,
            context=harness.context(action, payload),
            render_context=harness.context(
                "content_read_clip_render_output",
                {"render_id": job.render_id},
            ),
            command_id=str(uuid4()),
        )
    assert store.get_publishing_attempt(attempt.identity.attempt_id) == attempt
    assert len(store.list_publishing_attempt_events(attempt.identity.attempt_id)) == (
        event_count
    )

    publishing.set_kill_switch(
        enabled=False,
        reason="operator safety hold cleared",
        context=harness.context(
            "content_set_publishing_kill_switch",
            {"enabled": False, "reason": "operator safety hold cleared"},
        ),
        command_id=str(uuid4()),
    )
    class ExpiredDateTime(datetime):
        current = datetime.now(UTC) + timedelta(days=1)

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr(publishing_module, "datetime", ExpiredDateTime)
    with pytest.raises(PermissionError, match="current exact independent release approval"):
        publishing.record_attempt_initiation_intent(
            attempt_id=attempt.identity.attempt_id,
            expected_revision=attempt.revision,
            context=harness.context(action, payload),
            render_context=harness.context(
                "content_read_clip_render_output",
                {"render_id": job.render_id},
            ),
            command_id=str(uuid4()),
        )
    assert store.get_publishing_attempt(attempt.identity.attempt_id) == attempt
    assert len(store.list_publishing_attempt_events(attempt.identity.attempt_id)) == (
        event_count
    )


def test_initiation_intent_rechecks_rights_after_attempt_creation(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    attempt = _create_attempt(harness, publishing, job.job_id)
    grant_id = store._connection.execute(
        "SELECT grant_id FROM rights_grants WHERE grantee_owner_id = ? LIMIT 1",
        (job.owner_id,),
    ).fetchone()["grant_id"]
    reason = "source authorization withdrawn"
    action = "content_revoke_rights_grant"
    payload = {"grant_id": grant_id, "reason": reason}
    harness.service.revoke_rights_grant(
        grant_id=grant_id,
        reason=reason,
        context=harness.context(action, payload),
        command_id=str(uuid4()),
    )

    action = "content_start_publishing_attempt"
    payload = {
        "attempt_id": attempt.identity.attempt_id,
        "expected_revision": attempt.revision,
    }
    event_count = len(store.list_publishing_attempt_events(attempt.identity.attempt_id))
    with pytest.raises(PermissionError):
        publishing.record_attempt_initiation_intent(
            attempt_id=attempt.identity.attempt_id,
            expected_revision=attempt.revision,
            context=harness.context(action, payload),
            render_context=harness.context(
                "content_read_clip_render_output",
                {"render_id": job.render_id},
            ),
            command_id=str(uuid4()),
        )
    assert store.get_publishing_attempt(attempt.identity.attempt_id) == attempt
    assert len(store.list_publishing_attempt_events(attempt.identity.attempt_id)) == (
        event_count
    )


def test_ambiguous_initiation_survives_restart_and_blocks_replacement(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    attempt = _create_attempt(harness, publishing, job.job_id)
    intent = _transition(
        store,
        attempt,
        PublishingAttemptState.INITIATION_INTENT,
        PublishingAttemptCode.INITIATION_AUTHORIZED,
    )
    uncertain = _transition(
        store,
        intent,
        PublishingAttemptState.INITIATION_UNCERTAIN,
        PublishingAttemptCode.INITIATION_OUTCOME_UNKNOWN,
    )
    with pytest.raises(PublishingAttemptConflictError):
        _create_attempt(harness, publishing, job.job_id)

    database = store.database_identity
    store.close()
    reopened = ContentStore(database)
    recovered = reopened.get_latest_publishing_attempt(job.job_id)
    assert recovered is not None
    assert recovered.identity.attempt_id == attempt.identity.attempt_id
    assert recovered.revision == uncertain.revision
    assert recovered.state == PublishingAttemptState.INITIATION_UNCERTAIN
    reopened.close()


def test_startup_recovery_marks_persisted_initiation_intent_uncertain(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    attempt = _create_attempt(harness, publishing, job.job_id)
    intent = _transition(
        store,
        attempt,
        PublishingAttemptState.INITIATION_INTENT,
        PublishingAttemptCode.INITIATION_AUTHORIZED,
    )
    database = store.database_identity
    store.close()

    recovered_store = ContentStore(database)
    recovered = recovered_store.get_publishing_attempt(attempt.identity.attempt_id)
    assert recovered is not None
    assert recovered.state == PublishingAttemptState.INITIATION_UNCERTAIN
    assert recovered.revision == intent.revision + 1
    event = recovered_store.list_publishing_attempt_events(
        attempt.identity.attempt_id
    )[-1]
    assert event.code == PublishingAttemptCode.INITIATION_OUTCOME_UNKNOWN
    assert event.actor == "system:startup_recovery"
    audit = recovered_store.list_audit_events(attempt.identity.attempt_id)
    assert sum(
        event["action"] == "content_recover_publishing_attempt"
        for event in audit
    ) == 1
    with pytest.raises(sqlite3.IntegrityError):
        recovered_store._connection.execute(
            """
            UPDATE content_audit_events SET decision = 'tampered'
            WHERE subject_id = ?
              AND action = 'content_recover_publishing_attempt'
            """,
            (attempt.identity.attempt_id,),
        )
    recovered_store.close()


def test_startup_rejects_schema_tampering_before_attempt_recovery(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    attempt = _create_attempt(harness, publishing, job.job_id)
    intent = _transition(
        store,
        attempt,
        PublishingAttemptState.INITIATION_INTENT,
        PublishingAttemptCode.INITIATION_AUTHORIZED,
    )
    reference = store._prepare_publishing_attempt_secret_reference(
        intent.identity.attempt_id,
        expected_revision=intent.revision,
        command_id=str(uuid4()),
        actor="system:publishing_service",
    )
    prepared = store.get_publishing_attempt(attempt.identity.attempt_id)
    store._update_publishing_attempt_secret_reference(
        attempt.identity.attempt_id,
        reference.reference.reference_id,
        expected_revision=prepared.revision,
        new_status=PublishingAttemptSecretState.ACTIVE,
        command_id=str(uuid4()),
        actor="system:publishing_service",
    )
    current = store.get_publishing_attempt(attempt.identity.attempt_id)
    _transition(
        store,
        current,
        PublishingAttemptState.SESSION_READY,
        PublishingAttemptCode.SESSION_STORED,
    )
    assert store.get_active_upload_session_handle(
        attempt.identity.attempt_id
    ) is not None

    store._connection.execute("PRAGMA foreign_keys = OFF")
    store._connection.execute(
        "DROP TRIGGER content_publishing_attempt_events_append_only_delete"
    )
    store._connection.execute(
        "DROP TRIGGER content_publishing_attempt_secret_ref_no_delete"
    )
    store._connection.execute(
        "DELETE FROM content_publishing_attempt_events WHERE reference_id = ?",
        (reference.reference.reference_id,),
    )
    store._connection.execute(
        "DELETE FROM content_publishing_attempt_secret_refs WHERE reference_id = ?",
        (reference.reference.reference_id,),
    )
    store._connection.execute("PRAGMA foreign_keys = ON")
    database = store.database_identity
    store.close()

    with pytest.raises(RuntimeError, match="incomplete or inconsistent"):
        ContentStore(database)


def test_startup_recovery_requires_same_session_reconciliation(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    attempt = _create_attempt(harness, publishing, job.job_id)
    intent = _transition(
        store,
        attempt,
        PublishingAttemptState.INITIATION_INTENT,
        PublishingAttemptCode.INITIATION_AUTHORIZED,
    )
    reference = store._prepare_publishing_attempt_secret_reference(
        intent.identity.attempt_id,
        expected_revision=intent.revision,
        command_id=str(uuid4()),
        actor="system:publishing_service",
    )
    prepared = store.get_publishing_attempt(attempt.identity.attempt_id)
    store._update_publishing_attempt_secret_reference(
        attempt.identity.attempt_id,
        reference.reference.reference_id,
        expected_revision=prepared.revision,
        new_status=PublishingAttemptSecretState.ACTIVE,
        command_id=str(uuid4()),
        actor="system:publishing_service",
    )
    active = store.get_publishing_attempt(attempt.identity.attempt_id)
    ready = _transition(
        store,
        active,
        PublishingAttemptState.SESSION_READY,
        PublishingAttemptCode.SESSION_STORED,
    )
    uploading = _transition(
        store,
        ready,
        PublishingAttemptState.UPLOADING,
        PublishingAttemptCode.UPLOAD_STARTED,
    )
    progressed = store._record_server_confirmed_publishing_offset(
        attempt.identity.attempt_id,
        expected_revision=uploading.revision,
        confirmed_offset=23,
        command_id=str(uuid4()),
        actor="system:publishing_service",
    )
    database = store.database_identity
    store.close()

    recovered_store = ContentStore(database)
    recovered = recovered_store.get_publishing_attempt(attempt.identity.attempt_id)
    assert recovered is not None
    assert recovered.state == PublishingAttemptState.RECONCILIATION_REQUIRED
    assert recovered.confirmed_offset == progressed.confirmed_offset == 23
    assert recovered.revision == progressed.revision + 1
    assert recovered_store.get_active_upload_session_handle(
        attempt.identity.attempt_id
    ) is not None
    assert recovered_store.list_publishing_attempt_events(
        attempt.identity.attempt_id
    )[-1].code == PublishingAttemptCode.REMOTE_OUTCOME_UNKNOWN
    recovered_store.close()


def test_attempt_state_machine_requires_evidence_and_confirmed_failure(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    attempt = _create_attempt(harness, publishing, job.job_id)

    with pytest.raises(PublishingAttemptConflictError):
        _transition(
            store,
            attempt,
            PublishingAttemptState.PUBLISHED,
            PublishingAttemptCode.PUBLICATION_CONFIRMED,
        )
    with pytest.raises(PublishingAttemptConflictError):
        _transition(
            store,
            attempt,
            PublishingAttemptState.INITIATION_INTENT,
            PublishingAttemptCode.INTERNAL_ERROR,
        )
    with pytest.raises(PublishingAttemptConflictError):
        _transition(
            store,
            attempt,
            PublishingAttemptState.BLOCKED,
            PublishingAttemptCode.REMOTE_OUTCOME_UNKNOWN,
        )

    intent = _transition(
        store,
        attempt,
        PublishingAttemptState.INITIATION_INTENT,
        PublishingAttemptCode.INITIATION_AUTHORIZED,
    )
    with pytest.raises(PublishingAttemptConflictError):
        _transition(
            store,
            intent,
            PublishingAttemptState.FAILED,
            PublishingAttemptCode.REMOTE_OUTCOME_UNKNOWN,
        )
    assert store.get_publishing_attempt(attempt.identity.attempt_id).state == (
        PublishingAttemptState.INITIATION_INTENT
    )


def test_confirmed_offsets_are_monotonic_bounded_and_optimistic(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    attempt = _create_attempt(harness, publishing, job.job_id)
    intent = _transition(
        store,
        attempt,
        PublishingAttemptState.INITIATION_INTENT,
        PublishingAttemptCode.INITIATION_AUTHORIZED,
    )
    reference = store._prepare_publishing_attempt_secret_reference(
        intent.identity.attempt_id,
        expected_revision=intent.revision,
        command_id=str(uuid4()),
        actor="system:publishing_service",
    )
    prepared = store.get_publishing_attempt(intent.identity.attempt_id)
    active_ref = store._update_publishing_attempt_secret_reference(
        intent.identity.attempt_id,
        reference.reference.reference_id,
        expected_revision=prepared.revision,
        new_status=PublishingAttemptSecretState.ACTIVE,
        command_id=str(uuid4()),
        actor="system:publishing_service",
    )
    assert active_ref.status == PublishingAttemptSecretState.ACTIVE
    active = store.get_publishing_attempt(intent.identity.attempt_id)
    ready = _transition(
        store,
        active,
        PublishingAttemptState.SESSION_READY,
        PublishingAttemptCode.SESSION_STORED,
    )
    uploading = _transition(
        store,
        ready,
        PublishingAttemptState.UPLOADING,
        PublishingAttemptCode.UPLOAD_STARTED,
    )

    offset_command = str(uuid4())
    progressed = store._record_server_confirmed_publishing_offset(
        uploading.identity.attempt_id,
        expected_revision=uploading.revision,
        confirmed_offset=17,
        command_id=offset_command,
        actor="system:publishing_service",
    )
    assert progressed.confirmed_offset == 17
    assert store._record_server_confirmed_publishing_offset(
        uploading.identity.attempt_id,
        expected_revision=uploading.revision,
        confirmed_offset=17,
        command_id=offset_command,
        actor="system:publishing_service",
    ) == progressed
    with pytest.raises(PublishingAttemptConflictError):
        store._record_server_confirmed_publishing_offset(
            uploading.identity.attempt_id,
            expected_revision=progressed.revision,
            confirmed_offset=16,
            command_id=str(uuid4()),
            actor="system:publishing_service",
        )
    with pytest.raises(PublishingAttemptConflictError):
        store._record_server_confirmed_publishing_offset(
            uploading.identity.attempt_id,
            expected_revision=progressed.revision,
            confirmed_offset=progressed.total_bytes + 1,
            command_id=str(uuid4()),
            actor="system:publishing_service",
        )
    with pytest.raises(PublishingAttemptConflictError):
        store._record_server_confirmed_publishing_offset(
            uploading.identity.attempt_id,
            expected_revision=uploading.revision,
            confirmed_offset=18,
            command_id=str(uuid4()),
            actor="system:publishing_service",
        )


def test_secret_reference_lifecycle_is_opaque_versioned_and_bound(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    attempt = _create_attempt(harness, publishing, job.job_id)
    intent = _transition(
        store,
        attempt,
        PublishingAttemptState.INITIATION_INTENT,
        PublishingAttemptCode.INITIATION_AUTHORIZED,
    )
    prep_command = str(uuid4())
    first_ref = store._prepare_publishing_attempt_secret_reference(
        intent.identity.attempt_id,
        expected_revision=intent.revision,
        command_id=prep_command,
        actor="system:publishing_service",
    )
    assert store._prepare_publishing_attempt_secret_reference(
        intent.identity.attempt_id,
        expected_revision=intent.revision,
        command_id=prep_command,
        actor="system:publishing_service",
    ) == first_ref
    with pytest.raises(PublishingAttemptConflictError):
        store._update_publishing_attempt_secret_reference(
            "not-the-owning-attempt",
            first_ref.reference.reference_id,
            expected_revision=store.get_publishing_attempt(
                intent.identity.attempt_id
            ).revision,
            new_status=PublishingAttemptSecretState.ACTIVE,
            command_id=str(uuid4()),
            actor="system:publishing_service",
        )

    current = store.get_publishing_attempt(intent.identity.attempt_id)
    pending_cleanup = store._update_publishing_attempt_secret_reference(
        intent.identity.attempt_id,
        first_ref.reference.reference_id,
        expected_revision=current.revision,
        new_status=PublishingAttemptSecretState.DELETION_PENDING,
        command_id=str(uuid4()),
        actor="system:publishing_service",
    )
    assert pending_cleanup.status == PublishingAttemptSecretState.DELETION_PENDING
    current = store.get_publishing_attempt(intent.identity.attempt_id)
    deletion_failed = store._update_publishing_attempt_secret_reference(
        intent.identity.attempt_id,
        first_ref.reference.reference_id,
        expected_revision=current.revision,
        new_status=PublishingAttemptSecretState.DELETION_FAILED,
        command_id=str(uuid4()),
        actor="system:publishing_service",
    )
    assert deletion_failed.status == PublishingAttemptSecretState.DELETION_FAILED
    current = store.get_publishing_attempt(intent.identity.attempt_id)
    with pytest.raises(PublishingAttemptConflictError):
        store._prepare_publishing_attempt_secret_reference(
            intent.identity.attempt_id,
            expected_revision=current.revision,
            command_id=str(uuid4()),
            actor="system:publishing_service",
        )
    with pytest.raises(PublishingAttemptConflictError):
        _create_attempt(harness, publishing, job.job_id)

    references = store._connection.execute(
        "SELECT reference_id, purpose, schema_version, generation, status "
        "FROM content_publishing_attempt_secret_refs"
    ).fetchall()
    assert len(references) == 1
    assert first_ref.reference.reference_id == references[0]["reference_id"]
    assert "session_uri" not in {column[1] for column in store._connection.execute(
        "PRAGMA table_info(content_publishing_attempt_secret_refs)"
    )}


def test_secret_reference_generations_increase_after_confirmed_invalidation(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    attempt = _create_attempt(harness, publishing, job.job_id)
    intent = _transition(
        store,
        attempt,
        PublishingAttemptState.INITIATION_INTENT,
        PublishingAttemptCode.INITIATION_AUTHORIZED,
    )
    ref1 = store._prepare_publishing_attempt_secret_reference(
        intent.identity.attempt_id,
        expected_revision=intent.revision,
        command_id=str(uuid4()),
        actor="system:publishing_service",
    )
    current = store.get_publishing_attempt(intent.identity.attempt_id)
    store._update_publishing_attempt_secret_reference(
        intent.identity.attempt_id,
        ref1.reference.reference_id,
        expected_revision=current.revision,
        new_status=PublishingAttemptSecretState.INVALIDATED,
        command_id=str(uuid4()),
        actor="system:publishing_service",
    )
    current = store.get_publishing_attempt(intent.identity.attempt_id)
    ref2 = store._prepare_publishing_attempt_secret_reference(
        intent.identity.attempt_id,
        expected_revision=current.revision,
        command_id=str(uuid4()),
        actor="system:publishing_service",
    )
    assert ref2.generation.value == ref1.generation.value + 1
    assert ref2.reference.reference_id != ref1.reference.reference_id


def test_attempt_creation_is_serialized_under_concurrent_commands(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)

    def create(command):
        try:
            return _create_attempt(harness, publishing, job.job_id, command)
        except PublishingAttemptConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, (str(uuid4()), str(uuid4()))))
    created = [result for result in results if result is not None]
    assert len(created) == 1
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_publishing_attempts WHERE job_id = ?",
        (job.job_id,),
    ).fetchone()[0] == 1


def test_attempt_creation_is_serialized_across_independent_connections(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    second_store = ContentStore(store.database_identity)
    second_publishing = _publishing_for_store(publishing, second_store)
    barrier = threading.Barrier(2)

    def create(service, command):
        barrier.wait(timeout=5)
        try:
            return _create_attempt(harness, service, job.job_id, command)
        except PublishingAttemptConflictError:
            return None

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda args: create(*args),
                    (
                        (publishing, str(uuid4())),
                        (second_publishing, str(uuid4())),
                    ),
                )
            )
        created = [result for result in results if result is not None]
        assert len(created) == 1
        for connection in (store._connection, second_store._connection):
            assert connection.execute(
                "SELECT COUNT(*) FROM content_publishing_attempts WHERE job_id = ?",
                (job.job_id,),
            ).fetchone()[0] == 1
            assert connection.execute(
                """
                SELECT COUNT(*) FROM content_publishing_attempt_events
                WHERE attempt_id = ?
                """,
                (created[0].identity.attempt_id,),
            ).fetchone()[0] == 1

        attempt = created[0]
        transition_barrier = threading.Barrier(2)

        def start_attempt(service):
            transition_barrier.wait(timeout=5)
            action = "content_start_publishing_attempt"
            payload = {
                "attempt_id": attempt.identity.attempt_id,
                "expected_revision": attempt.revision,
            }
            try:
                return service.record_attempt_initiation_intent(
                    attempt_id=attempt.identity.attempt_id,
                    expected_revision=attempt.revision,
                    context=harness.context(action, payload),
                    render_context=harness.context(
                        "content_read_clip_render_output",
                        {"render_id": job.render_id},
                    ),
                    command_id=str(uuid4()),
                )
            except PublishingAttemptConflictError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            transition_results = list(
                pool.map(start_attempt, (publishing, second_publishing))
            )
        transitioned = [item for item in transition_results if item is not None]
        assert len(transitioned) == 1
        assert transitioned[0].state == PublishingAttemptState.INITIATION_INTENT
        assert store.get_publishing_attempt(attempt.identity.attempt_id).revision == (
            attempt.revision + 1
        )
        assert len(
            store.list_publishing_attempt_events(attempt.identity.attempt_id)
        ) == 2
    finally:
        second_store.close()


def test_attempt_creation_is_serialized_across_independent_processes(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(
            target=_create_attempt_in_process,
            args=(store.database_identity, job.job_id, barrier, results, str(uuid4())),
        )
        for _ in range(2)
    ]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=30)
        assert all(not process.is_alive() for process in processes)
        assert [process.exitcode for process in processes] == [0, 0]
        outcomes = [results.get(timeout=5) for _ in processes]
        assert sorted(outcome[0] for outcome in outcomes) == ["conflict", "created"]
        created_id = next(outcome[1] for outcome in outcomes if outcome[0] == "created")
        assert store._connection.execute(
            "SELECT COUNT(*) FROM content_publishing_attempts WHERE job_id = ?",
            (job.job_id,),
        ).fetchone()[0] == 1
        assert store._connection.execute(
            """
            SELECT COUNT(*) FROM content_publishing_attempt_events
            WHERE attempt_id = ?
            """,
            (created_id,),
        ).fetchone()[0] == 1
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        results.close()


def test_attempt_idempotency_is_stable_across_connections_and_rejects_conflicts(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    second_store = ContentStore(store.database_identity)
    second_publishing = _publishing_for_store(publishing, second_store)
    command_id = str(uuid4())
    try:
        barrier = threading.Barrier(2)

        def create(service):
            barrier.wait(timeout=5)
            return _create_attempt(harness, service, job.job_id, command_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, (publishing, second_publishing)))
        original, replay = results
        assert replay == original

        action = "content_create_publishing_attempt"
        conflicting_payload = {"job_id": "different-job"}
        authorization = second_publishing.content_rights._authorize(
            harness.context(action, conflicting_payload),
            action=action,
            payload=conflicting_payload,
        )
        with pytest.raises(IdempotencyConflictError):
            second_store.run_authorized(
                authorization=authorization,
                action=action,
                payload=conflicting_payload,
                command_id=command_id,
                subject_id="different-job",
                operation=lambda connection: ("wrong", "success", {}),
            )
        assert store._connection.execute(
            "SELECT COUNT(*) FROM content_publishing_attempts"
        ).fetchone()[0] == 1
    finally:
        second_store.close()


def test_authorized_operation_rolls_back_partial_mutations_on_failure(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    attempt = _create_attempt(harness, publishing, job.job_id)
    action = "content_start_publishing_attempt"
    payload = {
        "attempt_id": attempt.identity.attempt_id,
        "expected_revision": attempt.revision,
    }
    context = harness.context(action, payload)
    authorization = publishing.content_rights._authorize(
        context,
        action=action,
        payload=payload,
    )
    command_id = str(uuid4())

    def fail_after_partial_write(connection):
        connection.execute(
            """
            INSERT INTO content_commands (
                principal, action, command_id, request_fingerprint,
                result_id, created_at
            ) VALUES ('alice', ?, ?, 'partial', 'partial', 'partial')
            """,
            (action, command_id),
        )
        raise RuntimeError("simulated operation failure")

    with pytest.raises(RuntimeError, match="simulated operation failure"):
        store.run_authorized(
            authorization=authorization,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=attempt.identity.attempt_id,
            operation=fail_after_partial_write,
        )

    assert store.get_publishing_attempt(attempt.identity.attempt_id) == attempt
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_commands WHERE command_id = ?",
        (command_id,),
    ).fetchone()[0] == 0
    assert store._connection.execute(
        "SELECT COUNT(*) FROM authorization_claims WHERE grant_id = ?",
        (context.grant_id,),
    ).fetchone()[0] == 0
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_audit_events WHERE action = ? AND subject_id = ?",
        (action, attempt.identity.attempt_id),
    ).fetchone()[0] == 0


def test_database_lock_failure_leaves_attempt_creation_retryable(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    blocker = ContentStore(store.database_identity)
    store._connection.execute("PRAGMA busy_timeout = 25")
    try:
        blocker._connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            _create_attempt(harness, publishing, job.job_id)
        assert store.get_latest_publishing_attempt(job.job_id) is None
        assert store._connection.execute(
            "SELECT COUNT(*) FROM content_audit_events WHERE action = ?",
            ("content_create_publishing_attempt",),
        ).fetchone()[0] == 0
        blocker._connection.rollback()

        retried = _create_attempt(harness, publishing, job.job_id)
        assert store.get_latest_publishing_attempt(job.job_id) == retried
    finally:
        if blocker._connection.in_transaction:
            blocker._connection.rollback()
        blocker.close()


def test_explicit_migration_rolls_back_on_interruption(
    tmp_path,
    monkeypatch,
):
    harness, _, _ = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    set_legacy_schema_version(store, 7)
    original = ContentStore._create_publishing_attempt_tables

    def interrupted(connection):
        connection.execute(
            "CREATE TABLE content_attempt_migration_partial (value TEXT)"
        )
        raise RuntimeError("simulated interrupted migration")

    monkeypatch.setattr(
        ContentStore,
        "_create_publishing_attempt_tables",
        staticmethod(interrupted),
    )
    with pytest.raises(RuntimeError, match="simulated interrupted migration"):
        store.migrate_publishing_attempt_schema()
    assert store._connection.execute(
        "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
    ).fetchone()[0] == 7
    assert store._connection.execute(
        """
        SELECT COUNT(*) FROM sqlite_master
        WHERE type = 'table' AND name = 'content_attempt_migration_partial'
        """
    ).fetchone()[0] == 0
    assert store._connection.execute(
        """
        SELECT COUNT(*) FROM sqlite_master
        WHERE type = 'table' AND name = 'content_publishing_attempts'
        """
    ).fetchone()[0] == 0
    monkeypatch.setattr(
        ContentStore,
        "_create_publishing_attempt_tables",
        staticmethod(original),
    )
    assert store.migrate_publishing_attempt_schema() == 9


def test_migration_rejects_unknown_or_non_v7_schema(tmp_path, monkeypatch):
    harness, _, _ = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    with store._transaction() as connection:
        connection.execute(
            "UPDATE content_schema_meta SET schema_version = 6 WHERE singleton = 1"
        )
    with pytest.raises(RuntimeError, match="exact schema-v7"):
        store.migrate_publishing_attempt_schema()
    with store._transaction() as connection:
        connection.execute(
            "UPDATE content_schema_meta SET schema_version = 99 WHERE singleton = 1"
        )
    with pytest.raises(RuntimeError, match="exact schema-v7"):
        store.migrate_publishing_attempt_schema()


@pytest.mark.parametrize(
    ("object_type", "object_name", "replacement_sql"),
    (
        (
            "trigger",
            "content_publishing_attempt_events_append_only_update",
            """
            CREATE TRIGGER content_publishing_attempt_events_append_only_update
            BEFORE UPDATE ON content_publishing_attempt_events
            BEGIN SELECT RAISE(ABORT, 'unexpected trigger definition'); END
            """,
        ),
        (
            "index",
            "content_publishing_attempt_events_order",
            """
            CREATE INDEX content_publishing_attempt_events_order
            ON content_publishing_attempt_events(attempt_id)
            """,
        ),
        (
            "trigger",
            "content_publishing_attempt_state_transition",
            """
            CREATE TRIGGER content_publishing_attempt_state_transition
            BEFORE UPDATE OF state ON content_publishing_attempts
            WHEN NEW.state != OLD.state
             AND NOT (OLD.state = 'created' AND NEW.state = 'blocked')
            BEGIN
                SELECT RAISE(ABORT, 'altered state transition');
            END
            """,
        ),
    ),
)
def test_schema_verification_rejects_altered_same_named_objects(
    tmp_path,
    monkeypatch,
    object_type,
    object_name,
    replacement_sql,
):
    harness, _, _ = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    with store._transaction() as connection:
        connection.execute(f"DROP {object_type.upper()} {object_name}")
        connection.execute(replacement_sql)
    with pytest.raises(RuntimeError, match="incomplete or inconsistent"):
        ContentStore(store.database_identity)
    with pytest.raises(RuntimeError, match="schema definitions are inconsistent"):
        store.migrate_publishing_attempt_schema()


@pytest.mark.parametrize(
    ("object_type", "object_name"),
    (
        ("table", "content_publishing_attempts"),
        ("index", "content_publishing_attempt_events_order"),
        ("trigger", "content_publishing_attempt_events_append_only_update"),
    ),
)
def test_startup_rejects_missing_publishing_attempt_schema_objects(
    tmp_path,
    monkeypatch,
    object_type,
    object_name,
):
    harness, _, _ = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    database = store.database_identity
    with store._transaction() as connection:
        connection.execute(f"DROP {object_type.upper()} {object_name}")
    store.close()

    with pytest.raises(RuntimeError, match="incomplete or inconsistent"):
        ContentStore(database)


def test_append_only_attempt_history_and_immutable_identity(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    attempt = _create_attempt(harness, publishing, job.job_id)
    with pytest.raises(sqlite3.IntegrityError):
        store._connection.execute(
            "UPDATE content_publishing_attempts SET owner_id = 'mallory' "
            "WHERE attempt_id = ?",
            (attempt.identity.attempt_id,),
        )
    with pytest.raises(sqlite3.IntegrityError):
        store._connection.execute(
            "UPDATE content_publishing_attempt_events SET actor = 'mallory' "
            "WHERE attempt_id = ?",
            (attempt.identity.attempt_id,),
        )
    with pytest.raises(sqlite3.IntegrityError):
        store._connection.execute(
            "DELETE FROM content_publishing_attempt_events WHERE attempt_id = ?",
            (attempt.identity.attempt_id,),
        )
    assert len(store.list_publishing_attempt_events(attempt.identity.attempt_id)) == 1


def test_secret_reference_values_never_enter_attempt_persistence(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    attempt = _create_attempt(harness, publishing, job.job_id)
    intent = _transition(
        store,
        attempt,
        PublishingAttemptState.INITIATION_INTENT,
        PublishingAttemptCode.INITIATION_AUTHORIZED,
    )
    reference = store._prepare_publishing_attempt_secret_reference(
        intent.identity.attempt_id,
        expected_revision=intent.revision,
        command_id=str(uuid4()),
        actor="system:publishing_service",
    )
    serialized = store._canonical(
        {
            "attempt": store.get_publishing_attempt(
                attempt.identity.attempt_id
            ).model_dump(mode="json"),
            "reference": reference.model_dump(mode="json"),
            "events": [
                event.model_dump(mode="json")
                for event in store.list_publishing_attempt_events(
                    attempt.identity.attempt_id
                )
            ],
        }
    )
    forbidden_names = (
        "session_uri",
        "access_token",
        "refresh_token",
        "authorization_header",
        "provider_response_body",
    )
    columns = {
        row["name"]
        for table in (
            "content_publishing_attempts",
            "content_publishing_attempt_secret_refs",
            "content_publishing_attempt_events",
        )
        for row in store._connection.execute(f"PRAGMA table_info({table})")
    }
    assert not any(name in columns for name in forbidden_names)
    assert reference.reference.reference_id in serialized
    assert len(store.list_publishing_attempt_events(attempt.identity.attempt_id)) == 3


def test_operator_safe_replacement_is_audited_and_explicit(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    attempt = _create_attempt(harness, publishing, job.job_id)
    blocked = _transition(
        store,
        attempt,
        PublishingAttemptState.BLOCKED,
        PublishingAttemptCode.AUTHORIZATION_BLOCKED,
    )
    action = "content_resolve_publishing_attempt_safe_to_replace"
    reason = "operator confirmed retry disposition"
    payload = {
        "attempt_id": blocked.identity.attempt_id,
        "expected_revision": blocked.revision,
        "resolution_code": "safe_to_replace",
        "reason": reason,
    }
    event_count = len(store.list_publishing_attempt_events(
        blocked.identity.attempt_id
    ))
    invalid_context = replace(harness.context(action, payload), signature="invalid")
    with pytest.raises(PermissionError):
        publishing.resolve_blocked_attempt_for_safe_replacement(
            attempt_id=blocked.identity.attempt_id,
            expected_revision=blocked.revision,
            reason=reason,
            command_id=str(uuid4()),
            context=invalid_context,
        )
    assert store.get_publishing_attempt(blocked.identity.attempt_id) == blocked
    assert len(
        store.list_publishing_attempt_events(blocked.identity.attempt_id)
    ) == event_count
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_audit_events WHERE action = ? AND subject_id = ?",
        (action, blocked.identity.attempt_id),
    ).fetchone()[0] == 0

    resolved = publishing.resolve_blocked_attempt_for_safe_replacement(
        attempt_id=blocked.identity.attempt_id,
        expected_revision=blocked.revision,
        reason=reason,
        command_id=str(uuid4()),
        context=harness.context(action, payload),
    )
    assert resolved.state == PublishingAttemptState.BLOCKED
    assert resolved.operator_resolution_code == "safe_to_replace"
    assert resolved.operator_resolved_by == "alice"
    assert any(
        event.event_type == "operator_resolution"
        and event.code == PublishingAttemptCode.OPERATOR_SAFE_TO_REPLACE
        for event in store.list_publishing_attempt_events(
            blocked.identity.attempt_id
        )
    )
    audit = store._connection.execute(
        """
        SELECT details_json FROM content_audit_events
        WHERE action = ? AND subject_id = ?
        ORDER BY event_id DESC LIMIT 1
        """,
        (action, blocked.identity.attempt_id),
    ).fetchone()
    assert audit is not None
    assert reason in audit["details_json"]
    assert (
        _create_attempt(harness, publishing, job.job_id).identity.attempt_number
        == 2
    )


def test_operator_replacement_is_serialized_across_independent_connections(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    second_store = ContentStore(store.database_identity)
    second_publishing = _publishing_for_store(publishing, second_store)
    attempt = _create_attempt(harness, publishing, job.job_id)
    blocked = _transition(
        store,
        attempt,
        PublishingAttemptState.BLOCKED,
        PublishingAttemptCode.AUTHORIZATION_BLOCKED,
    )
    action = "content_resolve_publishing_attempt_safe_to_replace"
    reason = "operator confirmed retry disposition"
    payload = {
        "attempt_id": blocked.identity.attempt_id,
        "expected_revision": blocked.revision,
        "resolution_code": "safe_to_replace",
        "reason": reason,
    }
    barrier = threading.Barrier(2)

    def resolve(service):
        barrier.wait(timeout=5)
        try:
            return service.resolve_blocked_attempt_for_safe_replacement(
                attempt_id=blocked.identity.attempt_id,
                expected_revision=blocked.revision,
                reason=reason,
                command_id=str(uuid4()),
                context=harness.context(action, payload),
            )
        except PublishingAttemptConflictError:
            return None

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(resolve, (publishing, second_publishing)))
        resolved = [item for item in results if item is not None]
        assert len(resolved) == 1
        final = store.get_publishing_attempt(blocked.identity.attempt_id)
        assert final is not None
        assert final.operator_resolution_code == "safe_to_replace"
        assert final.revision == blocked.revision + 1
        assert len(
            store.list_publishing_attempt_events(blocked.identity.attempt_id)
        ) == 3
        assert store._connection.execute(
            "SELECT COUNT(*) FROM content_audit_events WHERE action = ? AND subject_id = ?",
            (action, blocked.identity.attempt_id),
        ).fetchone()[0] == 1
    finally:
        second_store.close()


def test_attempt_creation_requires_owner_and_respects_kill_switch(
    tmp_path,
    monkeypatch,
):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    action = "content_create_publishing_attempt"
    payload = {"job_id": job.job_id}
    invalid_context = replace(harness.context(action, payload), signature="invalid")
    with pytest.raises(PermissionError):
        publishing.create_attempt(
            job_id=job.job_id,
            context=invalid_context,
            render_context=harness.context(
                "content_read_clip_render_output",
                {"render_id": job.render_id},
            ),
            command_id=str(uuid4()),
        )
    assert store.get_latest_publishing_attempt(job.job_id) is None
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_audit_events WHERE action = ?",
        (action,),
    ).fetchone()[0] == 0

    reviewer_authority, reviewer_websocket, _ = _reviewer_setup(harness)
    with pytest.raises(PermissionError):
        publishing.create_attempt(
            job_id=job.job_id,
            context=_context(
                reviewer_authority,
                reviewer_websocket,
                action,
                payload,
            ),
            render_context=harness.context(
                "content_read_clip_render_output",
                {"render_id": job.render_id},
            ),
            command_id=str(uuid4()),
        )

    control_payload = {"enabled": True, "reason": "test_attempt_block"}
    publishing.set_kill_switch(
        enabled=True,
        reason="test_attempt_block",
        context=harness.context(
            "content_set_publishing_kill_switch",
            control_payload,
        ),
        command_id=str(uuid4()),
    )
    with pytest.raises(PermissionError, match="kill switch"):
        _create_attempt(harness, publishing, job.job_id)
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_publishing_attempts WHERE job_id = ?",
        (job.job_id,),
    ).fetchone()[0] == 0


def test_attempt_creation_rechecks_approval_expiry(tmp_path, monkeypatch):
    harness, job, publishing = _approved_job(tmp_path, monkeypatch)
    store = harness.store
    _migrate(store)
    store._connection.execute(
        "DROP TRIGGER content_publishing_approvals_no_update"
    )
    store._connection.execute(
        """
        UPDATE content_publishing_approvals
        SET expires_at = '2000-01-01T00:00:00+00:00'
        WHERE job_id = ?
        """,
        (job.job_id,),
    )

    with pytest.raises(PermissionError, match="approval"):
        _create_attempt(harness, publishing, job.job_id)
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_publishing_attempts WHERE job_id = ?",
        (job.job_id,),
    ).fetchone()[0] == 0
