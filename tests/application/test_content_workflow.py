from __future__ import annotations

import json
import multiprocessing
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

import pytest

from ultron.application.content.store import ContentStore
from ultron.application.content.workflow import (
    ContentTaskHandler,
    ContentTaskHandlerRegistry,
    ContentTaskIdempotencyConflictError,
    ContentWorkflowService,
    ContentWorkflowRuntime,
    ContentWorkflowWorker,
    UnknownWorkflowOutcome,
    WorkflowTaskFailure,
    _bounded_backoff,
    _task_output_id,
)
from ultron.domain.content import ContentWorkflowState


def _advance_to_approved(service, workflow):
    workflow = service.record_research(
        workflow,
        evidence=[
            {
                "source": "https://research.example/item",
                "observed_at": datetime.now(UTC).isoformat(),
            }
        ],
    )
    workflow = service.record_script(workflow)
    workflow = service.record_production(workflow)
    workflow = service.request_approval(workflow)
    artifact = service.store.create_script_artifact(
        artifact_id=f"approval-script-{workflow.workflow_id}",
        workflow_id=workflow.workflow_id,
        content="Approved script",
        provenance={"source": "https://research.example/approved"},
    )
    return service.approve(
        workflow,
        approver="alice",
        artifact_id=artifact.artifact_id,
        artifact_version=artifact.version,
        artifact_sha256=artifact.content_sha256,
    )


def test_workflow_is_durable_and_idempotent(tmp_path):
    database = tmp_path / "content.db"
    store = ContentStore(database)
    service = ContentWorkflowService(store)
    workflow = service.discover(
        owner_id="alice",
        title="Verified topic",
        idempotency_key="discover-1",
        request_fingerprint="fingerprint-1",
    )
    duplicate = service.discover(
        owner_id="alice",
        title="Verified topic",
        idempotency_key="discover-1",
        request_fingerprint="fingerprint-1",
    )
    assert duplicate.workflow_id == workflow.workflow_id
    assert service.store.get_content_workflow(workflow.workflow_id) == workflow
    store.close()

    reopened = ContentStore(database)
    assert reopened.get_content_workflow(workflow.workflow_id).state == ContentWorkflowState.DISCOVERED
    reopened.close()


def test_workflow_rejects_stale_and_invalid_transitions(tmp_path):
    store = ContentStore(tmp_path / "content.db")
    service = ContentWorkflowService(store)
    workflow = service.discover(
        owner_id="alice",
        title="Verified topic",
        idempotency_key="discover-2",
        request_fingerprint="fingerprint-2",
    )
    with pytest.raises(ValueError, match="Invalid workflow transition"):
        store.transition_content_workflow(
            workflow.workflow_id,
            expected_version=workflow.version,
            target_state=ContentWorkflowState.APPROVED,
            actor="test",
            reason="invalid",
        )
    with pytest.raises(ValueError, match="version is stale"):
        store.transition_content_workflow(
            workflow.workflow_id,
            expected_version=99,
            target_state=ContentWorkflowState.RESEARCHING,
            actor="test",
            reason="stale",
        )
    store.close()


def test_distribution_is_explicitly_dry_run(tmp_path):
    store = ContentStore(tmp_path / "content.db")
    service = ContentWorkflowService(store)
    workflow = service.discover(
        owner_id="alice",
        title="Verified topic",
        idempotency_key="discover-3",
        request_fingerprint="fingerprint-3",
    )
    approved = _advance_to_approved(service, workflow)
    task = service.prepare_dry_run_distribution(
        approved,
        platform="youtube",
        idempotency_key="distribution-1",
        artifact_id=f"approval-script-{approved.workflow_id}",
        artifact_version=1,
        artifact_sha256=store.get_script_artifact(
            f"approval-script-{approved.workflow_id}"
        ).content_sha256,
    )
    assert task.state == "completed"
    assert service.task_result(task)["external_publish"] is False
    assert task.provider == "dry-run"
    history = store.get_content_workflow_task_history(task.task_id)
    assert history[-1]["event_type"] == "administrative_state_update"
    assert history[-1]["from_state"] == "pending"
    assert history[-1]["to_state"] == "completed"
    assert store.get_content_workflow(workflow.workflow_id).state == ContentWorkflowState.SCHEDULED
    store.close()


def _workflow_and_task(database, *, external_side_effect=False):
    store = ContentStore(database)
    service = ContentWorkflowService(store)
    workflow = service.discover(
        owner_id="alice",
        title="Worker task",
        idempotency_key=f"workflow-{database}",
        request_fingerprint="worker-fingerprint",
    )
    task = store.create_content_workflow_task(
        task_id=f"task-{database}",
        workflow_id=workflow.workflow_id,
        task_type="render",
        idempotency_key=f"task-{database}",
        external_side_effect=external_side_effect,
        max_attempts=2,
    )
    return store, task


def _capture_task_submission(submit, *args):
    try:
        return submit(*args)
    except Exception as exc:
        return exc


