from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import pytest

from tests.application import test_content_lifecycle as lifecycle
from ultron.application.content.processing import (
    LocalTemporaryContentStore,
    SyntheticContentProcessor,
)
from ultron.application.content.service import ContentRightsService
from ultron.application.content.store import ContentStore
from ultron.domain.content import (
    ContentLifecycleState,
    ContentTargetContext,
    ProcessingJobState,
    RightsAction,
)


SYNTHETIC_BYTES = b"ULTRON-SYNTHETIC:bounded test payload"


def test_source_video_size_limit_is_configurable_and_bounded():
    environment = os.environ.copy()
    environment["ULTRON_CRE_MAX_SOURCE_BYTES"] = str(8 * 1024 * 1024)
    configured = subprocess.run(
        [
            sys.executable,
            "-c",
            "from ultron.application.content.limits import MAX_SOURCE_BYTES; "
            "print(MAX_SOURCE_BYTES)",
        ],
        capture_output=True,
        check=False,
        env=environment,
        timeout=10,
    )
    assert configured.returncode == 0
    assert configured.stdout.strip() == str(8 * 1024 * 1024).encode()

    environment["ULTRON_CRE_MAX_SOURCE_BYTES"] = str(33 * 1024 * 1024)
    rejected = subprocess.run(
        [
            sys.executable,
            "-c",
            "from ultron.application.content.limits import MAX_SOURCE_BYTES",
        ],
        capture_output=True,
        check=False,
        env=environment,
        timeout=10,
    )
    assert rejected.returncode != 0


def _harness(
    tmp_path,
    monkeypatch,
    *,
    with_rights: bool = True,
    media_bytes: bytes = SYNTHETIC_BYTES,
    rights_actions=frozenset({RightsAction.ANALYZE}),
    rights_valid_until=None,
):
    monkeypatch.setattr(lifecycle, "MEDIA", media_bytes)
    monkeypatch.setattr(
        lifecycle,
        "MEDIA_SHA256",
        hashlib.sha256(media_bytes).hexdigest(),
    )
    harness = lifecycle.Harness(tmp_path / "content.db")
    storage = LocalTemporaryContentStore(tmp_path / "managed-content")
    harness.service = ContentRightsService(
        store=harness.store,
        authority=harness.authority,
        reviewer_principals=harness.service.reviewer_principals,
        content_storage=storage,
        processor=SyntheticContentProcessor(),
    )
    asset = lifecycle._register_asset(harness)
    grant = (
        lifecycle._add_analyze_grant(
            harness,
            valid_until=rights_valid_until,
            actions=rights_actions,
        )
        if with_rights
        else None
    )
    return harness, asset, grant, storage


def _use():
    return lifecycle._rights_use()


def _create_job(harness, use=None):
    use = use or _use()
    target = ContentTargetContext.YOUTUBE_SHORTS
    payload = {
        "use": use.model_dump(mode="json"),
        "target_context": target.value,
    }
    return harness.invoke(
        "content_create_processing_job",
        payload,
        harness.service.create_processing_job,
        use=use,
        target_context=target,
    )


def _run_job(harness, job_id, *, command_id=None):
    payload = {"job_id": job_id}
    return harness.invoke(
        "content_run_processing_job",
        payload,
        harness.service.run_processing_job,
        job_id=job_id,
        **({"command_id": command_id} if command_id else {}),
    )


def _cancel_job(harness, job_id):
    return harness.invoke(
        "content_cancel_processing_job",
        {"job_id": job_id},
        harness.service.cancel_processing_job,
        job_id=job_id,
    )


