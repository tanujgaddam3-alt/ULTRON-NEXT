from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class SQLiteOperationStore:
    """Durable lifecycle and idempotency storage for gateway operations."""

    _OPERATION_STATES = {
        "PLANNED",
        "SUGGESTED",
        "APPROVED",
        "EXECUTING",
        "COMPLETED",
        "VERIFIED",
        "UNVERIFIED",
        "UNCERTAIN",
        "FAILED",
        "CANCELLED",
    }
    _OPERATION_TRANSITIONS = {
        "PLANNED": {"SUGGESTED", "FAILED", "CANCELLED"},
        "SUGGESTED": {"APPROVED", "FAILED", "CANCELLED"},
        "APPROVED": {"EXECUTING", "FAILED", "UNCERTAIN", "CANCELLED"},
        "EXECUTING": {
            "COMPLETED",
            "VERIFIED",
            "UNVERIFIED",
            "UNCERTAIN",
            "FAILED",
        },
        "COMPLETED": {"VERIFIED", "FAILED", "UNCERTAIN"},
        "UNVERIFIED": {"VERIFIED", "FAILED", "UNCERTAIN"},
        "UNCERTAIN": {"VERIFIED", "FAILED"},
        "VERIFIED": set(),
        "FAILED": set(),
        "CANCELLED": set(),
    }

    def __init__(self, database: str | Path | None = None) -> None:
        if database is None:
            database = os.environ.get("ULTRON_OPERATION_DB")
        if database is None:
            database = (
                Path(__file__).resolve().parents[3]
                / "data"
                / "ultron_operations.db"
            )

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
                raise ValueError(
                    "Operation database path must not be a symbolic link."
                )
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

        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            database_path,
            timeout=5.0,
            check_same_thread=False,
            isolation_level=None,
        )
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._connection.execute("PRAGMA busy_timeout = 10000")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS operations (
                operation_id TEXT PRIMARY KEY,
                correlation_id TEXT UNIQUE,
                request_fingerprint TEXT,
                lifecycle_state TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                parent_operation_id TEXT
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS authorization_grants (
                grant_id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL,
                purpose TEXT NOT NULL,
                principal TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                action TEXT NOT NULL DEFAULT '',
                resource_fingerprint TEXT NOT NULL DEFAULT '',
                dispatch_claimed_at TEXT,
                parent_operation_id TEXT,
                dispatch_enabled INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        grant_columns = {
            row[1]
            for row in self._connection.execute(
                "PRAGMA table_info(authorization_grants)"
            ).fetchall()
        }
        operation_columns = {
            row[1]
            for row in self._connection.execute(
                "PRAGMA table_info(operations)"
            ).fetchall()
        }
        if "parent_operation_id" not in operation_columns:
            self._connection.execute(
                "ALTER TABLE operations ADD COLUMN parent_operation_id TEXT"
            )
        for column, definition in (
            ("action", "TEXT NOT NULL DEFAULT ''"),
            ("resource_fingerprint", "TEXT NOT NULL DEFAULT ''"),
            ("dispatch_claimed_at", "TEXT"),
            ("parent_operation_id", "TEXT"),
            ("dispatch_enabled", "INTEGER NOT NULL DEFAULT 1"),
        ):
            if column not in grant_columns:
                self._connection.execute(
                    "ALTER TABLE authorization_grants "
                    f"ADD COLUMN {column} {definition}"
                )
        legacy_operations = self._connection.execute(
            """
            SELECT operation_id, data FROM operations
            WHERE parent_operation_id IS NULL
            """
        ).fetchall()
        for operation_id, raw_data in legacy_operations:
            operation = self._deserialize(raw_data)
            parent_operation_id = operation.get("parentOperationId")
            if isinstance(parent_operation_id, str) and parent_operation_id:
                self._connection.execute(
                    """
                    UPDATE operations SET parent_operation_id = ?
                    WHERE operation_id = ? AND parent_operation_id IS NULL
                    """,
                    (parent_operation_id, operation_id),
                )
        self._connection.execute(
            """
            UPDATE authorization_grants
            SET parent_operation_id = (
                    SELECT parent_operation_id FROM operations
                    WHERE operations.operation_id =
                        authorization_grants.operation_id
                ),
                dispatch_enabled = 0
            WHERE parent_operation_id IS NULL
              AND EXISTS (
                  SELECT 1 FROM operations
                  WHERE operations.operation_id =
                        authorization_grants.operation_id
                    AND operations.parent_operation_id IS NOT NULL
              )
            """
        )
        self._connection.execute(
                "CREATE INDEX IF NOT EXISTS operations_parent "
                "ON operations(parent_operation_id, lifecycle_state)"
        )
        self._connection.execute(
                "CREATE INDEX IF NOT EXISTS grants_parent "
                "ON authorization_grants(parent_operation_id, dispatch_enabled)"
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reconciliation_requests (
                correlation_id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                result TEXT
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
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS task_cancellation_requests (
                correlation_id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                grant_id TEXT NOT NULL UNIQUE,
                principal TEXT NOT NULL,
                session_id TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_audit (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT,
                operation_id TEXT,
                plan_id TEXT,
                attempt_id TEXT,
                correlation_id TEXT,
                action TEXT NOT NULL,
                event TEXT NOT NULL,
                execution_outcome TEXT NOT NULL,
                verification_outcome TEXT NOT NULL,
                failure_classification TEXT,
                evidence TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS operation_lifecycle_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL,
                from_state TEXT,
                to_state TEXT,
                event_type TEXT NOT NULL,
                grant_id TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS execution_audit_operation "
            "ON execution_audit(operation_id, audit_id)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS operation_lifecycle_events_operation "
            "ON operation_lifecycle_events(operation_id, event_id)"
        )

    def _record_operation_event(
        self,
        operation_id: str,
        *,
        from_state: str | None,
        to_state: str | None,
        event_type: str,
        grant_id: str | None = None,
        created_at: str | None = None,
    ) -> None:
        if from_state is not None and from_state not in self._OPERATION_STATES:
            raise ValueError("Unsupported operation event source state.")
        if to_state is not None and to_state not in self._OPERATION_STATES:
            raise ValueError("Unsupported operation event target state.")
        self._connection.execute(
            """
            INSERT INTO operation_lifecycle_events (
                operation_id, from_state, to_state, event_type, grant_id,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                operation_id,
                from_state,
                to_state,
                event_type,
                grant_id,
                created_at or datetime.now(UTC).isoformat(
                    timespec="milliseconds"
                ),
            ),
        )

    def _rollback_preserving_error(self, error: Exception) -> bool:
        try:
            self._connection.rollback()
            return True
        except Exception as rollback_error:
            error.add_note(
                "SQLite rollback failed "
                f"({type(rollback_error).__name__}); transaction state may "
                "require operator inspection."
            )
            return False

    @staticmethod
    def _safe_audit_text(value: Any, limit: int = 160) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("Audit identifiers and labels must be strings.")
        return value[:limit]

    def record_execution_audit(self, record: dict[str, Any]) -> int:
        """Persist allow-listed execution metadata, never parameters or messages."""
        allowed_evidence = {
            key: record["evidence"][key]
            for key in (
                "dispatch_started",
                "retry_allowed",
                "retry",
                "attempt_number",
                "max_retries",
            )
            if key in record.get("evidence", {})
        }
        for value in allowed_evidence.values():
            if not isinstance(value, (bool, int)):
                raise TypeError("Audit evidence must contain only booleans or integers.")

        row = (
            self._safe_audit_text(record.get("task_id")),
            self._safe_audit_text(record.get("operation_id")),
            self._safe_audit_text(record.get("plan_id")),
            self._safe_audit_text(record.get("attempt_id")),
            self._safe_audit_text(record.get("correlation_id")),
            self._safe_audit_text(record.get("action")) or "unknown",
            self._safe_audit_text(record.get("event")) or "unknown",
            self._safe_audit_text(record.get("execution_outcome")) or "unknown",
            self._safe_audit_text(record.get("verification_outcome"))
            or "NOT_PERFORMED",
            self._safe_audit_text(record.get("failure_classification")),
            self._serialize(allowed_evidence),
            datetime.now(UTC).isoformat(timespec="milliseconds"),
        )
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._connection.execute(
                    """
                    INSERT INTO execution_audit (
                        task_id, operation_id, plan_id, attempt_id,
                        correlation_id, action, event, execution_outcome,
                        verification_outcome, failure_classification,
                        evidence, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )
                audit_id = int(cursor.lastrowid)
                self._connection.commit()
                return audit_id
            except Exception as error:
                self._rollback_preserving_error(error)
                raise

    def execution_audit(
        self,
        *,
        task_id: str | None = None,
        operation_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 5000:
            raise ValueError("Audit limit must be between 1 and 5000.")
        clauses = []
        parameters: list[Any] = []
        if task_id is not None:
            clauses.append("task_id = ?")
            parameters.append(task_id)
        if operation_id is not None:
            clauses.append("operation_id = ?")
            parameters.append(operation_id)
        where = "" if not clauses else "WHERE " + " AND ".join(clauses)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT audit_id, task_id, operation_id, plan_id, attempt_id,
                       correlation_id, action, event, execution_outcome,
                       verification_outcome, failure_classification,
                       evidence, created_at
                FROM execution_audit
                {where}
                ORDER BY audit_id
                LIMIT ?
                """,
                (*parameters, limit),
            ).fetchall()
        fields = (
            "audit_id",
            "task_id",
            "operation_id",
            "plan_id",
            "attempt_id",
            "correlation_id",
            "action",
            "event",
            "execution_outcome",
            "verification_outcome",
            "failure_classification",
            "evidence",
            "created_at",
        )
        return [
            {
                **dict(zip(fields, row)),
                "evidence": json.loads(row[11]),
            }
            for row in rows
        ]

    def operation_lifecycle_events(
        self,
        operation_id: str,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT event_id, from_state, to_state, event_type, grant_id,
                       created_at
                FROM operation_lifecycle_events
                WHERE operation_id = ?
                ORDER BY event_id
                """,
                (operation_id,),
            ).fetchall()
        return [
            {
                "event_id": row[0],
                "from_state": row[1],
                "to_state": row[2],
                "event_type": row[3],
                "grant_id": row[4],
                "created_at": row[5],
            }
            for row in rows
        ]

    @staticmethod
    def _serialize(operation: dict[str, Any]) -> str:
        return json.dumps(operation, default=str, separators=(",", ":"))

    @staticmethod
    def _deserialize(data: str) -> dict[str, Any]:
        operation = json.loads(data)
        if not isinstance(operation, dict):
            raise ValueError("Persisted operation must be a JSON object.")
        return operation

    def create(self, operation: dict[str, Any]) -> None:
        state = operation.get("lifecycleState")
        if state not in self._OPERATION_STATES:
            raise ValueError("Unsupported operation lifecycle state.")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """
                    INSERT INTO operations (
                        operation_id, correlation_id, request_fingerprint,
                        lifecycle_state, data, created_at, updated_at,
                        parent_operation_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        operation["operationId"],
                        operation.get("correlationId"),
                        operation.get("requestFingerprint"),
                        state,
                        self._serialize(operation),
                        operation["createdAt"],
                        operation["updatedAt"],
                        operation.get("parentOperationId"),
                    ),
                )
                self._record_operation_event(
                    operation["operationId"],
                    from_state=None,
                    to_state=state,
                    event_type="OPERATION_CREATED",
                    created_at=operation["createdAt"],
                )
                self._connection.commit()
            except Exception as error:
                self._rollback_preserving_error(error)
                raise

    def update(self, operation: dict[str, Any]) -> None:
        operation_id = operation.get("operationId")
        target_state = operation.get("lifecycleState")
        if not isinstance(operation_id, str) or not operation_id:
            raise ValueError("Operation updates require a persisted operation ID.")
        if target_state not in self._OPERATION_STATES:
            raise ValueError("Unsupported operation lifecycle state.")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    """
                    SELECT lifecycle_state, data
                    FROM operations
                    WHERE operation_id = ?
                    """,
                    (operation_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"Operation {operation_id} is not persisted.")
                current_state = row[0]
                persisted_operation = self._deserialize(row[1])
                if persisted_operation.get("lifecycleState") != current_state:
                    raise RuntimeError(
                        "Persisted operation state and lifecycle data disagree."
                    )
                if target_state != current_state and target_state not in (
                    self._OPERATION_TRANSITIONS.get(current_state, set())
                ):
                    raise ValueError(
                        f"Invalid operation transition: "
                        f"{current_state} -> {target_state}."
                    )
                if current_state == "SUGGESTED" and target_state == "APPROVED":
                    raise ValueError(
                        "Operation approval must atomically persist its trusted grant."
                    )
                if (
                    operation.get("correlationId")
                    != persisted_operation.get("correlationId")
                    or operation.get("requestFingerprint")
                    != persisted_operation.get("requestFingerprint")
                ):
                    raise ValueError(
                        "Operation identity and request scope are immutable."
                    )
                cursor = self._connection.execute(
                    """
                    UPDATE operations
                    SET lifecycle_state = ?, data = ?, updated_at = ?,
                        parent_operation_id = ?
                    WHERE operation_id = ? AND lifecycle_state = ?
                    """,
                    (
                        target_state,
                        self._serialize(operation),
                        operation["updatedAt"],
                        operation.get("parentOperationId"),
                        operation_id,
                        current_state,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("Conditional operation update was lost.")
                if target_state != current_state:
                    self._record_operation_event(
                        operation_id,
                        from_state=current_state,
                        to_state=target_state,
                        event_type="STATE_TRANSITION",
                        grant_id=(
                            operation.get("authorization", {}).get("grantId")
                            if isinstance(operation.get("authorization"), dict)
                            else None
                        ),
                        created_at=operation["updatedAt"],
                    )
                self._connection.commit()
            except Exception as error:
                self._rollback_preserving_error(error)
                raise

    def claim_authorization_grant(self, grant: dict[str, Any]) -> bool:
        """Persist one-use grant IDs; replayed grants cannot authorize another action."""
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """
                    INSERT INTO authorization_grants (
                        grant_id, operation_id, correlation_id,
                        request_fingerprint, purpose, principal,
                        expires_at, created_at, action, resource_fingerprint,
                        parent_operation_id, dispatch_enabled
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 1)
                    """,
                    (
                        grant["grantId"],
                        grant["operationId"],
                        grant["correlationId"],
                        grant["requestFingerprint"],
                        grant["purpose"],
                        grant["principal"],
                        grant["expiresAt"],
                        grant["issuedAt"],
                        grant.get("action", ""),
                        grant.get("resourceFingerprint", ""),
                    ),
                )
            except sqlite3.IntegrityError as error:
                if not self._rollback_preserving_error(error):
                    raise
                return False
            try:
                operation = self._connection.execute(
                    """
                    SELECT lifecycle_state FROM operations
                    WHERE operation_id = ?
                    """,
                    (grant["operationId"],),
                ).fetchone()
                if operation is None:
                    raise KeyError(
                        f"Operation {grant['operationId']} is not persisted."
                    )
                self._record_operation_event(
                    grant["operationId"],
                    from_state=operation[0],
                    to_state=operation[0],
                    event_type="AUTHORIZATION_GRANT_CLAIMED",
                    grant_id=grant["grantId"],
                )
                self._connection.commit()
                return True
            except Exception as error:
                self._rollback_preserving_error(error)
                raise

    def claim_authorization_grant_and_approve_operation(
        self,
        grant: dict[str, Any],
        operation: dict[str, Any],
        *,
        parent_operation_id: str | None = None,
    ) -> bool:
        """Atomically consume a grant and persist its operation approval."""
        if operation.get("lifecycleState") != "APPROVED":
            raise ValueError("Approved operation state is required.")
        operation_id = operation["operationId"]
        if grant.get("operationId") != operation_id:
            raise ValueError("Grant and operation identifiers must match.")

        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                try:
                    self._connection.execute(
                        """
                        INSERT INTO authorization_grants (
                            grant_id, operation_id, correlation_id,
                            request_fingerprint, purpose, principal,
                            expires_at, created_at, action,
                            resource_fingerprint, parent_operation_id,
                            dispatch_enabled
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            grant["grantId"],
                            grant["operationId"],
                            grant["correlationId"],
                            grant["requestFingerprint"],
                            grant["purpose"],
                            grant["principal"],
                            grant["expiresAt"],
                            grant["issuedAt"],
                            grant["action"],
                            grant["resourceFingerprint"],
                            parent_operation_id,
                            int(parent_operation_id is None),
                        ),
                    )
                except sqlite3.IntegrityError:
                    self._connection.rollback()
                    return False

                cursor = self._connection.execute(
                    """
                    UPDATE operations
                    SET lifecycle_state = ?, data = ?, updated_at = ?
                    WHERE operation_id = ?
                      AND correlation_id = ?
                      AND request_fingerprint = ?
                      AND lifecycle_state = 'SUGGESTED'
                    """,
                    (
                        operation["lifecycleState"],
                        self._serialize(operation),
                        operation["updatedAt"],
                        operation_id,
                        operation.get("correlationId"),
                        operation.get("requestFingerprint"),
                    ),
                )
                if cursor.rowcount != 1:
                    self._connection.rollback()
                    return False
                self._record_operation_event(
                    operation_id,
                    from_state="SUGGESTED",
                    to_state="APPROVED",
                    event_type="AUTHORIZATION_APPROVED",
                    grant_id=grant["grantId"],
                    created_at=operation["updatedAt"],
                )
                self._connection.commit()
                return True
            except Exception as error:
                self._rollback_preserving_error(error)
                raise

    def activate_child_authorization_dispatch(
        self,
        *,
        grant_id: str,
        operation_id: str,
        correlation_id: str,
        request_fingerprint: str,
        purpose: str,
        principal: str,
        action: str,
        resource_fingerprint: str,
        expires_at: str,
        parent_operation_id: str,
        task_id: str,
        attempt_id: str,
        child_operation: dict[str, Any],
    ) -> bool:
        """Atomically activate one child grant and mark its step executing."""
        if (
            child_operation.get("operationId") != operation_id
            or child_operation.get("parentOperationId") != parent_operation_id
            or child_operation.get("lifecycleState") != "EXECUTING"
            or task_id != f"task-{parent_operation_id}"
            or not attempt_id
        ):
            raise ValueError("Child operation scope is inconsistent.")
        now = datetime.now(UTC).isoformat(timespec="milliseconds")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                parent = self._connection.execute(
                    """
                    SELECT 1 FROM operations
                    WHERE operation_id = ? AND lifecycle_state = 'EXECUTING'
                      AND NOT EXISTS (
                          SELECT 1 FROM operation_cancellations
                          WHERE operation_id = ?
                      )
                    """,
                    (parent_operation_id, parent_operation_id),
                ).fetchone()
                if parent is None:
                    self._connection.rollback()
                    return False
                task = self._connection.execute(
                    """
                    SELECT 1 FROM executive_tasks
                    WHERE task_id = ?
                      AND attempt_id = ?
                      AND lifecycle_state = 'executing'
                      AND cancellation_requested_at IS NULL
                    """,
                    (task_id, attempt_id),
                ).fetchone()
                if task is None:
                    self._connection.rollback()
                    return False
                grant = self._connection.execute(
                    """
                    UPDATE authorization_grants
                    SET dispatch_enabled = 1
                    WHERE grant_id = ?
                      AND operation_id = ?
                      AND correlation_id = ?
                      AND request_fingerprint = ?
                      AND purpose = ?
                      AND principal = ?
                      AND action = ?
                      AND resource_fingerprint = ?
                      AND expires_at = ?
                      AND parent_operation_id = ?
                      AND dispatch_enabled = 0
                      AND dispatch_claimed_at IS NULL
                      AND expires_at > ?
                    """,
                    (
                        grant_id,
                        operation_id,
                        correlation_id,
                        request_fingerprint,
                        purpose,
                        principal,
                        action,
                        resource_fingerprint,
                        expires_at,
                        parent_operation_id,
                        now,
                    ),
                )
                if grant.rowcount != 1:
                    self._connection.rollback()
                    return False
                operation_update = self._connection.execute(
                    """
                    UPDATE operations
                    SET lifecycle_state = 'EXECUTING', data = ?, updated_at = ?
                    WHERE operation_id = ?
                      AND parent_operation_id = ?
                      AND lifecycle_state = 'APPROVED'
                    """,
                    (
                        self._serialize(child_operation),
                        child_operation["updatedAt"],
                        operation_id,
                        parent_operation_id,
                    ),
                )
                if operation_update.rowcount != 1:
                    self._connection.rollback()
                    return False
                self._record_operation_event(
                    operation_id,
                    from_state="APPROVED",
                    to_state="EXECUTING",
                    event_type="CHILD_DISPATCH_ACTIVATED",
                    grant_id=grant_id,
                    created_at=child_operation["updatedAt"],
                )
                self._connection.commit()
                return True
            except Exception as error:
                self._rollback_preserving_error(error)
                raise

    def claim_authorization_dispatch(
        self,
        *,
        grant_id: str,
        operation_id: str,
        correlation_id: str,
        request_fingerprint: str,
        purpose: str,
        principal: str,
        action: str,
        resource_fingerprint: str,
        expires_at: str,
    ) -> bool:
        """Atomically claim the single final dispatch allowed by a grant."""
        claimed_at = datetime.now(UTC).isoformat(timespec="milliseconds")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._connection.execute(
                    """
                    UPDATE authorization_grants
                    SET dispatch_claimed_at = ?
                    WHERE grant_id = ?
                      AND operation_id = ?
                      AND correlation_id = ?
                      AND request_fingerprint = ?
                      AND purpose = ?
                      AND principal = ?
                      AND action = ?
                      AND resource_fingerprint = ?
                      AND expires_at = ?
                      AND dispatch_enabled = 1
                      AND dispatch_claimed_at IS NULL
                      AND expires_at > ?
                      AND EXISTS (
                          SELECT 1 FROM operations AS operation
                          WHERE operation.operation_id =
                              authorization_grants.operation_id
                            AND operation.lifecycle_state = 'EXECUTING'
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM operation_cancellations AS cancel
                          WHERE cancel.operation_id =
                              authorization_grants.operation_id
                      )
                      AND (
                          parent_operation_id IS NULL
                          OR EXISTS (
                              SELECT 1 FROM operations AS parent
                              WHERE parent.operation_id =
                                  authorization_grants.parent_operation_id
                                AND parent.lifecycle_state = 'EXECUTING'
                                AND NOT EXISTS (
                                    SELECT 1 FROM operation_cancellations AS cancel
                                    WHERE cancel.operation_id =
                                        authorization_grants.parent_operation_id
                                )
                          )
                      )
                    """,
                    (
                        claimed_at,
                        grant_id,
                        operation_id,
                        correlation_id,
                        request_fingerprint,
                        purpose,
                        principal,
                        action,
                        resource_fingerprint,
                        expires_at,
                        claimed_at,
                    ),
                )
                if cursor.rowcount != 1:
                    self._connection.rollback()
                    return False
                self._record_operation_event(
                    operation_id,
                    from_state="EXECUTING",
                    to_state="EXECUTING",
                    event_type="FINAL_DISPATCH_CLAIMED",
                    grant_id=grant_id,
                    created_at=claimed_at,
                )
                self._connection.commit()
                return True
            except Exception as error:
                self._rollback_preserving_error(error)
                raise

    def cancel_task_with_grant(
        self,
        grant: dict[str, Any],
        *,
        task_id: str,
    ) -> dict[str, str] | None:
        """Atomically consume a cancellation grant and persist task cancellation."""
        required = (
            "grantId",
            "operationId",
            "operationCorrelationId",
            "requestCorrelationId",
            "requestFingerprint",
            "purpose",
            "principal",
            "action",
            "resourceFingerprint",
            "expiresAt",
            "authorizationContext",
        )
        if any(key not in grant for key in required):
            raise ValueError("Task cancellation grant is incomplete.")
        context = grant["authorizationContext"]
        session_id = context.get("sessionId") if isinstance(context, dict) else None
        if (
            grant["purpose"] != "cancel_task"
            or grant["action"] != "cancel_task_before_dispatch"
            or not isinstance(task_id, str)
            or not task_id
            or not isinstance(session_id, str)
            or not session_id
        ):
            raise ValueError("Task cancellation grant scope is invalid.")

        now = datetime.now(UTC).isoformat(timespec="milliseconds")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._connection.execute(
                    """
                    SELECT 1 FROM task_cancellation_requests
                    WHERE correlation_id = ? OR grant_id = ?
                    """,
                    (grant["requestCorrelationId"], grant["grantId"]),
                ).fetchone()
                if replay is not None:
                    self._connection.rollback()
                    return None

                operation_row = self._connection.execute(
                    """
                    SELECT lifecycle_state, data
                    FROM operations
                    WHERE operation_id = ?
                    """,
                    (grant["operationId"],),
                ).fetchone()
                task_row = self._connection.execute(
                    """
                    SELECT task_data, lifecycle_state, attempt_id,
                           attempt_count, claimed_at, started_at,
                           updated_at, completed_at, cancellation_requested_at
                    FROM executive_tasks
                    WHERE task_id = ? AND removed_at IS NULL
                    """,
                    (task_id,),
                ).fetchone()
                if operation_row is None or task_row is None:
                    raise KeyError("The authorized task or parent operation is absent.")

                parent_state, raw_operation = operation_row
                operation = self._deserialize(raw_operation)
                if (
                    operation.get("lifecycleState") != parent_state
                    or operation.get("verb") != "execute_task"
                    or operation.get("operationId") != grant["operationId"]
                    or operation.get("correlationId")
                    != grant["operationCorrelationId"]
                    or operation.get("requestFingerprint")
                    != grant["requestFingerprint"]
                    or operation.get("lifecycleState") != "EXECUTING"
                    or task_id != f"task-{grant['operationId']}"
                ):
                    raise ValueError("Task cancellation does not match its active parent.")
                authorization = operation.get("authorization")
                original_context = (
                    authorization.get("authorizationContext")
                    if isinstance(authorization, dict)
                    else None
                )
                if (
                    not isinstance(authorization, dict)
                    or authorization.get("authorizer") != grant["principal"]
                    or not isinstance(original_context, dict)
                    or original_context.get("sessionId") != session_id
                ):
                    raise PermissionError(
                        "Task cancellation must use the parent operation's session."
                    )
                approval_claim = self._connection.execute(
                    """
                    SELECT 1 FROM authorization_grants
                    WHERE grant_id = ?
                      AND operation_id = ?
                      AND correlation_id = ?
                      AND request_fingerprint = ?
                      AND purpose = 'execute'
                      AND principal = ?
                      AND action = 'run_task'
                      AND resource_fingerprint = ?
                      AND dispatch_enabled = 1
                      AND dispatch_claimed_at IS NOT NULL
                    """,
                    (
                        authorization.get("grantId"),
                        grant["operationId"],
                        authorization.get("requestCorrelationId"),
                        grant["requestFingerprint"],
                        grant["principal"],
                        authorization.get("resourceFingerprint"),
                    ),
                ).fetchone()
                if approval_claim is None:
                    raise PermissionError(
                        "Task cancellation requires a claimed trusted parent grant."
                    )

                columns = {
                    row[1]
                    for row in self._connection.execute(
                        "PRAGMA table_info(executive_tasks)"
                    )
                }
                if not {
                    "lifecycle_state",
                    "task_data",
                    "parent_operation_id",
                    "cancellation_requested_at",
                    "removed_at",
                }.issubset(columns):
                    raise RuntimeError(
                        "Durable task cancellation requires the SQLite task schema."
                    )
                task_parent = self._connection.execute(
                    "SELECT parent_operation_id FROM executive_tasks "
                    "WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0]
                if task_parent != grant["operationId"]:
                    raise PermissionError(
                        "Task cancellation is outside the approved operation scope."
                    )

                try:
                    expires_at = datetime.fromisoformat(
                        grant["expiresAt"].replace("Z", "+00:00")
                    )
                except (AttributeError, ValueError) as error:
                    raise ValueError("Task cancellation grant expiry is invalid.") from error
                if expires_at.tzinfo is None or expires_at <= datetime.now(UTC):
                    raise PermissionError("Task cancellation grant has expired.")

                self._connection.execute(
                    """
                    INSERT INTO authorization_grants (
                        grant_id, operation_id, correlation_id,
                        request_fingerprint, purpose, principal, expires_at,
                        created_at, action, resource_fingerprint,
                        parent_operation_id, dispatch_enabled
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0)
                    """,
                    (
                        grant["grantId"],
                        grant["operationId"],
                        grant["requestCorrelationId"],
                        grant["requestFingerprint"],
                        grant["purpose"],
                        grant["principal"],
                        grant["expiresAt"],
                        grant["issuedAt"],
                        grant["action"],
                        grant["resourceFingerprint"],
                    ),
                )

                task_data, task_state = task_row[0], task_row[1]
                task_document = json.loads(task_data)
                if not isinstance(task_document, dict):
                    raise ValueError("Persisted task must be a JSON object.")
                if task_state not in {
                    "pending",
                    "ready",
                    "claimed",
                    "executing",
                    "uncertain",
                } or task_row[8] is not None:
                    raise ValueError(
                        f"Task in {task_state} state cannot accept cancellation."
                    )
                has_dispatch_claim = self._connection.execute(
                    """
                    SELECT 1 FROM authorization_grants
                    WHERE parent_operation_id = ?
                      AND dispatch_claimed_at IS NOT NULL
                    LIMIT 1
                    """,
                    (grant["operationId"],),
                ).fetchone() is not None
                potential_effect = (
                    task_state == "uncertain"
                    or (task_state == "executing" and has_dispatch_claim)
                )
                target_state = "uncertain" if potential_effect else "cancelled"
                disposition = (
                    "UNCERTAIN_REQUIRES_RECONCILIATION"
                    if potential_effect
                    else "CANCELLED_BEFORE_DISPATCH"
                )
                task_document.update(
                    {
                        "status": target_state,
                        "cancellation_requested_at": now,
                        "updated_at": now,
                        "completed_at": None,
                    }
                )
                task_update = self._connection.execute(
                    """
                    UPDATE executive_tasks
                    SET lifecycle_state = ?, task_data = ?,
                        cancellation_requested_at = ?, updated_at = ?,
                        completed_at = NULL,
                        failure_classification = ?
                    WHERE task_id = ? AND lifecycle_state = ?
                      AND cancellation_requested_at IS NULL
                      AND removed_at IS NULL
                    """,
                    (
                        target_state,
                        json.dumps(task_document, separators=(",", ":")),
                        now,
                        now,
                        (
                            "cancellation_requested_after_dispatch"
                            if potential_effect
                            else None
                        ),
                        task_id,
                        task_state,
                    ),
                )
                if task_update.rowcount != 1:
                    raise RuntimeError("Task cancellation lost its conditional update.")
                self._connection.execute(
                    """
                    INSERT INTO operation_cancellations (
                        operation_id, task_id, attempt_id, requested_at,
                        disposition
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        grant["operationId"],
                        task_id,
                        task_row[2],
                        now,
                        disposition,
                    ),
                )
                self._connection.execute(
                    """
                    INSERT INTO executive_task_events (
                        task_id, attempt_id, from_state, to_state,
                        failure_classification, event_type, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        task_row[2],
                        task_state,
                        target_state,
                        (
                            "cancellation_requested_after_dispatch"
                            if potential_effect
                            else None
                        ),
                        (
                            "CANCELLATION_REQUESTED"
                            if potential_effect
                            else "CANCELLATION_CONFIRMED"
                        ),
                        now,
                    ),
                )
                self._connection.execute(
                    """
                    INSERT INTO task_cancellation_requests (
                        correlation_id, operation_id, task_id, grant_id,
                        principal, session_id, request_fingerprint, action,
                        status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        grant["requestCorrelationId"],
                        grant["operationId"],
                        task_id,
                        grant["grantId"],
                        grant["principal"],
                        session_id,
                        grant["resourceFingerprint"],
                        grant["action"],
                        target_state.upper(),
                        now,
                    ),
                )
                self._record_operation_event(
                    grant["operationId"],
                    from_state=parent_state,
                    to_state=parent_state,
                    event_type="TASK_CANCELLATION_REQUESTED",
                    grant_id=grant["grantId"],
                    created_at=now,
                )
                self._connection.commit()
                return {
                    "taskId": task_id,
                    "operationId": grant["operationId"],
                    "status": target_state.upper(),
                    "disposition": disposition,
                    "correlationId": grant["requestCorrelationId"],
                }
            except Exception as error:
                self._rollback_preserving_error(error)
                raise

    def authorization_dispatch_claimed(
        self,
        *,
        grant_id: str,
        operation_id: str,
        correlation_id: str,
        request_fingerprint: str,
        principal: str,
        action: str,
        resource_fingerprint: str,
    ) -> bool:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT 1
                FROM authorization_grants
                WHERE grant_id = ?
                  AND operation_id = ?
                  AND correlation_id = ?
                  AND request_fingerprint = ?
                  AND principal = ?
                  AND action = ?
                  AND resource_fingerprint = ?
                  AND dispatch_claimed_at IS NOT NULL
                """,
                (
                    grant_id,
                    operation_id,
                    correlation_id,
                    request_fingerprint,
                    principal,
                    action,
                    resource_fingerprint,
                ),
            ).fetchone()
        return row is not None

    def get_reconciliation(self, correlation_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT operation_id, request_fingerprint, status, result
                FROM reconciliation_requests
                WHERE correlation_id = ?
                """,
                (correlation_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "operationId": row[0],
            "requestFingerprint": row[1],
            "status": row[2],
            "result": None if row[3] is None else json.loads(row[3]),
        }

    def begin_reconciliation(
        self,
        correlation_id: str,
        operation_id: str,
        request_fingerprint: str,
    ) -> dict[str, Any] | None:
        """Reserve a read-only reconciliation attempt, or return its prior result."""
        with self._lock:
            row = self._connection.execute(
                """
                SELECT operation_id, request_fingerprint, status, result
                FROM reconciliation_requests
                WHERE correlation_id = ?
                """,
                (correlation_id,),
            ).fetchone()
            if row is not None:
                if row[0] != operation_id or row[1] != request_fingerprint:
                    raise ValueError(
                        "Reconciliation correlationId was reused for a different request."
                    )
                return {
                    "operationId": row[0],
                    "requestFingerprint": row[1],
                    "status": row[2],
                    "result": None if row[3] is None else json.loads(row[3]),
                }
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO reconciliation_requests (
                    correlation_id, operation_id, request_fingerprint, status
                ) VALUES (?, ?, ?, 'PENDING')
                """,
                (correlation_id, operation_id, request_fingerprint),
            )
            if cursor.rowcount == 0:
                row = self._connection.execute(
                    """
                    SELECT operation_id, request_fingerprint, status, result
                    FROM reconciliation_requests
                    WHERE correlation_id = ?
                    """,
                    (correlation_id,),
                ).fetchone()
                if row is None:
                    self._connection.rollback()
                    raise RuntimeError(
                        "Concurrent reconciliation reservation disappeared."
                    )
                if row[0] != operation_id or row[1] != request_fingerprint:
                    self._connection.rollback()
                    raise ValueError(
                        "Reconciliation correlationId was reused for a different request."
                    )
                self._connection.commit()
                return {
                    "operationId": row[0],
                    "requestFingerprint": row[1],
                    "status": row[2],
                    "result": None if row[3] is None else json.loads(row[3]),
                }
            self._connection.commit()
        return None

    def finish_reconciliation(
        self,
        operation: dict[str, Any],
        correlation_id: str,
        request_fingerprint: str,
        result: dict[str, Any],
    ) -> None:
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                current = self._connection.execute(
                    """
                    SELECT lifecycle_state, data
                    FROM operations
                    WHERE operation_id = ?
                    """,
                    (operation["operationId"],),
                ).fetchone()
                if current is None:
                    raise KeyError(
                        f"Operation {operation['operationId']} is not persisted."
                    )
                current_state = current[0]
                if self._deserialize(current[1]).get("lifecycleState") != current_state:
                    raise RuntimeError(
                        "Persisted operation state and lifecycle data disagree."
                    )
                target_state = operation.get("lifecycleState")
                if target_state not in self._OPERATION_STATES:
                    raise ValueError("Unsupported operation lifecycle state.")
                if target_state != current_state and target_state not in (
                    self._OPERATION_TRANSITIONS.get(current_state, set())
                ):
                    raise ValueError(
                        f"Invalid operation transition: "
                        f"{current_state} -> {target_state}."
                    )
                cursor = self._connection.execute(
                    """
                    UPDATE operations
                    SET lifecycle_state = ?, data = ?, updated_at = ?
                    WHERE operation_id = ? AND lifecycle_state = ?
                    """,
                    (
                        operation["lifecycleState"],
                        self._serialize(operation),
                        operation["updatedAt"],
                        operation["operationId"],
                        current_state,
                    ),
                )
                if cursor.rowcount != 1:
                    raise KeyError(
                        f"Operation {operation['operationId']} is not persisted."
                    )
                request_cursor = self._connection.execute(
                    """
                    UPDATE reconciliation_requests
                    SET status = 'COMPLETE', result = ?
                    WHERE correlation_id = ? AND operation_id = ?
                      AND request_fingerprint = ? AND status = 'PENDING'
                    """,
                    (
                        self._serialize(result),
                        correlation_id,
                        operation["operationId"],
                        request_fingerprint,
                    ),
                )
                if request_cursor.rowcount != 1:
                    raise ValueError(
                        "Reconciliation request is not pending for this operation."
                    )
                if target_state != current_state:
                    self._record_operation_event(
                        operation["operationId"],
                        from_state=current_state,
                        to_state=target_state,
                        event_type="RECONCILIATION_COMPLETED",
                        created_at=operation["updatedAt"],
                    )
                self._connection.commit()
            except Exception as error:
                self._rollback_preserving_error(error)
                raise

    def get_by_id(self, operation_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT data FROM operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return None if row is None else self._deserialize(row[0])

    def get_by_correlation(
        self,
        correlation_id: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT data FROM operations WHERE correlation_id = ?",
                (correlation_id,),
            ).fetchone()
        return None if row is None else self._deserialize(row[0])

    def recent(self, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT data FROM operations
                ORDER BY updated_at DESC, created_at DESC, rowid DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._deserialize(row[0]) for row in rows]

    def recover_interrupted(self) -> int:
        return self._recover_interrupted(reconcile_tasks=False)

    def recover_gateway_state(self) -> int:
        """Recover gateway operations and any task store sharing this database."""
        return self._recover_interrupted(reconcile_tasks=None)

    def recover_interrupted_with_tasks(self) -> int:
        """Atomically reconcile gateway operations and their durable tasks."""
        if self.database_identity == ":memory:":
            raise RuntimeError(
                "Coordinated gateway recovery requires a durable SQLite database."
            )
        return self._recover_interrupted(reconcile_tasks=True)

    def _recover_interrupted(
        self,
        *,
        reconcile_tasks: bool | None,
    ) -> int:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                task_rows = []
                task_parents: set[str] = set()
                required_tables = {
                    row[0]
                    for row in self._connection.execute(
                        """
                        SELECT name FROM sqlite_master
                        WHERE type = 'table'
                          AND name IN (
                              'executive_tasks',
                              'executive_task_events'
                          )
                        """
                    )
                }
                if reconcile_tasks is None:
                    if not required_tables:
                        reconcile_tasks = False
                    elif required_tables == {
                        "executive_tasks",
                        "executive_task_events",
                    }:
                        reconcile_tasks = True
                    else:
                        raise RuntimeError(
                            "Gateway recovery found an incomplete durable task store."
                        )
                if reconcile_tasks:
                    if required_tables != {
                        "executive_tasks",
                        "executive_task_events",
                    }:
                        raise RuntimeError(
                            "Coordinated gateway recovery requires the durable "
                            "task tables in the shared database."
                        )

                    operations = self._connection.execute(
                        """
                        SELECT operation_id, lifecycle_state, data,
                               parent_operation_id
                        FROM operations
                        """
                    ).fetchall()
                    operation_rows = {}
                    for operation_id, state, raw_data, parent_id in operations:
                        operation = self._deserialize(raw_data)
                        if (
                            operation.get("operationId") != operation_id
                            or operation.get("lifecycleState") != state
                            or operation.get("parentOperationId") != parent_id
                        ):
                            raise RuntimeError(
                                "An operation has inconsistent persisted identity or state."
                            )
                        operation_rows[operation_id] = (
                            state,
                            operation,
                            parent_id,
                        )
                    task_rows = self._connection.execute(
                        """
                        SELECT queue_position, task_id, task_data,
                               lifecycle_state, attempt_id,
                               parent_operation_id, removed_at,
                               cancellation_requested_at,
                               failure_classification
                        FROM executive_tasks
                        WHERE removed_at IS NULL
                        """
                    ).fetchall()
                    active_task_states = {
                        "pending",
                        "ready",
                        "claimed",
                        "executing",
                        "active",
                        "uncertain",
                    }
                    for row in task_rows:
                        task_id, parent_id = row[1], row[5]
                        if not parent_id:
                            continue
                        parent_record = operation_rows.get(parent_id)
                        if parent_record is None:
                            raise RuntimeError(
                                "A durable task references a missing parent operation."
                            )
                        parent_state, parent, parent_link = parent_record
                        if (
                            parent.get("operationId") != parent_id
                            or parent_link is not None
                            or parent.get("parentOperationId") is not None
                            or parent.get("verb") != "execute_task"
                        ):
                            raise RuntimeError(
                                "A durable task has an inconsistent parent operation."
                            )
                        task_document = json.loads(row[2])
                        if (
                            not isinstance(task_document, dict)
                            or task_document.get("id") != task_id
                            or task_document.get("parent_operation_id")
                            not in (None, parent_id)
                        ):
                            raise RuntimeError(
                                "A durable task has inconsistent persisted identity."
                            )
                        document_state = task_document.get("status")
                        if document_state != row[3] and not (
                            row[3] == "uncertain" and document_state == "active"
                        ):
                            raise RuntimeError(
                                "A durable task has inconsistent persisted lifecycle state."
                            )
                        if parent_state not in {
                            "APPROVED",
                            "EXECUTING",
                            "UNCERTAIN",
                        } and row[3] in active_task_states:
                            raise RuntimeError(
                                "An active task is paired with a terminal or "
                                "unapproved parent operation."
                            )
                        if parent_state in {
                            "APPROVED",
                            "EXECUTING",
                            "UNCERTAIN",
                        }:
                            task_parents.add(parent_id)

                    for operation_id, state, raw_data, parent_id in operations:
                        if not parent_id:
                            continue
                        parent_record = operation_rows.get(parent_id)
                        if parent_record is None:
                            if state in {"SUGGESTED", "APPROVED", "EXECUTING"}:
                                raise RuntimeError(
                                    "An active child operation references a "
                                    "missing parent operation."
                                )
                            continue
                        child = self._deserialize(raw_data)
                        if (
                            child.get("operationId") != operation_id
                            or child.get("parentOperationId") != parent_id
                        ):
                            raise RuntimeError(
                                "A child operation has inconsistent persisted identity."
                            )

                    orphan_grant = self._connection.execute(
                        """
                        SELECT 1
                        FROM authorization_grants AS grant
                        LEFT JOIN operations AS child
                          ON child.operation_id = grant.operation_id
                        LEFT JOIN operations AS parent
                          ON parent.operation_id = grant.parent_operation_id
                        WHERE grant.parent_operation_id IS NOT NULL
                          AND (
                              child.operation_id IS NULL
                              OR parent.operation_id IS NULL
                              OR child.parent_operation_id
                                 != grant.parent_operation_id
                          )
                        LIMIT 1
                        """
                    ).fetchone()
                    if orphan_grant is not None:
                        raise RuntimeError(
                            "A child authorization grant has inconsistent operation links."
                        )

                rows = self._connection.execute(
                    """
                    SELECT data FROM operations
                    WHERE lifecycle_state IN ('APPROVED', 'EXECUTING')
                      AND parent_operation_id IS NULL
                    """
                ).fetchall()
                now = datetime.now(UTC).isoformat(timespec="milliseconds")
                for (raw_data,) in rows:
                    operation = self._deserialize(raw_data)
                    previous_state = operation.get("lifecycleState")
                    operation["lifecycleState"] = "UNCERTAIN"
                    operation["executionResult"] = {
                        "success": False,
                        "status": "UNCERTAIN",
                        "error": (
                            "The process stopped while this operation was in "
                            "progress. Its effect must be checked before retrying."
                        ),
                    }
                    operation["updatedAt"] = now
                    self._connection.execute(
                        """
                        UPDATE operations
                        SET lifecycle_state = ?, data = ?, updated_at = ?
                        WHERE operation_id = ? AND lifecycle_state = ?
                        """,
                        (
                            operation["lifecycleState"],
                            self._serialize(operation),
                            operation["updatedAt"],
                            operation["operationId"],
                            previous_state,
                        ),
                    )
                    self._record_operation_event(
                        operation["operationId"],
                        from_state=previous_state,
                        to_state="UNCERTAIN",
                        event_type="INTERRUPTION_RECOVERED",
                        created_at=now,
                    )

                parent_rows = self._connection.execute(
                    """
                    SELECT DISTINCT child.parent_operation_id
                    FROM operations AS child
                    JOIN operations AS parent
                      ON parent.operation_id = child.parent_operation_id
                    WHERE child.parent_operation_id IS NOT NULL
                      AND parent.lifecycle_state != 'EXECUTING'
                    """
                ).fetchall()
                inactive_parent_ids = [row[0] for row in parent_rows]
                if inactive_parent_ids:
                    placeholders = ",".join("?" for _ in inactive_parent_ids)
                    self._connection.execute(
                        f"""
                        UPDATE authorization_grants
                        SET dispatch_enabled = 0
                        WHERE parent_operation_id IN ({placeholders})
                        """,
                        inactive_parent_ids,
                    )
                    children = self._connection.execute(
                        f"""
                        SELECT data, lifecycle_state
                        FROM operations
                        WHERE parent_operation_id IN ({placeholders})
                          AND lifecycle_state IN (
                              'SUGGESTED', 'APPROVED', 'EXECUTING'
                          )
                        """,
                        inactive_parent_ids,
                    ).fetchall()
                    for raw_data, state in children:
                        child = self._deserialize(raw_data)
                        has_dispatch_claim = self._connection.execute(
                            """
                            SELECT 1 FROM authorization_grants
                            WHERE operation_id = ?
                              AND dispatch_claimed_at IS NOT NULL
                            LIMIT 1
                            """,
                            (child["operationId"],),
                        ).fetchone() is not None
                        interrupted = (
                            state == "EXECUTING" or has_dispatch_claim
                        )
                        child_state = "UNCERTAIN" if interrupted else "CANCELLED"
                        child["lifecycleState"] = child_state
                        child["executionResult"] = {
                            "success": False,
                            "status": child_state,
                            "error": (
                                "The process stopped during child dispatch; "
                                "reconcile its effect before retrying."
                                if interrupted
                                else "The parent task is no longer executing; "
                                "this child grant cannot be used."
                            ),
                        }
                        child["updatedAt"] = now
                        self._connection.execute(
                            """
                            UPDATE operations
                            SET lifecycle_state = ?, data = ?, updated_at = ?
                            WHERE operation_id = ? AND lifecycle_state = ?
                            """,
                            (
                                child_state,
                                self._serialize(child),
                                now,
                                child["operationId"],
                                state,
                            ),
                        )
                        self._record_operation_event(
                            child["operationId"],
                            from_state=state,
                            to_state=child_state,
                            event_type=(
                                "CHILD_INTERRUPTION_RECOVERED"
                                if interrupted
                                else "CHILD_CANCELLED_DURING_RECOVERY"
                            ),
                            created_at=now,
                        )

                recovered_tasks = 0
                if reconcile_tasks:
                    for row in task_rows:
                        (
                            queue_position,
                            task_id,
                            raw_task,
                            task_state,
                            attempt_id,
                            parent_id,
                            _removed_at,
                            cancellation_requested_at,
                            failure_classification,
                        ) = row
                        if not parent_id or parent_id not in task_parents:
                            continue
                        if task_state not in {
                            "pending",
                            "ready",
                            "claimed",
                            "executing",
                            "active",
                            "uncertain",
                        }:
                            continue

                        task_document = json.loads(raw_task)
                        child_states = self._connection.execute(
                            """
                            SELECT lifecycle_state
                            FROM operations
                            WHERE parent_operation_id = ?
                            """,
                            (parent_id,),
                        ).fetchall()
                        child_dispatch_claim = self._connection.execute(
                            """
                            SELECT 1
                            FROM authorization_grants AS grant
                            LEFT JOIN operations AS child
                              ON child.operation_id = grant.operation_id
                            WHERE grant.dispatch_claimed_at IS NOT NULL
                              AND (
                                  grant.parent_operation_id = ?
                                  OR child.parent_operation_id = ?
                              )
                            LIMIT 1
                            """,
                            (parent_id, parent_id),
                        ).fetchone() is not None
                        possible_execution = (
                            task_state in {"uncertain", "active"}
                            or child_dispatch_claim
                            or any(
                                state in {"EXECUTING", "UNCERTAIN"}
                                for (state,) in child_states
                            )
                            or bool(task_document.get("step_results"))
                        )
                        target_state = (
                            "uncertain" if possible_execution else "cancelled"
                        )
                        document_cancel_at = task_document.get(
                            "cancellation_requested_at"
                        )
                        if (
                            cancellation_requested_at is not None
                            and document_cancel_at is not None
                            and datetime.fromisoformat(
                                cancellation_requested_at.replace(
                                    "Z",
                                    "+00:00",
                                )
                            )
                            != datetime.fromisoformat(
                                document_cancel_at.replace("Z", "+00:00")
                            )
                        ):
                            raise RuntimeError(
                                "A durable task has inconsistent cancellation evidence."
                            )
                        existing_cancel_at = (
                            cancellation_requested_at or document_cancel_at
                        )
                        if target_state == "cancelled" and not existing_cancel_at:
                            existing_cancel_at = now

                        data_changed = (
                            task_document.get("status") != target_state
                            or task_document.get("parent_operation_id")
                            != parent_id
                            or task_document.get("cancellation_requested_at")
                            != existing_cancel_at
                        )
                        state_changed = task_state != target_state
                        if not data_changed and not state_changed:
                            continue

                        task_document["status"] = target_state
                        task_document["parent_operation_id"] = parent_id
                        task_document["updated_at"] = now
                        task_document["cancellation_requested_at"] = (
                            existing_cancel_at
                        )
                        new_failure = (
                            "process_interrupted_after_dispatch_boundary"
                            if target_state == "uncertain"
                            else failure_classification
                        )
                        cursor = self._connection.execute(
                            """
                            UPDATE executive_tasks
                            SET task_data = ?, lifecycle_state = ?,
                                updated_at = ?, cancellation_requested_at = ?,
                                failure_classification = ?
                            WHERE queue_position = ? AND task_id = ?
                              AND removed_at IS NULL
                            """,
                            (
                                json.dumps(
                                    task_document,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ),
                                target_state,
                                now,
                                existing_cancel_at,
                                new_failure,
                                queue_position,
                                task_id,
                            ),
                        )
                        if cursor.rowcount != 1:
                            raise RuntimeError(
                                "A task changed during coordinated recovery."
                            )
                        if state_changed:
                            self._connection.execute(
                                """
                                INSERT INTO executive_task_events (
                                    task_id, attempt_id, from_state, to_state,
                                    failure_classification, event_type,
                                    created_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    task_id,
                                    attempt_id,
                                    task_state,
                                    target_state,
                                    new_failure,
                                    (
                                        "INTERRUPTION_RECOVERED"
                                        if target_state == "uncertain"
                                        else "RECOVERY_CANCELLED_BEFORE_DISPATCH"
                                    ),
                                    now,
                                ),
                            )
                            recovered_tasks += 1

                self._connection.commit()
                return len(rows) + recovered_tasks
            except Exception as error:
                self._rollback_preserving_error(error)
                raise

    def close(self) -> None:
        with self._lock:
            self._connection.close()