def test_artifacts_are_versioned_and_approval_is_hash_bound(tmp_path):
    store = ContentStore(tmp_path / "content.db")
    service = ContentWorkflowService(store)
    workflow = service.discover(
        owner_id="alice",
        title="Versioned script",
        idempotency_key="artifact-workflow",
        request_fingerprint="artifact-fingerprint",
    )
    first = store.create_script_artifact(
        artifact_id="script-1",
        workflow_id=workflow.workflow_id,
        content="First version",
        provenance={"source": "https://research.example/1"},
    )
    second = store.create_script_artifact(
        artifact_id="script-2",
        workflow_id=workflow.workflow_id,
        content="Second version",
        provenance={"source": "https://research.example/2"},
    )
    assert (first.version, second.version) == (1, 2)
    approval = store.create_approval_decision(
        decision_id="approval-1",
        workflow_id=workflow.workflow_id,
        artifact_type="script",
        artifact_id=first.artifact_id,
        artifact_version=first.version,
        artifact_sha256=first.content_sha256,
        decision="approved",
        actor="alice",
    )
    assert store.get_current_approval(
        workflow_id=workflow.workflow_id,
        artifact_id=first.artifact_id,
        artifact_version=first.version,
        artifact_sha256=first.content_sha256,
    ) == approval
    assert store.get_current_approval(
        workflow_id=workflow.workflow_id,
        artifact_id=second.artifact_id,
        artifact_version=second.version,
        artifact_sha256=second.content_sha256,
    ) is None
    store.create_production_job(
        job_id="production-1",
        workflow_id=workflow.workflow_id,
        script_artifact_id=second.artifact_id,
        configuration={"renderer": "local"},
    )
    store.create_validation_report(
        report_id="validation-1",
        workflow_id=workflow.workflow_id,
        artifact_type="script",
        artifact_id=second.artifact_id,
        artifact_version=second.version,
        artifact_sha256=second.content_sha256,
        rules=[{"id": "non_empty"}],
        results=[{"id": "non_empty", "passed": True}],
    )
    store.close()


def test_concurrent_claims_only_return_one_worker(tmp_path):
    database = tmp_path / "claims.db"
    seed, task = _workflow_and_task(database)
    seed.close()

    def claim(worker_id):
        store = ContentStore(database)
        try:
            claimed = store.claim_content_workflow_task(
                worker_id=worker_id,
                lease_seconds=60,
            )
            return None if claimed is None else claimed.worker_id
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("worker-a", "worker-b")))
    assert sorted(item for item in results if item is not None) == ["worker-a"] or sorted(
        item for item in results if item is not None
    ) == ["worker-b"]


def test_expired_internal_lease_can_be_reclaimed_but_external_is_uncertain(tmp_path):
    database = tmp_path / "leases.db"
    store, task = _workflow_and_task(database)
    claimed = store.claim_content_workflow_task(worker_id="worker-a", lease_seconds=1)
    assert claimed is not None
    expired = datetime.fromtimestamp(datetime.now(UTC).timestamp() + 3600, UTC)
    reclaimed = store.claim_content_workflow_task(
        worker_id="worker-b",
        lease_seconds=60,
        now=expired,
    )
    assert reclaimed is not None
    with pytest.raises(RuntimeError, match="stale"):
        store.complete_content_workflow_task(
            task.task_id,
            worker_id="worker-a",
            lease_token=claimed.lease_token,
            result={"stale": True},
        )
    store.close()

    external_store, external_task = _workflow_and_task(
        tmp_path / "external.db",
        external_side_effect=True,
    )
    external_claim = external_store.claim_content_workflow_task(
        worker_id="worker-a",
        lease_seconds=1,
    )
    assert external_claim is not None
    assert external_store.claim_content_workflow_task(
        worker_id="worker-b",
        lease_seconds=60,
        now=expired,
    ) is None
    assert external_store.get_content_workflow_task(external_task.task_id).state == "uncertain"
    assert any(
        event["event_type"] == "external_lease_expired"
        for event in external_store.get_content_workflow_task_history(
            external_task.task_id
        )
    )
    external_store.close()


def test_worker_classifies_retryable_and_unknown_failures(tmp_path):
    store, task = _workflow_and_task(tmp_path / "worker.db")
    worker = ContentWorkflowWorker(store, worker_id="worker-a")
    result = worker.run_once(
        lambda _: (_ for _ in ()).throw(
            WorkflowTaskFailure(
                "temporary",
                failure_class="transient",
                error_code="TEMP",
                retryable=True,
            )
        ),
        backoff_seconds=0,
    )
    assert result is not None
    assert result.state == "pending"
    result = worker.run_once(
        lambda _: (_ for _ in ()).throw(UnknownWorkflowOutcome("provider uncertain")),
    )
    assert result is not None
    assert result.state == "uncertain"
    uncertain_event = store.get_content_workflow_task_history(task.task_id)[-1]
    assert uncertain_event["event_type"] == "task_failed"
    assert uncertain_event["to_state"] == "uncertain"
    assert uncertain_event["failure_class"] == "unknown_outcome"
    store.close()


def test_worker_stops_after_retry_limit(tmp_path):
    store, _ = _workflow_and_task(tmp_path / "retry.db")
    worker = ContentWorkflowWorker(store, worker_id="worker-a")

    def transient(_):
        raise WorkflowTaskFailure(
            "temporary",
            failure_class="transient",
            error_code="TEMP",
            retryable=True,
        )

    first = worker.run_once(transient, backoff_seconds=0)
    second = worker.run_once(transient, backoff_seconds=0)
    assert first is not None and first.state == "pending"
    assert second is not None and second.state == "failed"
    assert second.failure_class == "retry_exhausted"
    assert worker.run_once(transient, backoff_seconds=0) is None
    store.close()