def test_authorized_job_revalidates_rights_and_persists_output(tmp_path, monkeypatch):
    h, asset, _, storage = _harness(tmp_path, monkeypatch)
    job = _create_job(h)
    assert job.state == ProcessingJobState.QUEUED
    assert job.asset_sha256 == asset.content_sha256
    assert job.rights_use.actions == frozenset({RightsAction.ANALYZE})

    completed = _run_job(h, job.job_id)

    assert completed.state == ProcessingJobState.SUCCEEDED
    output = h.store.get_processing_output(job.job_id)
    assert output is not None
    output_context = h.context(
        "content_read_processing_output",
        {"job_id": job.job_id},
    )
    result = h.service.get_processing_output_bytes(
        job.job_id,
        context=output_context,
    )
    assert hashlib.sha256(result).hexdigest() == output.content_sha256
    assert storage.read_output(output.output_key, output.content_sha256) == result
    assert [event.to_state for event in h.store.list_processing_events(job.job_id)] == [
        ProcessingJobState.QUEUED,
        ProcessingJobState.RUNNING,
        ProcessingJobState.SUCCEEDED,
    ]


def test_managed_asset_storage_does_not_enable_synthetic_processing(tmp_path, monkeypatch):
    h, asset, _, storage = _harness(tmp_path, monkeypatch)
    h.service = ContentRightsService(
        store=h.store,
        authority=h.authority,
        reviewer_principals=h.service.reviewer_principals,
        content_storage=storage,
    )

    with pytest.raises(RuntimeError, match="Synthetic processing is not configured"):
        h.service.create_processing_job(
            _use(),
            target_context=ContentTargetContext.YOUTUBE_SHORTS,
            context=None,
            command_id="storage-only",
        )

    assert h.store.get_asset(asset.asset_id) == asset


def test_missing_rights_denies_job_creation_without_dispatch(tmp_path, monkeypatch):
    h, _, _, _ = _harness(tmp_path, monkeypatch, with_rights=False)

    with pytest.raises(PermissionError, match="Current rights"):
        _create_job(h)

    assert h.store._connection.execute(
        "SELECT COUNT(*) FROM content_processing_jobs"
    ).fetchone()[0] == 0


def test_revoked_rights_between_queue_and_claim_prevent_dispatch(tmp_path, monkeypatch):
    h, _, grant, _ = _harness(tmp_path, monkeypatch)
    job = _create_job(h)
    h.invoke(
        "content_revoke_rights_grant",
        {"grant_id": grant.grant_id, "reason": "synthetic revocation"},
        h.service.revoke_rights_grant,
        grant_id=grant.grant_id,
        reason="synthetic revocation",
    )
    called = False

    def processor(*args, **kwargs):
        nonlocal called
        called = True
        return b"should not run"

    h.service.processor.process = processor
    with pytest.raises(RuntimeError, match="failed before processor"):
        _run_job(h, job.job_id)

    assert not called
    assert h.store.get_processing_job(job.job_id).state == ProcessingJobState.FAILED


def test_duplicate_command_is_idempotent_and_conflicting_reuse_rejected(
    tmp_path, monkeypatch
):
    h, _, _, _ = _harness(tmp_path, monkeypatch)
    use = _use()
    target = ContentTargetContext.YOUTUBE_SHORTS
    payload = {"use": use.model_dump(mode="json"), "target_context": target.value}
    first = h.invoke(
        "content_create_processing_job",
        payload,
        h.service.create_processing_job,
        use=use,
        target_context=target,
        command_id="stable-job-command",
    )
    replay = h.invoke(
        "content_create_processing_job",
        payload,
        h.service.create_processing_job,
        use=use,
        target_context=target,
        command_id="stable-job-command",
    )
    assert replay.job_id == first.job_id
    assert h.store._connection.execute(
        "SELECT COUNT(*) FROM content_processing_jobs"
    ).fetchone()[0] == 1

    wrong_use = use.model_copy(update={"territory": "CA"})
    wrong_payload = {
        "use": wrong_use.model_dump(mode="json"),
        "target_context": target.value,
    }
    with pytest.raises(ValueError, match="different content"):
        h.invoke(
            "content_create_processing_job",
            wrong_payload,
            h.service.create_processing_job,
            use=wrong_use,
            target_context=target,
            command_id="stable-job-command",
        )


