import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ultron.application.runtime.operation_store import SQLiteOperationStore
from ultron.domain.enums import Priority, TaskStatus
from ultron.domain.goal import Goal
from ultron.domain.plan import Plan, PlanStep
from ultron.domain.task import Task
from ultron.application.task_service import TaskService
from ultron.executive.tasks.manager import TaskManager
from ultron.executive.tasks.translator import TaskTranslator


def test_task_creation():

    manager = TaskManager()

    translator = TaskTranslator()

    goal = Goal(
        name="Build API",
    )

    tasks = translator.translate(goal)

    manager.create_tasks(tasks)

    assert manager.next_task() == tasks[0]


def test_sqlite_task_queue_survives_process_termination_and_restart(tmp_path):
    workspace = Path(__file__).resolve().parents[1]
    database = tmp_path / "runtime.db"
    ready_file = tmp_path / "task-committed"
    worker = """
import json
import sys
import time
from pathlib import Path

from ultron.domain.enums import Priority
from ultron.domain.task import Task
from ultron.executive.tasks.manager import TaskManager

mode, database, ready_file = sys.argv[1:]
manager = TaskManager(database=database)
if mode == "create":
    manager.create_tasks([
        Task(
            id="durable-low-task",
            goal_id="durable-goal",
            title="Low priority task",
            priority=Priority.LOW,
        ),
        Task(
            id="durable-high-task",
            goal_id="durable-goal",
            title="High priority task",
            priority=Priority.HIGH,
        ),
    ])
    Path(ready_file).write_text("committed", encoding="ascii")
    while True:
        time.sleep(1)

tasks = manager.all_tasks()
if mode == "inspect":
    print(json.dumps({
        "ids": [task.id for task in tasks],
        "next": manager.next_task().id,
    }))
    manager.remove_task("durable-high-task")
elif mode == "verify-removal":
    print(json.dumps([task.id for task in tasks]))
manager.close()
"""
    environment = {
        "HOME": str(tmp_path),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(workspace),
    }

    creator = subprocess.Popen(
        [
            sys.executable,
            "-c",
            worker,
            "create",
            str(database),
            str(ready_file),
        ],
        cwd=workspace,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready_file.exists() and creator.poll() is None:
            if time.monotonic() >= deadline:
                raise AssertionError("Task writer did not persist its queue.")
            time.sleep(0.05)
        assert creator.poll() is None
    finally:
        creator.terminate()
        creator.communicate(timeout=10)

    assert database.stat().st_mode & 0o777 == 0o600

    inspection = subprocess.run(
        [
            sys.executable,
            "-c",
            worker,
            "inspect",
            str(database),
            str(ready_file),
        ],
        cwd=workspace,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert json.loads(inspection.stdout) == {
        "ids": ["durable-high-task", "durable-low-task"],
        "next": "durable-high-task",
    }

    after_removal = subprocess.run(
        [
            sys.executable,
            "-c",
            worker,
            "verify-removal",
            str(database),
            str(ready_file),
        ],
        cwd=workspace,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert json.loads(after_removal.stdout) == ["durable-low-task"]

    operation_store = SQLiteOperationStore(database)
    assert operation_store.get_by_id("missing-operation") is None
    operation_store.close()

    restarted_queue = TaskManager(database=database)
    assert [task.id for task in restarted_queue.all_tasks()] == [
        "durable-low-task"
    ]
    restarted_queue.close()


def test_durable_task_transitions_and_interruption_recovery(tmp_path):
    database = tmp_path / "lifecycle.db"
    manager = TaskManager(database=database)
    with pytest.raises(ValueError, match="lifecycle API"):
        manager.create_tasks([
            Task(
                id="forged-completed-task",
                goal_id="recovery-goal",
                title="Must not enter completed directly",
                status=TaskStatus.COMPLETED,
            )
        ])
    tasks = [
        Task(
            id="before-dispatch",
            goal_id="recovery-goal",
            title="Claimed before dispatch",
            priority=Priority.HIGH,
        ),
        Task(
            id="after-dispatch",
            goal_id="recovery-goal",
            title="Executing at interruption",
            priority=Priority.MEDIUM,
        ),
        Task(
            id="failed-task",
            goal_id="recovery-goal",
            title="Failed task",
            priority=Priority.LOW,
        ),
    ]
    manager.create_tasks(tasks)
    manager.create_tasks([tasks[0]])
    assert len(manager.all_tasks()) == 3

    with pytest.raises(ValueError, match="different content"):
        manager.create_tasks([
            tasks[0].model_copy(update={"title": "Conflicting duplicate"})
        ])

    claimed = manager.claim_task()
    assert claimed is not None
    assert claimed.id == "before-dispatch"
    assert claimed.status == TaskStatus.CLAIMED
    assert claimed.attempt_count == 1
    assert claimed.attempt_id
    attempt_before_dispatch = claimed.attempt_id
    manager.close()

    restarted = TaskManager(database=database)
    safely_requeued = restarted.recover_interrupted_task("before-dispatch")
    assert safely_requeued.status == TaskStatus.READY
    assert restarted.claim_task().id == "before-dispatch"
    restarted.close()

    executing = TaskManager(database=database)
    current = executing.get_task("before-dispatch")
    assert current is not None and current.status == TaskStatus.CLAIMED
    executing.start_task(current.id, current.attempt_id)
    attempt_during_dispatch = current.attempt_id
    executing.mark_task_uncertain(
        current.id,
        current.attempt_id,
        failure_classification="executor_reported_uncertain",
    )
    next_claim = executing.claim_task()
    assert next_claim is not None
    assert next_claim.id == "after-dispatch"
    executing.start_task(next_claim.id, next_claim.attempt_id)
    completed_claim = executing.claim_task()
    assert completed_claim is not None
    assert completed_claim.id == "failed-task"
    executing.start_task(completed_claim.id, completed_claim.attempt_id)
    failed = executing.fail_task(
        completed_claim.id,
        completed_claim.attempt_id,
        failure_classification="executor_reported_failure",
    )
    assert failed.status == TaskStatus.FAILED
    assert executing.fail_task(
        completed_claim.id,
        completed_claim.attempt_id,
        failure_classification="executor_reported_failure",
    ).status == TaskStatus.FAILED
    with pytest.raises(ValueError, match="Invalid task transition"):
        executing.start_task(
            completed_claim.id,
            completed_claim.attempt_id,
        )
    executing.close()

    recovered = TaskManager(database=database)
    assert recovered.claim_task() is None
    uncertain = recovered.recover_interrupted_task("after-dispatch")
    assert uncertain.status == TaskStatus.UNCERTAIN
    assert recovered.recover_interrupted_task("after-dispatch").status == (
        TaskStatus.UNCERTAIN
    )
    assert recovered.get_task("failed-task").status == TaskStatus.FAILED
    assert recovered.claim_task() is None
    assert recovered.task_events("after-dispatch")[-1][
        "failure_classification"
    ] == "process_interrupted_after_dispatch_boundary"
    assert attempt_before_dispatch != attempt_during_dispatch
    with pytest.raises(ValueError, match="uncertain"):
        recovered.remove_task("after-dispatch")
    unresolved = recovered.get_task("after-dispatch")
    assert unresolved is not None and unresolved.attempt_id
    with pytest.raises(ValueError, match="confirmed verifier evidence"):
        recovered.queue.transition(
            unresolved.id,
            TaskStatus.FAILED,
            attempt_id=unresolved.attempt_id,
        )
    with pytest.raises(RuntimeError, match="shared operation store"):
        recovered.resolve_uncertain_task(
            unresolved.id,
            unresolved.attempt_id,
            operation_id="missing-operation",
        )
    assert recovered.get_task("after-dispatch").status == TaskStatus.UNCERTAIN
    recovered.remove_task("failed-task")
    recovered.remove_task("failed-task")
    recovered.close()
    final = TaskManager(database=database)
    assert final.get_task("failed-task") is None
    assert final.claim_task() is None
    final.close()


@pytest.mark.parametrize(
    "boundary",
    [
        "create_event",
        "claim_update",
        "claim_event",
        "start_update",
        "start_event",
        "checkpoint_update",
        "checkpoint_event",
        "uncertain_update",
        "uncertain_event",
        "cancel_update",
        "cancel_event",
        "remove_update",
        "remove_event",
    ],
)
def test_task_persistence_failure_matrix_rolls_back_transition(
    tmp_path,
    boundary,
):
    manager = TaskManager(database=tmp_path / f"{boundary}.db")
    connection = manager.queue._connection
    task = Task(
        id=f"fault-{boundary}",
        goal_id="fault-matrix",
        title="Fault-injected lifecycle transition",
        parent_operation_id=f"parent-{boundary}",
    )

    if boundary == "create_event":
        connection.execute(
            """
            CREATE TRIGGER fail_event_insert
            BEFORE INSERT ON executive_task_events
            BEGIN SELECT RAISE(ABORT, 'injected event persistence failure'); END
            """
        )
        with pytest.raises(sqlite3.IntegrityError, match="injected event"):
            manager.create_tasks([task])
        assert manager.get_task(task.id) is None
        manager.close()
        return

    manager.create_tasks([task])

    if boundary.startswith("claim_"):
        if boundary == "claim_update":
            connection.execute(
                """
                CREATE TRIGGER fail_claim_update
                BEFORE UPDATE OF lifecycle_state ON executive_tasks
                WHEN NEW.lifecycle_state = 'claimed'
                BEGIN SELECT RAISE(ABORT, 'injected claim update failure'); END
                """
            )
        else:
            connection.execute(
                """
                CREATE TRIGGER fail_claim_event
                BEFORE INSERT ON executive_task_events
                WHEN NEW.to_state = 'claimed'
                BEGIN SELECT RAISE(ABORT, 'injected claim event failure'); END
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="injected claim"):
            manager.claim_task(task.id)
        assert manager.get_task(task.id).status == TaskStatus.PENDING
        assert len(manager.task_events(task.id)) == 1
        manager.close()
        return

    if boundary.startswith("cancel_"):
        manager.close()
        manager = TaskManager(database=tmp_path / f"{boundary}.db")
        connection = manager.queue._connection
        if boundary == "cancel_update":
            connection.execute(
                """
                CREATE TRIGGER fail_cancel_update
                BEFORE UPDATE OF lifecycle_state ON executive_tasks
                WHEN NEW.lifecycle_state = 'cancelled'
                BEGIN SELECT RAISE(ABORT, 'injected cancel update failure'); END
                """
            )
        else:
            connection.execute(
                """
                CREATE TRIGGER fail_cancel_event
                BEFORE INSERT ON executive_task_events
                WHEN NEW.event_type = 'CANCELLATION_CONFIRMED'
                BEGIN SELECT RAISE(ABORT, 'injected cancel event failure'); END
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="injected cancel"):
            manager.request_cancellation(task.id)
        assert manager.get_task(task.id).status == TaskStatus.PENDING
        cancellation = connection.execute(
            "SELECT COUNT(*) FROM operation_cancellations WHERE task_id = ?",
            (task.id,),
        ).fetchone()[0]
        assert cancellation == 0
        manager.close()
        return

    claimed = manager.claim_task(task.id)
    assert claimed is not None and claimed.attempt_id

    if boundary.startswith("start_"):
        if boundary == "start_update":
            connection.execute(
                """
                CREATE TRIGGER fail_start_update
                BEFORE UPDATE OF lifecycle_state ON executive_tasks
                WHEN NEW.lifecycle_state = 'executing'
                BEGIN SELECT RAISE(ABORT, 'injected start update failure'); END
                """
            )
        else:
            connection.execute(
                """
                CREATE TRIGGER fail_start_event
                BEFORE INSERT ON executive_task_events
                WHEN NEW.to_state = 'executing'
                BEGIN SELECT RAISE(ABORT, 'injected start event failure'); END
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="injected start"):
            manager.start_task(task.id, claimed.attempt_id)
        assert manager.get_task(task.id).status == TaskStatus.CLAIMED
        assert len(manager.task_events(task.id)) == 2
        manager.close()
        return

    manager.start_task(task.id, claimed.attempt_id)

    if boundary.startswith("checkpoint_"):
        if boundary == "checkpoint_update":
            connection.execute(
                """
                CREATE TRIGGER fail_checkpoint_update
                BEFORE UPDATE OF task_data ON executive_tasks
                WHEN NEW.lifecycle_state = 'executing'
                BEGIN SELECT RAISE(ABORT, 'injected checkpoint update failure'); END
                """
            )
        else:
            connection.execute(
                """
                CREATE TRIGGER fail_checkpoint_event
                BEFORE INSERT ON executive_task_events
                WHEN NEW.event_type = 'STEP_RESULT'
                BEGIN SELECT RAISE(ABORT, 'injected checkpoint event failure'); END
                """
            )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="injected checkpoint",
        ):
            manager.queue.record_step_result(
                task.id,
                claimed.attempt_id,
                1,
                {"status": "COMPLETED"},
            )
        persisted = manager.get_task(task.id)
        assert persisted.status == TaskStatus.EXECUTING
        assert persisted.step_results == []
        assert len(manager.task_events(task.id)) == 3
        manager.close()
        return

    if boundary.startswith("uncertain_"):
        if boundary == "uncertain_update":
            connection.execute(
                """
                CREATE TRIGGER fail_uncertain_update
                BEFORE UPDATE OF lifecycle_state ON executive_tasks
                WHEN NEW.lifecycle_state = 'uncertain'
                BEGIN SELECT RAISE(ABORT, 'injected uncertain update failure'); END
                """
            )
        else:
            connection.execute(
                """
                CREATE TRIGGER fail_uncertain_event
                BEFORE INSERT ON executive_task_events
                WHEN NEW.to_state = 'uncertain'
                BEGIN SELECT RAISE(ABORT, 'injected uncertain event failure'); END
                """
            )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="injected uncertain",
        ):
            manager.mark_task_uncertain(
                task.id,
                claimed.attempt_id,
                failure_classification="executor_reported_uncertain",
            )
        assert manager.get_task(task.id).status == TaskStatus.EXECUTING
        assert len(manager.task_events(task.id)) == 3
        manager.close()
        return

    if boundary.startswith("remove_"):
        manager.fail_task(
            task.id,
            claimed.attempt_id,
            failure_classification="executor_reported_failure",
        )
        if boundary == "remove_update":
            connection.execute(
                """
                CREATE TRIGGER fail_remove_update
                BEFORE UPDATE OF removed_at ON executive_tasks
                WHEN NEW.removed_at IS NOT NULL
                BEGIN SELECT RAISE(ABORT, 'injected remove update failure'); END
                """
            )
        else:
            connection.execute(
                """
                CREATE TRIGGER fail_remove_event
                BEFORE INSERT ON executive_task_events
                WHEN NEW.to_state = 'removed'
                BEGIN SELECT RAISE(ABORT, 'injected remove event failure'); END
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="injected remove"):
            manager.remove_task(task.id)
        assert manager.get_task(task.id).status == TaskStatus.FAILED
        assert len(manager.task_events(task.id)) == 4
        manager.close()
        return

    pytest.fail(f"Unhandled fault-injection boundary: {boundary}")


@pytest.mark.parametrize("claimed_before_cancellation", [False, True])
def test_pre_dispatch_cancellation_survives_restart_and_blocks_claim(
    tmp_path,
    claimed_before_cancellation,
):
    database = tmp_path / f"cancelled-{claimed_before_cancellation}.db"
    manager = TaskManager(database=database)
    task = Task(
        id="cancel-before-dispatch",
        goal_id="cancellation-recovery",
        title="Cancel before dispatch",
        parent_operation_id="parent-cancellation-recovery",
    )
    manager.create_tasks([task])

    attempt_id = None
    if claimed_before_cancellation:
        claimed = manager.claim_task(task.id)
        assert claimed is not None
        attempt_id = claimed.attempt_id

    cancelled = manager.request_cancellation(
        task.id,
        attempt_id=attempt_id,
    )
    assert cancelled.status == TaskStatus.CANCELLED
    assert cancelled.cancellation_requested_at is not None
    manager.close()

    recovered = TaskManager(database=database)
    persisted = recovered.get_task(task.id)
    assert persisted is not None
    assert persisted.status == TaskStatus.CANCELLED
    assert persisted.cancellation_requested_at is not None
    assert recovered.claim_task() is None
    cancellation = recovered.queue._connection.execute(
        """
        SELECT task_id, attempt_id, disposition
        FROM operation_cancellations
        WHERE operation_id = ?
        """,
        ("parent-cancellation-recovery",),
    ).fetchone()
    assert cancellation == (
        task.id,
        attempt_id,
        "CANCELLED_BEFORE_DISPATCH",
    )
    recovered.close()


def test_approved_plan_cannot_use_legacy_untrusted_task_runner(tmp_path):
    manager = TaskManager(database=tmp_path / "legacy-runner.db")
    task = Task(
        id="trusted-plan-task",
        goal_id="trusted-plan-operation",
        title="Inspect local identity",
        plan=Plan(
            goal="Inspect local identity",
            steps=[
                PlanStep(
                    order=1,
                    action="run_command",
                    parameters={"command": "pwd"},
                )
            ],
        ),
        parent_operation_id="trusted-plan-operation",
    )
    manager.create_tasks([task])

    class MustNotExecute:
        def execute(self, *_args, **_kwargs):
            raise AssertionError("Protected task plan reached the legacy executor.")

    service = TaskService(manager, executor=MustNotExecute())
    with pytest.raises(PermissionError, match="trusted task lifecycle"):
        service.execute_next_task()

    persisted = manager.get_task(task.id)
    assert persisted.status == TaskStatus.READY
    assert persisted.attempt_count == 1
    manager.close()


def test_sqlite_claims_are_atomic_across_worker_processes(tmp_path):
    workspace = Path(__file__).resolve().parents[1]
    database = tmp_path / "concurrent-claims.db"
    start_file = tmp_path / "start-workers"
    manager = TaskManager(database=database)
    task = Task(
        id="single-claim",
        goal_id="concurrency-goal",
        title="Only one worker may claim",
    )
    manager.create_tasks([task])
    manager.close()

    worker = """
import json
import sys
import time
from pathlib import Path
from ultron.executive.tasks.manager import TaskManager

database, ready, start = sys.argv[1:]
manager = TaskManager(database=database)
Path(ready).touch()
while not Path(start).exists():
    time.sleep(0.01)
task = manager.claim_task()
print(json.dumps(None if task is None else {
    "id": task.id,
    "attempt_id": task.attempt_id,
}), flush=True)
manager.close()
"""
    environment = {
        "HOME": str(tmp_path),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(workspace),
    }
    processes = []
    ready_files = [tmp_path / "worker-1-ready", tmp_path / "worker-2-ready"]
    try:
        for ready in ready_files:
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        worker,
                        str(database),
                        str(ready),
                        str(start_file),
                    ],
                    cwd=workspace,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        deadline = time.monotonic() + 10
        while not all(path.exists() for path in ready_files):
            if time.monotonic() >= deadline:
                raise AssertionError("Claim workers did not become ready.")
            time.sleep(0.01)
        start_file.touch()
        outputs = [process.communicate(timeout=10) for process in processes]
        assert [process.returncode for process in processes] == [0, 0], [
            stderr for _, stderr in outputs
        ]
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=10)

    claims = [json.loads(stdout) for stdout, _ in outputs]
    assert len(claims) == 2
    assert sum(claim is not None for claim in claims) == 1
    winning_claim = next(claim for claim in claims if claim is not None)
    assert winning_claim["id"] == "single-claim"
    assert winning_claim["attempt_id"]


def test_task_claim_recovery_survives_process_termination(tmp_path):
    workspace = Path(__file__).resolve().parents[1]
    database = tmp_path / "process-lifecycle.db"
    manager = TaskManager(database=database)
    manager.create_tasks([
        Task(
            id="crash-before-dispatch",
            goal_id="process-goal",
            title="Crash before dispatch",
        ),
        Task(
            id="crash-after-dispatch",
            goal_id="process-goal",
            title="Crash after dispatch boundary",
        ),
    ])
    manager.close()

    worker = """
import os
import sys
from ultron.executive.tasks.manager import TaskManager

database, task_id, start = sys.argv[1:]
manager = TaskManager(database=database)
task = manager.claim_task()
if task is None or task.id != task_id:
    raise RuntimeError("Unexpected task claim.")
if start == "yes":
    manager.start_task(task.id, task.attempt_id)
print(task.id, flush=True)
os._exit(0)
"""
    environment = {
        "HOME": str(tmp_path),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(workspace),
    }
    for task_id, start in (
        ("crash-before-dispatch", "no"),
        ("crash-after-dispatch", "yes"),
    ):
        process = subprocess.run(
            [
                sys.executable,
                "-c",
                worker,
                str(database),
                task_id,
                start,
            ],
            cwd=workspace,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert process.stdout.strip() == task_id

    restarted = TaskManager(database=database)
    assert restarted.recover_interrupted_task(
        "crash-before-dispatch"
    ).status == TaskStatus.READY
    assert restarted.recover_interrupted_task(
        "crash-after-dispatch"
    ).status == TaskStatus.UNCERTAIN
    assert restarted.claim_task().id == "crash-before-dispatch"
    assert restarted.claim_task() is None
    restarted.close()