def test_handler_registry_validates_and_runs_exact_script_validation(tmp_path):
    store = ContentStore(tmp_path / "handlers.db")
    service = ContentWorkflowService(store)
    workflow = service.discover(
        owner_id="alice",
        title="Handler workflow",
        idempotency_key="handler-workflow",
        request_fingerprint="handler-fingerprint",
    )
    artifact = store.create_script_artifact(
        artifact_id="handler-script",
        workflow_id=workflow.workflow_id,
        content="A valid script",
        provenance={"source": "https://research.example/script"},
    )
    task = store.create_content_workflow_task(
        task_id="validation-task",
        workflow_id=workflow.workflow_id,
        task_type="validate_script",
        idempotency_key="validation-task",
        input_data={
            "artifact_id": artifact.artifact_id,
            "artifact_sha256": artifact.content_sha256,
        },
    )
    runtime = ContentWorkflowRuntime(
        store,
        worker_id="handler-worker",
        poll_interval_seconds=0.01,
        permissions=frozenset({"content.validate"}),
    )
    runtime.start()
    for _ in range(100):
        if store.get_content_workflow_task(task.task_id).state == "completed":
            break
        import time
        time.sleep(0.01)
    assert store.get_content_workflow_task(task.task_id).state == "completed"
    assert runtime.status()["handlers"] == [
        "ingest_rss_feed",
        "score_opportunity",
        "validate_script",
    ]
    runtime.stop()
    store.close()


def test_runtime_persists_registered_external_effect_policy_before_execution(tmp_path):
    store, task = _workflow_and_task(tmp_path / "external-policy.db")
    registry = ContentTaskHandlerRegistry()
    registry.register(
        ContentTaskHandler(
            "render",
            lambda _task, _store: {"completed": True},
            external_side_effect=True,
            required_permission="content.validate",
            timeout_seconds=5,
        )
    )
    runtime = ContentWorkflowRuntime(
        store,
        worker_id="external-policy-worker",
        handlers=registry,
        poll_interval_seconds=0.01,
        permissions=frozenset({"content.validate"}),
    )
    runtime.start()
    try:
        for _ in range(100):
            task = store.get_content_workflow_task(task.task_id)
            if task.state == "completed":
                break
            time.sleep(0.01)
        task = store.get_content_workflow_task(task.task_id)
        assert task.state == "completed"
        assert task.external_side_effect is True
        assert any(
            event["event_type"] == "external_effect_policy_confirmed"
            for event in store.get_content_workflow_task_history(task.task_id)
        )
    finally:
        runtime.stop()
        store.close()


def test_reconciliation_requires_operator_and_preserves_history(tmp_path):
    store, task = _workflow_and_task(tmp_path / "reconcile.db", external_side_effect=True)
    claimed = store.claim_content_workflow_task(worker_id="worker-a", lease_seconds=1)
    assert claimed is not None
    unresolved = store.claim_content_workflow_task(
        worker_id="worker-b",
        lease_seconds=60,
        now=datetime.fromtimestamp(datetime.now(UTC).timestamp() + 3600, UTC),
    )
    assert unresolved is None
    with pytest.raises(ValueError, match="Operator identity"):
        store.reconcile_content_workflow_task(
            task.task_id,
            operator_id="",
            outcome="failed",
            safe_to_retry=False,
            reason="not unresolved",
        )
    reconciled = store.reconcile_content_workflow_task(
        task.task_id,
        operator_id="operator",
        outcome="retry",
        safe_to_retry=True,
        external_operation_id="provider-op-1",
        reason="provider confirmed no side effect",
    )
    assert reconciled.state == "pending"
    assert any("reconciled:retry" in (item["detail"] or "") for item in store.get_content_workflow_task_history(task.task_id))
    store.close()


def test_task_idempotency_rejects_conflicting_inputs_and_reuses_equivalent_request(tmp_path):
    store, task = _workflow_and_task(tmp_path / "idempotency.db")
    equivalent = store.create_content_workflow_task(
        task_id="different-task-id",
        workflow_id=task.workflow_id,
        task_type=task.task_type,
        idempotency_key=task.idempotency_key,
        input_data={},
        external_side_effect=False,
        max_attempts=2,
    )
    assert equivalent.task_id == task.task_id
    with pytest.raises(ContentTaskIdempotencyConflictError, match="conflicts"):
        store.create_content_workflow_task(
            task_id="conflicting-task-id",
            workflow_id=task.workflow_id,
            task_type="different",
            idempotency_key=task.idempotency_key,
            input_data={"different": True},
            max_attempts=2,
        )
    store.close()


def test_pending_cancellation_is_persisted_with_request_and_ack_events(tmp_path):
    store, task = _workflow_and_task(tmp_path / "cancel-pending.db")
    cancelled = store.request_content_workflow_task_cancellation(
        task.task_id,
        actor="test-operator",
    )
    history = store.get_content_workflow_task_history(task.task_id)
    assert cancelled.state == "cancelled"
    assert [event["event_type"] for event in history] == [
        "task_created",
        "cancellation_requested",
        "cancellation_acknowledged",
    ]
    assert store.claim_content_workflow_task(worker_id="worker", lease_seconds=30) is None
    store.close()


def test_administrative_update_rejects_active_task_reset_and_preserves_lease(tmp_path):
    store, task = _workflow_and_task(tmp_path / "admin-active-reset.db")
    claimed = store.claim_content_workflow_task(
        worker_id="worker-a",
        lease_seconds=30,
    )
    assert claimed is not None

    with pytest.raises(ValueError, match="Active tasks"):
        store.update_content_workflow_task(task.task_id, state="pending")

    persisted = store.get_content_workflow_task(task.task_id)
    assert persisted.state == "running"
    assert persisted.worker_id == "worker-a"
    assert persisted.lease_token == claimed.lease_token
    renewed = store.renew_content_workflow_task(
        task.task_id,
        worker_id="worker-a",
        lease_token=claimed.lease_token,
        lease_seconds=30,
    )
    assert renewed.state == "running"
    completed = store.complete_content_workflow_task(
        task.task_id,
        worker_id="worker-a",
        lease_token=claimed.lease_token,
        result={"owner": "worker-a"},
    )
    assert completed.state == "completed"
    history = store.get_content_workflow_task_history(task.task_id)
    assert [event["event_type"] for event in history] == [
        "task_created",
        "task_claimed",
        "task_completed",
    ]
    store.close()