def test_concurrent_claims_dispatch_processor_once(tmp_path, monkeypatch):
    h, _, _, _ = _harness(tmp_path, monkeypatch)
    job = _create_job(h)
    calls = 0
    lock = threading.Lock()
    original = h.service.processor.process

    def counted(content, *, max_output_bytes):
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.03)
        return original(content, max_output_bytes=max_output_bytes)

    h.service.processor.process = counted

    def run():
        try:
            return _run_job(h, job.job_id)
        except PermissionError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: run(), range(2)))

    assert calls == 1
    assert sum(result is not None for result in results) == 1
    assert h.store.get_processing_job(job.job_id).state == ProcessingJobState.SUCCEEDED


def test_concurrent_duplicate_worker_command_dispatches_once(tmp_path, monkeypatch):
    h, _, _, _ = _harness(tmp_path, monkeypatch)
    job = _create_job(h)
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    original = h.service.processor.process

    def blocked(content, *, max_output_bytes):
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(timeout=2)
        return original(content, max_output_bytes=max_output_bytes)

    h.service.processor.process = blocked
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            _run_job,
            h,
            job.job_id,
            command_id="duplicate-worker-command",
        )
        assert entered.wait(timeout=2)
        second = executor.submit(
            _run_job,
            h,
            job.job_id,
            command_id="duplicate-worker-command",
        )
        replay = second.result(timeout=2)
        release.set()
        completed = first.result(timeout=2)

    assert calls == 1
    assert replay.state == ProcessingJobState.RUNNING
    assert completed.state == ProcessingJobState.SUCCEEDED


def test_queued_cancellation_never_dispatches(tmp_path, monkeypatch):
    h, _, _, _ = _harness(tmp_path, monkeypatch)
    job = _create_job(h)
    cancelled = _cancel_job(h, job.job_id)
    assert cancelled.state == ProcessingJobState.CANCELLED
    with pytest.raises(PermissionError, match="not available"):
        _run_job(h, job.job_id)


def test_running_cancellation_is_uncertain_and_discards_output(tmp_path, monkeypatch):
    h, _, _, _ = _harness(tmp_path, monkeypatch)
    job = _create_job(h)
    entered = threading.Event()
    release = threading.Event()
    original = h.service.processor.process

    def blocked(content, *, max_output_bytes):
        entered.set()
        assert release.wait(timeout=2)
        return original(content, max_output_bytes=max_output_bytes)

    h.service.processor.process = blocked
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_run_job, h, job.job_id)
        assert entered.wait(timeout=2)
        cancelled = _cancel_job(h, job.job_id)
        release.set()
        result = future.result(timeout=2)

    assert cancelled.state == ProcessingJobState.UNCERTAIN
    assert result.state == ProcessingJobState.UNCERTAIN
    assert h.store.get_processing_output(job.job_id) is None


def test_invalid_or_tampered_input_fails_without_dispatch(tmp_path, monkeypatch):
    h, asset, _, storage = _harness(tmp_path, monkeypatch)
    job = _create_job(h)
    (storage._root / "inputs" / asset.storage_key).write_bytes(b"tampered bytes")
    called = False

    def processor(*args, **kwargs):
        nonlocal called
        called = True
        return b"not dispatched"

    h.service.processor.process = processor
    with pytest.raises(RuntimeError, match="failed before processor"):
        _run_job(h, job.job_id)
    assert not called
    assert h.store.get_processing_job(job.job_id).state == ProcessingJobState.FAILED


def test_oversized_input_is_rejected_and_not_registered(tmp_path, monkeypatch):
    h, _, _, storage = _harness(tmp_path, monkeypatch, with_rights=False)
    payload = b"x" * (storage.MAX_INPUT_BYTES + 1)
    asset = lifecycle._asset().model_copy(
        update={
            "asset_id": "asset-oversized",
            "storage_key": "asset:oversized",
            "content_sha256": hashlib.sha256(payload).hexdigest(),
        }
    )
    with pytest.raises(ValueError, match="size limit"):
        h.invoke(
            "content_register_asset",
            asset.model_dump(mode="json"),
            h.service.register_asset,
            asset=asset,
            source_stream=BytesIO(payload),
        )
    assert h.store.get_asset(asset.asset_id) is None


