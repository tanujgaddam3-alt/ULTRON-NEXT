import hashlib
import os
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ultron.domain.enums import Priority, TaskStatus
from ultron.domain.task import Task

from .queue import TaskQueue


class SQLiteTaskQueue(TaskQueue):
    """SQLite-backed task records with atomic lifecycle transitions."""

    _FAILURE_CLASSIFICATIONS = {
        "executor_exception_after_execution_started",
        "executor_reported_failure",
        "executor_reported_uncertain",
        "pre_dispatch_failure",
        "process_interrupted_after_dispatch_boundary",
        "verification_confirmed_failure",
        "verification_unavailable",
        "cancellation_requested_after_dispatch",
    }
    _TRANSITIONS = {
        TaskStatus.CLAIMED: {
            TaskStatus.READY,
            TaskStatus.EXECUTING,
            TaskStatus.FAILED,
            TaskStatus.UNCERTAIN,
        },
        TaskStatus.EXECUTING: {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.UNCERTAIN,
        },
        TaskStatus.UNCERTAIN: {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
        },
    }

    def __init__(self, database: str | Path) -> None:
        super().__init__()
        self._lock = threading.RLock()
        database_path = str(database)
        self.database_identity = (
            ":memory:"
            if database_path == ":memory:"
            else str(Path(database_path).resolve())
        )

        if database_path != ":memory:":
            path = Path(database_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                raise ValueError("Task database path must not be a symbolic link.")
            try:
                descriptor = os.open(
                    path,
                    os.O_CREAT | os.O_EXCL | os.O_RDWR,
                    0o600,
                )
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
            os.chmod(database_path, 0o600)

        self._connection = sqlite3.connect(
            database_path,
            timeout=10.0,
            check_same_thread=False,
            isolation_level=None,
        )
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._connection.execute("PRAGMA busy_timeout = 10000")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS executive_tasks (
                queue_position INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                task_data TEXT NOT NULL
            )
            """
        )
        self._connection.execute("BEGIN IMMEDIATE")
        existing_columns = {
            row[1]
            for row in self._connection.execute(
                "PRAGMA table_info(executive_tasks)"
            )
        }
        needs_lifecycle_migration = "lifecycle_state" not in existing_columns
        for name, declaration in (
            ("lifecycle_state", "TEXT NOT NULL DEFAULT 'pending'"),
            ("attempt_id", "TEXT"),
            ("attempt_count", "INTEGER NOT NULL DEFAULT 0"),
            ("claimed_at", "TEXT"),
            ("started_at", "TEXT"),
            ("updated_at", "TEXT"),
            ("completed_at", "TEXT"),
            ("failure_classification", "TEXT"),
            ("removed_at", "TEXT"),
            ("cancellation_requested_at", "TEXT"),
            ("parent_operation_id", "TEXT"),
        ):
            if name not in existing_columns:
                self._connection.execute(
                    f"ALTER TABLE executive_tasks ADD COLUMN {name} {declaration}"
                )
        parent_operation_rows = self._connection.execute(
            "SELECT queue_position, task_data FROM executive_tasks "
            "WHERE parent_operation_id IS NULL"
        ).fetchall()
        for queue_position, task_data in parent_operation_rows:
            task_document = Task.model_validate_json(task_data)
            if task_document.parent_operation_id:
                self._connection.execute(
                    """
                    UPDATE executive_tasks SET parent_operation_id = ?
                    WHERE queue_position = ? AND parent_operation_id IS NULL
                    """,
                    (task_document.parent_operation_id, queue_position),
                )
        duplicates = self._connection.execute(
            """
            SELECT task_id
            FROM executive_tasks
            GROUP BY task_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        ).fetchone()
        if duplicates is not None:
            raise RuntimeError(
                "Task store contains duplicate identifiers; resolve them before "
                "enabling durable task claims."
            )
        if needs_lifecycle_migration:
            legacy_rows = self._connection.execute(
                "SELECT queue_position, task_data FROM executive_tasks"
            ).fetchall()
            for queue_position, task_data in legacy_rows:
                legacy_task = Task.model_validate_json(task_data)
                state = legacy_task.status
                if state in {TaskStatus.ACTIVE, TaskStatus.COMPLETED}:
                    state = TaskStatus.UNCERTAIN
                self._connection.execute(
                    """
                    UPDATE executive_tasks
                    SET lifecycle_state = ?, attempt_count = ?,
                        claimed_at = ?, started_at = ?, updated_at = ?,
                        completed_at = ?
                    WHERE queue_position = ?
                    """,
                    (
                        state.value,
                        legacy_task.attempt_count,
                        None if legacy_task.claimed_at is None else legacy_task.claimed_at.isoformat(),
                        None if legacy_task.started_at is None else legacy_task.started_at.isoformat(),
                        legacy_task.updated_at.isoformat(),
                        None if legacy_task.completed_at is None else legacy_task.completed_at.isoformat(),
                        queue_position,
                    ),
                )
        self._connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS executive_tasks_task_id "
            "ON executive_tasks(task_id)"
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS executive_task_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                attempt_id TEXT,
                from_state TEXT,
                to_state TEXT NOT NULL,
                failure_classification TEXT,
                verification_outcome TEXT,
                evidence_fingerprint TEXT,
                event_type TEXT NOT NULL DEFAULT 'STATE_TRANSITION',
                step_order INTEGER,
                created_at TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS operation_cancellations (
                operation_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                attempt_id TEXT,
                requested_at TEXT NOT NULL,
                disposition TEXT NOT NULL
            )
            """
        )
        event_columns = {
            row[1]
            for row in self._connection.execute(
                "PRAGMA table_info(executive_task_events)"
            )
        }
        for name in (
            "verification_outcome",
            "evidence_fingerprint",
            "event_type",
            "step_order",
        ):
            if name not in event_columns:
                declaration = "INTEGER" if name == "step_order" else "TEXT"
                self._connection.execute(
                    "ALTER TABLE executive_task_events "
                    f"ADD COLUMN {name} {declaration}"
                )
        self._connection.execute(
            """
            UPDATE executive_tasks
            SET lifecycle_state = 'uncertain'
            WHERE lifecycle_state = 'active'
            """
        )
        self._connection.commit()
        if database_path != ":memory:":
            os.chmod(database_path, 0o600)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat(timespec="milliseconds")

    @staticmethod
    def _submission_matches(existing: Task, incoming: Task) -> bool:
        fields = (
            "goal_id",
            "title",
            "description",
            "priority",
            "dependencies",
            "assigned_skill",
            "plan",
            "parent_operation_id",
        )
        return all(getattr(existing, field) == getattr(incoming, field) for field in fields)

    @staticmethod
    def _datetime(value):
        if value is None or isinstance(value, datetime):
            return value
        return datetime.fromisoformat(value)

    def _load(self, row) -> Task:
        task = Task.model_validate_json(row[0])
        return task.model_copy(
            update={
                "status": TaskStatus(row[1]),
                "attempt_id": row[2],
                "attempt_count": row[3],
                "claimed_at": self._datetime(row[4]),
                "started_at": self._datetime(row[5]),
                "updated_at": self._datetime(row[6]) or task.updated_at,
                "completed_at": self._datetime(row[7]),
                "cancellation_requested_at": self._datetime(row[8])
                if len(row) > 8
                else task.cancellation_requested_at,
            }
        )

    def add(self, task: Task) -> None:
        if task.status in {
            TaskStatus.CLAIMED,
            TaskStatus.EXECUTING,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.UNCERTAIN,
            TaskStatus.CANCELLED,
        }:
            raise ValueError(
                "Claimed and terminal task states must be reached through "
                "the task lifecycle API."
            )
        now = self._now()
        initial_task = (
            task.uncertain()
            if task.status == TaskStatus.ACTIVE
            else task
        )
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    """
                    SELECT task_data FROM executive_tasks
                    WHERE task_id = ?
                    """,
                    (task.id,),
                ).fetchone()
                if row is not None:
                    existing = Task.model_validate_json(row[0])
                    if not self._submission_matches(existing, initial_task):
                        raise ValueError(
                            f"Task identifier {task.id} was reused with different content."
                        )
                    self._connection.commit()
                    return
                self._connection.execute(
                    """
                    INSERT INTO executive_tasks (
                        task_id, task_data, lifecycle_state, updated_at,
                        parent_operation_id
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        initial_task.id,
                        initial_task.model_dump_json(),
                        initial_task.status.value,
                        now,
                        initial_task.parent_operation_id,
                    ),
                )
                self._event(
                    initial_task.id,
                    None,
                    initial_task.status.value,
                    None,
                    now,
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def claim_next(self, task_id: str | None = None) -> Task | None:
        now = self._now()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                task_filter = "" if task_id is None else "AND task_id = ?"
                query_parameters = () if task_id is None else (task_id,)
                rows = self._connection.execute(
                    f"""
                    SELECT task_data, lifecycle_state, attempt_id, attempt_count,
                           claimed_at, started_at, updated_at, completed_at,
                           queue_position
                    FROM executive_tasks
                    WHERE lifecycle_state IN ('pending', 'ready')
                      AND removed_at IS NULL
                      AND cancellation_requested_at IS NULL
                      {task_filter}
                    ORDER BY queue_position
                    """,
                    query_parameters,
                ).fetchall()
                candidates = [
                    (self._load(row[:8]), row[8])
                    for row in rows
                ]
                if not candidates:
                    self._connection.commit()
                    return None
                task, queue_position = min(
                    candidates,
                    key=lambda item: (
                        -self.PRIORITY_ORDER[item[0].priority],
                        item[1],
                    ),
                )
                attempt_id = str(uuid4())
                attempt_count = task.attempt_count + 1
                updated = task.model_copy(
                    update={
                        "status": TaskStatus.CLAIMED,
                        "attempt_id": attempt_id,
                        "attempt_count": attempt_count,
                        "claimed_at": datetime.fromisoformat(now),
                        "started_at": None,
                        "updated_at": datetime.fromisoformat(now),
                        "completed_at": None,
                    }
                )
                cursor = self._connection.execute(
                    """
                    UPDATE executive_tasks
                    SET lifecycle_state = 'claimed', attempt_id = ?,
                        attempt_count = ?, claimed_at = ?, started_at = NULL,
                        updated_at = ?, completed_at = NULL, task_data = ?
                    WHERE queue_position = ?
                      AND lifecycle_state IN ('pending', 'ready')
                      AND removed_at IS NULL
                    """,
                    (
                        attempt_id,
                        attempt_count,
                        now,
                        now,
                        updated.model_dump_json(),
                        queue_position,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("Atomic task claim was lost.")
                self._event(
                    task.id,
                    attempt_id,
                    TaskStatus.CLAIMED.value,
                    None,
                    now,
                    previous=task.status.value,
                )
                self._connection.commit()
                return updated
            except Exception:
                self._connection.rollback()
                raise

    def record_step_result(
        self,
        task_id: str,
        attempt_id: str,
        step_order: int,
        result: dict[str, object],
    ) -> Task:
        now = self._now()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    """
                    SELECT task_data, lifecycle_state, attempt_id, attempt_count,
                           claimed_at, started_at, updated_at, completed_at
                    FROM executive_tasks
                    WHERE task_id = ? AND removed_at IS NULL
                    """,
                    (task_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"Task {task_id} does not exist.")
                task = self._load(row)
                if (
                    task.status != TaskStatus.EXECUTING
                    or task.attempt_id != attempt_id
                    or any(
                        item.get("order") == step_order
                        for item in task.step_results
                    )
                ):
                    raise ValueError(
                        "Task step checkpoint does not match the active attempt."
                    )
                checkpoint = {"order": step_order, **result}
                updated = task.model_copy(
                    update={
                        "step_results": [*task.step_results, checkpoint],
                        "updated_at": datetime.fromisoformat(now),
                    }
                )
                cursor = self._connection.execute(
                    """
                    UPDATE executive_tasks
                    SET task_data = ?, updated_at = ?
                    WHERE task_id = ? AND lifecycle_state = 'executing'
                      AND attempt_id = ?
                    """,
                    (
                        updated.model_dump_json(),
                        now,
                        task_id,
                        attempt_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("Task step checkpoint was lost.")
                self._event(
                    task_id,
                    attempt_id,
                    TaskStatus.EXECUTING.value,
                    None,
                    now,
                    previous=TaskStatus.EXECUTING.value,
                    event_type="STEP_RESULT",
                    step_order=step_order,
                )
                self._connection.commit()
                return updated
            except Exception:
                self._connection.rollback()
                raise

    def transition(
        self,
        task_id: str,
        target: TaskStatus,
        *,
        attempt_id: str | None = None,
        failure_classification: str | None = None,
        verification_outcome: str | None = None,
        evidence_id: str | None = None,
    ) -> Task:
        if (
            failure_classification is not None
            and failure_classification not in self._FAILURE_CLASSIFICATIONS
        ):
            raise ValueError("Unsupported task failure classification.")
        now = self._now()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    """
                    SELECT task_data, lifecycle_state, attempt_id, attempt_count,
                           claimed_at, started_at, updated_at, completed_at
                    FROM executive_tasks
                    WHERE task_id = ? AND removed_at IS NULL
                    """,
                    (task_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"Task {task_id} is not present.")
                task = self._load(row)
                current = task.status
                if current == target:
                    if attempt_id is not None and task.attempt_id != attempt_id:
                        raise ValueError("Task attempt does not match current state.")
                    self._connection.commit()
                    return task
                if target not in self._TRANSITIONS.get(current, set()):
                    raise ValueError(
                        f"Invalid task transition: {current.value} -> {target.value}."
                    )
                if attempt_id is not None and task.attempt_id != attempt_id:
                    raise ValueError("Task attempt does not match current state.")
                if target == TaskStatus.EXECUTING and current != TaskStatus.CLAIMED:
                    raise ValueError("Only a claimed task can begin execution.")
                if (
                    target == TaskStatus.EXECUTING
                    and task.cancellation_requested_at is not None
                ):
                    raise ValueError("A task with a cancellation request cannot start.")
                if target == TaskStatus.READY and current != TaskStatus.CLAIMED:
                    raise ValueError("Only a pre-dispatch claim can be released.")
                if target == TaskStatus.COMPLETED and (
                    verification_outcome != "CONFIRMED_SUCCESS"
                    or not evidence_id
                ):
                    raise ValueError(
                        "Completed tasks require confirmed verifier evidence."
                    )
                if current == TaskStatus.UNCERTAIN:
                    expected_outcome = (
                        "CONFIRMED_SUCCESS"
                        if target == TaskStatus.COMPLETED
                        else "CONFIRMED_FAILURE"
                        if target == TaskStatus.FAILED
                        else None
                    )
                    if (
                        verification_outcome != expected_outcome
                        or not evidence_id
                    ):
                        raise ValueError(
                            "Uncertain tasks require confirmed verifier evidence."
                        )
                elif verification_outcome is not None or evidence_id is not None:
                    if (
                        target != TaskStatus.COMPLETED
                        or verification_outcome != "CONFIRMED_SUCCESS"
                        or not evidence_id
                    ):
                        raise ValueError(
                            "Verifier evidence does not match the task transition."
                        )

                started_at = row[5]
                completed_at = row[7]
                if target == TaskStatus.EXECUTING:
                    started_at = now
                if target in {
                    TaskStatus.COMPLETED,
                    TaskStatus.FAILED,
                    TaskStatus.UNCERTAIN,
                }:
                    completed_at = now if target == TaskStatus.COMPLETED else None
                updated = task.model_copy(
                    update={
                        "status": target,
                        "updated_at": datetime.fromisoformat(now),
                        "started_at": self._datetime(started_at),
                        "completed_at": self._datetime(completed_at),
                    }
                )
                self._connection.execute(
                    """
                    UPDATE executive_tasks
                    SET lifecycle_state = ?, task_data = ?, started_at = ?,
                        completed_at = ?, updated_at = ?,
                        failure_classification = ?
                    WHERE task_id = ? AND removed_at IS NULL
                    """,
                    (
                        target.value,
                        updated.model_dump_json(),
                        started_at,
                        completed_at,
                        now,
                        failure_classification,
                        task_id,
                    ),
                )
                self._event(
                    task_id,
                    task.attempt_id,
                    target.value,
                    failure_classification,
                    now,
                    previous=current.value,
                    verification_outcome=verification_outcome,
                    evidence_id=evidence_id,
                )
                self._connection.commit()
                return updated
            except Exception:
                self._connection.rollback()
                raise

    def request_cancellation(
        self,
        task_id: str,
        *,
        attempt_id: str | None = None,
    ) -> Task:
        """Persist cancellation; in-flight dispatched work remains uncertain."""
        now = self._now()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    """
                    SELECT task_data, lifecycle_state, attempt_id, attempt_count,
                           claimed_at, started_at, updated_at, completed_at
                    FROM executive_tasks
                    WHERE task_id = ? AND removed_at IS NULL
                    """,
                    (task_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"Task {task_id} is not present.")
                task = self._load(row)
                if attempt_id is not None and task.attempt_id != attempt_id:
                    raise ValueError("Task attempt does not match current state.")
                if task.status == TaskStatus.CANCELLED:
                    self._connection.commit()
                    return task
                cancellable_states = {
                    TaskStatus.PENDING,
                    TaskStatus.READY,
                    TaskStatus.CLAIMED,
                    TaskStatus.EXECUTING,
                    TaskStatus.UNCERTAIN,
                }
                if task.status not in cancellable_states:
                    raise ValueError(
                        f"Task in {task.status.value} state cannot be cancelled."
                    )

                has_dispatch_claim = False
                if task.parent_operation_id:
                    grants_table = self._connection.execute(
                        """
                        SELECT 1 FROM sqlite_master
                        WHERE type = 'table' AND name = 'authorization_grants'
                        """
                    ).fetchone()
                    if grants_table:
                        has_dispatch_claim = self._connection.execute(
                            """
                            SELECT 1 FROM authorization_grants
                            WHERE parent_operation_id = ?
                              AND dispatch_claimed_at IS NOT NULL
                            LIMIT 1
                            """,
                            (task.parent_operation_id,),
                        ).fetchone() is not None

                no_dispatch_possible = task.status in {
                    TaskStatus.PENDING,
                    TaskStatus.READY,
                    TaskStatus.CLAIMED,
                } or (
                    task.status == TaskStatus.EXECUTING
                    and not has_dispatch_claim
                )
                target = (
                    TaskStatus.CANCELLED
                    if no_dispatch_possible
                    else task.status
                )
                disposition = (
                    "CANCELLED_BEFORE_DISPATCH"
                    if target == TaskStatus.CANCELLED
                    else "UNCERTAIN_REQUIRES_RECONCILIATION"
                )
                cancellation_at = task.cancellation_requested_at or now
                updated = task.model_copy(
                    update={
                        "status": target,
                        "cancellation_requested_at": datetime.fromisoformat(
                            cancellation_at
                        ),
                        "updated_at": datetime.fromisoformat(now),
                        "completed_at": None,
                    }
                )
                self._connection.execute(
                    """
                    UPDATE executive_tasks
                    SET lifecycle_state = ?, task_data = ?,
                        cancellation_requested_at = ?, updated_at = ?,
                        completed_at = NULL
                    WHERE task_id = ? AND removed_at IS NULL
                    """,
                    (
                        target.value,
                        updated.model_dump_json(),
                        cancellation_at,
                        now,
                        task_id,
                    ),
                )
                if task.parent_operation_id:
                    self._connection.execute(
                        """
                        INSERT INTO operation_cancellations (
                            operation_id, task_id, attempt_id,
                            requested_at, disposition
                        ) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(operation_id) DO UPDATE SET
                            attempt_id = excluded.attempt_id,
                            disposition = excluded.disposition
                        """,
                        (
                            task.parent_operation_id,
                            task_id,
                            task.attempt_id,
                            cancellation_at,
                            disposition,
                        ),
                    )
                self._event(
                    task_id,
                    task.attempt_id,
                    target.value,
                    (
                        "cancellation_requested_after_dispatch"
                        if target != TaskStatus.CANCELLED
                        else None
                    ),
                    now,
                    previous=task.status.value,
                    event_type=(
                        "CANCELLATION_CONFIRMED"
                        if target == TaskStatus.CANCELLED
                        else "CANCELLATION_REQUESTED"
                    ),
                )
                self._connection.commit()
                return updated
            except Exception:
                self._connection.rollback()
                raise

    def recover_interrupted(self, task_id: str) -> Task:
        """Resolve a stopped claim safely without automatically replaying execution."""
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"Task {task_id} is not present.")
        if task.status == TaskStatus.CLAIMED:
            return self.transition(
                task_id,
                TaskStatus.READY,
                attempt_id=task.attempt_id,
            )
        if task.status == TaskStatus.EXECUTING:
            return self.transition(
                task_id,
                TaskStatus.UNCERTAIN,
                attempt_id=task.attempt_id,
                failure_classification="process_interrupted_after_dispatch_boundary",
            )
        if task.status == TaskStatus.CANCELLED:
            return task
        if task.status in {TaskStatus.READY, TaskStatus.UNCERTAIN}:
            return task
        raise ValueError(
            f"Task in {task.status.value} state cannot be recovered."
        )

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT task_data, lifecycle_state, attempt_id, attempt_count,
                       claimed_at, started_at, updated_at, completed_at
                FROM executive_tasks
                WHERE task_id = ? AND removed_at IS NULL
                """,
                (task_id,),
            ).fetchone()
        return None if row is None else self._load(row)

    def next(self) -> Task | None:
        tasks = self.all()
        runnable = [
            task for task in tasks
            if task.status in {TaskStatus.PENDING, TaskStatus.READY}
        ]
        return min(
            runnable,
            key=lambda task: -self.PRIORITY_ORDER[task.priority],
            default=None,
        )

    def remove(self, task_id: str) -> None:
        now = self._now()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    """
                    SELECT lifecycle_state, attempt_id
                    FROM executive_tasks
                    WHERE task_id = ? AND removed_at IS NULL
                    """,
                    (task_id,),
                ).fetchone()
                if row is None:
                    self._connection.commit()
                    return
                status = TaskStatus(row[0])
                if status in {
                    TaskStatus.CLAIMED,
                    TaskStatus.EXECUTING,
                    TaskStatus.UNCERTAIN,
                }:
                    raise ValueError(
                        "Claimed, executing, or uncertain tasks must be resolved "
                        "before removal."
                    )
                self._connection.execute(
                    "UPDATE executive_tasks SET removed_at = ?, updated_at = ? "
                    "WHERE task_id = ? AND removed_at IS NULL",
                    (now, now, task_id),
                )
                self._event(
                    task_id,
                    row[1],
                    "removed",
                    None,
                    now,
                    previous=status.value,
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def all(self) -> list[Task]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT task_data, lifecycle_state, attempt_id, attempt_count,
                       claimed_at, started_at, updated_at, completed_at
                FROM executive_tasks
                WHERE removed_at IS NULL
                ORDER BY queue_position
                """
            ).fetchall()
        tasks = [self._load(row) for row in rows]
        tasks.sort(
            key=lambda task: -self.PRIORITY_ORDER[task.priority]
        )
        return tasks

    def events(self, task_id: str) -> list[dict[str, str | int | None]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT attempt_id, from_state, to_state,
                       failure_classification, verification_outcome,
                       evidence_fingerprint, event_type, step_order,
                       created_at
                FROM executive_task_events
                WHERE task_id = ?
                ORDER BY event_id
                """,
                (task_id,),
            ).fetchall()
        return [
            {
                "attempt_id": row[0],
                "from_state": row[1],
                "to_state": row[2],
                "failure_classification": row[3],
                "verification_outcome": row[4],
                "evidence_fingerprint": row[5],
                "event_type": row[6],
                "step_order": row[7],
                "created_at": row[8],
            }
            for row in rows
        ]

    def _event(
        self,
        task_id: str,
        attempt_id: str | None,
        target: str,
        failure_classification: str | None,
        timestamp: str,
        *,
        previous: str | None = None,
        verification_outcome: str | None = None,
        evidence_id: str | None = None,
        event_type: str = "STATE_TRANSITION",
        step_order: int | None = None,
    ) -> None:
        evidence_fingerprint = (
            None
            if evidence_id is None
            else hashlib.sha256(evidence_id.encode("utf-8")).hexdigest()
        )
        self._connection.execute(
            """
            INSERT INTO executive_task_events (
                task_id, attempt_id, from_state, to_state,
                failure_classification, verification_outcome,
                evidence_fingerprint, event_type, step_order, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                attempt_id,
                previous,
                target,
                failure_classification,
                verification_outcome,
                evidence_fingerprint,
                event_type,
                step_order,
                timestamp,
            ),
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()