def test_administrative_reset_cannot_overlap_active_handler(tmp_path):
    store, task = _workflow_and_task(tmp_path / "admin-no-overlap.db")
    entered = threading.Event()
    release = threading.Event()
    effects: list[str] = []
    results = []
    errors = []

    def handler(_task):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("Test handler was not released.")
        effects.append("worker-a")
        return {"owner": "worker-a"}

    worker_a = ContentWorkflowWorker(store, worker_id="worker-a")

    def run_worker_a():
        try:
            results.append(worker_a.run_once(handler, lease_seconds=30))
        except Exception as exc:
            errors.append(exc)

    execution = threading.Thread(target=run_worker_a)
    execution.start()
    assert entered.wait(3)
    try:
        with pytest.raises(ValueError, match="Active tasks"):
            store.update_content_workflow_task(task.task_id, state="pending")
        assert store.claim_content_workflow_task(
            worker_id="worker-b",
            lease_seconds=30,
        ) is None
        assert store.get_content_workflow_task(task.task_id).state == "running"
    finally:
        release.set()
        execution.join(5)

    assert not execution.is_alive()
    assert errors == []
    assert len(results) == 1 and results[0].state == "completed"
    assert effects == ["worker-a"]
    assert store.get_content_workflow_task(task.task_id).state == "completed"
    assert [
        event["event_type"]
        for event in store.get_content_workflow_task_history(task.task_id)
    ] == ["task_created", "task_claimed", "task_completed"]
    store.close()


def test_administrative_cancellation_uses_cooperative_acknowledgement(tmp_path):
    store, task = _workflow_and_task(tmp_path / "admin-cooperative-cancel.db")
    entered = threading.Event()
    acknowledged = threading.Event()
    results = []
    worker = ContentWorkflowWorker(store, worker_id="cooperative-worker")

    def cooperative_handler(_task, cancellation_event):
        entered.set()
        if not cancellation_event.wait(5):
            raise TimeoutError("Cancellation signal was not delivered.")
        acknowledged.set()
        return {"stopped": True}

    execution = threading.Thread(
        target=lambda: results.append(
            worker.run_once(
                lambda _task: {"unexpected": True},
                lease_seconds=30,
                handler_with_cancellation=cooperative_handler,
            )
        )
    )
    execution.start()
    assert entered.wait(3)
    with pytest.raises(ValueError, match="Active tasks"):
        store.update_content_workflow_task(task.task_id, state="cancelled")
    requested = store.request_content_workflow_task_cancellation(
        task.task_id,
        actor="administrator",
    )
    assert requested.state == "cancellation_requested"
    assert store.get_content_workflow_task(task.task_id).state == "cancellation_requested"
    assert acknowledged.wait(3)
    execution.join(5)

    assert not execution.is_alive()
    assert len(results) == 1 and results[0].state == "cancelled"
    history = store.get_content_workflow_task_history(task.task_id)
    assert [event["event_type"] for event in history][-2:] == [
        "cancellation_requested",
        "cancellation_acknowledged",
    ]
    assert history[-1]["from_state"] == "cancellation_requested"
    assert history[-1]["to_state"] == "cancelled"
    store.close()


def test_administrative_external_cancellation_preserves_uncertain_outcome(tmp_path):
    store, task = _workflow_and_task(
        tmp_path / "admin-external-cancel.db",
        external_side_effect=True,
    )
    entered = threading.Event()
    unresolved = threading.Event()
    effects: list[str] = []
    results = []
    worker = ContentWorkflowWorker(store, worker_id="external-worker")

    def fake_external_handler(_task, cancellation_event):
        entered.set()
        if not cancellation_event.wait(5):
            raise TimeoutError("Cancellation signal was not delivered.")
        effects.append("external-outcome-unresolved")
        unresolved.set()
        return {"provider_outcome": "unknown"}

    execution = threading.Thread(
        target=lambda: results.append(
            worker.run_once(
                lambda _task: {"unexpected": True},
                lease_seconds=30,
                handler_with_cancellation=fake_external_handler,
            )
        )
    )
    execution.start()
    assert entered.wait(3)
    requested = store.request_content_workflow_task_cancellation(
        task.task_id,
        actor="administrator",
    )
    assert requested.state == "cancellation_requested"
    assert unresolved.wait(3)
    execution.join(5)

    assert not execution.is_alive()
    assert len(results) == 1 and results[0].state == "uncertain"
    persisted = store.get_content_workflow_task(task.task_id)
    assert persisted.state == "uncertain"
    assert persisted.next_attempt_at is None
    assert effects == ["external-outcome-unresolved"]
    assert store.claim_content_workflow_task(
        worker_id="retry-worker",
        lease_seconds=30,
    ) is None
    history = store.get_content_workflow_task_history(task.task_id)
    assert history[-1]["event_type"] == "cancellation_acknowledged"
    assert history[-1]["from_state"] == "cancellation_requested"
    assert history[-1]["to_state"] == "uncertain"
    store.close()