def test_processor_exception_is_durably_failed(tmp_path, monkeypatch):
    h, _, _, _ = _harness(tmp_path, monkeypatch)
    job = _create_job(h)

    def explode(*args, **kwargs):
        raise RuntimeError("synthetic processor failure")

    h.service.processor.process = explode
    with pytest.raises(RuntimeError, match="synthetic processor failure"):
        _run_job(h, job.job_id)
    assert h.store.get_processing_job(job.job_id).state == ProcessingJobState.FAILED


def test_invalid_synthetic_payload_is_failed(tmp_path, monkeypatch):
    h, _, _, _ = _harness(
        tmp_path,
        monkeypatch,
        media_bytes=b"not a synthetic payload",
    )
    job = _create_job(h)
    with pytest.raises(ValueError, match="synthetic test payload"):
        _run_job(h, job.job_id)
    assert h.store.get_processing_job(job.job_id).state == ProcessingJobState.FAILED


def test_processor_timeout_is_uncertain_and_not_retried(tmp_path, monkeypatch):
    h, _, _, _ = _harness(tmp_path, monkeypatch)
    job = _create_job(h)
    h.service.MAX_PROCESSING_SECONDS = 0.001

    def slow(content, *, max_output_bytes):
        time.sleep(0.02)
        return b"late result"

    h.service.processor.process = slow
    with pytest.raises(TimeoutError):
        _run_job(h, job.job_id)
    assert h.store.get_processing_job(job.job_id).state == ProcessingJobState.UNCERTAIN
    with pytest.raises(PermissionError):
        _run_job(h, job.job_id)


def test_storage_output_failure_is_not_reported_as_success(tmp_path, monkeypatch):
    h, _, _, storage = _harness(tmp_path, monkeypatch)
    job = _create_job(h)

    def fail_write(*args, **kwargs):
        raise OSError("injected output storage failure")

    storage.put_output = fail_write
    with pytest.raises(OSError, match="injected output storage"):
        _run_job(h, job.job_id)
    assert h.store.get_processing_job(job.job_id).state == ProcessingJobState.RUNNING
    assert h.store.get_processing_output(job.job_id) is None


def test_audit_failure_rolls_back_job_and_authorization_claim(tmp_path, monkeypatch):
    h, _, _, _ = _harness(tmp_path, monkeypatch)
    h.store._connection.execute(
        """
        CREATE TRIGGER fail_processing_audit
        BEFORE INSERT ON content_audit_events
        WHEN NEW.action = 'content_create_processing_job'
        BEGIN SELECT RAISE(ABORT, 'injected processing audit failure'); END
        """
    )
    with pytest.raises(sqlite3.IntegrityError, match="injected processing"):
        _create_job(h)
    assert h.store._connection.execute(
        "SELECT COUNT(*) FROM content_processing_jobs"
    ).fetchone()[0] == 0
    assert h.store._connection.execute(
        "SELECT COUNT(*) FROM authorization_claims "
        "WHERE action = 'content_create_processing_job'"
    ).fetchone()[0] == 0


def test_output_audit_failure_cleans_output_and_restart_marks_uncertain(
    tmp_path, monkeypatch
):
    h, _, _, storage = _harness(tmp_path, monkeypatch)
    job = _create_job(h)
    h.store._connection.execute(
        """
        CREATE TRIGGER fail_processing_terminal_audit
        BEFORE INSERT ON content_audit_events
        WHEN NEW.action = 'content_processing_succeeded'
        BEGIN SELECT RAISE(ABORT, 'injected output audit failure'); END
        """
    )
    with pytest.raises(sqlite3.IntegrityError, match="injected output"):
        _run_job(h, job.job_id)
    assert not (storage._root / "outputs" / f"output-{job.job_id}").exists()
    assert h.store.get_processing_job(job.job_id).state == ProcessingJobState.RUNNING

    database = tmp_path / "content.db"
    h.close()
    reopened = ContentStore(database)
    recovered = reopened.get_processing_job(job.job_id)
    assert recovered.state == ProcessingJobState.UNCERTAIN
    assert [event.to_state for event in reopened.list_processing_events(job.job_id)][-1] == (
        ProcessingJobState.UNCERTAIN
    )
    reopened.close()