def test_administrative_updates_preserve_pending_and_reconciliation_contracts(tmp_path):
    store, task = _workflow_and_task(tmp_path / "admin-allowed.db")
    completed = store.update_content_workflow_task(
        task.task_id,
        state="completed",
        result={"mode": "dry_run"},
    )
    assert completed.state == "completed"
    assert store.get_content_workflow_task_history(task.task_id)[-1]["event_type"] == (
        "administrative_state_update"
    )
    with pytest.raises(ValueError, match="cannot be reopened"):
        store.update_content_workflow_task(task.task_id, state="pending")

    unresolved_task = store.create_content_workflow_task(
        task_id="admin-reconcile-task",
        workflow_id=task.workflow_id,
        task_type="external_probe",
        idempotency_key="admin-reconcile-task",
        external_side_effect=True,
    )
    claimed = store.claim_content_workflow_task(
        worker_id="external-worker",
        lease_seconds=1,
    )
    assert claimed is not None and claimed.task_id == unresolved_task.task_id
    expired_at = datetime.now(UTC) + timedelta(seconds=2)
    assert store.claim_content_workflow_task(
        worker_id="recovery-worker",
        lease_seconds=30,
        now=expired_at,
    ) is None
    assert store.get_content_workflow_task(unresolved_task.task_id).state == "uncertain"
    reconciled = store.reconcile_content_workflow_task(
        unresolved_task.task_id,
        operator_id="operator",
        outcome="failed",
        safe_to_retry=False,
        reason="Fake external outcome verified not to have completed.",
    )
    assert reconciled.state == "failed"
    assert store.get_content_workflow_task_history(unresolved_task.task_id)[-1][
        "event_type"
    ] == "reconciled_failed"
    store.close()


def test_unacknowledged_cancellation_lease_expiry_becomes_uncertain(tmp_path):
    store, task = _workflow_and_task(tmp_path / "cancel-recovery.db")
    stale_start = datetime.now(UTC) - timedelta(seconds=5)
    claimed = store.claim_content_workflow_task(
        worker_id="lost-worker",
        lease_seconds=1,
        now=stale_start,
    )
    assert claimed is not None
    requested = store.request_content_workflow_task_cancellation(
        task.task_id,
        actor="operator",
    )
    assert requested.state == "cancellation_requested"
    assert store.claim_content_workflow_task(
        worker_id="recovery-worker",
        lease_seconds=30,
    ) is None
    assert store.get_content_workflow_task(task.task_id).state == "uncertain"
    assert any(
        event["event_type"] == "cancellation_worker_lost"
        for event in store.get_content_workflow_task_history(task.task_id)
    )
    store.close()


def test_timed_out_external_handler_is_not_retried_while_still_running(tmp_path):
    store, task = _workflow_and_task(
        tmp_path / "timeout-fence.db",
        external_side_effect=True,
    )
    entered = threading.Event()
    release = threading.Event()
    side_effects: list[str] = []
    registry = ContentTaskHandlerRegistry()

    def handler(_task, _store):
        entered.set()
        release.wait(5)
        side_effects.append("performed")
        return {"side_effect": True}

    registry.register(
        ContentTaskHandler(
            "render",
            handler,
            external_side_effect=True,
            required_permission="content.validate",
            timeout_seconds=0.05,
        )
    )
    worker = ContentWorkflowWorker(store, worker_id="timeout-worker")
    results = []
    execution = threading.Thread(
        target=lambda: results.append(
            worker.run_once(
                lambda claimed: registry.execute(claimed, store),
                lease_seconds=30,
            )
        )
    )
    execution.start()
    assert entered.wait(2)
    time.sleep(0.1)
    assert store.get_content_workflow_task(task.task_id).state == "running"
    assert store.claim_content_workflow_task(
        worker_id="retry-worker",
        lease_seconds=30,
    ) is None
    requested = store.request_content_workflow_task_cancellation(
        task.task_id,
        actor="operator",
    )
    assert requested.state == "cancellation_requested"
    release.set()
    execution.join(3)
    assert not execution.is_alive()
    assert results[0].state == "uncertain"
    assert side_effects == ["performed"]
    assert store.claim_content_workflow_task(worker_id="retry-worker", lease_seconds=30) is None
    assert any(
        event["event_type"] == "task_timeout"
        for event in store.get_content_workflow_task_history(task.task_id)
    )
    store.close()


def test_cooperative_cancellation_waits_for_handler_acknowledgement(tmp_path):
    store, task = _workflow_and_task(tmp_path / "cancel-running.db")
    entered = threading.Event()
    registry = ContentTaskHandlerRegistry()

    def cooperative_handler(_task, _store, cancellation_event):
        entered.set()
        while not cancellation_event.wait(0.01):
            pass
        return {"stopped": True}

    registry.register(
        ContentTaskHandler(
            "render",
            lambda _task, _store: {"unexpected": True},
            external_side_effect=False,
            required_permission="content.validate",
            timeout_seconds=10,
            cooperative_handler=cooperative_handler,
        )
    )
    worker = ContentWorkflowWorker(store, worker_id="cancel-worker")
    results = []
    execution = threading.Thread(
        target=lambda: results.append(
            worker.run_once(
                lambda claimed: registry.execute(claimed, store),
                lease_seconds=30,
                handler_with_cancellation=lambda claimed, event: registry.execute(
                    claimed,
                    store,
                    cancellation_event=event,
                ),
            )
        )
    )
    execution.start()
    assert entered.wait(2)
    requested = store.request_content_workflow_task_cancellation(
        task.task_id,
        actor="operator",
    )
    assert requested.state == "cancellation_requested"
    assert store.get_content_workflow_task(task.task_id).lease_token is not None
    execution.join(4)
    assert not execution.is_alive()
    assert results[0].state == "cancelled"
    assert [
        event["event_type"]
        for event in store.get_content_workflow_task_history(task.task_id)
    ][-1] == "cancellation_acknowledged"
    store.close()


def test_runtime_shutdown_requests_and_waits_for_cooperative_cancellation(tmp_path):
    store, task = _workflow_and_task(tmp_path / "shutdown-cancel.db")
    entered = threading.Event()
    registry = ContentTaskHandlerRegistry()

    def cooperative_handler(_task, _store, cancellation_event):
        entered.set()
        cancellation_event.wait(5)
        return {"stopped": cancellation_event.is_set()}

    registry.register(
        ContentTaskHandler(
            "render",
            lambda _task, _store: {"unexpected": True},
            external_side_effect=False,
            required_permission="content.validate",
            timeout_seconds=30,
            cooperative_handler=cooperative_handler,
        )
    )
    runtime = ContentWorkflowRuntime(
        store,
        worker_id="shutdown-worker",
        handlers=registry,
        poll_interval_seconds=0.01,
        lease_seconds=30,
        permissions=frozenset({"content.validate"}),
    )
    runtime.start()
    assert entered.wait(2)
    runtime.stop(timeout=2)
    assert store.get_content_workflow_task(task.task_id).state == "cancelled"
    assert any(
        event["event_type"] == "cancellation_acknowledged"
        for event in store.get_content_workflow_task_history(task.task_id)
    )
    store.close()


def test_expired_lease_cannot_be_renewed_completed_or_failed(tmp_path):
    store, task = _workflow_and_task(tmp_path / "lease-expiry.db")
    claimed = store.claim_content_workflow_task(worker_id="owner", lease_seconds=1)
    assert claimed is not None
    time.sleep(1.05)
    with pytest.raises(RuntimeError, match="stale"):
        store.renew_content_workflow_task(
            task.task_id,
            worker_id="owner",
            lease_token=claimed.lease_token,
            lease_seconds=30,
        )
    with pytest.raises(RuntimeError, match="expired"):
        store.complete_content_workflow_task(
            task.task_id,
            worker_id="owner",
            lease_token=claimed.lease_token,
            result={"late": True},
        )
    with pytest.raises(RuntimeError, match="expired"):
        store.fail_content_workflow_task(
            task.task_id,
            worker_id="owner",
            lease_token=claimed.lease_token,
            failure_class="transient",
            error_code="LATE",
            detail="late failure",
            retryable=True,
        )
    recovered = store.claim_content_workflow_task(
        worker_id="recovery",
        lease_seconds=30,
    )
    assert recovered is not None
    assert recovered.lease_token != claimed.lease_token
    with pytest.raises(RuntimeError, match="stale"):
        store.complete_content_workflow_task(
            task.task_id,
            worker_id="owner",
            lease_token=claimed.lease_token,
            result={"stale": True},
        )
    store.close()


def test_completion_is_accepted_while_lease_is_still_valid(tmp_path):
    store, task = _workflow_and_task(tmp_path / "lease-valid.db")
    claim_time = datetime.now(UTC) - timedelta(seconds=2.5)
    claimed = store.claim_content_workflow_task(
        worker_id="owner",
        lease_seconds=3,
        now=claim_time,
    )
    assert claimed is not None
    assert timedelta(0) < claimed.lease_expires_at - datetime.now(UTC) < timedelta(seconds=1)
    completed = store.complete_content_workflow_task(
        task.task_id,
        worker_id="owner",
        lease_token=claimed.lease_token,
        result={"ok": True},
    )
    assert completed.state == "completed"
    store.close()


def test_concurrent_recovery_attempts_issue_only_one_new_lease(tmp_path):
    database = tmp_path / "concurrent-recovery.db"
    store, task = _workflow_and_task(database)
    expired_time = datetime.now(UTC) - timedelta(seconds=5)
    original = store.claim_content_workflow_task(
        worker_id="original",
        lease_seconds=1,
        now=expired_time,
    )
    assert original is not None
    store.close()
    barrier = threading.Barrier(2)

    def recover(worker_id):
        separate_store = ContentStore(database)
        try:
            barrier.wait()
            return separate_store.claim_content_workflow_task(
                worker_id=worker_id,
                lease_seconds=30,
            )
        finally:
            separate_store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(recover, ("recovery-a", "recovery-b")))
    claimed = [result for result in results if result is not None]
    assert len(claimed) == 1
    assert claimed[0].lease_token != original.lease_token
    assert claimed[0].attempt == 2


def test_concurrent_task_submission_deduplicates_equal_and_rejects_conflict(tmp_path):
    database = tmp_path / "concurrent-idempotency.db"
    seed, task = _workflow_and_task(database)
    workflow_id = task.workflow_id
    seed.close()
    barrier = threading.Barrier(2)

    def submit(task_id, input_data):
        separate_store = ContentStore(database)
        try:
            barrier.wait()
            return separate_store.create_content_workflow_task(
                task_id=task_id,
                workflow_id=workflow_id,
                task_type="render",
                idempotency_key="shared-key",
                input_data=input_data,
                max_attempts=2,
            )
        finally:
            separate_store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        equal_results = list(
            pool.map(
                lambda task_id: submit(task_id, {}),
                ("same-a", "same-b"),
            )
        )
    assert len({result.task_id for result in equal_results}) == 1

    conflict_barrier = threading.Barrier(2)

    def submit_conflict(task_id, input_data):
        separate_store = ContentStore(database)
        try:
            conflict_barrier.wait()
            return separate_store.create_content_workflow_task(
                task_id=task_id,
                workflow_id=workflow_id,
                task_type="render",
                idempotency_key="conflict-key",
                input_data=input_data,
                max_attempts=2,
            )
        finally:
            separate_store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                lambda args: _capture_task_submission(submit_conflict, *args),
                (
                    ("conflict-a", {"value": 1}),
                    ("conflict-b", {"value": 2}),
                ),
            )
        )
    assert sum(isinstance(outcome, ContentTaskIdempotencyConflictError) for outcome in outcomes) == 1
    store = ContentStore(database)
    with store._lock:
        conflict_count = store._connection.execute(
            "SELECT COUNT(*) FROM content_workflow_tasks WHERE idempotency_key = 'conflict-key'"
        ).fetchone()[0]
    assert conflict_count == 1
    store.close()