def test_output_cleanup_failure_is_reported_and_orphan_can_be_recovered(
    tmp_path, monkeypatch
):
    h, _, _, storage = _harness(tmp_path, monkeypatch)
    job = _create_job(h)
    h.store._connection.execute(
        """
        CREATE TRIGGER fail_processing_terminal_audit
        BEFORE INSERT ON content_audit_events
        WHEN NEW.action = 'content_processing_succeeded'
        BEGIN SELECT RAISE(ABORT, 'injected output audit failure'); END
        """
    )
    storage.delete_output = lambda key: (_ for _ in ()).throw(
        OSError("injected cleanup failure")
    )
    with pytest.raises(RuntimeError, match="cleanup failed"):
        _run_job(h, job.job_id)
    assert (storage._root / "outputs" / f"output-{job.job_id}").exists()

    storage.delete_output = lambda key: None
    h.service.recover_processing_storage()
    assert not (storage._root / "outputs" / f"output-{job.job_id}").exists()


def test_recovery_is_idempotent_and_orphan_outputs_are_cleaned(tmp_path, monkeypatch):
    h, _, _, storage = _harness(tmp_path, monkeypatch)
    job = _create_job(h)
    h.store._connection.execute(
        "UPDATE content_processing_jobs SET state = ? WHERE job_id = ?",
        (ProcessingJobState.RUNNING.value, job.job_id),
    )
    storage.put_output("orphan-job", b"orphan")
    h.close()

    reopened = ContentStore(tmp_path / "content.db")
    assert reopened.get_processing_job(job.job_id).state == ProcessingJobState.UNCERTAIN
    service = ContentRightsService(
        store=reopened,
        authority=h.authority,
        content_storage=storage,
        processor=SyntheticContentProcessor(),
    )
    service.recover_processing_storage()
    service.recover_processing_storage()
    assert not (storage._root / "outputs" / "output-orphan-job").exists()
    assert len(reopened.list_processing_events(job.job_id)) == 2
    reopened.close()


def test_recovery_preserves_referenced_successful_output(tmp_path, monkeypatch):
    h, _, _, storage = _harness(tmp_path, monkeypatch)
    job = _run_job(h, _create_job(h).job_id)
    output = h.store.get_processing_output(job.job_id)

    assert output is not None
    output_path = storage._root / "outputs" / output.output_key
    assert output_path.is_file()

    h.service.recover_processing_storage()

    assert output_path.is_file()
    assert storage.read_output(output.output_key, output.content_sha256)


@pytest.mark.parametrize("old_version", [3, 4])
def test_migration_preserves_assets_and_creates_processing_tables(
    tmp_path,
    monkeypatch,
    old_version,
):
    h, asset, _, _ = _harness(tmp_path, monkeypatch)
    h.store._connection.execute(
        "UPDATE content_schema_meta SET schema_version = ? WHERE singleton = 1",
        (old_version,),
    )
    h.close()
    reopened = ContentStore(tmp_path / "content.db")
    assert reopened.get_asset(asset.asset_id) == asset
    assert reopened._connection.execute(
        "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
    ).fetchone()[0] == 7
    assert reopened._connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name = 'content_processing_jobs'"
    ).fetchone() is not None
    assert reopened._connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name = 'content_transcripts'"
    ).fetchone() is not None
    reopened.close()


def test_target_scope_and_non_analysis_actions_are_rejected(tmp_path, monkeypatch):
    h, _, _, _ = _harness(tmp_path, monkeypatch)
    wrong_target = ContentTargetContext.INSTAGRAM_REELS
    with pytest.raises(ValueError, match="exact non-commercial ANALYZE"):
        _create_job(h, _use().model_copy(update={"actions": frozenset({RightsAction.PUBLISH})}))
    use = _use()
    with pytest.raises(ValueError, match="exact non-commercial ANALYZE"):
        h.invoke(
            "content_create_processing_job",
            {
                "use": use.model_dump(mode="json"),
                "target_context": wrong_target.value,
            },
            h.service.create_processing_job,
            use=use,
            target_context=wrong_target,
        )