def test_retry_classification_is_permanent_and_backoff_is_capped(tmp_path):
    store, task = _workflow_and_task(tmp_path / "retry-policy.db")
    claimed = store.claim_content_workflow_task(worker_id="worker", lease_seconds=30)
    permanent = store.fail_content_workflow_task(
        task.task_id,
        worker_id="worker",
        lease_token=claimed.lease_token,
        failure_class="permanent",
        error_code="PERMANENT",
        detail="must not retry",
        retryable=True,
        backoff_seconds=1000,
        max_backoff_seconds=5,
    )
    assert permanent.state == "failed"
    assert permanent.next_attempt_at is None

    second_task = store.create_content_workflow_task(
        task_id="capped-retry",
        workflow_id=task.workflow_id,
        task_type="render",
        idempotency_key="capped-retry",
        max_attempts=3,
    )
    claimed_again = store.claim_content_workflow_task(
        worker_id="worker",
        lease_seconds=30,
    )
    assert claimed_again.task_id == second_task.task_id
    before = datetime.now(UTC)
    retry = store.fail_content_workflow_task(
        second_task.task_id,
        worker_id="worker",
        lease_token=claimed_again.lease_token,
        failure_class="transient",
        error_code="TRANSIENT",
        detail="retry",
        retryable=True,
        backoff_seconds=1000,
        max_backoff_seconds=2,
    )
    assert retry.state == "pending"
    assert retry.next_attempt_at is not None
    assert 1.8 <= (retry.next_attempt_at - before).total_seconds() <= 2.2
    assert _bounded_backoff(2, 1, 3) == 2
    assert _bounded_backoff(2, 2, 3) == 3
    store.close()


def test_retry_schedule_survives_store_restart_and_exhausts(tmp_path):
    database = tmp_path / "retry-restart.db"
    store, task = _workflow_and_task(database)
    claimed = store.claim_content_workflow_task(worker_id="first", lease_seconds=30)
    retry = store.fail_content_workflow_task(
        task.task_id,
        worker_id="first",
        lease_token=claimed.lease_token,
        failure_class="transient",
        error_code="TRANSIENT",
        detail="retry after restart",
        retryable=True,
        backoff_seconds=1,
        max_backoff_seconds=2,
    )
    scheduled_at = retry.next_attempt_at
    store.close()

    reopened = ContentStore(database)
    assert reopened.get_content_workflow_task(task.task_id).next_attempt_at == scheduled_at
    assert reopened.claim_content_workflow_task(worker_id="early", lease_seconds=30) is None
    time.sleep(1.05)
    second = reopened.claim_content_workflow_task(worker_id="second", lease_seconds=30)
    assert second is not None and second.attempt == 2
    exhausted = reopened.fail_content_workflow_task(
        task.task_id,
        worker_id="second",
        lease_token=second.lease_token,
        failure_class="transient",
        error_code="TRANSIENT",
        detail="attempt exhausted",
        retryable=True,
        backoff_seconds=1,
    )
    assert exhausted.state == "failed"
    assert exhausted.failure_class == "retry_exhausted"
    history = reopened.get_content_workflow_task_history(task.task_id)
    assert [event["event_type"] for event in history][-2:] == [
        "task_claimed",
        "retry_exhausted",
    ]
    reopened.close()


def test_task_events_include_transition_metadata_and_roll_back_atomically(tmp_path):
    store, task = _workflow_and_task(tmp_path / "events.db")
    claim = store.claim_content_workflow_task(worker_id="worker", lease_seconds=30)
    history = store.get_content_workflow_task_history(task.task_id)
    assert [event["event_type"] for event in history] == ["task_created", "task_claimed"]
    assert history[-1]["event_id"] == history[-1]["sequence"]
    assert history[-1]["workflow_id"] == task.workflow_id
    assert history[-1]["attempt"] == 1
    assert history[-1]["from_state"] == "pending"
    assert history[-1]["to_state"] == "running"
    with store._transaction() as connection:
        connection.execute(
            """
            CREATE TRIGGER abort_completed_event
            BEFORE INSERT ON content_workflow_task_events
            WHEN NEW.state = 'completed'
            BEGIN SELECT RAISE(ABORT, 'forced event failure'); END
            """
        )
    with pytest.raises(sqlite3.IntegrityError, match="forced event failure"):
        store.complete_content_workflow_task(
            task.task_id,
            worker_id="worker",
            lease_token=claim.lease_token,
            result={"done": True},
        )
    assert store.get_content_workflow_task(task.task_id).state == "running"
    assert [event["event_type"] for event in store.get_content_workflow_task_history(task.task_id)] == [
        "task_created",
        "task_claimed",
    ]
    store.close()


def test_script_generation_reuses_task_derived_artifact_after_reentry(tmp_path):
    from ultron.application.content.discovery import ContentDiscoveryService
    from ultron.application.content.strategy import ContentStrategyService
    from tests.application.test_content_discovery import _document
    from ultron.domain.content_strategy import AudienceKnowledgeLevel
    from ultron.application.content.workflow import _generate_script_task

    store = ContentStore(tmp_path / "script-reentry.db")
    opportunity = ContentDiscoveryService(store).discover(
        topic="Recovery topic",
        niche="technology",
        target_audience="builders",
        target_language="en",
        content_format="short",
        source_documents=(_document(),),
    )
    _, report = ContentDiscoveryService(store).research(
        opportunity,
        summary="Evidence-backed summary.",
    )
    strategy = ContentStrategyService(store).create_brief(
        opportunity_id=opportunity.opportunity_id,
        research_report_id=report.report_id,
        target_audience="builders",
        audience_knowledge_level=AudienceKnowledgeLevel.INTERMEDIATE,
        intended_audience_outcome="Understand the evidence.",
        content_objective="Educate",
        content_format="short",
        platform="youtube",
        language="en",
        competitive_positioning="Evidence-first explanation.",
        differentiation_strategy="Separate observation from inference.",
        supporting_evidence_ids=tuple(
            evidence.evidence_id
            for evidence in store.list_content_evidence(opportunity.opportunity_id)
        ),
    )
    angle = ContentStrategyService(store).generate_angles(strategy)[0]
    workflow = ContentWorkflowService(store).discover(
        owner_id="alice",
        title="Script recovery",
        idempotency_key="script-recovery-workflow",
        request_fingerprint="script-recovery-fingerprint",
    )
    task = store.create_content_workflow_task(
        task_id="script-recovery-task",
        workflow_id=workflow.workflow_id,
        task_type="generate_script",
        idempotency_key="script-recovery-task",
        input_data={
            "strategy_id": strategy.strategy_id,
            "angle_id": angle.angle_id,
            "duration_seconds": 60,
        },
    )
    first = _generate_script_task(task, store)
    second = _generate_script_task(task, store)
    assert second == first
    with store._lock:
        artifact_count = store._connection.execute(
            "SELECT COUNT(*) FROM content_script_artifacts WHERE workflow_id = ?",
            (workflow.workflow_id,),
        ).fetchone()[0]
        claim_count = store._connection.execute(
            """
            SELECT COUNT(*) FROM content_script_claims
            WHERE artifact_id = ?
            """,
            (first["artifact_id"],),
        ).fetchone()[0]
    assert artifact_count == 1
    assert claim_count > 0
    store.close()


def _crash_after_task_artifact(database: str, task_id: str, connection) -> None:
    store = ContentStore(database)
    task = store.claim_content_workflow_task(worker_id="crash-worker", lease_seconds=2)
    if task is None:
        connection.send({"error": "task_not_claimed"})
        return
    artifact_id = _task_output_id(task.task_id, "crash-test-output")
    store.create_script_artifact(
        artifact_id=artifact_id,
        workflow_id=task.workflow_id,
        content="stable crash-recovery output",
        provenance={"task_id": task.task_id, "role": "crash-test-output"},
        generation_metadata={"input": task.input_json},
    )
    connection.send({"artifact_id": artifact_id, "attempt": task.attempt})
    while True:
        time.sleep(1)


def _recover_task_artifact(database: str, connection) -> None:
    store = ContentStore(database)
    worker = ContentWorkflowWorker(store, worker_id="recovery-worker")

    def handler(task):
        artifact_id = _task_output_id(task.task_id, "crash-test-output")
        provenance = {"task_id": task.task_id, "role": "crash-test-output"}
        existing = store.get_script_artifact(artifact_id)
        if existing is None:
            existing = store.create_script_artifact(
                artifact_id=artifact_id,
                workflow_id=task.workflow_id,
                content="stable crash-recovery output",
                provenance=provenance,
                generation_metadata={"input": task.input_json},
            )
        elif (
            existing.workflow_id != task.workflow_id
            or existing.provenance_json != store._canonical(provenance)
            or existing.generation_metadata_json
            != store._canonical({"input": task.input_json})
        ):
            raise ValueError("Existing task artifact conflicts with recovered task input.")
        return {"artifact_id": existing.artifact_id}

    result = worker.run_once(handler, lease_seconds=10)
    connection.send(
        {
            "state": None if result is None else result.state,
            "attempt": None if result is None else result.attempt,
            "artifact_id": None
            if result is None
            else json.loads(result.result_json)["artifact_id"],
        }
    )
    store.close()


def test_process_crash_recovery_reuses_task_output_artifact(tmp_path):
    database = str(tmp_path / "crash-recovery.db")
    store, task = _workflow_and_task(database)
    store.close()
    context = multiprocessing.get_context("fork")
    parent, child = context.Pipe(duplex=False)
    crashed = context.Process(
        target=_crash_after_task_artifact,
        args=(database, task.task_id, child),
    )
    crashed.start()
    first = parent.recv()
    assert first["attempt"] == 1
    crashed.terminate()
    crashed.join(5)
    assert crashed.exitcode is not None and crashed.exitcode != 0
    time.sleep(2.1)

    parent2, child2 = context.Pipe(duplex=False)
    restarted = context.Process(
        target=_recover_task_artifact,
        args=(database, child2),
    )
    restarted.start()
    recovered = parent2.recv()
    restarted.join(10)
    assert restarted.exitcode == 0
    assert recovered == {
        "state": "completed",
        "attempt": 2,
        "artifact_id": first["artifact_id"],
    }

    verified = ContentStore(database)
    with verified._lock:
        artifact_count = verified._connection.execute(
            "SELECT COUNT(*) FROM content_script_artifacts WHERE workflow_id = ?",
            (task.workflow_id,),
        ).fetchone()[0]
    assert artifact_count == 1
    with pytest.raises(ValueError, match="identity conflicts"):
        verified.create_script_artifact(
            artifact_id=first["artifact_id"],
            workflow_id=task.workflow_id,
            content="conflicting recovery output",
            provenance={"task_id": task.task_id, "role": "crash-test-output"},
            generation_metadata={"input": task.input_json},
        )
    history = verified.get_content_workflow_task_history(task.task_id)
    assert [event["event_type"] for event in history] == [
        "task_created",
        "task_claimed",
        "task_recovered",
        "task_completed",
    ]
    verified.close()
