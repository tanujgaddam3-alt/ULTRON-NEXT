from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import fcntl
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from ultron.domain.content import (
    ContentPackage,
    ContentPackageVersion,
    ContentPlatform,
    ContentPublicationRecord,
    ContentLifecycleEvent,
    ContentLifecycleRecord,
    ContentProcessingEvent,
    ContentProcessingJob,
    ContentProcessingOutput,
    ContentClipRender,
    ContentClipRenderEvent,
    ContentPrivateRenderOperation,
    ContentPrivateRenderOperationEvent,
    PrivateRenderOperationState,
    ContentCreativeEditPlan,
    ContentCreativeEditPlanRecord,
    ContentCreativeEditPlanV2,
    ContentEditProject,
    ContentEditPlanSegment,
    ContentEditSegment,
    PublishingApprovalState,
    PublishingAttempt,
    PublishingAttemptCode,
    PublishingAttemptEvent,
    PublishingAttemptIdentity,
    PublishingAttemptSecretReference,
    PublishingAttemptSecretState,
    PublishingAttemptState,
    PublishingJob,
    PublishingJobEvent,
    PublishingJobState,
    PublishingProvider,
    PublishingReleaseApproval,
    PublishingVisibility,
    OpaqueSecretReference,
    SecretGeneration,
    UploadSecretBinding,
    UploadSecretPurpose,
    UploadSessionSecretHandle,
    ProcessingJobState,
    ContentTargetContext,
    RightsUse,
    AgreementStatus,
    EvidenceDecision,
    GrantorAuthority,
    GrantorAuthorityReview,
    GrantorIdentity,
    GrantorIdentityReview,
    OwnershipClaim,
    OwnershipClaimState,
    PublicationState,
    ReviewApproval,
    ReviewState,
    RightsAction,
    RightsEvidence,
    RightsEvidenceReview,
    RightsEvaluation,
    RightsGrant,
    RightsTerms,
    VerificationStatus,
    RightsAgreement,
    RightsAgreementEvent,
    RightsAgreementReview,
    SourceAsset,
    SourceAccessPermissionState,
    SourceHashStatus,
    SourceReferenceRecord,
)
from ultron.domain.content_intelligence import (
    HighlightCandidate,
    HighlightConfig,
    TranscriptRecord,
    TranscriptionConfig,
)
from ultron.domain.content_visual_analysis import (
    ContentVisualAnalysis,
    ContentVisualAnalysisEvent,
    VisualAnalysisRequest,
    VisualAnalysisRetentionPlan,
    VisualAnalysisRetentionPolicy,
    VisualAnalysisState,
)
from ultron.domain.content_visual_intelligence import VisualVideoAnalysis
from ultron.security.identity.authorization import (
    AuthorizationContext,
    ValidatedAuthorization,
    resource_fingerprint,
)
from ultron.security.identity.authenticator import WebSocketAuthenticator

from .policy import GrantRecord


class IdempotencyConflictError(ValueError):
    """An idempotency key was reused with a different request."""


class PublishingAttemptConflictError(ValueError):
    """A durable attempt command conflicts with current state."""


class VisualAnalysisConflictError(ValueError):
    """A visual-analysis idempotency key conflicts with an existing request."""


class ContentSchemaVersionMismatch(RuntimeError):
    """An existing CRE database has a valid but unsupported schema version."""

    def __init__(self, actual_version: int, expected_version: int) -> None:
        self.actual_version = actual_version
        self.expected_version = expected_version
        super().__init__(
            "Existing CRE database schema does not match the current runtime version."
        )


_PUBLISHING_ATTEMPT_TRANSITIONS = {
    PublishingAttemptState.CREATED: {
        PublishingAttemptState.INITIATION_INTENT,
        PublishingAttemptState.BLOCKED,
    },
    PublishingAttemptState.INITIATION_INTENT: {
        PublishingAttemptState.SESSION_READY,
        PublishingAttemptState.INITIATION_UNCERTAIN,
        PublishingAttemptState.BLOCKED,
        PublishingAttemptState.FAILED,
    },
    PublishingAttemptState.SESSION_READY: {
        PublishingAttemptState.UPLOADING,
        PublishingAttemptState.SESSION_EXPIRED,
        PublishingAttemptState.REMOTE_UPLOADED,
        PublishingAttemptState.RECONCILIATION_REQUIRED,
        PublishingAttemptState.BLOCKED,
        PublishingAttemptState.OPERATOR_ACTION_REQUIRED,
        PublishingAttemptState.FAILED,
    },
    PublishingAttemptState.UPLOADING: {
        PublishingAttemptState.SESSION_EXPIRED,
        PublishingAttemptState.REMOTE_UPLOADED,
        PublishingAttemptState.RECONCILIATION_REQUIRED,
        PublishingAttemptState.BLOCKED,
        PublishingAttemptState.OPERATOR_ACTION_REQUIRED,
        PublishingAttemptState.FAILED,
    },
    PublishingAttemptState.INITIATION_UNCERTAIN: {
        PublishingAttemptState.RECONCILIATION_REQUIRED,
        PublishingAttemptState.REMOTE_UPLOADED,
        PublishingAttemptState.SESSION_EXPIRED,
        PublishingAttemptState.OPERATOR_ACTION_REQUIRED,
        PublishingAttemptState.BLOCKED,
        PublishingAttemptState.FAILED,
    },
    PublishingAttemptState.RECONCILIATION_REQUIRED: {
        PublishingAttemptState.SESSION_READY,
        PublishingAttemptState.UPLOADING,
        PublishingAttemptState.REMOTE_UPLOADED,
        PublishingAttemptState.SESSION_EXPIRED,
        PublishingAttemptState.OPERATOR_ACTION_REQUIRED,
        PublishingAttemptState.BLOCKED,
        PublishingAttemptState.FAILED,
    },
    PublishingAttemptState.REMOTE_UPLOADED: {
        PublishingAttemptState.PROCESSING,
        PublishingAttemptState.OPERATOR_ACTION_REQUIRED,
        PublishingAttemptState.RECONCILIATION_REQUIRED,
        PublishingAttemptState.BLOCKED,
        PublishingAttemptState.FAILED,
    },
    PublishingAttemptState.PROCESSING: {
        PublishingAttemptState.PUBLISHED,
        PublishingAttemptState.AVAILABLE_PRIVATE,
        PublishingAttemptState.OPERATOR_ACTION_REQUIRED,
        PublishingAttemptState.RECONCILIATION_REQUIRED,
        PublishingAttemptState.BLOCKED,
        PublishingAttemptState.FAILED,
    },
    PublishingAttemptState.SESSION_EXPIRED: {
        PublishingAttemptState.RECONCILIATION_REQUIRED,
        PublishingAttemptState.OPERATOR_ACTION_REQUIRED,
        PublishingAttemptState.FAILED,
    },
    PublishingAttemptState.BLOCKED: {
        PublishingAttemptState.RECONCILIATION_REQUIRED,
        PublishingAttemptState.OPERATOR_ACTION_REQUIRED,
    },
    PublishingAttemptState.OPERATOR_ACTION_REQUIRED: {
        PublishingAttemptState.RECONCILIATION_REQUIRED,
        PublishingAttemptState.SESSION_READY,
        PublishingAttemptState.REMOTE_UPLOADED,
        PublishingAttemptState.SESSION_EXPIRED,
        PublishingAttemptState.BLOCKED,
        PublishingAttemptState.FAILED,
    },
}
_PUBLISHING_ATTEMPT_CONFIRMED_FAILURE_CODES = {
    PublishingAttemptCode.PROVIDER_REJECTED,
    PublishingAttemptCode.SESSION_EXPIRED_CONFIRMED,
    PublishingAttemptCode.PROCESSING_FAILED_CONFIRMED,
}
_PUBLISHING_ATTEMPT_COMMAND_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_PUBLISHING_ATTEMPT_ACTOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")


def _verification_status(decision: str | None) -> VerificationStatus:
    if decision is None:
        return VerificationStatus.PENDING_REVIEW
    if decision == EvidenceDecision.VERIFIED.value:
        return VerificationStatus.VERIFIED
    return VerificationStatus.REJECTED


class ContentStore:
    """Durable, isolated SQLite records for content provenance and rights."""

    # SCHEMA_VERSION is the base content layer. Runtime integrations requiring
    # private-render support must check RUNTIME_SCHEMA_VERSION instead.
    SCHEMA_VERSION = 7
    PUBLISHING_ATTEMPT_SCHEMA_VERSION = 9
    PRIVATE_RENDER_OPERATION_SCHEMA_VERSION = 10
    RUNTIME_SCHEMA_VERSION = PRIVATE_RENDER_OPERATION_SCHEMA_VERSION
    VISUAL_ANALYSIS_SCHEMA_VERSION = 2
    _CONTENT_SCHEMA_META_DDL = """
        CREATE TABLE content_schema_meta (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            schema_version INTEGER NOT NULL
        )
    """

    def __init__(
        self,
        database: str | Path,
        *,
        recover_interrupted_jobs: bool = True,
    ) -> None:
        database_path = str(database)
        if database_path == ":memory:":
            self.database_identity = ":memory:"
        else:
            path = Path(database_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                raise ValueError("Content database path must not be a symbolic link.")
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
            os.chmod(path, 0o600)
            self.database_identity = str(path.resolve())

        self._lock = threading.RLock()
        self._visual_analysis_recovery_complete = False
        self._visual_analysis_worker_lock_fd: int | None = None
        self._visual_analysis_worker_token: object | None = None
        self._visual_analysis_active_claims: set[tuple[str, str]] = set()
        self._transaction_savepoint_counter = 0
        self._authorization_authorities: list[WebSocketAuthenticator] = []
        self._authorization_authorities_sealed = False
        self._connection = sqlite3.connect(
            database_path,
            timeout=10.0,
            check_same_thread=False,
            isolation_level=None,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._connection.execute("PRAGMA busy_timeout = 10000")
        self._initialize_schema()
        if recover_interrupted_jobs:
            self._recover_interrupted_processing_jobs()

    def _register_authorization_authority(
        self,
        authority: WebSocketAuthenticator,
    ) -> None:
        if type(authority) is not WebSocketAuthenticator:
            raise TypeError("A configured WebSocketAuthenticator is required.")
        with self._lock:
            if authority in self._authorization_authorities:
                return
            if self._authorization_authorities_sealed:
                if not any(
                    authority.shares_configured_credential(configured)
                    for configured in self._authorization_authorities
                ):
                    raise RuntimeError(
                        "Content-store authorization authority is not trusted."
                    )
            self._authorization_authorities.append(authority)

    def _seal_authorization_authorities(self) -> None:
        with self._lock:
            if not self._authorization_authorities:
                raise RuntimeError("No content-store authorization authority is configured.")
            self._authorization_authorities_sealed = True

    @property
    def private_render_operations_enabled(self) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
            ).fetchone()
        return (
            row is not None
            and row["schema_version"]
            == self.PRIVATE_RENDER_OPERATION_SCHEMA_VERSION
        )

    def _has_trusted_authority(self, authority: object) -> bool:
        with self._lock:
            return any(authority is configured for configured in self._authorization_authorities)

    def _initialize_schema(self) -> None:
        with self._transaction() as connection:
            meta_exists = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'content_schema_meta'
                """
            ).fetchone()
            if meta_exists is None:
                existing_tables = connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                    LIMIT 1
                    """
                ).fetchone()
                if existing_tables is not None:
                    raise RuntimeError(
                        "Content schema metadata is missing from a non-empty database."
                    )
                connection.execute(
                    self._CONTENT_SCHEMA_META_DDL
                )
            else:
                meta_columns = {
                    item["name"]
                    for item in connection.execute(
                        "PRAGMA table_info(content_schema_meta)"
                    )
                }
                if meta_columns != {"singleton", "schema_version"}:
                    raise RuntimeError("Content schema metadata is malformed.")
            row = connection.execute(
                "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
            ).fetchone()
            if meta_exists is not None and row is None:
                raise RuntimeError("Content schema metadata is missing its version row.")
            if row is not None and type(row["schema_version"]) is not int:
                raise RuntimeError("Content schema version metadata is malformed.")
            if (
                row is not None
                and row["schema_version"] > self.PRIVATE_RENDER_OPERATION_SCHEMA_VERSION
            ):
                raise RuntimeError(
                    "Content database schema is newer than this application supports."
                )
            self._create_tables(connection)
            self._ensure_clip_quality_columns(connection)
            if row is None:
                connection.execute(
                    "INSERT INTO content_schema_meta(singleton, schema_version) "
                    "VALUES (1, ?)",
                    (self.SCHEMA_VERSION,),
                )
            elif row["schema_version"] < self.SCHEMA_VERSION:
                if row["schema_version"] not in {1, 2, 3, 4, 5, 6}:
                    raise RuntimeError(
                        "Content database requires an explicit schema migration."
                    )
                self._migrate_schema(connection, row["schema_version"])
                connection.execute(
                    "UPDATE content_schema_meta SET schema_version = ? "
                    "WHERE singleton = 1",
                    (self.SCHEMA_VERSION,),
                )
            elif row["schema_version"] == self.PUBLISHING_ATTEMPT_SCHEMA_VERSION:
                self._verify_publishing_attempt_schema(connection)
            elif row["schema_version"] == self.PRIVATE_RENDER_OPERATION_SCHEMA_VERSION:
                self._verify_publishing_attempt_schema(connection)
                self._verify_private_render_operation_schema(connection)
            integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError("Content database failed SQLite integrity check.")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise RuntimeError("Content database contains broken foreign keys.")

    @staticmethod
    def _ensure_clip_quality_columns(connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(content_clip_renders)")
        }
        definitions = {
            "quality_output_sha256": "TEXT",
            "quality_status": "TEXT",
            "quality_black_frames": "INTEGER",
            "quality_frozen_frames": "INTEGER",
            "quality_black_ranges_json": "TEXT",
            "quality_frozen_ranges_json": "TEXT",
            "quality_revision_count": "INTEGER",
        }
        for column, definition in definitions.items():
            if column not in columns:
                connection.execute(
                    f"ALTER TABLE content_clip_renders ADD COLUMN {column} {definition}"
                )

    @staticmethod
    def _migrate_schema(connection: sqlite3.Connection, old_version: int) -> None:
        if old_version < 3:
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(rights_grants)")
            }
            if "agreement_id" not in columns:
                connection.execute(
                    "ALTER TABLE rights_grants ADD COLUMN agreement_id TEXT"
                )
            if "agreement_version" not in columns:
                connection.execute(
                    "ALTER TABLE rights_grants ADD COLUMN agreement_version INTEGER"
                )
        if old_version < 4:
            pass
        if old_version < 5:
            pass

    def migrate_publishing_attempt_schema(self) -> int:
        """Explicitly migrate a v7/v8 database; ordinary startup never invokes this."""
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
            ).fetchone()
            if row is None:
                raise RuntimeError("Content schema metadata is missing.")
            version = row["schema_version"]
            if version == self.PUBLISHING_ATTEMPT_SCHEMA_VERSION:
                self._verify_publishing_attempt_schema(connection)
                return version
            if version == self.SCHEMA_VERSION:
                self._create_publishing_attempt_tables(connection)
                connection.execute(
                    "UPDATE content_schema_meta SET schema_version = 8 "
                    "WHERE singleton = 1 AND schema_version = ?",
                    (self.SCHEMA_VERSION,),
                )
                version = 8
            if version == 8:
                self._upgrade_publishing_attempt_schema_v9(connection)
                connection.execute(
                    "UPDATE content_schema_meta SET schema_version = ? "
                    "WHERE singleton = 1 AND schema_version = 8",
                    (self.PUBLISHING_ATTEMPT_SCHEMA_VERSION,),
                )
                self._verify_publishing_attempt_schema(connection)
                return self.PUBLISHING_ATTEMPT_SCHEMA_VERSION
            else:
                raise RuntimeError(
                    "Publishing attempt migration requires an exact schema-v7 or v8 database."
                )

    def migrate_private_render_operation_schema(self) -> int:
        """Explicitly migrate a backed-up schema-v9 database to v10."""
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
            ).fetchone()
            if row is None:
                raise RuntimeError("Content schema metadata is missing.")
            version = row["schema_version"]
            if version == self.PRIVATE_RENDER_OPERATION_SCHEMA_VERSION:
                self._verify_publishing_attempt_schema(connection)
                self._verify_private_render_operation_schema(connection)
                return version
            if version != self.PUBLISHING_ATTEMPT_SCHEMA_VERSION:
                raise RuntimeError(
                    "Private render operation migration requires an exact schema-v9 database."
                )
            self._verify_publishing_attempt_schema(connection)
            self._create_private_render_operation_tables(connection)
            connection.execute(
                "UPDATE content_schema_meta SET schema_version = ? "
                "WHERE singleton = 1 AND schema_version = ?",
                (
                    self.PRIVATE_RENDER_OPERATION_SCHEMA_VERSION,
                    self.PUBLISHING_ATTEMPT_SCHEMA_VERSION,
                ),
            )
            self._verify_private_render_operation_schema(connection)
            return self.PRIVATE_RENDER_OPERATION_SCHEMA_VERSION

    def migrate_visual_analysis_schema(self) -> int:
        """Explicitly add owner-scoped visual-analysis tables to a v10 database."""
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
            ).fetchone()
            if (
                row is None
                or row["schema_version"] != self.PRIVATE_RENDER_OPERATION_SCHEMA_VERSION
            ):
                raise RuntimeError(
                    "Visual analysis migration requires an exact schema-v10 database."
                )
            meta_exists = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'content_visual_analysis_schema_meta'
                """
            ).fetchone()
            visual_tables_exist = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name IN (
                    'content_visual_analyses', 'content_visual_analysis_events'
                )
                LIMIT 1
                """
            ).fetchone()
            if meta_exists is None:
                if visual_tables_exist is not None:
                    raise RuntimeError(
                        "Visual analysis schema metadata is missing for existing tables."
                    )
                self._create_visual_analysis_tables(connection)
                connection.execute(
                    """
                    INSERT INTO content_visual_analysis_schema_meta(singleton, schema_version)
                    VALUES (1, ?)
                    """,
                    (self.VISUAL_ANALYSIS_SCHEMA_VERSION,),
                )
            else:
                meta_columns = {
                    item["name"]
                    for item in connection.execute(
                        "PRAGMA table_info(content_visual_analysis_schema_meta)"
                    )
                }
                if meta_columns != {"singleton", "schema_version"}:
                    raise RuntimeError(
                        "Visual analysis schema metadata is malformed."
                    )
                meta = connection.execute(
                    """
                    SELECT schema_version FROM content_visual_analysis_schema_meta
                    WHERE singleton = 1
                    """
                ).fetchone()
                if meta is None or type(meta["schema_version"]) is not int:
                    raise RuntimeError(
                        "Visual analysis schema metadata is inconsistent."
                    )
                version = meta["schema_version"]
                if version > self.VISUAL_ANALYSIS_SCHEMA_VERSION:
                    raise RuntimeError(
                        "Visual analysis schema is newer than this application supports."
                    )
                if version < 1:
                    raise RuntimeError(
                        "Visual analysis schema version is unsupported."
                    )
                if version == 1:
                    self._migrate_visual_analysis_v1_to_v2(connection)
            self._verify_visual_analysis_schema(connection)
            return self.VISUAL_ANALYSIS_SCHEMA_VERSION

    @staticmethod
    def _create_visual_analysis_tables(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS content_visual_analysis_schema_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                schema_version INTEGER NOT NULL CHECK (schema_version > 0)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS content_visual_analyses (
                analysis_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                project_id TEXT NOT NULL
                    REFERENCES content_edit_projects(project_id),
                asset_id TEXT NOT NULL REFERENCES content_assets(asset_id),
                asset_sha256 TEXT NOT NULL CHECK (length(asset_sha256) = 64),
                idempotency_key TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL
                    CHECK (length(request_fingerprint) = 64),
                cache_fingerprint TEXT NOT NULL
                    CHECK (length(cache_fingerprint) = 64),
                cache_source_analysis_id TEXT,
                request_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK (
                    state IN (
                        'queued', 'analyzing', 'completed', 'failed',
                        'reconciliation_required'
                    )
                ),
                result_json TEXT,
                failure_code TEXT,
                failure_detail TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                UNIQUE(owner_id, idempotency_key),
                CHECK (
                    (state = 'completed' AND result_json IS NOT NULL
                        AND completed_at IS NOT NULL)
                    OR state != 'completed'
                ),
                CHECK (
                    (state = 'failed' AND failure_code IS NOT NULL
                        AND failure_detail IS NOT NULL)
                    OR state != 'failed'
                )
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
                content_visual_analyses_owner_cache_idx
            ON content_visual_analyses(
                owner_id, request_fingerprint, state, created_at
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
                content_visual_analyses_owner_cache_lookup_idx
            ON content_visual_analyses(
                owner_id, cache_fingerprint, state, created_at
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS content_visual_analysis_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_id TEXT NOT NULL
                    REFERENCES content_visual_analyses(analysis_id),
                owner_id TEXT NOT NULL,
                from_state TEXT CHECK (
                    from_state IS NULL OR from_state IN (
                        'queued', 'analyzing', 'completed', 'failed',
                        'reconciliation_required'
                    )
                ),
                to_state TEXT NOT NULL CHECK (
                    to_state IN (
                        'queued', 'analyzing', 'completed', 'failed',
                        'reconciliation_required'
                    )
                ),
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        for definition in ContentStore._visual_analysis_trigger_definitions().values():
            connection.execute(definition)

    @staticmethod
    def _visual_analysis_trigger_definitions() -> dict[str, str]:
        return {
            "content_visual_analysis_events_no_update": """
                CREATE TRIGGER IF NOT EXISTS
                    content_visual_analysis_events_no_update
                BEFORE UPDATE ON content_visual_analysis_events
                BEGIN
                    SELECT RAISE(ABORT, 'visual analysis events are append-only');
                END
            """,
            "content_visual_analysis_events_no_delete": """
                CREATE TRIGGER IF NOT EXISTS
                    content_visual_analysis_events_no_delete
                BEFORE DELETE ON content_visual_analysis_events
                BEGIN
                    SELECT RAISE(ABORT, 'visual analysis events are append-only');
                END
            """,
            "content_visual_analyses_identity_immutable": """
                CREATE TRIGGER IF NOT EXISTS
                    content_visual_analyses_identity_immutable
                BEFORE UPDATE OF owner_id, project_id, asset_id, asset_sha256,
                    idempotency_key, request_fingerprint, cache_fingerprint,
                    cache_source_analysis_id, request_json
                ON content_visual_analyses
                BEGIN
                    SELECT RAISE(ABORT, 'visual analysis request identity is immutable');
                END
            """,
            "content_visual_analyses_fingerprints_valid": """
                CREATE TRIGGER IF NOT EXISTS
                    content_visual_analyses_fingerprints_valid
                BEFORE INSERT ON content_visual_analyses
                WHEN length(NEW.request_fingerprint) != 64
                  OR NEW.request_fingerprint GLOB '*[^a-f0-9]*'
                  OR NEW.cache_fingerprint IS NULL
                  OR length(NEW.cache_fingerprint) != 64
                  OR NEW.cache_fingerprint GLOB '*[^a-f0-9]*'
                BEGIN
                    SELECT RAISE(ABORT, 'invalid visual analysis fingerprint');
                END
            """,
            "content_visual_analyses_cache_source_valid": """
                CREATE TRIGGER IF NOT EXISTS
                    content_visual_analyses_cache_source_valid
                BEFORE INSERT ON content_visual_analyses
                WHEN NEW.cache_source_analysis_id IS NOT NULL
                BEGIN
                    SELECT CASE WHEN NOT EXISTS (
                        SELECT 1 FROM content_visual_analyses source
                        WHERE source.analysis_id = NEW.cache_source_analysis_id
                          AND source.owner_id = NEW.owner_id
                          AND source.cache_fingerprint = NEW.cache_fingerprint
                          AND source.state = 'completed'
                    ) THEN RAISE(ABORT, 'invalid visual analysis cache source') END;
                END
            """,
        }

    @staticmethod
    def _migrate_visual_analysis_v1_to_v2(
        connection: sqlite3.Connection,
    ) -> None:
        expected_v1 = {
            "analysis_id", "owner_id", "project_id", "asset_id", "asset_sha256",
            "idempotency_key", "request_fingerprint", "request_json", "state",
            "result_json", "failure_code", "failure_detail", "created_at",
            "updated_at", "completed_at",
        }
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(content_visual_analyses)"
            )
        }
        if not expected_v1.issubset(columns):
            raise RuntimeError(
                "Visual analysis v1 table is incomplete; migration was not applied."
            )
        if "cache_fingerprint" not in columns:
            connection.execute(
                "ALTER TABLE content_visual_analyses ADD COLUMN cache_fingerprint TEXT"
            )
        if "cache_source_analysis_id" not in columns:
            connection.execute(
                "ALTER TABLE content_visual_analyses "
                "ADD COLUMN cache_source_analysis_id TEXT"
            )
        rows = connection.execute(
            "SELECT analysis_id, request_json FROM content_visual_analyses"
        ).fetchall()
        for row in rows:
            request = VisualAnalysisRequest.model_validate_json(row["request_json"])
            connection.execute(
                """
                UPDATE content_visual_analyses
                SET request_fingerprint = ?, cache_fingerprint = ?
                WHERE analysis_id = ?
                """,
                (
                    request.request_fingerprint(),
                    request.cache_fingerprint(),
                    row["analysis_id"],
                ),
            )
        connection.execute(
            """
            CREATE TABLE content_visual_analysis_schema_meta_v2 (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                schema_version INTEGER NOT NULL CHECK (schema_version > 0)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO content_visual_analysis_schema_meta_v2(singleton, schema_version)
            VALUES (1, ?)
            """,
            (ContentStore.VISUAL_ANALYSIS_SCHEMA_VERSION,),
        )
        connection.execute("DROP TABLE content_visual_analysis_schema_meta")
        connection.execute(
            "ALTER TABLE content_visual_analysis_schema_meta_v2 "
            "RENAME TO content_visual_analysis_schema_meta"
        )
        ContentStore._create_visual_analysis_tables(connection)

    @staticmethod
    def _verify_visual_analysis_schema(connection: sqlite3.Connection) -> None:
        meta = connection.execute(
            """
            SELECT schema_version FROM content_visual_analysis_schema_meta
            WHERE singleton = 1
            """
        ).fetchone()
        if (
            meta is None
            or type(meta["schema_version"]) is not int
            or meta["schema_version"] != ContentStore.VISUAL_ANALYSIS_SCHEMA_VERSION
        ):
            raise RuntimeError("Visual analysis schema metadata is inconsistent.")
        expected = {
            "content_visual_analyses": {
                "analysis_id",
                "owner_id",
                "project_id",
                "asset_id",
                "asset_sha256",
                "idempotency_key",
                "request_fingerprint",
                "cache_fingerprint",
                "cache_source_analysis_id",
                "request_json",
                "state",
                "result_json",
                "failure_code",
                "failure_detail",
                "created_at",
                "updated_at",
                "completed_at",
            },
            "content_visual_analysis_events": {
                "sequence",
                "analysis_id",
                "owner_id",
                "from_state",
                "to_state",
                "actor",
                "reason",
                "created_at",
            },
        }
        for table_name, expected_columns in expected.items():
            columns = {
                row["name"]
                for row in connection.execute(f"PRAGMA table_info({table_name})")
            }
            if not expected_columns.issubset(columns):
                raise RuntimeError(
                    f"Visual analysis table {table_name} is incomplete."
                )
        required_objects = {
            ("index", "content_visual_analyses_owner_cache_lookup_idx"),
            ("trigger", "content_visual_analysis_events_no_update"),
            ("trigger", "content_visual_analysis_events_no_delete"),
            ("trigger", "content_visual_analyses_identity_immutable"),
            ("trigger", "content_visual_analyses_fingerprints_valid"),
            ("trigger", "content_visual_analyses_cache_source_valid"),
        }
        actual_objects = {
            (row["type"], row["name"])
            for row in connection.execute(
                """
                SELECT type, name FROM sqlite_master
                WHERE name IN (
                    'content_visual_analyses_owner_cache_lookup_idx',
                    'content_visual_analysis_events_no_update',
                    'content_visual_analysis_events_no_delete',
                    'content_visual_analyses_identity_immutable',
                    'content_visual_analyses_fingerprints_valid',
                    'content_visual_analyses_cache_source_valid'
                )
                """
            )
        }
        if not required_objects.issubset(actual_objects):
            raise RuntimeError("Visual analysis schema objects are incomplete.")
        expected_triggers = ContentStore._visual_analysis_trigger_definitions()
        actual_triggers = {
            row["name"]: row["sql"]
            for row in connection.execute(
                """
                SELECT name, sql FROM sqlite_master
                WHERE type = 'trigger' AND name IN (
                    'content_visual_analysis_events_no_update',
                    'content_visual_analysis_events_no_delete',
                    'content_visual_analyses_identity_immutable',
                    'content_visual_analyses_fingerprints_valid',
                    'content_visual_analyses_cache_source_valid'
                )
                """
            )
        }
        for name, expected_sql in expected_triggers.items():
            actual_sql = actual_triggers.get(name)
            if (
                actual_sql is None
                or ContentStore._normalize_schema_sql(actual_sql)
                != ContentStore._normalize_schema_sql(expected_sql)
            ):
                raise RuntimeError(
                    f"Visual analysis trigger {name} has an invalid definition."
                )

    @staticmethod
    def _normalize_schema_sql(sql: str) -> str:
        normalized = " ".join(sql.split()).casefold()
        return normalized.replace("create trigger if not exists ", "create trigger ")

    @classmethod
    def verify_runtime_schema_metadata(
        cls,
        connection: sqlite3.Connection,
    ) -> None:
        """Validate v10 metadata without changing the opened database."""
        definition = connection.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'table' AND name = 'content_schema_meta'
            """
        ).fetchone()
        if (
            definition is None
            or cls._normalize_schema_sql(definition["sql"])
            != cls._normalize_schema_sql(cls._CONTENT_SCHEMA_META_DDL)
        ):
            raise RuntimeError("Content schema metadata definition is malformed.")

        columns = [
            (row["name"], row["type"].upper(), row["notnull"], row["pk"])
            for row in connection.execute("PRAGMA table_info(content_schema_meta)")
        ]
        if columns != [
            ("singleton", "INTEGER", 0, 1),
            ("schema_version", "INTEGER", 1, 0),
        ]:
            raise RuntimeError("Content schema metadata columns are malformed.")

        rows = connection.execute(
            "SELECT singleton, schema_version FROM content_schema_meta"
        ).fetchall()
        if (
            len(rows) != 1
            or type(rows[0]["singleton"]) is not int
            or rows[0]["singleton"] != 1
            or type(rows[0]["schema_version"]) is not int
        ):
            raise RuntimeError("Content schema metadata is inconsistent.")
        if rows[0]["schema_version"] != cls.RUNTIME_SCHEMA_VERSION:
            raise ContentSchemaVersionMismatch(
                rows[0]["schema_version"],
                cls.RUNTIME_SCHEMA_VERSION,
            )

    def _require_visual_analysis_schema(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        main = connection.execute(
            "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
        ).fetchone()
        if (
            main is None
            or main["schema_version"] != self.PRIVATE_RENDER_OPERATION_SCHEMA_VERSION
        ):
            raise RuntimeError("Visual analysis requires the content schema-v10 foundation.")
        exists = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'content_visual_analysis_schema_meta'
            """
        ).fetchone()
        if exists is None:
            raise RuntimeError(
                "Visual analysis requires explicit visual-analysis schema migration."
            )
        self._verify_visual_analysis_schema(connection)

    def prepare_visual_analysis_worker(self, worker_token: object) -> None:
        """Take the single-worker lease and reconcile prior process work once."""
        with self._lock:
            if self._visual_analysis_recovery_complete:
                if self._visual_analysis_worker_token is worker_token:
                    return
                raise RuntimeError(
                    "Another visual-analysis worker instance owns this store."
                )
            lock_fd: int | None = None
            if self.database_identity != ":memory:":
                database_path = Path(self.database_identity)
                lock_path = database_path.with_name(
                    f"{database_path.name}.visual-analysis-worker.lock"
                )
                flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
                lock_fd = os.open(lock_path, flags, 0o600)
                os.fchmod(lock_fd, 0o600)
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    os.close(lock_fd)
                    raise RuntimeError(
                        "Another visual-analysis worker process owns the database lease."
                    ) from exc
            self._visual_analysis_worker_lock_fd = lock_fd
            self._visual_analysis_worker_token = worker_token
            try:
                self._recover_interrupted_visual_analyses()
            except Exception:
                self._visual_analysis_worker_token = None
                self._release_visual_analysis_worker_lease()
                raise
            self._visual_analysis_recovery_complete = True

    def stop_visual_analysis_worker(self, worker_token: object) -> None:
        with self._lock:
            if self._visual_analysis_worker_token is not worker_token:
                raise RuntimeError(
                    "Only the owning visual-analysis worker can release its lease."
                )
            if self._visual_analysis_active_claims:
                raise RuntimeError(
                    "Visual-analysis worker cannot stop while analyses are claimed."
                )
            self._visual_analysis_worker_token = None
            self._visual_analysis_recovery_complete = False
            self._release_visual_analysis_worker_lease()

    def _require_visual_analysis_worker(self, worker_token: object | None) -> None:
        if not self._visual_analysis_recovery_complete:
            raise RuntimeError(
                "Visual-analysis startup recovery must complete before worker operations."
            )
        if (
            worker_token is None
            or self._visual_analysis_worker_token is not worker_token
        ):
            raise RuntimeError(
                "Visual-analysis operation requires the owning worker instance."
            )

    def _release_visual_analysis_worker_lease(self) -> None:
        if self._visual_analysis_worker_lock_fd is None:
            return
        fcntl.flock(self._visual_analysis_worker_lock_fd, fcntl.LOCK_UN)
        os.close(self._visual_analysis_worker_lock_fd)
        self._visual_analysis_worker_lock_fd = None

    def create_visual_analysis(
        self,
        request: VisualAnalysisRequest,
    ) -> tuple[ContentVisualAnalysis, bool]:
        if type(request) is not VisualAnalysisRequest:
            raise TypeError("Visual analysis creation requires a validated request.")
        request = VisualAnalysisRequest.model_validate(
            request.model_dump(mode="python")
        )
        request_json = self._canonical(request)
        request_fingerprint = request.request_fingerprint()
        cache_fingerprint = request.cache_fingerprint()
        now = self._now()
        with self._transaction() as connection:
            self._require_visual_analysis_schema(connection)
            source = connection.execute(
                """
                SELECT p.owner_id AS project_owner,
                       a.owner_id AS asset_owner,
                       a.content_sha256 AS asset_sha256
                FROM content_edit_projects p
                JOIN content_edit_project_sources ps
                  ON ps.project_id = p.project_id
                JOIN content_assets a ON a.asset_id = ps.source_asset_id
                WHERE p.project_id = ? AND a.asset_id = ?
                """,
                (request.project_id, request.asset_id),
            ).fetchone()
            if (
                source is None
                or source["project_owner"] != request.owner_id
                or source["asset_owner"] != request.owner_id
            ):
                raise PermissionError("Visual analysis source is unavailable.")
            if source["asset_sha256"] != request.asset_sha256:
                raise ValueError("Visual analysis source digest does not match the registered asset.")
            existing = connection.execute(
                """
                SELECT * FROM content_visual_analyses
                WHERE owner_id = ? AND idempotency_key = ?
                """,
                (request.owner_id, request.idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["request_fingerprint"] != request_fingerprint:
                    raise VisualAnalysisConflictError(
                        "Visual analysis idempotency key conflicts with an existing request."
                    )
                return self._load_visual_analysis(existing), False
            # Reuse is disabled until provider and analysis implementation
            # identities are bound to independently verified immutable evidence.
            cached_source_id = None
            state = VisualAnalysisState.QUEUED
            connection.execute(
                """
                INSERT INTO content_visual_analyses (
                    analysis_id, owner_id, project_id, asset_id, asset_sha256,
                    idempotency_key, request_fingerprint, cache_fingerprint,
                    cache_source_analysis_id, request_json, state, result_json,
                    created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.analysis_id,
                    request.owner_id,
                    request.project_id,
                    request.asset_id,
                    request.asset_sha256,
                    request.idempotency_key,
                    request_fingerprint,
                    cache_fingerprint,
                    cached_source_id,
                    request_json,
                    state.value,
                    None,
                    now,
                    now,
                    None,
                ),
            )
            self._append_visual_analysis_event(
                connection,
                analysis_id=request.analysis_id,
                owner_id=request.owner_id,
                from_state=None,
                to_state=state,
                actor=request.owner_id,
                reason="visual_analysis_accepted",
                now=now,
            )
            row = connection.execute(
                """
                SELECT * FROM content_visual_analyses
                WHERE analysis_id = ? AND owner_id = ?
                """,
                (request.analysis_id, request.owner_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("Visual analysis disappeared after creation.")
            return self._load_visual_analysis(row), True

    def claim_visual_analysis(
        self,
        analysis_id: str,
        owner_id: str,
        *,
        worker_token: object | None = None,
    ) -> bool:
        with self._lock:
            self._require_visual_analysis_worker(worker_token)
            now = self._now()
            with self._transaction() as connection:
                self._require_visual_analysis_schema(connection)
                cursor = connection.execute(
                    """
                    UPDATE content_visual_analyses
                    SET state = ?, updated_at = ?
                    WHERE analysis_id = ? AND owner_id = ? AND state = ?
                    """,
                    (
                        VisualAnalysisState.ANALYZING.value,
                        now,
                        analysis_id,
                        owner_id,
                        VisualAnalysisState.QUEUED.value,
                    ),
                )
                if cursor.rowcount != 1:
                    return False
                self._append_visual_analysis_event(
                    connection,
                    analysis_id=analysis_id,
                    owner_id=owner_id,
                    from_state=VisualAnalysisState.QUEUED,
                    to_state=VisualAnalysisState.ANALYZING,
                    actor=owner_id,
                    reason="analysis_worker_claimed",
                    now=now,
                )
            self._visual_analysis_active_claims.add((analysis_id, owner_id))
            return True

    def complete_visual_analysis(
        self,
        analysis_id: str,
        owner_id: str,
        result: VisualVideoAnalysis,
        *,
        worker_token: object | None = None,
    ) -> None:
        with self._lock:
            self._require_visual_analysis_worker(worker_token)
            if (analysis_id, owner_id) not in self._visual_analysis_active_claims:
                raise RuntimeError(
                    "Visual analysis completion requires an active worker claim."
                )
            if type(result) is not VisualVideoAnalysis:
                raise TypeError("Visual analysis completion requires a validated result.")
            result = VisualVideoAnalysis.model_validate(result.model_dump(mode="python"))
            result_json = self._canonical(result)
            if len(result_json.encode("utf-8")) > 2 * 1024 * 1024:
                raise ValueError("Visual analysis result exceeds its storage size limit.")
            now = self._now()
            with self._transaction() as connection:
                self._require_visual_analysis_schema(connection)
                row = connection.execute(
                    """
                    SELECT * FROM content_visual_analyses
                    WHERE analysis_id = ? AND owner_id = ?
                    """,
                    (analysis_id, owner_id),
                ).fetchone()
                if row is None:
                    raise PermissionError("Visual analysis is unavailable.")
                request = VisualAnalysisRequest.model_validate_json(row["request_json"])
                if (
                    result.asset_id != request.asset_id
                    or result.asset_sha256 != request.asset_sha256
                    or len(result.frames) != len(request.timestamps_seconds)
                    or tuple(
                        frame.evidence.timestamp_seconds for frame in result.frames
                    )
                    != request.timestamps_seconds
                    or any(
                        frame.provider_id != request.provider_id
                        or frame.model_id != request.model_id
                        or frame.model_version != request.model_version
                        for frame in result.frames
                    )
                ):
                    raise ValueError(
                        "Visual analysis result does not match its registered request."
                    )
                cursor = connection.execute(
                    """
                    UPDATE content_visual_analyses
                    SET state = ?, result_json = ?, failure_code = NULL,
                        failure_detail = NULL, completed_at = ?, updated_at = ?
                    WHERE analysis_id = ? AND owner_id = ? AND state = ?
                    """,
                    (
                        VisualAnalysisState.COMPLETED.value,
                        result_json,
                        now,
                        now,
                        analysis_id,
                        owner_id,
                        VisualAnalysisState.ANALYZING.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("Visual analysis state changed before completion.")
                self._append_visual_analysis_event(
                    connection,
                    analysis_id=analysis_id,
                    owner_id=owner_id,
                    from_state=VisualAnalysisState.ANALYZING,
                    to_state=VisualAnalysisState.COMPLETED,
                    actor=owner_id,
                    reason="visual_analysis_completed",
                    now=now,
                )
            self._visual_analysis_active_claims.remove((analysis_id, owner_id))

    def fail_visual_analysis(
        self,
        analysis_id: str,
        owner_id: str,
        *,
        failure_code: str,
        failure_detail: str,
        worker_token: object | None = None,
    ) -> bool:
        with self._lock:
            self._require_visual_analysis_worker(worker_token)
            if (
                not isinstance(failure_code, str)
                or not isinstance(failure_detail, str)
                or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", failure_code)
                or not failure_detail.strip()
                or len(failure_detail) > 1000
            ):
                raise ValueError("Visual analysis failure details are invalid.")
            now = self._now()
            with self._transaction() as connection:
                self._require_visual_analysis_schema(connection)
                row = connection.execute(
                    """
                    SELECT state FROM content_visual_analyses
                    WHERE analysis_id = ? AND owner_id = ?
                    """,
                    (analysis_id, owner_id),
                ).fetchone()
                if row is None:
                    return False
                state = VisualAnalysisState(row["state"])
                if state not in {VisualAnalysisState.QUEUED, VisualAnalysisState.ANALYZING}:
                    return False
                cursor = connection.execute(
                    """
                    UPDATE content_visual_analyses
                    SET state = ?, failure_code = ?, failure_detail = ?, updated_at = ?
                    WHERE analysis_id = ? AND owner_id = ? AND state = ?
                    """,
                    (
                        VisualAnalysisState.FAILED.value,
                        failure_code,
                        failure_detail,
                        now,
                        analysis_id,
                        owner_id,
                        state.value,
                    ),
                )
                if cursor.rowcount != 1:
                    return False
                self._append_visual_analysis_event(
                    connection,
                    analysis_id=analysis_id,
                    owner_id=owner_id,
                    from_state=state,
                    to_state=VisualAnalysisState.FAILED,
                    actor=owner_id,
                    reason=failure_code.lower(),
                    now=now,
                )
            self._visual_analysis_active_claims.discard((analysis_id, owner_id))
            return True

    def _recover_interrupted_visual_analyses(self) -> int:
        now = self._now()
        with self._transaction() as connection:
            self._require_visual_analysis_schema(connection)
            rows = connection.execute(
                """
                SELECT analysis_id, owner_id FROM content_visual_analyses
                WHERE state = ?
                ORDER BY created_at, analysis_id
                """,
                (VisualAnalysisState.ANALYZING.value,),
            ).fetchall()
            for row in rows:
                cursor = connection.execute(
                    """
                    UPDATE content_visual_analyses
                    SET state = ?, updated_at = ?
                    WHERE analysis_id = ? AND owner_id = ? AND state = ?
                    """,
                    (
                        VisualAnalysisState.RECONCILIATION_REQUIRED.value,
                        now,
                        row["analysis_id"],
                        row["owner_id"],
                        VisualAnalysisState.ANALYZING.value,
                    ),
                )
                if cursor.rowcount == 1:
                    self._append_visual_analysis_event(
                        connection,
                        analysis_id=row["analysis_id"],
                        owner_id=row["owner_id"],
                        from_state=VisualAnalysisState.ANALYZING,
                        to_state=VisualAnalysisState.RECONCILIATION_REQUIRED,
                        actor="system",
                        reason="interrupted_analysis_requires_reconciliation",
                        now=now,
                    )
            return len(rows)

    def get_visual_analysis(
        self,
        analysis_id: str,
        owner_id: str,
    ) -> ContentVisualAnalysis | None:
        with self._lock:
            self._require_visual_analysis_schema(self._connection)
            row = self._connection.execute(
                """
                SELECT * FROM content_visual_analyses
                WHERE analysis_id = ? AND owner_id = ?
                """,
                (analysis_id, owner_id),
            ).fetchone()
        return None if row is None else self._load_visual_analysis(row)

    def list_visual_analysis_events(
        self,
        analysis_id: str,
        owner_id: str,
    ) -> list[ContentVisualAnalysisEvent]:
        with self._lock:
            self._require_visual_analysis_schema(self._connection)
            rows = self._connection.execute(
                """
                SELECT e.* FROM content_visual_analysis_events e
                JOIN content_visual_analyses a
                  ON a.analysis_id = e.analysis_id
                WHERE e.analysis_id = ? AND a.owner_id = ? AND e.owner_id = ?
                ORDER BY e.sequence
                """,
                (analysis_id, owner_id, owner_id),
            ).fetchall()
        return [
            ContentVisualAnalysisEvent(
                sequence=row["sequence"],
                analysis_id=row["analysis_id"],
                owner_id=row["owner_id"],
                from_state=(
                    None
                    if row["from_state"] is None
                    else VisualAnalysisState(row["from_state"])
                ),
                to_state=VisualAnalysisState(row["to_state"]),
                actor=row["actor"],
                reason=row["reason"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def plan_visual_analysis_retention(
        self,
        policy: VisualAnalysisRetentionPolicy,
    ) -> VisualAnalysisRetentionPlan:
        if type(policy) is not VisualAnalysisRetentionPolicy:
            raise TypeError("An explicit visual-analysis retention policy is required.")
        cutoffs = {
            VisualAnalysisState.COMPLETED: policy.completed_before.isoformat(),
            VisualAnalysisState.FAILED: policy.failed_before.isoformat(),
            VisualAnalysisState.RECONCILIATION_REQUIRED:
                policy.reconciliation_required_before.isoformat(),
        }
        candidates: list[str] = []
        audit_protected: list[str] = []
        cache_referenced: list[str] = []
        active: list[str] = []
        with self._lock:
            self._require_visual_analysis_schema(self._connection)
            rows = self._connection.execute(
                """
                SELECT analysis_id, state, updated_at
                FROM content_visual_analyses
                WHERE owner_id = ?
                ORDER BY analysis_id
                """,
                (policy.owner_id,),
            ).fetchall()
            for row in rows:
                state = VisualAnalysisState(row["state"])
                if state in {VisualAnalysisState.QUEUED, VisualAnalysisState.ANALYZING}:
                    active.append(row["analysis_id"])
                    continue
                cutoff = cutoffs.get(state)
                if cutoff is None or row["updated_at"] >= cutoff:
                    continue
                has_events = self._connection.execute(
                    """
                    SELECT 1 FROM content_visual_analysis_events
                    WHERE analysis_id = ? AND owner_id = ? LIMIT 1
                    """,
                    (row["analysis_id"], policy.owner_id),
                ).fetchone()
                has_cache_references = self._connection.execute(
                    """
                    SELECT 1 FROM content_visual_analyses
                    WHERE cache_source_analysis_id = ? AND owner_id = ? LIMIT 1
                    """,
                    (row["analysis_id"], policy.owner_id),
                ).fetchone()
                if has_events is not None:
                    audit_protected.append(row["analysis_id"])
                if has_cache_references is not None:
                    cache_referenced.append(row["analysis_id"])
                if has_events is None and has_cache_references is None:
                    candidates.append(row["analysis_id"])
        return VisualAnalysisRetentionPlan(
            owner_id=policy.owner_id,
            candidate_analysis_ids=tuple(candidates),
            audit_protected_analysis_ids=tuple(audit_protected),
            cache_referenced_analysis_ids=tuple(cache_referenced),
            active_analysis_ids=tuple(active),
            dry_run=True,
        )

    @staticmethod
    def _append_visual_analysis_event(
        connection: sqlite3.Connection,
        *,
        analysis_id: str,
        owner_id: str,
        from_state: VisualAnalysisState | None,
        to_state: VisualAnalysisState,
        actor: str,
        reason: str,
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO content_visual_analysis_events (
                analysis_id, owner_id, from_state, to_state, actor, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                analysis_id,
                owner_id,
                None if from_state is None else from_state.value,
                to_state.value,
                actor[:128],
                reason[:200],
                now,
            ),
        )

    @staticmethod
    def _load_visual_analysis(row: sqlite3.Row) -> ContentVisualAnalysis:
        request = VisualAnalysisRequest.model_validate_json(row["request_json"])
        result = (
            None
            if row["result_json"] is None
            else VisualVideoAnalysis.model_validate_json(row["result_json"])
        )
        return ContentVisualAnalysis(
            analysis_id=row["analysis_id"],
            owner_id=row["owner_id"],
            project_id=row["project_id"],
            asset_id=row["asset_id"],
            asset_sha256=row["asset_sha256"],
            idempotency_key=row["idempotency_key"],
            request_fingerprint=row["request_fingerprint"],
            cache_fingerprint=row["cache_fingerprint"],
            request=request,
            state=VisualAnalysisState(row["state"]),
            result=result,
            cache_source_analysis_id=row["cache_source_analysis_id"],
            failure_code=row["failure_code"],
            failure_detail=row["failure_detail"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            completed_at=(
                None
                if row["completed_at"] is None
                else datetime.fromisoformat(row["completed_at"])
            ),
        )

    @staticmethod
    def _create_private_render_operation_tables(
        connection: sqlite3.Connection,
    ) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS content_private_render_operations (
                operation_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                project_id TEXT NOT NULL
                    REFERENCES content_edit_projects(project_id),
                idempotency_key TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL
                    CHECK (length(request_fingerprint) = 64),
                input_snapshot_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK (
                    state IN (
                        'queued', 'rendering', 'completed', 'failed',
                        'reconciliation_required'
                    )
                ),
                artifact_key TEXT UNIQUE,
                artifact_sha256 TEXT CHECK (
                    artifact_sha256 IS NULL OR length(artifact_sha256) = 64
                ),
                artifact_byte_count INTEGER CHECK (
                    artifact_byte_count IS NULL OR
                    artifact_byte_count BETWEEN 1 AND 16777216
                ),
                result_json TEXT,
                failure_code TEXT,
                failure_detail TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                UNIQUE(owner_id, idempotency_key),
                CHECK (
                    (state = 'completed' AND artifact_key IS NOT NULL
                        AND artifact_sha256 IS NOT NULL
                        AND artifact_byte_count IS NOT NULL
                        AND result_json IS NOT NULL AND completed_at IS NOT NULL)
                    OR state != 'completed'
                )
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS content_private_render_operation_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL
                    REFERENCES content_private_render_operations(operation_id),
                from_state TEXT CHECK (
                    from_state IS NULL OR from_state IN (
                        'queued', 'rendering', 'completed', 'failed',
                        'reconciliation_required'
                    )
                ),
                to_state TEXT NOT NULL CHECK (
                    to_state IN (
                        'queued', 'rendering', 'completed', 'failed',
                        'reconciliation_required'
                    )
                ),
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS content_private_render_operations_owner
            ON content_private_render_operations(owner_id, created_at)
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS content_private_render_operation_identity_immutable
            BEFORE UPDATE OF operation_id, owner_id, project_id, idempotency_key,
                request_fingerprint, input_snapshot_json, created_at
            ON content_private_render_operations
            BEGIN
                SELECT RAISE(ABORT, 'private render operation identity is immutable');
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS content_private_render_operation_state_transition
            BEFORE UPDATE OF state ON content_private_render_operations
            WHEN NOT (
                (OLD.state = 'queued' AND NEW.state IN ('rendering', 'failed'))
                OR (OLD.state = 'rendering' AND NEW.state IN (
                    'completed', 'failed', 'reconciliation_required'
                ))
            )
            BEGIN
                SELECT RAISE(ABORT, 'invalid private render operation state transition');
            END
            """
        )

    @staticmethod
    def _verify_private_render_operation_schema(
        connection: sqlite3.Connection,
    ) -> None:
        required_columns = {
            "content_private_render_operations": {
                "operation_id",
                "owner_id",
                "project_id",
                "idempotency_key",
                "request_fingerprint",
                "input_snapshot_json",
                "state",
                "artifact_key",
                "artifact_sha256",
                "artifact_byte_count",
                "result_json",
                "failure_code",
                "failure_detail",
                "created_at",
                "updated_at",
                "completed_at",
            },
            "content_private_render_operation_events": {
                "sequence",
                "operation_id",
                "from_state",
                "to_state",
                "actor",
                "reason",
                "created_at",
            },
        }
        for table, expected_columns in required_columns.items():
            columns = {
                row["name"]
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
            if not expected_columns.issubset(columns):
                raise RuntimeError(
                    f"Private render operation schema is incomplete: {table}."
                )
        required_triggers = {
            "content_private_render_operation_identity_immutable",
            "content_private_render_operation_state_transition",
        }
        triggers = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        if not required_triggers.issubset(triggers):
            raise RuntimeError("Private render operation schema is incomplete.")

    @staticmethod
    def _upgrade_publishing_attempt_schema_v9(
        connection: sqlite3.Connection,
    ) -> None:
        invalid_terminal_failures = connection.execute(
            """
            SELECT COUNT(*)
            FROM content_publishing_attempts a
            WHERE a.state = 'failed'
              AND (
                  a.error_code != 'provider_rejected'
                  OR NOT EXISTS (
                      SELECT 1 FROM content_publishing_attempt_events e
                      WHERE e.attempt_id = a.attempt_id
                        AND e.to_state = 'failed'
                        AND e.code = 'provider_rejected'
                        AND e.from_state = 'initiation_intent'
                  )
              )
            """
        ).fetchone()[0]
        if invalid_terminal_failures:
            raise RuntimeError(
                "Publishing attempt migration found terminal failures without "
                "safe pre-upload rejection evidence."
            )
        connection.execute(
            "DROP INDEX content_publishing_attempt_one_unresolved_per_job"
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX content_publishing_attempt_one_unresolved_per_job
            ON content_publishing_attempts(job_id)
            WHERE state NOT IN ('published', 'available_private')
              AND NOT (
                  state = 'failed'
                  AND error_code = 'provider_rejected'
              )
              AND NOT (
                  state = 'blocked'
                  AND operator_resolution_code = 'safe_to_replace'
              )
            """
        )
        transition_checks = []
        for old_state, new_states in _PUBLISHING_ATTEMPT_TRANSITIONS.items():
            allowed = ", ".join(
                f"'{state.value}'"
                for state in sorted(new_states, key=lambda state: state.value)
            )
            transition_checks.append(
                f"(OLD.state = '{old_state.value}' AND NEW.state IN ({allowed}))"
            )
        transition_sql = " OR ".join(transition_checks)
        connection.execute(
            "DROP TRIGGER content_publishing_attempt_state_transition"
        )
        connection.execute(
            f"""
            CREATE TRIGGER content_publishing_attempt_state_transition
            BEFORE UPDATE OF state ON content_publishing_attempts
            WHEN NEW.state != OLD.state
              AND NOT ({transition_sql})
            BEGIN
                SELECT RAISE(ABORT, 'invalid publishing attempt state transition');
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER content_publishing_attempt_failure_source_valid
            BEFORE UPDATE OF state ON content_publishing_attempts
            WHEN NEW.state = 'failed'
              AND (NEW.error_code != 'provider_rejected'
                   OR OLD.state != 'initiation_intent')
            BEGIN
                SELECT RAISE(ABORT, 'publishing failure evidence does not match source state');
            END
            """
        )

    @staticmethod
    def _verify_publishing_attempt_schema(
        connection: sqlite3.Connection,
    ) -> None:
        required = {
            ("table", "content_publishing_attempts"),
            ("table", "content_publishing_attempt_secret_refs"),
            ("table", "content_publishing_attempt_events"),
            ("index", "content_publishing_attempt_one_unresolved_per_job"),
            ("index", "content_publishing_attempts_job_created"),
            ("index", "content_publishing_attempt_one_live_secret_ref"),
            ("index", "content_publishing_attempt_secret_ref_lookup"),
            ("index", "content_publishing_attempt_events_order"),
            ("trigger", "content_publishing_attempt_identity_immutable"),
            ("trigger", "content_publishing_attempt_revision_requires_event"),
            ("trigger", "content_publishing_attempt_state_transition"),
            ("trigger", "content_publishing_attempt_failure_is_confirmed"),
            ("trigger", "content_publishing_attempt_success_requires_evidence"),
            ("trigger", "content_publishing_attempt_state_requires_evidence"),
            ("trigger", "content_publishing_attempt_session_state_requires_secret"),
            ("trigger", "content_publishing_attempt_offset_monotonic"),
            ("trigger", "content_publishing_attempt_events_append_only_update"),
            ("trigger", "content_publishing_attempt_events_append_only_delete"),
            ("trigger", "content_publishing_attempt_event_revision_valid"),
            ("trigger", "content_publishing_attempt_secret_ref_identity_immutable"),
            ("trigger", "content_publishing_attempt_secret_ref_status_transition"),
            ("trigger", "content_publishing_attempt_secret_ref_no_delete"),
            ("trigger", "content_publishing_attempt_operator_resolution_once"),
            ("trigger", "content_publishing_attempt_failure_source_valid"),
        }
        existing = {
            (item["type"], item["name"])
            for item in connection.execute(
                """
                SELECT type, name FROM sqlite_master
                WHERE type IN ('table', 'index', 'trigger')
                """
            )
        }
        if not required.issubset(existing):
            raise RuntimeError("Publishing attempt schema is inconsistent.")
        expected_connection = sqlite3.connect(":memory:")
        expected_connection.row_factory = sqlite3.Row
        try:
            ContentStore._create_publishing_attempt_tables(expected_connection)
            ContentStore._upgrade_publishing_attempt_schema_v9(expected_connection)

            def normalize_definition(definition: str) -> str:
                def normalize_in_clause(match: re.Match[str]) -> str:
                    values = [value.strip() for value in match.group(1).split(",")]
                    if not all(
                        value.startswith("'") and value.endswith("'")
                        for value in values
                    ):
                        return match.group(0)
                    return f"IN ({', '.join(sorted(values))})"

                return re.sub(
                    r"\bIN\s*\(([^()]*)\)",
                    normalize_in_clause,
                    " ".join(definition.split()),
                )

            expected_definitions = {
                (item["type"], item["name"]): normalize_definition(item["sql"])
                for item in expected_connection.execute(
                    """
                    SELECT type, name, sql FROM sqlite_master
                    WHERE type IN ('table', 'index', 'trigger')
                    """
                )
                if item["sql"] is not None
            }
        finally:
            expected_connection.close()
        actual_definitions = {
            (item["type"], item["name"]): normalize_definition(item["sql"])
            for item in connection.execute(
                """
                SELECT type, name, sql FROM sqlite_master
                WHERE type IN ('table', 'index', 'trigger')
                """
            )
            if item["sql"] is not None
        }
        if any(
            actual_definitions.get(key) != expected_definitions.get(key)
            for key in required
        ):
            raise RuntimeError("Publishing attempt schema definitions are inconsistent.")
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("Content database failed SQLite integrity check.")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("Content database contains broken foreign keys.")

    @staticmethod
    def _create_publishing_attempt_tables(connection: sqlite3.Connection) -> None:
        states = ", ".join(f"'{state.value}'" for state in PublishingAttemptState)
        codes = ", ".join(f"'{code.value}'" for code in PublishingAttemptCode)
        transition_checks = []
        for old_state, new_states in _PUBLISHING_ATTEMPT_TRANSITIONS.items():
            allowed = ", ".join(
                f"'{state.value}'"
                for state in sorted(new_states, key=lambda state: state.value)
            )
            transition_checks.append(
                f"(OLD.state = '{old_state.value}' AND NEW.state IN ({allowed}))"
            )
        transition_sql = " OR ".join(transition_checks)
        schema_sql = f"""
            CREATE TABLE content_publishing_attempts (
                attempt_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES content_publishing_jobs(job_id),
                attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
                owner_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                provider TEXT NOT NULL CHECK (provider = 'youtube'),
                release_identity TEXT NOT NULL,
                package_id TEXT NOT NULL,
                package_version INTEGER NOT NULL CHECK (package_version > 0),
                output_sha256 TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ({states})),
                confirmed_offset INTEGER NOT NULL DEFAULT 0
                    CHECK (confirmed_offset >= 0),
                total_bytes INTEGER NOT NULL CHECK (total_bytes > 0),
                error_code TEXT CHECK (error_code IS NULL OR error_code IN ({codes})),
                revision INTEGER NOT NULL CHECK (revision >= 0),
                operator_resolution_code TEXT,
                operator_resolved_by TEXT,
                operator_resolved_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK (confirmed_offset <= total_bytes),
                CHECK (
                    state NOT IN ('published', 'available_private')
                    OR error_code IS NULL
                ),
                CHECK (
                    (operator_resolution_code IS NULL
                        AND operator_resolved_by IS NULL
                        AND operator_resolved_at IS NULL)
                    OR
                    (operator_resolution_code = 'safe_to_replace'
                        AND state = 'blocked'
                        AND operator_resolved_by IS NOT NULL
                        AND operator_resolved_at IS NOT NULL)
                ),
                UNIQUE(job_id, attempt_number)
            );

            CREATE UNIQUE INDEX content_publishing_attempt_one_unresolved_per_job
            ON content_publishing_attempts(job_id)
            WHERE state NOT IN ('published', 'available_private', 'failed')
              AND NOT (
                  state = 'blocked'
                  AND operator_resolution_code = 'safe_to_replace'
              );
            CREATE INDEX content_publishing_attempts_job_created
            ON content_publishing_attempts(job_id, created_at);

            CREATE TABLE content_publishing_attempt_secret_refs (
                reference_id TEXT PRIMARY KEY,
                attempt_id TEXT NOT NULL
                    REFERENCES content_publishing_attempts(attempt_id),
                purpose TEXT NOT NULL
                    CHECK (purpose = 'resumable_upload_session'),
                schema_version INTEGER NOT NULL CHECK (schema_version > 0),
                generation INTEGER NOT NULL CHECK (generation > 0),
                status TEXT NOT NULL CHECK (status IN (
                    'pending', 'active', 'invalidated', 'deletion_pending',
                    'deletion_failed', 'deleted'
                )),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(attempt_id, purpose, generation)
            );
            CREATE UNIQUE INDEX content_publishing_attempt_one_live_secret_ref
            ON content_publishing_attempt_secret_refs(attempt_id, purpose)
            WHERE status IN ('pending', 'active', 'deletion_pending', 'deletion_failed');
            CREATE INDEX content_publishing_attempt_secret_ref_lookup
            ON content_publishing_attempt_secret_refs(attempt_id, purpose, generation);

            CREATE TABLE content_publishing_attempt_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                attempt_id TEXT NOT NULL
                    REFERENCES content_publishing_attempts(attempt_id),
                revision INTEGER NOT NULL CHECK (revision > 0),
                event_type TEXT NOT NULL CHECK (event_type IN (
                    'created', 'transition', 'offset_confirmed',
                    'secret_reference_prepared', 'secret_reference_activated',
                    'secret_reference_status_changed', 'operator_resolution'
                )),
                from_state TEXT CHECK (from_state IS NULL OR from_state IN ({states})),
                to_state TEXT NOT NULL CHECK (to_state IN ({states})),
                code TEXT NOT NULL CHECK (code IN ({codes})),
                actor TEXT NOT NULL,
                command_id TEXT NOT NULL UNIQUE,
                reference_id TEXT REFERENCES content_publishing_attempt_secret_refs(
                    reference_id
                ),
                action TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(attempt_id, revision)
            );
            CREATE INDEX content_publishing_attempt_events_order
            ON content_publishing_attempt_events(attempt_id, sequence);

            CREATE TRIGGER content_publishing_attempt_identity_immutable
            BEFORE UPDATE OF attempt_id, job_id, attempt_number, owner_id,
                account_id, provider, release_identity, package_id,
                package_version, output_sha256, total_bytes, created_at
            ON content_publishing_attempts
            BEGIN
                SELECT RAISE(ABORT, 'publishing attempt identity is immutable');
            END;

            CREATE TRIGGER content_publishing_attempt_revision_requires_event
            BEFORE UPDATE ON content_publishing_attempts
            WHEN NEW.revision != OLD.revision + 1
              OR NOT EXISTS (
                  SELECT 1 FROM content_publishing_attempt_events e
                  WHERE e.attempt_id = OLD.attempt_id
                    AND e.revision = NEW.revision
                    AND (
                        e.from_state = OLD.state
                        OR (OLD.revision = 0 AND e.from_state IS NULL
                            AND e.event_type = 'created')
                    )
                    AND e.to_state = NEW.state
              )
            BEGIN
                SELECT RAISE(ABORT, 'publishing attempt update requires next event');
            END;

            CREATE TRIGGER content_publishing_attempt_state_transition
            BEFORE UPDATE OF state ON content_publishing_attempts
            WHEN NEW.state != OLD.state
              AND NOT ({transition_sql})
            BEGIN
                SELECT RAISE(ABORT, 'invalid publishing attempt state transition');
            END;

            CREATE TRIGGER content_publishing_attempt_failure_is_confirmed
            BEFORE UPDATE OF state ON content_publishing_attempts
            WHEN NEW.state = 'failed'
              AND (NEW.error_code IS NULL OR NEW.error_code NOT IN (
                  'provider_rejected', 'session_expired_confirmed',
                  'processing_failed_confirmed'
              ))
            BEGIN
                SELECT RAISE(ABORT, 'failed attempt requires confirmed failure');
            END;

            CREATE TRIGGER content_publishing_attempt_success_requires_evidence
            BEFORE UPDATE OF state ON content_publishing_attempts
            WHEN (NEW.state = 'published'
                    AND NOT EXISTS (
                        SELECT 1 FROM content_publishing_attempt_events e
                        WHERE e.attempt_id = OLD.attempt_id
                          AND e.revision = NEW.revision
                          AND e.code = 'publication_confirmed'
                    ))
              OR (NEW.state = 'available_private'
                    AND NOT EXISTS (
                        SELECT 1 FROM content_publishing_attempt_events e
                        WHERE e.attempt_id = OLD.attempt_id
                          AND e.revision = NEW.revision
                          AND e.code = 'private_availability_confirmed'
                    ))
            BEGIN
                SELECT RAISE(ABORT, 'attempt completion requires provider evidence');
            END;

            CREATE TRIGGER content_publishing_attempt_state_requires_evidence
            BEFORE UPDATE OF state ON content_publishing_attempts
            WHEN NOT (
                NEW.state = OLD.state
                OR (
                    NEW.state = 'initiation_intent'
                    AND EXISTS (
                        SELECT 1 FROM content_publishing_attempt_events e
                        WHERE e.attempt_id = OLD.attempt_id
                          AND e.revision = NEW.revision
                          AND e.code = 'initiation_authorized'
                    )
                )
                OR (
                    NEW.state = 'session_ready'
                    AND EXISTS (
                        SELECT 1 FROM content_publishing_attempt_events e
                        WHERE e.attempt_id = OLD.attempt_id
                          AND e.revision = NEW.revision
                          AND e.code = 'session_stored'
                    )
                )
                OR (
                    NEW.state = 'uploading'
                    AND EXISTS (
                        SELECT 1 FROM content_publishing_attempt_events e
                        WHERE e.attempt_id = OLD.attempt_id
                          AND e.revision = NEW.revision
                          AND e.code = 'upload_started'
                    )
                )
                OR (
                    NEW.state = 'initiation_uncertain'
                    AND EXISTS (
                        SELECT 1 FROM content_publishing_attempt_events e
                        WHERE e.attempt_id = OLD.attempt_id
                          AND e.revision = NEW.revision
                          AND e.code IN (
                              'initiation_outcome_unknown',
                              'remote_outcome_unknown'
                          )
                    )
                )
                OR (
                    NEW.state = 'reconciliation_required'
                    AND EXISTS (
                        SELECT 1 FROM content_publishing_attempt_events e
                        WHERE e.attempt_id = OLD.attempt_id
                          AND e.revision = NEW.revision
                          AND e.code IN (
                              'reconciliation_started',
                              'reconciliation_mismatch',
                              'remote_outcome_unknown'
                          )
                    )
                )
                OR (
                    NEW.state = 'remote_uploaded'
                    AND EXISTS (
                        SELECT 1 FROM content_publishing_attempt_events e
                        WHERE e.attempt_id = OLD.attempt_id
                          AND e.revision = NEW.revision
                          AND e.code = 'remote_upload_confirmed'
                    )
                )
                OR (
                    NEW.state = 'processing'
                    AND EXISTS (
                        SELECT 1 FROM content_publishing_attempt_events e
                        WHERE e.attempt_id = OLD.attempt_id
                          AND e.revision = NEW.revision
                          AND e.code = 'processing_started'
                    )
                )
                OR (
                    NEW.state = 'published'
                    AND EXISTS (
                        SELECT 1 FROM content_publishing_attempt_events e
                        WHERE e.attempt_id = OLD.attempt_id
                          AND e.revision = NEW.revision
                          AND e.code = 'publication_confirmed'
                    )
                )
                OR (
                    NEW.state = 'available_private'
                    AND EXISTS (
                        SELECT 1 FROM content_publishing_attempt_events e
                        WHERE e.attempt_id = OLD.attempt_id
                          AND e.revision = NEW.revision
                          AND e.code = 'private_availability_confirmed'
                    )
                )
                OR (
                    NEW.state = 'failed'
                    AND EXISTS (
                        SELECT 1 FROM content_publishing_attempt_events e
                        WHERE e.attempt_id = OLD.attempt_id
                          AND e.revision = NEW.revision
                          AND e.code = NEW.error_code
                    )
                )
                OR (
                    NEW.state = 'session_expired'
                    AND EXISTS (
                        SELECT 1 FROM content_publishing_attempt_events e
                        WHERE e.attempt_id = OLD.attempt_id
                          AND e.revision = NEW.revision
                          AND e.code = 'session_expired_confirmed'
                    )
                )
                OR (
                    NEW.state = 'blocked'
                    AND EXISTS (
                        SELECT 1 FROM content_publishing_attempt_events e
                        WHERE e.attempt_id = OLD.attempt_id
                          AND e.revision = NEW.revision
                          AND e.code IN (
                              'rights_blocked', 'authorization_blocked',
                              'secret_store_unavailable',
                              'secret_binding_mismatch',
                              'reconciliation_mismatch'
                          )
                    )
                )
                OR (
                    NEW.state = 'operator_action_required'
                    AND EXISTS (
                        SELECT 1 FROM content_publishing_attempt_events e
                        WHERE e.attempt_id = OLD.attempt_id
                          AND e.revision = NEW.revision
                          AND e.code IN (
                              'secret_store_unavailable',
                              'secret_binding_mismatch',
                              'remote_outcome_unknown',
                              'reconciliation_mismatch'
                          )
                    )
                )
            )
            BEGIN
                SELECT RAISE(ABORT, 'attempt transition lacks required evidence code');
            END;

            CREATE TRIGGER content_publishing_attempt_session_state_requires_secret
            BEFORE UPDATE OF state ON content_publishing_attempts
            WHEN NEW.state IN ('session_ready', 'uploading')
              AND NOT EXISTS (
                  SELECT 1 FROM content_publishing_attempt_secret_refs r
                  WHERE r.attempt_id = NEW.attempt_id
                    AND r.purpose = 'resumable_upload_session'
                    AND r.status = 'active'
              )
            BEGIN
                SELECT RAISE(ABORT, 'active upload secret reference is required');
            END;

            CREATE TRIGGER content_publishing_attempt_offset_monotonic
            BEFORE UPDATE OF confirmed_offset ON content_publishing_attempts
            WHEN NEW.confirmed_offset < OLD.confirmed_offset
              OR NEW.confirmed_offset > OLD.total_bytes
            BEGIN
                SELECT RAISE(ABORT, 'invalid confirmed upload offset');
            END;

            CREATE TRIGGER content_publishing_attempt_events_append_only_update
            BEFORE UPDATE ON content_publishing_attempt_events
            BEGIN
                SELECT RAISE(ABORT, 'publishing attempt events are append-only');
            END;
            CREATE TRIGGER content_publishing_attempt_events_append_only_delete
            BEFORE DELETE ON content_publishing_attempt_events
            BEGIN
                SELECT RAISE(ABORT, 'publishing attempt events are append-only');
            END;

            CREATE TRIGGER content_publishing_attempt_event_revision_valid
            BEFORE INSERT ON content_publishing_attempt_events
            WHEN NOT EXISTS (
                SELECT 1 FROM content_publishing_attempts a
                WHERE a.attempt_id = NEW.attempt_id
                  AND (
                      (a.revision = 0 AND NEW.revision = 1
                          AND NEW.event_type = 'created'
                          AND NEW.from_state IS NULL
                          AND NEW.to_state = 'created')
                      OR (NEW.revision = a.revision + 1
                          AND NEW.from_state = a.state)
                  )
            )
            BEGIN
                SELECT RAISE(ABORT, 'invalid publishing attempt event revision');
            END;

            CREATE TRIGGER content_publishing_attempt_secret_ref_identity_immutable
            BEFORE UPDATE OF reference_id, attempt_id, purpose, schema_version,
                generation, created_at
            ON content_publishing_attempt_secret_refs
            BEGIN
                SELECT RAISE(ABORT, 'publishing secret reference identity is immutable');
            END;
            CREATE TRIGGER content_publishing_attempt_secret_ref_status_transition
            BEFORE UPDATE OF status ON content_publishing_attempt_secret_refs
            WHEN NOT (
                (OLD.status = 'pending' AND NEW.status IN (
                    'active', 'deletion_pending', 'invalidated'
                ))
                OR (OLD.status = 'active' AND NEW.status IN (
                    'deletion_pending', 'invalidated'
                ))
                OR (OLD.status = 'deletion_pending' AND NEW.status IN (
                    'deleted', 'deletion_failed'
                ))
                OR (OLD.status = 'deletion_failed' AND NEW.status IN (
                    'deletion_pending', 'invalidated'
                ))
            )
            BEGIN
                SELECT RAISE(ABORT, 'invalid publishing secret reference transition');
            END;
            CREATE TRIGGER content_publishing_attempt_secret_ref_no_delete
            BEFORE DELETE ON content_publishing_attempt_secret_refs
            BEGIN
                SELECT RAISE(ABORT, 'publishing secret references are append-only');
            END;

            CREATE TRIGGER content_publishing_attempt_operator_resolution_once
            BEFORE UPDATE OF operator_resolution_code, operator_resolved_by,
                operator_resolved_at ON content_publishing_attempts
            WHEN OLD.operator_resolution_code IS NOT NULL
              OR NEW.operator_resolution_code != 'safe_to_replace'
              OR NEW.state != 'blocked'
              OR NOT EXISTS (
                  SELECT 1 FROM content_publishing_attempt_events e
                  WHERE e.attempt_id = OLD.attempt_id
                    AND e.revision = NEW.revision
                    AND e.event_type = 'operator_resolution'
                    AND e.code = 'operator_safe_to_replace'
                    AND e.actor = NEW.operator_resolved_by
              )
            BEGIN
                SELECT RAISE(ABORT, 'operator resolution is immutable and audited');
            END;
            """
        statement_buffer = ""
        for line in schema_sql.splitlines():
            statement_buffer += f"{line}\n"
            if sqlite3.complete_statement(statement_buffer):
                statement = statement_buffer.strip()
                if statement:
                    connection.execute(statement)
                statement_buffer = ""
        if statement_buffer.strip():
            raise RuntimeError("Publishing attempt migration SQL is incomplete.")

    @staticmethod
    def _create_tables(connection: sqlite3.Connection) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS content_assets (
                asset_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                category TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                provenance_json TEXT NOT NULL,
                storage_key TEXT,
                asset_state TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_edit_projects (
                project_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                source_asset_ids_json TEXT NOT NULL,
                script TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_edit_project_sources (
                project_id TEXT NOT NULL REFERENCES content_edit_projects(project_id),
                source_asset_id TEXT NOT NULL REFERENCES content_assets(asset_id),
                source_order INTEGER NOT NULL CHECK (source_order BETWEEN 1 AND 12),
                PRIMARY KEY (project_id, source_asset_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_edit_segments (
                project_id TEXT NOT NULL REFERENCES content_edit_projects(project_id),
                timeline_order INTEGER NOT NULL CHECK (timeline_order BETWEEN 1 AND 100),
                source_asset_id TEXT NOT NULL REFERENCES content_assets(asset_id),
                source_in_seconds REAL NOT NULL CHECK (source_in_seconds >= 0),
                source_out_seconds REAL NOT NULL CHECK (source_out_seconds > source_in_seconds),
                PRIMARY KEY (project_id, timeline_order)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_edit_plan_revisions (
                project_id TEXT NOT NULL REFERENCES content_edit_projects(project_id),
                revision INTEGER NOT NULL CHECK (revision >= 1),
                plan_json TEXT NOT NULL,
                plan_sha256 TEXT NOT NULL CHECK (length(plan_sha256) = 64),
                created_at TEXT NOT NULL,
                PRIMARY KEY (project_id, revision)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS ownership_claims (
                claim_id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL REFERENCES content_assets(asset_id),
                claimant_id TEXT NOT NULL,
                rights_holder TEXT NOT NULL,
                authority_description TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS rights_evidence (
                evidence_id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL REFERENCES content_assets(asset_id),
                claim_id TEXT NOT NULL REFERENCES ownership_claims(claim_id),
                submitted_by TEXT NOT NULL,
                evidence_kind TEXT NOT NULL,
                evidence_blob BLOB NOT NULL,
                evidence_sha256 TEXT NOT NULL,
                grantor_identity TEXT NOT NULL,
                grantor_authority TEXT NOT NULL,
                claimed_terms_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS evidence_reviews (
                review_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                review_id TEXT NOT NULL UNIQUE,
                evidence_id TEXT NOT NULL REFERENCES rights_evidence(evidence_id),
                decision TEXT NOT NULL,
                reviewer_principal TEXT NOT NULL,
                evidence_sha256 TEXT NOT NULL,
                verified_terms_sha256 TEXT,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS rights_grants (
                grant_id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL REFERENCES content_assets(asset_id),
                claim_id TEXT NOT NULL REFERENCES ownership_claims(claim_id),
                evidence_id TEXT NOT NULL REFERENCES rights_evidence(evidence_id),
                grantor_identity TEXT NOT NULL,
                grantor_authority TEXT NOT NULL,
                grantee_owner_id TEXT NOT NULL,
                terms_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                agreement_id TEXT,
                agreement_version INTEGER
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS rights_grant_revocations (
                grant_id TEXT PRIMARY KEY REFERENCES rights_grants(grant_id),
                revoked_by TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS rights_decisions (
                decision_id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL REFERENCES content_assets(asset_id),
                use_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                evaluated_at TEXT NOT NULL,
                policy_version TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_packages (
                package_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_package_versions (
                package_id TEXT NOT NULL REFERENCES content_packages(package_id),
                version INTEGER NOT NULL CHECK (version > 0),
                platform TEXT NOT NULL,
                account_id TEXT NOT NULL,
                artifact_sha256 TEXT NOT NULL,
                metadata_sha256 TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                asset_uses_json TEXT NOT NULL,
                review_state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (package_id, version)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_package_assets (
                package_id TEXT NOT NULL,
                package_version INTEGER NOT NULL,
                asset_id TEXT NOT NULL REFERENCES content_assets(asset_id),
                expected_asset_sha256 TEXT NOT NULL,
                PRIMARY KEY (package_id, package_version, asset_id),
                FOREIGN KEY (package_id, package_version)
                    REFERENCES content_package_versions(package_id, version)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_review_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                package_id TEXT NOT NULL,
                package_version INTEGER NOT NULL,
                state TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (package_id, package_version)
                    REFERENCES content_package_versions(package_id, version)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_publication_states (
                package_id TEXT NOT NULL,
                package_version INTEGER NOT NULL,
                platform TEXT NOT NULL,
                account_id TEXT NOT NULL,
                state TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (package_id, package_version, platform, account_id),
                FOREIGN KEY (package_id, package_version)
                    REFERENCES content_package_versions(package_id, version)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS review_approvals (
                approval_id TEXT PRIMARY KEY,
                package_id TEXT NOT NULL,
                package_version INTEGER NOT NULL,
                artifact_sha256 TEXT NOT NULL,
                metadata_sha256 TEXT NOT NULL,
                platform TEXT NOT NULL,
                account_id TEXT NOT NULL,
                permitted_action TEXT NOT NULL,
                principal TEXT NOT NULL,
                session_id TEXT NOT NULL,
                rights_snapshot_sha256 TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                nonce TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                FOREIGN KEY (package_id, package_version)
                    REFERENCES content_package_versions(package_id, version)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS approval_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                approval_id TEXT NOT NULL REFERENCES review_approvals(approval_id),
                state TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS authorization_claims (
                grant_id TEXT PRIMARY KEY,
                principal TEXT NOT NULL,
                session_id TEXT NOT NULL,
                action TEXT NOT NULL,
                resource_fingerprint TEXT NOT NULL,
                claimed_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_commands (
                principal TEXT NOT NULL,
                action TEXT NOT NULL,
                command_id TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL,
                result_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (principal, action, command_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_audit_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_uuid TEXT NOT NULL UNIQUE,
                principal TEXT NOT NULL,
                session_id TEXT NOT NULL,
                action TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS source_intakes (
                intake_id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL UNIQUE REFERENCES content_assets(asset_id),
                owner_id TEXT NOT NULL,
                source_reference TEXT NOT NULL,
                acquisition_method TEXT NOT NULL,
                provenance_json TEXT NOT NULL,
                evidence_reference TEXT,
                claimed_source_sha256 TEXT,
                source_hash_status TEXT NOT NULL,
                access_permission_state TEXT NOT NULL,
                target_context TEXT NOT NULL,
                rights_use_json TEXT,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_lifecycle (
                intake_id TEXT PRIMARY KEY REFERENCES source_intakes(intake_id),
                asset_id TEXT NOT NULL REFERENCES content_assets(asset_id),
                asset_sha256 TEXT NOT NULL,
                target_context TEXT NOT NULL,
                state TEXT NOT NULL,
                rights_decision_id TEXT REFERENCES rights_decisions(decision_id),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_lifecycle_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                intake_id TEXT NOT NULL REFERENCES content_lifecycle(intake_id),
                from_state TEXT,
                to_state TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                rights_decision_id TEXT REFERENCES rights_decisions(decision_id),
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS grantor_identities (
                grantor_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                claimed_identity TEXT NOT NULL,
                verification_method TEXT NOT NULL,
                evidence_reference TEXT NOT NULL,
                evidence_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS grantor_identity_reviews (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                review_id TEXT NOT NULL UNIQUE,
                grantor_id TEXT NOT NULL REFERENCES grantor_identities(grantor_id),
                decision TEXT NOT NULL,
                evidence_sha256 TEXT NOT NULL,
                reviewer_principal TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS grantor_authorities (
                authority_id TEXT PRIMARY KEY,
                grantor_id TEXT NOT NULL REFERENCES grantor_identities(grantor_id),
                asset_id TEXT NOT NULL REFERENCES content_assets(asset_id),
                rights_holder TEXT NOT NULL,
                relationship TEXT NOT NULL,
                scope_description TEXT NOT NULL,
                evidence_reference TEXT NOT NULL,
                evidence_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS grantor_authority_reviews (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                review_id TEXT NOT NULL UNIQUE,
                authority_id TEXT NOT NULL REFERENCES grantor_authorities(authority_id),
                decision TEXT NOT NULL,
                evidence_sha256 TEXT NOT NULL,
                reviewer_principal TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS rights_agreements (
                agreement_id TEXT NOT NULL,
                version INTEGER NOT NULL CHECK (version > 0),
                grantor_id TEXT NOT NULL REFERENCES grantor_identities(grantor_id),
                authority_id TEXT NOT NULL REFERENCES grantor_authorities(authority_id),
                rights_holder TEXT NOT NULL,
                grantee_owner_id TEXT NOT NULL,
                asset_ids_json TEXT NOT NULL,
                terms_json TEXT NOT NULL,
                evidence_reference TEXT NOT NULL,
                evidence_sha256 TEXT NOT NULL,
                agreement_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (agreement_id, version)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS rights_agreement_reviews (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                review_id TEXT NOT NULL UNIQUE,
                agreement_id TEXT NOT NULL,
                agreement_version INTEGER NOT NULL,
                decision TEXT NOT NULL,
                agreement_sha256 TEXT NOT NULL,
                evidence_sha256 TEXT NOT NULL,
                reviewer_principal TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (agreement_id, agreement_version)
                    REFERENCES rights_agreements(agreement_id, version)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS rights_agreement_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                agreement_id TEXT NOT NULL,
                agreement_version INTEGER NOT NULL,
                status TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (agreement_id, agreement_version)
                    REFERENCES rights_agreements(agreement_id, version)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_processing_jobs (
                job_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                command_id TEXT NOT NULL,
                asset_id TEXT NOT NULL REFERENCES content_assets(asset_id),
                asset_sha256 TEXT NOT NULL,
                target_context TEXT NOT NULL,
                rights_use_json TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_processing_outputs (
                job_id TEXT PRIMARY KEY
                    REFERENCES content_processing_jobs(job_id),
                output_key TEXT NOT NULL UNIQUE,
                content_sha256 TEXT NOT NULL,
                byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_processing_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES content_processing_jobs(job_id),
                from_state TEXT,
                to_state TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_transcription_requests (
                job_id TEXT PRIMARY KEY
                    REFERENCES content_processing_jobs(job_id),
                config_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_transcripts (
                transcript_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL UNIQUE
                    REFERENCES content_processing_jobs(job_id),
                asset_id TEXT NOT NULL REFERENCES content_assets(asset_id),
                asset_sha256 TEXT NOT NULL,
                target_context TEXT NOT NULL,
                duration_seconds REAL NOT NULL CHECK (duration_seconds > 0),
                engine_id TEXT NOT NULL,
                engine_version TEXT NOT NULL,
                language TEXT,
                raw_output_sha256 TEXT NOT NULL,
                validation_version TEXT NOT NULL,
                segments_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_highlight_runs (
                run_id TEXT PRIMARY KEY,
                transcript_id TEXT NOT NULL REFERENCES content_transcripts(transcript_id),
                transcript_fingerprint TEXT NOT NULL,
                config_json TEXT NOT NULL,
                scoring_version TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_highlight_candidates (
                candidate_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES content_highlight_runs(run_id),
                transcript_id TEXT NOT NULL REFERENCES content_transcripts(transcript_id),
                asset_id TEXT NOT NULL REFERENCES content_assets(asset_id),
                asset_sha256 TEXT NOT NULL,
                start_seconds REAL NOT NULL CHECK (start_seconds >= 0),
                end_seconds REAL NOT NULL CHECK (end_seconds > start_seconds),
                duration_seconds REAL NOT NULL CHECK (duration_seconds > 0),
                rank INTEGER NOT NULL CHECK (rank > 0),
                score REAL NOT NULL CHECK (score >= 0 AND score <= 1),
                scores_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                segment_ids_json TEXT NOT NULL,
                explanation TEXT NOT NULL,
                warnings_json TEXT NOT NULL,
                scoring_version TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_clip_renders (
                render_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                command_id TEXT NOT NULL,
                candidate_id TEXT NOT NULL
                    REFERENCES content_highlight_candidates(candidate_id),
                transcript_id TEXT NOT NULL REFERENCES content_transcripts(transcript_id),
                asset_id TEXT NOT NULL REFERENCES content_assets(asset_id),
                asset_sha256 TEXT NOT NULL,
                target_context TEXT NOT NULL,
                rights_use_json TEXT NOT NULL,
                start_seconds REAL NOT NULL CHECK (start_seconds >= 0),
                end_seconds REAL NOT NULL CHECK (end_seconds > start_seconds),
                source_duration_seconds REAL,
                output_duration_seconds REAL,
                has_video INTEGER,
                has_audio INTEGER,
                state TEXT NOT NULL,
                output_key TEXT UNIQUE,
                output_sha256 TEXT,
                output_byte_size INTEGER CHECK (
                    output_byte_size IS NULL OR output_byte_size > 0
                ),
                quality_output_sha256 TEXT,
                quality_status TEXT,
                quality_black_frames INTEGER,
                quality_frozen_frames INTEGER,
                quality_black_ranges_json TEXT,
                quality_frozen_ranges_json TEXT,
                quality_revision_count INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(candidate_id, rights_use_json)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_clip_render_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                render_id TEXT NOT NULL REFERENCES content_clip_renders(render_id),
                from_state TEXT,
                to_state TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_publishing_jobs (
                job_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                package_id TEXT NOT NULL,
                package_version INTEGER NOT NULL,
                render_id TEXT NOT NULL REFERENCES content_clip_renders(render_id),
                output_sha256 TEXT NOT NULL,
                metadata_sha256 TEXT NOT NULL,
                platform TEXT NOT NULL,
                account_id TEXT NOT NULL,
                visibility TEXT NOT NULL CHECK (
                    visibility IN ('private', 'unlisted', 'public')
                ),
                intended_uses_json TEXT NOT NULL,
                synthetic_media_disclosed INTEGER NOT NULL CHECK (
                    synthetic_media_disclosed IN (0, 1)
                ),
                rights_decision_ids_json TEXT NOT NULL,
                rights_snapshot_sha256 TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL,
                release_identity TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN (
                    'awaiting_approval', 'queued', 'dispatching',
                    'mock_accepted', 'failed', 'uncertain', 'blocked', 'cancelled'
                )),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(owner_id, idempotency_key),
                FOREIGN KEY (package_id, package_version)
                    REFERENCES content_package_versions(package_id, version)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_publishing_approvals (
                approval_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES content_publishing_jobs(job_id),
                request_fingerprint TEXT NOT NULL,
                package_id TEXT NOT NULL,
                package_version INTEGER NOT NULL,
                output_sha256 TEXT NOT NULL,
                metadata_sha256 TEXT NOT NULL,
                platform TEXT NOT NULL,
                account_id TEXT NOT NULL,
                visibility TEXT NOT NULL,
                intended_uses_json TEXT NOT NULL,
                synthetic_media_disclosed INTEGER NOT NULL CHECK (
                    synthetic_media_disclosed IN (0, 1)
                ),
                rights_decision_ids_json TEXT NOT NULL,
                rights_snapshot_sha256 TEXT NOT NULL,
                request_principal TEXT NOT NULL,
                approved_by TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_publishing_approval_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                approval_id TEXT NOT NULL
                    REFERENCES content_publishing_approvals(approval_id),
                state TEXT NOT NULL CHECK (
                    state IN ('approved', 'invalidated', 'expired')
                ),
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_publishing_job_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES content_publishing_jobs(job_id),
                from_state TEXT,
                to_state TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_publishing_controls (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                kill_switch_enabled INTEGER NOT NULL CHECK (
                    kill_switch_enabled IN (0, 1)
                ),
                updated_by TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_publishing_control_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                kill_switch_enabled INTEGER NOT NULL CHECK (
                    kill_switch_enabled IN (0, 1)
                ),
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_original_script_previews (
                script_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                command_id TEXT NOT NULL UNIQUE,
                request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
                script_text TEXT NOT NULL,
                script_sha256 TEXT NOT NULL CHECK (length(script_sha256) = 64),
                references_json TEXT NOT NULL,
                audio_evidence_required INTEGER NOT NULL DEFAULT 0 CHECK (
                    audio_evidence_required IN (0, 1)
                ),
                state TEXT NOT NULL CHECK (
                    state IN ('RENDERING', 'PREVIEW_READY', 'FAILED')
                ),
                artifact_mp4 BLOB,
                artifact_sha256 TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK (
                    (state = 'PREVIEW_READY'
                        AND artifact_mp4 IS NOT NULL
                        AND artifact_sha256 IS NOT NULL)
                    OR
                    (state != 'PREVIEW_READY'
                        AND artifact_mp4 IS NULL
                        AND artifact_sha256 IS NULL)
                )
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_original_script_preview_audio_evidence (
                script_id TEXT PRIMARY KEY
                    REFERENCES content_original_script_previews(script_id),
                owner_id TEXT NOT NULL,
                evidence_id TEXT NOT NULL UNIQUE CHECK (length(evidence_id) = 64),
                algorithm_version TEXT NOT NULL,
                source_wav BLOB NOT NULL CHECK (
                    length(source_wav) BETWEEN 44 AND 2592128
                ),
                source_wav_sha256 TEXT NOT NULL
                    CHECK (length(source_wav_sha256) = 64),
                script_sha256 TEXT NOT NULL CHECK (length(script_sha256) = 64),
                artifact_sha256 TEXT NOT NULL CHECK (length(artifact_sha256) = 64),
                sample_count INTEGER NOT NULL CHECK (
                    sample_count BETWEEN 1 AND 1296000
                ),
                clipped_samples INTEGER NOT NULL CHECK (
                    clipped_samples BETWEEN 0 AND sample_count
                ),
                clipped_sample_ratio REAL NOT NULL CHECK (
                    clipped_sample_ratio BETWEEN 0 AND 0.001
                ),
                created_at TEXT NOT NULL,
                last_checked_at TEXT,
                last_verified_at TEXT,
                last_verification_state TEXT CHECK (
                    last_verification_state IS NULL
                    OR last_verification_state IN ('VERIFIED', 'INTEGRITY_FAILURE')
                ),
                last_failure_reason TEXT
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS content_original_script_previews_owner
            ON content_original_script_previews(owner_id, created_at DESC)
            """,
            """
            CREATE TABLE IF NOT EXISTS content_original_script_preview_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                script_id TEXT NOT NULL REFERENCES content_original_script_previews(script_id),
                owner_id TEXT NOT NULL,
                event_type TEXT NOT NULL CHECK (
                    event_type IN (
                        'PREVIEW_REQUESTED', 'PREVIEW_RENDERED', 'PREVIEW_FAILED'
                    )
                ),
                detail_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_original_script_preview_reviews (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                review_id TEXT NOT NULL UNIQUE,
                script_id TEXT NOT NULL REFERENCES content_original_script_previews(script_id),
                owner_id TEXT NOT NULL,
                reviewer_id TEXT NOT NULL,
                artifact_sha256 TEXT NOT NULL CHECK (length(artifact_sha256) = 64),
                command_id TEXT NOT NULL UNIQUE,
                request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
                editorial_verdict TEXT NOT NULL CHECK (
                    editorial_verdict IN (
                        'READY_FOR_FACTUAL_REVIEW', 'REVISIONS_REQUIRED', 'REJECTED'
                    )
                ),
                narration_assessment TEXT NOT NULL CHECK (
                    narration_assessment IN ('INTELLIGIBLE', 'UNINTELLIGIBLE')
                ),
                pacing_assessment TEXT NOT NULL CHECK (
                    pacing_assessment IN ('TOO_FAST', 'APPROPRIATE', 'TOO_SLOW')
                ),
                caption_assessment TEXT NOT NULL CHECK (
                    caption_assessment IN ('SYNCHRONIZED', 'NEEDS_CORRECTION')
                ),
                scene_assessment TEXT NOT NULL CHECK (
                    scene_assessment IN ('COHERENT', 'NEEDS_CORRECTION')
                ),
                factual_review_attested INTEGER NOT NULL CHECK (
                    factual_review_attested IN (0, 1)
                ),
                notes TEXT NOT NULL CHECK (length(notes) <= 1200),
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS content_original_script_preview_reviews_order
            ON content_original_script_preview_reviews(
                script_id, owner_id, sequence DESC
            )
            """,
        )
        for statement in statements:
            connection.execute(statement)
        preview_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(content_original_script_previews)"
            )
        }
        if "audio_evidence_required" not in preview_columns:
            connection.execute(
                """
                ALTER TABLE content_original_script_previews
                ADD COLUMN audio_evidence_required INTEGER NOT NULL DEFAULT 0
                CHECK (audio_evidence_required IN (0, 1))
                """
            )
            preview_events = connection.execute(
                """
                SELECT p.script_id, e.detail_json
                FROM content_original_script_previews p
                LEFT JOIN content_original_script_preview_events e
                  ON e.sequence = (
                      SELECT MAX(latest.sequence)
                      FROM content_original_script_preview_events latest
                      WHERE latest.script_id = p.script_id
                        AND latest.owner_id = p.owner_id
                        AND latest.event_type = 'PREVIEW_RENDERED'
                  )
                WHERE p.state = 'PREVIEW_READY'
                """
            ).fetchall()
            for preview_event in preview_events:
                detail_json = preview_event["detail_json"]
                requires_evidence = detail_json is None
                if detail_json is not None:
                    try:
                        details = json.loads(detail_json)
                        requires_evidence = (
                            not isinstance(details, dict)
                            or not isinstance(details.get("manifest"), dict)
                            or "audioEvidence" in details["manifest"]
                        )
                    except (json.JSONDecodeError, TypeError):
                        requires_evidence = True
                has_evidence = connection.execute(
                    """
                    SELECT 1 FROM content_original_script_preview_audio_evidence
                    WHERE script_id = ?
                    """,
                    (preview_event["script_id"],),
                ).fetchone() is not None
                if requires_evidence or has_evidence:
                    connection.execute(
                        """
                        UPDATE content_original_script_previews
                        SET audio_evidence_required = 1
                        WHERE script_id = ?
                        """,
                        (preview_event["script_id"],),
                    )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS content_publishing_active_release_unique
            ON content_publishing_jobs(release_identity)
            WHERE state IN (
                'awaiting_approval', 'queued', 'dispatching',
                'mock_accepted', 'uncertain'
            )
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO content_publishing_controls (
                singleton, kill_switch_enabled, updated_by, updated_at
            ) VALUES (1, 0, 'system:initialize', ?)
            """,
            (ContentStore._now(),),
        )

        immutable_triggers = {
            "ownership_claims_no_update": "ownership_claims",
            "ownership_claims_no_delete": "ownership_claims",
            "rights_evidence_no_update": "rights_evidence",
            "rights_evidence_no_delete": "rights_evidence",
            "rights_grants_no_update": "rights_grants",
            "rights_grants_no_delete": "rights_grants",
            "rights_grant_revocations_no_update": "rights_grant_revocations",
            "rights_grant_revocations_no_delete": "rights_grant_revocations",
            "evidence_reviews_no_update": "evidence_reviews",
            "evidence_reviews_no_delete": "evidence_reviews",
            "rights_decisions_no_update": "rights_decisions",
            "rights_decisions_no_delete": "rights_decisions",
            "content_package_versions_no_update": "content_package_versions",
            "content_package_versions_no_delete": "content_package_versions",
            "content_package_assets_no_update": "content_package_assets",
            "content_package_assets_no_delete": "content_package_assets",
            "review_approvals_no_update": "review_approvals",
            "review_approvals_no_delete": "review_approvals",
            "approval_events_no_update": "approval_events",
            "approval_events_no_delete": "approval_events",
            "content_review_events_no_update": "content_review_events",
            "content_review_events_no_delete": "content_review_events",
            "content_audit_events_no_update": "content_audit_events",
            "content_audit_events_no_delete": "content_audit_events",
            "source_intakes_no_update": "source_intakes",
            "source_intakes_no_delete": "source_intakes",
            "content_lifecycle_events_no_update": "content_lifecycle_events",
            "content_lifecycle_events_no_delete": "content_lifecycle_events",
            "grantor_identity_reviews_no_update": "grantor_identity_reviews",
            "grantor_identity_reviews_no_delete": "grantor_identity_reviews",
            "grantor_authority_reviews_no_update": "grantor_authority_reviews",
            "grantor_authority_reviews_no_delete": "grantor_authority_reviews",
            "rights_agreements_no_update": "rights_agreements",
            "rights_agreements_no_delete": "rights_agreements",
            "rights_agreement_reviews_no_update": "rights_agreement_reviews",
            "rights_agreement_reviews_no_delete": "rights_agreement_reviews",
            "rights_agreement_events_no_update": "rights_agreement_events",
            "rights_agreement_events_no_delete": "rights_agreement_events",
            "grantor_identities_no_update": "grantor_identities",
            "grantor_identities_no_delete": "grantor_identities",
            "grantor_authorities_no_update": "grantor_authorities",
            "grantor_authorities_no_delete": "grantor_authorities",
            "content_processing_outputs_no_update": "content_processing_outputs",
            "content_processing_outputs_no_delete": "content_processing_outputs",
            "content_processing_events_no_update": "content_processing_events",
            "content_processing_events_no_delete": "content_processing_events",
            "content_transcription_requests_no_update": "content_transcription_requests",
            "content_transcription_requests_no_delete": "content_transcription_requests",
            "content_transcripts_no_update": "content_transcripts",
            "content_transcripts_no_delete": "content_transcripts",
            "content_highlight_runs_no_update": "content_highlight_runs",
            "content_highlight_runs_no_delete": "content_highlight_runs",
            "content_highlight_candidates_no_update": "content_highlight_candidates",
            "content_highlight_candidates_no_delete": "content_highlight_candidates",
            "content_clip_renders_no_delete": "content_clip_renders",
            "content_clip_render_events_no_update": "content_clip_render_events",
            "content_clip_render_events_no_delete": "content_clip_render_events",
            "content_edit_plan_revisions_no_update": "content_edit_plan_revisions",
            "content_edit_plan_revisions_no_delete": "content_edit_plan_revisions",
            "content_publishing_approvals_no_update": "content_publishing_approvals",
            "content_publishing_approvals_no_delete": "content_publishing_approvals",
            "content_publishing_approval_events_no_update":
                "content_publishing_approval_events",
            "content_publishing_approval_events_no_delete":
                "content_publishing_approval_events",
            "content_publishing_job_events_no_update": "content_publishing_job_events",
            "content_publishing_job_events_no_delete": "content_publishing_job_events",
            "content_publishing_control_events_no_update":
                "content_publishing_control_events",
            "content_publishing_control_events_no_delete":
                "content_publishing_control_events",
            "content_original_script_preview_events_no_update": "content_original_script_preview_events",
            "content_original_script_preview_events_no_delete": "content_original_script_preview_events",
            "content_original_script_preview_reviews_no_update":
                "content_original_script_preview_reviews",
            "content_original_script_preview_reviews_no_delete":
                "content_original_script_preview_reviews",
            "content_original_script_preview_audio_evidence_no_delete":
                "content_original_script_preview_audio_evidence",
        }
        for trigger_name, table_name in immutable_triggers.items():
            connection.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS {trigger_name}
                BEFORE {"DELETE" if trigger_name.endswith("_no_delete") else "UPDATE"}
                ON {table_name}
                BEGIN
                    SELECT RAISE(ABORT, 'append-only record');
                END
                """
            )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS content_edit_plan_revision_sequence
            BEFORE INSERT ON content_edit_plan_revisions
            WHEN NEW.revision != COALESCE(
                (
                    SELECT MAX(revision) + 1
                    FROM content_edit_plan_revisions
                    WHERE project_id = NEW.project_id
                ),
                1
            )
            BEGIN
                SELECT RAISE(ABORT, 'creative plan revisions must be sequential');
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS content_original_script_previews_audio_evidence_requirement_immutable
            BEFORE UPDATE OF audio_evidence_required
            ON content_original_script_previews
            BEGIN
                SELECT RAISE(ABORT, 'audio evidence requirement is immutable');
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS content_original_script_preview_audio_evidence_immutable
            BEFORE UPDATE OF
                script_id, owner_id, evidence_id, algorithm_version, source_wav,
                source_wav_sha256, script_sha256, artifact_sha256, sample_count,
                clipped_samples, clipped_sample_ratio, created_at
            ON content_original_script_preview_audio_evidence
            BEGIN
                SELECT RAISE(ABORT, 'audio evidence is immutable');
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS content_lifecycle_identity_immutable
            BEFORE UPDATE OF intake_id, asset_id, asset_sha256, target_context
            ON content_lifecycle
            BEGIN
                SELECT RAISE(ABORT, 'content lifecycle identity is immutable');
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS content_asset_identity_immutable
            BEFORE UPDATE OF owner_id, content_sha256, category ON content_assets
            BEGIN
                SELECT RAISE(ABORT, 'asset identity is immutable');
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS content_clip_render_identity_immutable
            BEFORE UPDATE OF render_id, owner_id, command_id, candidate_id,
                transcript_id, asset_id, asset_sha256, target_context,
                rights_use_json, start_seconds, end_seconds, created_at
            ON content_clip_renders
            BEGIN
                SELECT RAISE(ABORT, 'clip render identity is immutable');
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS content_clip_render_state_transition
            BEFORE UPDATE OF state ON content_clip_renders
            WHEN NOT (
                (OLD.state = 'queued' AND NEW.state IN ('running', 'failed'))
                OR (
                    OLD.state = 'running'
                    AND NEW.state IN ('succeeded', 'failed', 'uncertain')
                )
            )
            BEGIN
                SELECT RAISE(ABORT, 'invalid clip render state transition');
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS content_clip_render_success_requires_output
            BEFORE UPDATE OF state ON content_clip_renders
            WHEN NEW.state = 'succeeded' AND (
                NEW.output_key IS NULL
                OR NEW.output_sha256 IS NULL
                OR NEW.output_byte_size IS NULL
                OR NEW.output_duration_seconds IS NULL
                OR NEW.source_duration_seconds IS NULL
                OR NEW.has_video IS NULL
                OR NEW.has_audio IS NULL
            )
            BEGIN
                SELECT RAISE(ABORT, 'successful clip render requires validated output');
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS content_publishing_job_identity_immutable
            BEFORE UPDATE OF job_id, owner_id, package_id, package_version,
                render_id, output_sha256, metadata_sha256, platform, account_id,
                visibility, intended_uses_json, synthetic_media_disclosed,
                rights_decision_ids_json, rights_snapshot_sha256,
                idempotency_key, request_fingerprint, release_identity, created_at
            ON content_publishing_jobs
            BEGIN
                SELECT RAISE(ABORT, 'publishing job identity is immutable');
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS content_publishing_job_state_transition
            BEFORE UPDATE OF state ON content_publishing_jobs
            WHEN NOT (
                (OLD.state = 'awaiting_approval' AND NEW.state IN (
                    'queued', 'blocked', 'cancelled'
                ))
                OR (OLD.state = 'queued' AND NEW.state IN (
                    'dispatching', 'blocked', 'cancelled'
                ))
                OR (OLD.state = 'dispatching' AND NEW.state IN (
                    'mock_accepted', 'failed', 'uncertain'
                ))
            )
            BEGIN
                SELECT RAISE(ABORT, 'invalid publishing job state transition');
            END
            """
        )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            nested = self._connection.in_transaction
            if nested:
                self._transaction_savepoint_counter += 1
                savepoint = f"content_store_sp_{self._transaction_savepoint_counter}"
                self._connection.execute(f"SAVEPOINT {savepoint}")
            else:
                savepoint = None
                self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
                if savepoint is None:
                    self._connection.commit()
                else:
                    self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            except Exception:
                if savepoint is None:
                    self._connection.rollback()
                else:
                    self._connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                raise

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _canonical(value: Any) -> str:
        if isinstance(value, BaseModel):
            value = value.model_dump(mode="json")
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _fingerprint(action: str, payload: Any) -> str:
        canonical = ContentStore._canonical({"action": action, "payload": payload})
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _require_publishing_attempt_schema(
        connection: sqlite3.Connection,
    ) -> None:
        row = connection.execute(
            "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
        ).fetchone()
        if (
            row is None
            or row["schema_version"] != ContentStore.PUBLISHING_ATTEMPT_SCHEMA_VERSION
        ):
            raise RuntimeError("Publishing attempt schema v9 is not enabled.")

    @staticmethod
    def _validate_attempt_command(command_id: str, actor: str) -> None:
        if (
            not isinstance(command_id, str)
            or not _PUBLISHING_ATTEMPT_COMMAND_ID_RE.fullmatch(command_id)
        ):
            raise ValueError("A bounded publishing attempt command ID is required.")
        if not isinstance(actor, str) or not _PUBLISHING_ATTEMPT_ACTOR_RE.fullmatch(
            actor
        ):
            raise ValueError("A bounded publishing attempt actor is required.")

    @staticmethod
    def _publishing_attempt_from_row(row: sqlite3.Row) -> PublishingAttempt:
        identity = PublishingAttemptIdentity(
            attempt_id=row["attempt_id"],
            attempt_number=row["attempt_number"],
            owner_id=row["owner_id"],
            job_id=row["job_id"],
            account_id=row["account_id"],
            provider=PublishingProvider(row["provider"]),
            release_identity=row["release_identity"],
            package_id=row["package_id"],
            package_version=row["package_version"],
            output_sha256=row["output_sha256"],
        )
        return PublishingAttempt(
            identity=identity,
            state=PublishingAttemptState(row["state"]),
            confirmed_offset=row["confirmed_offset"],
            total_bytes=row["total_bytes"],
            revision=row["revision"],
            error_code=(
                None
                if row["error_code"] is None
                else PublishingAttemptCode(row["error_code"])
            ),
            operator_resolution_code=row["operator_resolution_code"],
            operator_resolved_by=row["operator_resolved_by"],
            operator_resolved_at=(
                None
                if row["operator_resolved_at"] is None
                else datetime.fromisoformat(row["operator_resolved_at"])
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _insert_publishing_attempt_event(
        connection: sqlite3.Connection,
        *,
        attempt_id: str,
        revision: int,
        event_type: str,
        from_state: PublishingAttemptState | None,
        to_state: PublishingAttemptState,
        code: PublishingAttemptCode,
        actor: str,
        command_id: str,
        action: str,
        request_fingerprint: str,
        created_at: str,
        reference_id: str | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO content_publishing_attempt_events (
                attempt_id, revision, event_type, from_state, to_state, code,
                actor, command_id, reference_id, action, request_fingerprint,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt_id,
                revision,
                event_type,
                None if from_state is None else from_state.value,
                to_state.value,
                code.value,
                actor,
                command_id,
                reference_id,
                action,
                request_fingerprint,
                created_at,
            ),
        )

    @classmethod
    def _replayed_attempt(
        cls,
        connection: sqlite3.Connection,
        *,
        command_id: str,
        action: str,
        request_fingerprint: str,
    ) -> PublishingAttempt | None:
        event = connection.execute(
            """
            SELECT attempt_id, action, request_fingerprint
            FROM content_publishing_attempt_events
            WHERE command_id = ?
            """,
            (command_id,),
        ).fetchone()
        if event is None:
            return None
        if (
            event["action"] != action
            or event["request_fingerprint"] != request_fingerprint
        ):
            raise IdempotencyConflictError(
                "Publishing attempt command ID was reused with different content."
            )
        row = connection.execute(
            "SELECT * FROM content_publishing_attempts WHERE attempt_id = ?",
            (event["attempt_id"],),
        ).fetchone()
        if row is None:
            raise RuntimeError("Publishing attempt event has no attempt record.")
        return cls._publishing_attempt_from_row(row)

    def _create_publishing_attempt_in_transaction(
        self,
        job_id: str,
        *,
        command_id: str,
        actor: str,
        connection: sqlite3.Connection,
    ) -> PublishingAttempt:
        """Create an attempt from trusted persisted job, approval, and render data."""
        self._validate_attempt_command(command_id, actor)
        action = "content_create_publishing_attempt"
        fingerprint = self._fingerprint(action, {"job_id": job_id})
        now = self._now()
        with nullcontext(connection) as connection:
            self._require_publishing_attempt_schema(connection)
            replay = self._replayed_attempt(
                connection,
                command_id=command_id,
                action=action,
                request_fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            job = connection.execute(
                """
                SELECT j.*, p.owner_id AS package_owner,
                    v.platform AS package_platform, v.account_id AS package_account,
                    r.owner_id AS render_owner, r.state AS render_state,
                    r.output_sha256 AS render_sha256,
                    r.output_byte_size AS render_bytes
                FROM content_publishing_jobs j
                JOIN content_packages p ON p.package_id = j.package_id
                JOIN content_package_versions v
                    ON v.package_id = j.package_id
                    AND v.version = j.package_version
                JOIN content_clip_renders r ON r.render_id = j.render_id
                WHERE j.job_id = ?
                """,
                (job_id,),
            ).fetchone()
            if job is None:
                raise ValueError("Publishing job does not exist.")
            if (
                job["state"] != PublishingJobState.QUEUED.value
                or job["platform"] != ContentPlatform.YOUTUBE.value
                or job["package_platform"] != job["platform"]
                or job["package_account"] != job["account_id"]
                or job["package_owner"] != job["owner_id"]
                or job["render_owner"] != job["owner_id"]
                or job["render_state"] != ProcessingJobState.SUCCEEDED.value
                or job["render_sha256"] != job["output_sha256"]
                or not isinstance(job["render_bytes"], int)
                or job["render_bytes"] <= 0
            ):
                raise PublishingAttemptConflictError(
                    "Publishing job is not bound to a valid completed release."
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
                (job_id,),
            ).fetchone()
            if (
                approval is None
                or approval["latest_state"] != PublishingApprovalState.APPROVED.value
                or datetime.fromisoformat(approval["expires_at"]) <= datetime.now(UTC)
                or approval["approved_by"] == approval["request_principal"]
                or approval["request_fingerprint"] != job["request_fingerprint"]
                or approval["package_id"] != job["package_id"]
                or approval["package_version"] != job["package_version"]
                or approval["output_sha256"] != job["output_sha256"]
                or approval["platform"] != job["platform"]
                or approval["account_id"] != job["account_id"]
            ):
                raise PublishingAttemptConflictError(
                    "A current independent release approval is required."
                )
            unresolved = connection.execute(
                """
                SELECT 1 FROM content_publishing_attempts
                WHERE job_id = ?
                  AND state NOT IN ('published', 'available_private')
                  AND NOT (
                      state = 'failed'
                      AND error_code = 'provider_rejected'
                  )
                  AND NOT (
                      state = 'blocked'
                      AND operator_resolution_code = 'safe_to_replace'
                  )
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            if unresolved is not None:
                raise PublishingAttemptConflictError(
                    "An unresolved publishing attempt blocks replacement."
                )
            previous = connection.execute(
                """
                SELECT COALESCE(MAX(attempt_number), 0) AS attempt_number
                FROM content_publishing_attempts WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()["attempt_number"]
            attempt_id = str(uuid4())
            attempt_number = previous + 1
            try:
                identity = PublishingAttemptIdentity(
                    attempt_id=attempt_id,
                    attempt_number=attempt_number,
                    owner_id=job["owner_id"],
                    job_id=job["job_id"],
                    account_id=job["account_id"],
                    provider=PublishingProvider(job["platform"]),
                    release_identity=job["release_identity"],
                    package_id=job["package_id"],
                    package_version=job["package_version"],
                    output_sha256=job["output_sha256"],
                )
            except (ValueError, TypeError):
                raise PublishingAttemptConflictError(
                    "Publishing job identity is invalid for a durable attempt."
                ) from None
            connection.execute(
                """
                INSERT INTO content_publishing_attempts (
                    attempt_id, job_id, attempt_number, owner_id, account_id,
                    provider, release_identity, package_id, package_version,
                    output_sha256, state, confirmed_offset, total_bytes,
                    error_code, revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, NULL, 0, ?, ?)
                """,
                (
                    identity.attempt_id,
                    identity.job_id,
                    identity.attempt_number,
                    identity.owner_id,
                    identity.account_id,
                    identity.provider.value,
                    identity.release_identity,
                    identity.package_id,
                    identity.package_version,
                    identity.output_sha256,
                    PublishingAttemptState.CREATED.value,
                    job["render_bytes"],
                    now,
                    now,
                ),
            )
            self._insert_publishing_attempt_event(
                connection,
                attempt_id=attempt_id,
                revision=1,
                event_type="created",
                from_state=None,
                to_state=PublishingAttemptState.CREATED,
                code=PublishingAttemptCode.ATTEMPT_CREATED,
                actor=actor,
                command_id=command_id,
                action=action,
                request_fingerprint=fingerprint,
                created_at=now,
            )
            connection.execute(
                "UPDATE content_publishing_attempts SET revision = 1 WHERE attempt_id = ?",
                (attempt_id,),
            )
            row = connection.execute(
                "SELECT * FROM content_publishing_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            return self._publishing_attempt_from_row(row)

    def get_publishing_attempt(self, attempt_id: str) -> PublishingAttempt | None:
        with self._lock:
            self._require_publishing_attempt_schema(self._connection)
            row = self._connection.execute(
                "SELECT * FROM content_publishing_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
        return None if row is None else self._publishing_attempt_from_row(row)

    def get_latest_publishing_attempt(
        self,
        job_id: str,
    ) -> PublishingAttempt | None:
        with self._lock:
            self._require_publishing_attempt_schema(self._connection)
            row = self._connection.execute(
                """
                SELECT * FROM content_publishing_attempts
                WHERE job_id = ?
                ORDER BY attempt_number DESC LIMIT 1
                """,
                (job_id,),
            ).fetchone()
        return None if row is None else self._publishing_attempt_from_row(row)

    def get_active_upload_session_handle(
        self,
        attempt_id: str,
    ) -> UploadSessionSecretHandle | None:
        with self._lock:
            self._require_publishing_attempt_schema(self._connection)
            attempt_row = self._connection.execute(
                "SELECT * FROM content_publishing_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if attempt_row is None:
                return None
            reference_row = self._connection.execute(
                """
                SELECT * FROM content_publishing_attempt_secret_refs
                WHERE attempt_id = ? AND purpose = ? AND status = 'active'
                """,
                (attempt_id, UploadSecretPurpose.RESUMABLE_UPLOAD_SESSION.value),
            ).fetchone()
        if reference_row is None:
            return None
        attempt = self._publishing_attempt_from_row(attempt_row)
        reference = self._publishing_attempt_secret_ref_from_row(reference_row)
        binding = UploadSecretBinding(
            attempt=attempt.identity,
            purpose=reference.purpose,
            schema_version=reference.reference.schema_version,
        )
        return UploadSessionSecretHandle(
            binding=binding,
            reference=reference.reference,
            generation=reference.generation,
        )

    def list_publishing_attempt_events(
        self,
        attempt_id: str,
    ) -> list[PublishingAttemptEvent]:
        with self._lock:
            self._require_publishing_attempt_schema(self._connection)
            rows = self._connection.execute(
                """
                SELECT * FROM content_publishing_attempt_events
                WHERE attempt_id = ? ORDER BY sequence
                """,
                (attempt_id,),
            ).fetchall()
        return [
            PublishingAttemptEvent(
                sequence=row["sequence"],
                attempt_id=row["attempt_id"],
                revision=row["revision"],
                event_type=row["event_type"],
                from_state=(
                    None
                    if row["from_state"] is None
                    else PublishingAttemptState(row["from_state"])
                ),
                to_state=PublishingAttemptState(row["to_state"]),
                code=PublishingAttemptCode(row["code"]),
                actor=row["actor"],
                command_id=row["command_id"],
                reference_id=row["reference_id"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    @staticmethod
    def _publishing_attempt_secret_ref_from_row(
        row: sqlite3.Row,
    ) -> PublishingAttemptSecretReference:
        return PublishingAttemptSecretReference(
            attempt_id=row["attempt_id"],
            purpose=UploadSecretPurpose(row["purpose"]),
            reference=OpaqueSecretReference(
                reference_id=row["reference_id"],
                schema_version=row["schema_version"],
            ),
            generation=SecretGeneration(value=row["generation"]),
            status=PublishingAttemptSecretState(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _transition_publishing_attempt(
        self,
        attempt_id: str,
        *,
        expected_revision: int,
        to_state: PublishingAttemptState,
        code: PublishingAttemptCode,
        command_id: str,
        actor: str,
        connection: sqlite3.Connection | None = None,
    ) -> PublishingAttempt:
        self._validate_attempt_command(command_id, actor)
        action = "content_transition_publishing_attempt"
        fingerprint = self._fingerprint(
            action,
            {
                "attempt_id": attempt_id,
                "expected_revision": expected_revision,
                "to_state": to_state.value,
                "code": code.value,
            },
        )
        now = self._now()
        transaction = (
            self._transaction() if connection is None else nullcontext(connection)
        )
        with transaction as connection:
            self._require_publishing_attempt_schema(connection)
            replay = self._replayed_attempt(
                connection,
                command_id=command_id,
                action=action,
                request_fingerprint=fingerprint,
            )
            if replay is not None:
                if replay.identity.attempt_id != attempt_id:
                    raise IdempotencyConflictError(
                        "Publishing attempt command ID was reused with different content."
                    )
                return replay
            current_row = connection.execute(
                "SELECT * FROM content_publishing_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if current_row is None:
                raise ValueError("Publishing attempt does not exist.")
            current = self._publishing_attempt_from_row(current_row)
            if current.revision != expected_revision:
                raise PublishingAttemptConflictError(
                    "Publishing attempt changed since it was read."
                )
            if to_state not in _PUBLISHING_ATTEMPT_TRANSITIONS.get(current.state, set()):
                raise PublishingAttemptConflictError(
                    "Publishing attempt transition is not permitted."
                )
            if to_state == PublishingAttemptState.FAILED and (
                code not in _PUBLISHING_ATTEMPT_CONFIRMED_FAILURE_CODES
            ):
                raise PublishingAttemptConflictError(
                    "FAILED requires a confirmed provider or processing failure."
                )
            if to_state == PublishingAttemptState.FAILED:
                safe_failure_source = {
                    PublishingAttemptCode.PROVIDER_REJECTED:
                        PublishingAttemptState.INITIATION_INTENT,
                }
                expected_source = safe_failure_source.get(code)
                if expected_source is None or current.state != expected_source:
                    raise PublishingAttemptConflictError(
                        "Failure evidence does not establish a safe terminal outcome."
                    )
            if (
                code == PublishingAttemptCode.PROCESSING_FAILED_CONFIRMED
                and to_state != PublishingAttemptState.RECONCILIATION_REQUIRED
            ):
                raise PublishingAttemptConflictError(
                    "Processing failure requires remote-outcome reconciliation."
                )
            required_codes = {
                PublishingAttemptState.INITIATION_INTENT: {
                    PublishingAttemptCode.INITIATION_AUTHORIZED
                },
                PublishingAttemptState.SESSION_READY: {
                    PublishingAttemptCode.SESSION_STORED
                },
                PublishingAttemptState.UPLOADING: {
                    PublishingAttemptCode.UPLOAD_STARTED
                },
                PublishingAttemptState.INITIATION_UNCERTAIN: {
                    PublishingAttemptCode.INITIATION_OUTCOME_UNKNOWN,
                    PublishingAttemptCode.REMOTE_OUTCOME_UNKNOWN,
                },
                PublishingAttemptState.RECONCILIATION_REQUIRED: {
                    PublishingAttemptCode.RECONCILIATION_STARTED,
                    PublishingAttemptCode.RECONCILIATION_MISMATCH,
                    PublishingAttemptCode.REMOTE_OUTCOME_UNKNOWN,
                },
                PublishingAttemptState.REMOTE_UPLOADED: {
                    PublishingAttemptCode.REMOTE_UPLOAD_CONFIRMED
                },
                PublishingAttemptState.PROCESSING: {
                    PublishingAttemptCode.PROCESSING_STARTED
                },
                PublishingAttemptState.PUBLISHED: {
                    PublishingAttemptCode.PUBLICATION_CONFIRMED
                },
                PublishingAttemptState.AVAILABLE_PRIVATE: {
                    PublishingAttemptCode.PRIVATE_AVAILABILITY_CONFIRMED
                },
                PublishingAttemptState.SESSION_EXPIRED: {
                    PublishingAttemptCode.SESSION_EXPIRED_CONFIRMED
                },
                PublishingAttemptState.BLOCKED: {
                    PublishingAttemptCode.RIGHTS_BLOCKED,
                    PublishingAttemptCode.AUTHORIZATION_BLOCKED,
                    PublishingAttemptCode.SECRET_STORE_UNAVAILABLE,
                    PublishingAttemptCode.SECRET_BINDING_MISMATCH,
                    PublishingAttemptCode.RECONCILIATION_MISMATCH,
                },
                PublishingAttemptState.OPERATOR_ACTION_REQUIRED: {
                    PublishingAttemptCode.SECRET_STORE_UNAVAILABLE,
                    PublishingAttemptCode.SECRET_BINDING_MISMATCH,
                    PublishingAttemptCode.REMOTE_OUTCOME_UNKNOWN,
                    PublishingAttemptCode.RECONCILIATION_MISMATCH,
                },
            }
            accepted = required_codes.get(to_state)
            if accepted is not None and code not in accepted:
                raise PublishingAttemptConflictError(
                    "Publishing attempt transition requires its matching evidence code."
                )
            if to_state in {
                PublishingAttemptState.SESSION_READY,
                PublishingAttemptState.UPLOADING,
            }:
                active = connection.execute(
                    """
                    SELECT 1 FROM content_publishing_attempt_secret_refs
                    WHERE attempt_id = ? AND purpose = ?
                      AND status = 'active'
                    """,
                    (attempt_id, UploadSecretPurpose.RESUMABLE_UPLOAD_SESSION.value),
                ).fetchone()
                if active is None:
                    raise PublishingAttemptConflictError(
                        "An active bound upload-session reference is required."
                    )
            error_code = (
                code.value
                if to_state
                in {
                    PublishingAttemptState.FAILED,
                    PublishingAttemptState.BLOCKED,
                    PublishingAttemptState.OPERATOR_ACTION_REQUIRED,
                    PublishingAttemptState.SESSION_EXPIRED,
                    PublishingAttemptState.INITIATION_UNCERTAIN,
                    PublishingAttemptState.RECONCILIATION_REQUIRED,
                }
                else None
            )
            self._insert_publishing_attempt_event(
                connection,
                attempt_id=attempt_id,
                revision=current.revision + 1,
                event_type="transition",
                from_state=current.state,
                to_state=to_state,
                code=code,
                actor=actor,
                command_id=command_id,
                action=action,
                request_fingerprint=fingerprint,
                created_at=now,
            )
            try:
                cursor = connection.execute(
                    """
                    UPDATE content_publishing_attempts
                    SET state = ?, error_code = ?, revision = revision + 1,
                        updated_at = ?
                    WHERE attempt_id = ? AND revision = ?
                    """,
                    (
                        to_state.value,
                        error_code,
                        now,
                        attempt_id,
                        expected_revision,
                    ),
                )
            except sqlite3.IntegrityError:
                raise PublishingAttemptConflictError(
                    "Publishing attempt transition violated a persistence invariant."
                ) from None
            if cursor.rowcount != 1:
                raise PublishingAttemptConflictError(
                    "Publishing attempt changed since it was read."
                )
            updated = connection.execute(
                "SELECT * FROM content_publishing_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            return self._publishing_attempt_from_row(updated)

    def _record_server_confirmed_publishing_offset(
        self,
        attempt_id: str,
        *,
        expected_revision: int,
        confirmed_offset: int,
        command_id: str,
        actor: str,
    ) -> PublishingAttempt:
        self._validate_attempt_command(command_id, actor)
        if isinstance(confirmed_offset, bool) or confirmed_offset < 0:
            raise ValueError("Server-confirmed offset must not be negative.")
        action = "content_record_confirmed_publishing_offset"
        fingerprint = self._fingerprint(
            action,
            {
                "attempt_id": attempt_id,
                "expected_revision": expected_revision,
                "confirmed_offset": confirmed_offset,
            },
        )
        now = self._now()
        with self._transaction() as connection:
            self._require_publishing_attempt_schema(connection)
            replay = self._replayed_attempt(
                connection,
                command_id=command_id,
                action=action,
                request_fingerprint=fingerprint,
            )
            if replay is not None:
                if replay.identity.attempt_id != attempt_id:
                    raise IdempotencyConflictError(
                        "Publishing attempt command ID was reused with different content."
                    )
                return replay
            row = connection.execute(
                "SELECT * FROM content_publishing_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Publishing attempt does not exist.")
            current = self._publishing_attempt_from_row(row)
            if current.revision != expected_revision:
                raise PublishingAttemptConflictError(
                    "Publishing attempt changed since it was read."
                )
            if current.state != PublishingAttemptState.UPLOADING:
                raise PublishingAttemptConflictError(
                    "Confirmed byte progress is valid only while uploading."
                )
            if not current.confirmed_offset <= confirmed_offset <= current.total_bytes:
                raise PublishingAttemptConflictError(
                    "Server-confirmed offset is outside the remaining content range."
                )
            self._insert_publishing_attempt_event(
                connection,
                attempt_id=attempt_id,
                revision=current.revision + 1,
                event_type="offset_confirmed",
                from_state=current.state,
                to_state=current.state,
                code=PublishingAttemptCode.OFFSET_CONFIRMED,
                actor=actor,
                command_id=command_id,
                action=action,
                request_fingerprint=fingerprint,
                created_at=now,
            )
            try:
                cursor = connection.execute(
                    """
                    UPDATE content_publishing_attempts
                    SET confirmed_offset = ?, revision = revision + 1,
                        updated_at = ?
                    WHERE attempt_id = ? AND revision = ?
                    """,
                    (confirmed_offset, now, attempt_id, expected_revision),
                )
            except sqlite3.IntegrityError:
                raise PublishingAttemptConflictError(
                    "Confirmed offset violated a persistence invariant."
                ) from None
            if cursor.rowcount != 1:
                raise PublishingAttemptConflictError(
                    "Publishing attempt changed since it was read."
                )
            updated = connection.execute(
                "SELECT * FROM content_publishing_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            return self._publishing_attempt_from_row(updated)

    def _prepare_publishing_attempt_secret_reference(
        self,
        attempt_id: str,
        *,
        expected_revision: int,
        command_id: str,
        actor: str,
    ) -> PublishingAttemptSecretReference:
        self._validate_attempt_command(command_id, actor)
        action = "content_prepare_publishing_attempt_secret_reference"
        fingerprint = self._fingerprint(
            action,
            {
                "attempt_id": attempt_id,
                "expected_revision": expected_revision,
                "purpose": UploadSecretPurpose.RESUMABLE_UPLOAD_SESSION.value,
                "schema_version": 1,
            },
        )
        now = self._now()
        with self._transaction() as connection:
            self._require_publishing_attempt_schema(connection)
            replay = connection.execute(
                """
                SELECT attempt_id, action, request_fingerprint, reference_id
                FROM content_publishing_attempt_events WHERE command_id = ?
                """,
                (command_id,),
            ).fetchone()
            if replay is not None:
                if replay["action"] != action or replay["request_fingerprint"] != fingerprint:
                    raise IdempotencyConflictError(
                        "Publishing attempt command ID was reused with different content."
                    )
                ref_row = connection.execute(
                    """
                    SELECT * FROM content_publishing_attempt_secret_refs
                    WHERE reference_id = ?
                    """,
                    (replay["reference_id"],),
                ).fetchone()
                if ref_row is None:
                    raise RuntimeError("Publishing secret reference event is inconsistent.")
                return self._publishing_attempt_secret_ref_from_row(ref_row)
            attempt_row = connection.execute(
                "SELECT * FROM content_publishing_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if attempt_row is None:
                raise ValueError("Publishing attempt does not exist.")
            attempt = self._publishing_attempt_from_row(attempt_row)
            if attempt.revision != expected_revision:
                raise PublishingAttemptConflictError(
                    "Publishing attempt changed since it was read."
                )
            if attempt.state not in {
                PublishingAttemptState.INITIATION_INTENT,
                PublishingAttemptState.RECONCILIATION_REQUIRED,
                PublishingAttemptState.OPERATOR_ACTION_REQUIRED,
            }:
                raise PublishingAttemptConflictError(
                    "Secret references can be prepared only before session use."
                )
            current_ref = connection.execute(
                """
                SELECT 1 FROM content_publishing_attempt_secret_refs
                WHERE attempt_id = ? AND purpose = ?
                  AND status IN (
                      'pending', 'active', 'deletion_pending', 'deletion_failed'
                  )
                """,
                (attempt_id, UploadSecretPurpose.RESUMABLE_UPLOAD_SESSION.value),
            ).fetchone()
            if current_ref is not None:
                raise PublishingAttemptConflictError(
                    "An unresolved upload-session reference already exists."
                )
            generation_row = connection.execute(
                """
                SELECT COALESCE(MAX(generation), 0) AS generation
                FROM content_publishing_attempt_secret_refs
                WHERE attempt_id = ? AND purpose = ?
                """,
                (attempt_id, UploadSecretPurpose.RESUMABLE_UPLOAD_SESSION.value),
            ).fetchone()
            generation = generation_row["generation"] + 1
            reference_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO content_publishing_attempt_secret_refs (
                    reference_id, attempt_id, purpose, schema_version,
                    generation, status, created_at, updated_at
                ) VALUES (?, ?, ?, 1, ?, 'pending', ?, ?)
                """,
                (
                    reference_id,
                    attempt_id,
                    UploadSecretPurpose.RESUMABLE_UPLOAD_SESSION.value,
                    generation,
                    now,
                    now,
                ),
            )
            self._insert_publishing_attempt_event(
                connection,
                attempt_id=attempt_id,
                revision=attempt.revision + 1,
                event_type="secret_reference_prepared",
                from_state=attempt.state,
                to_state=attempt.state,
                code=PublishingAttemptCode.SECRET_REFERENCE_PREPARED,
                actor=actor,
                command_id=command_id,
                action=action,
                request_fingerprint=fingerprint,
                created_at=now,
                reference_id=reference_id,
            )
            connection.execute(
                """
                UPDATE content_publishing_attempts
                SET revision = revision + 1, updated_at = ?
                WHERE attempt_id = ? AND revision = ?
                """,
                (now, attempt_id, expected_revision),
            )
            ref_row = connection.execute(
                """
                SELECT * FROM content_publishing_attempt_secret_refs
                WHERE reference_id = ?
                """,
                (reference_id,),
            ).fetchone()
            return self._publishing_attempt_secret_ref_from_row(ref_row)

    def _update_publishing_attempt_secret_reference(
        self,
        attempt_id: str,
        reference_id: str,
        *,
        expected_revision: int,
        new_status: PublishingAttemptSecretState,
        command_id: str,
        actor: str,
    ) -> PublishingAttemptSecretReference:
        """Record confirmed secret-backend work; never accept a secret value."""
        self._validate_attempt_command(command_id, actor)
        action = "content_update_publishing_attempt_secret_reference"
        fingerprint = self._fingerprint(
            action,
            {
                "attempt_id": attempt_id,
                "reference_id": reference_id,
                "expected_revision": expected_revision,
                "new_status": new_status.value,
            },
        )
        now = self._now()
        allowed_statuses = {
            PublishingAttemptSecretState.PENDING: {
                PublishingAttemptSecretState.ACTIVE,
                PublishingAttemptSecretState.DELETION_PENDING,
                PublishingAttemptSecretState.INVALIDATED,
            },
            PublishingAttemptSecretState.ACTIVE: {
                PublishingAttemptSecretState.DELETION_PENDING,
                PublishingAttemptSecretState.INVALIDATED,
            },
            PublishingAttemptSecretState.DELETION_PENDING: {
                PublishingAttemptSecretState.DELETED,
                PublishingAttemptSecretState.DELETION_FAILED,
            },
            PublishingAttemptSecretState.DELETION_FAILED: {
                PublishingAttemptSecretState.DELETION_PENDING,
                PublishingAttemptSecretState.INVALIDATED,
            },
        }
        with self._transaction() as connection:
            self._require_publishing_attempt_schema(connection)
            replay = connection.execute(
                """
                SELECT attempt_id, action, request_fingerprint, reference_id
                FROM content_publishing_attempt_events WHERE command_id = ?
                """,
                (command_id,),
            ).fetchone()
            if replay is not None:
                if (
                    replay["action"] != action
                    or replay["request_fingerprint"] != fingerprint
                    or replay["reference_id"] != reference_id
                ):
                    raise IdempotencyConflictError(
                        "Publishing attempt command ID was reused with different content."
                    )
                row = connection.execute(
                    """
                    SELECT * FROM content_publishing_attempt_secret_refs
                    WHERE reference_id = ?
                    """,
                    (reference_id,),
                ).fetchone()
                if row is None:
                    raise RuntimeError("Publishing secret reference event is inconsistent.")
                return self._publishing_attempt_secret_ref_from_row(row)
            attempt_row = connection.execute(
                "SELECT * FROM content_publishing_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            ref_row = connection.execute(
                """
                SELECT * FROM content_publishing_attempt_secret_refs
                WHERE reference_id = ? AND attempt_id = ?
                """,
                (reference_id, attempt_id),
            ).fetchone()
            if attempt_row is None or ref_row is None:
                raise PublishingAttemptConflictError(
                    "Publishing attempt secret reference binding does not match."
                )
            attempt = self._publishing_attempt_from_row(attempt_row)
            old_status = PublishingAttemptSecretState(ref_row["status"])
            if attempt.revision != expected_revision:
                raise PublishingAttemptConflictError(
                    "Publishing attempt changed since it was read."
                )
            if new_status not in allowed_statuses.get(old_status, set()):
                raise PublishingAttemptConflictError(
                    "Publishing secret reference status transition is not permitted."
                )
            event_code = (
                PublishingAttemptCode.SECRET_REFERENCE_ACTIVATED
                if new_status == PublishingAttemptSecretState.ACTIVE
                else (
                    PublishingAttemptCode.SECRET_DELETION_PENDING
                    if new_status
                    in {
                        PublishingAttemptSecretState.DELETION_PENDING,
                        PublishingAttemptSecretState.DELETION_FAILED,
                    }
                    else PublishingAttemptCode.SECRET_INVALIDATED
                )
            )
            event_type = (
                "secret_reference_activated"
                if new_status == PublishingAttemptSecretState.ACTIVE
                else "secret_reference_status_changed"
            )
            self._insert_publishing_attempt_event(
                connection,
                attempt_id=attempt_id,
                revision=attempt.revision + 1,
                event_type=event_type,
                from_state=attempt.state,
                to_state=attempt.state,
                code=event_code,
                actor=actor,
                command_id=command_id,
                action=action,
                request_fingerprint=fingerprint,
                created_at=now,
                reference_id=reference_id,
            )
            try:
                connection.execute(
                    """
                    UPDATE content_publishing_attempt_secret_refs
                    SET status = ?, updated_at = ?
                    WHERE reference_id = ? AND attempt_id = ? AND status = ?
                    """,
                    (new_status.value, now, reference_id, attempt_id, old_status.value),
                )
                connection.execute(
                    """
                    UPDATE content_publishing_attempts
                    SET revision = revision + 1, updated_at = ?
                    WHERE attempt_id = ? AND revision = ?
                    """,
                    (now, attempt_id, expected_revision),
                )
            except sqlite3.IntegrityError:
                raise PublishingAttemptConflictError(
                    "Publishing secret reference update violated a persistence invariant."
                ) from None
            updated_ref = connection.execute(
                """
                SELECT * FROM content_publishing_attempt_secret_refs
                WHERE reference_id = ?
                """,
                (reference_id,),
            ).fetchone()
            return self._publishing_attempt_secret_ref_from_row(updated_ref)

    def _resolve_blocked_publishing_attempt_for_safe_replacement(
        self,
        attempt_id: str,
        *,
        expected_revision: int,
        reason: str,
        command_id: str,
        authorization: ValidatedAuthorization,
    ) -> PublishingAttempt:
        if type(authorization) is not ValidatedAuthorization:
            raise PermissionError("A validated operator authorization is required.")
        context = authorization.context
        action = "content_resolve_publishing_attempt_safe_to_replace"
        reason = reason.strip()
        if not reason or len(reason) > 1000:
            raise ValueError("A bounded operator recovery reason is required.")
        payload = {
            "attempt_id": attempt_id,
            "expected_revision": expected_revision,
            "resolution_code": "safe_to_replace",
            "reason": reason,
        }
        if (
            context.action != action
            or context.resource_fingerprint != resource_fingerprint(action, payload)
        ):
            raise PermissionError("Operator authorization is not bound to this resolution.")

        def operation(
            connection: sqlite3.Connection,
        ) -> tuple[str, str, dict[str, Any]]:
            self._require_publishing_attempt_schema(connection)
            row = connection.execute(
                "SELECT * FROM content_publishing_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Publishing attempt does not exist.")
            current = self._publishing_attempt_from_row(row)
            if (
                current.revision != expected_revision
                or current.state != PublishingAttemptState.BLOCKED
                or current.operator_resolution_code is not None
            ):
                raise PublishingAttemptConflictError(
                    "Only an unresolved blocked attempt can be resolved."
                )
            now = self._now()
            self._validate_attempt_command(command_id, context.principal)
            fingerprint = self._fingerprint(action, payload)
            self._insert_publishing_attempt_event(
                connection,
                attempt_id=attempt_id,
                revision=current.revision + 1,
                event_type="operator_resolution",
                from_state=current.state,
                to_state=current.state,
                code=PublishingAttemptCode.OPERATOR_SAFE_TO_REPLACE,
                actor=context.principal,
                command_id=command_id,
                action=action,
                request_fingerprint=fingerprint,
                created_at=now,
            )
            try:
                cursor = connection.execute(
                    """
                    UPDATE content_publishing_attempts
                    SET operator_resolution_code = 'safe_to_replace',
                        operator_resolved_by = ?, operator_resolved_at = ?,
                        revision = revision + 1, updated_at = ?
                    WHERE attempt_id = ? AND revision = ?
                    """,
                    (
                        context.principal,
                        now,
                        now,
                        attempt_id,
                        expected_revision,
                    ),
                )
            except sqlite3.IntegrityError:
                raise PublishingAttemptConflictError(
                    "Operator resolution violated a persistence invariant."
                ) from None
            if cursor.rowcount != 1:
                raise PublishingAttemptConflictError(
                    "Publishing attempt changed since it was read."
                )
            return (
                attempt_id,
                "operator_safe_to_replace",
                {
                    "attempt_id": attempt_id,
                    "resolution_code": "safe_to_replace",
                    "reason": reason,
                },
            )

        result_id = self.run_authorized(
            authorization=authorization,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=attempt_id,
            operation=operation,
        )
        attempt = self.get_publishing_attempt(result_id)
        if attempt is None:
            raise RuntimeError("Resolved publishing attempt could not be reloaded.")
        return attempt

    def run_authorized(
        self,
        *,
        authorization: ValidatedAuthorization,
        action: str,
        payload: dict[str, Any],
        command_id: str,
        subject_id: str,
        operation: Callable[[sqlite3.Connection], tuple[str, str, dict[str, Any]]],
    ) -> str:
        if (
            type(authorization) is not ValidatedAuthorization
            or not isinstance(authorization._authority, WebSocketAuthenticator)
            or not self._has_trusted_authority(authorization._authority)
            or not authorization.is_valid_for(action=action, parameters=payload)
        ):
            raise PermissionError("A valid bound authorization capability is required.")
        context = authorization.context
        if not command_id.strip() or len(command_id) > 200:
            raise ValueError("A bounded idempotency command ID is required.")
        fingerprint = self._fingerprint(action, payload)
        now = self._now()
        with self._transaction() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO authorization_claims (
                        grant_id, principal, session_id, action,
                        resource_fingerprint, claimed_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        context.grant_id,
                        context.principal,
                        context.session_id,
                        context.action,
                        context.resource_fingerprint,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise PermissionError("Authorization grant was already consumed.") from exc

            existing = connection.execute(
                """
                SELECT request_fingerprint, result_id
                FROM content_commands
                WHERE principal = ? AND action = ? AND command_id = ?
                """,
                (context.principal, action, command_id),
            ).fetchone()
            if existing is not None:
                if existing["request_fingerprint"] != fingerprint:
                    raise IdempotencyConflictError(
                        "Idempotency key was reused with different content."
                    )
                result_id = existing["result_id"]
                self._insert_audit(
                    connection,
                    context,
                    action,
                    subject_id or result_id,
                    "idempotent_replay",
                    fingerprint,
                    {"command_id": command_id, "result_id": result_id},
                    now,
                )
                return result_id

            result_id, decision, details = operation(connection)
            connection.execute(
                """
                INSERT INTO content_commands (
                    principal, action, command_id, request_fingerprint,
                    result_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    context.principal,
                    action,
                    command_id,
                    fingerprint,
                    result_id,
                    now,
                ),
            )
            self._insert_audit(
                connection,
                context,
                action,
                subject_id or result_id,
                decision,
                fingerprint,
                details,
                now,
            )
            return result_id

    @staticmethod
    def _insert_audit(
        connection: sqlite3.Connection,
        context: AuthorizationContext,
        action: str,
        subject_id: str,
        decision: str,
        fingerprint: str,
        details: dict[str, Any],
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO content_audit_events (
                event_uuid, principal, session_id, action, subject_id, decision,
                request_fingerprint, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{context.grant_id}:{action}",
                context.principal,
                context.session_id,
                action,
                subject_id,
                decision,
                fingerprint,
                ContentStore._canonical(details),
                now,
            ),
        )

    def get_asset(self, asset_id: str) -> SourceAsset | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM content_assets WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()
        return None if row is None else self._load_asset(row)

    def create_edit_project_in_transaction(
        self,
        connection: sqlite3.Connection,
        project: ContentEditProject,
    ) -> ContentEditProject:
        if not isinstance(project, ContentEditProject):
            raise TypeError("A supported CRE edit project is required.")
        for asset_id in project.source_asset_ids:
            row = connection.execute(
                "SELECT owner_id FROM content_assets WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()
            if row is None or row["owner_id"] != project.owner_id:
                raise PermissionError("Every CRE project source must belong to the project owner.")
        connection.execute(
            "INSERT INTO content_edit_projects "
            "(project_id, owner_id, source_asset_ids_json, script, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                project.project_id,
                project.owner_id,
                json.dumps(list(project.source_asset_ids), ensure_ascii=False, separators=(",", ":")),
                project.script,
                project.created_at.isoformat(),
            ),
        )
        for order, asset_id in enumerate(project.source_asset_ids, start=1):
            connection.execute(
                "INSERT INTO content_edit_project_sources "
                "(project_id, source_asset_id, source_order) VALUES (?, ?, ?)",
                (project.project_id, asset_id, order),
            )
        return project

    def create_edit_project(self, project: ContentEditProject) -> ContentEditProject:
        with self._transaction() as connection:
            return self.create_edit_project_in_transaction(connection, project)

    def get_edit_project(self, project_id: str, owner_id: str) -> ContentEditProject | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM content_edit_projects WHERE project_id = ? AND owner_id = ?",
                (project_id, owner_id),
            ).fetchone()
        if row is None:
            return None
        return ContentEditProject(
            project_id=row["project_id"],
            owner_id=row["owner_id"],
            source_asset_ids=tuple(json.loads(row["source_asset_ids_json"])),
            script=row["script"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def replace_edit_segments(
        self,
        project_id: str,
        owner_id: str,
        segments: tuple[ContentEditSegment, ...],
    ) -> tuple[ContentEditSegment, ...]:
        with self._transaction() as connection:
            return self.replace_edit_segments_in_transaction(
                connection,
                project_id,
                owner_id,
                segments,
            )

    def replace_edit_segments_in_transaction(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        owner_id: str,
        segments: tuple[ContentEditSegment, ...],
        *,
        allow_structured_plan_revision: bool = False,
    ) -> tuple[ContentEditSegment, ...]:
        row = connection.execute(
            "SELECT source_asset_ids_json FROM content_edit_projects "
            "WHERE project_id = ? AND owner_id = ?",
            (project_id, owner_id),
        ).fetchone()
        if row is None:
            raise PermissionError("An owned CRE editing project is required.")
        if not isinstance(segments, tuple) or not 2 <= len(segments) <= 100:
            raise ValueError("A CRE project timeline must contain 2 to 100 segments.")
        allowed = set(json.loads(row["source_asset_ids_json"]))
        for segment in segments:
            if not isinstance(segment, ContentEditSegment) or segment.source_asset_id not in allowed:
                raise PermissionError("Timeline segments must reference sources owned by the project.")
        has_structured_plan = connection.execute(
            "SELECT 1 FROM content_edit_plan_revisions "
            "WHERE project_id = ? LIMIT 1",
            (project_id,),
        ).fetchone() is not None
        if has_structured_plan and not allow_structured_plan_revision:
            current_rows = connection.execute(
                """
                SELECT source_asset_id, source_in_seconds, source_out_seconds,
                    timeline_order
                FROM content_edit_segments
                WHERE project_id = ?
                ORDER BY timeline_order
                """,
                (project_id,),
            ).fetchall()
            current_segments = tuple(
                ContentEditSegment(
                    source_asset_id=item["source_asset_id"],
                    source_in_seconds=item["source_in_seconds"],
                    source_out_seconds=item["source_out_seconds"],
                    timeline_order=item["timeline_order"],
                )
                for item in current_rows
            )
            if current_segments != segments:
                raise ValueError(
                    "Structured plan timelines can only change through a new plan revision."
                )
            return segments

        connection.execute("DELETE FROM content_edit_segments WHERE project_id = ?", (project_id,))
        for segment in segments:
            connection.execute(
                "INSERT INTO content_edit_segments "
                "(project_id, timeline_order, source_asset_id, source_in_seconds, source_out_seconds) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    project_id,
                    segment.timeline_order,
                    segment.source_asset_id,
                    segment.source_in_seconds,
                    segment.source_out_seconds,
                )
            )
        return segments

    def get_edit_segments(self, project_id: str, owner_id: str) -> tuple[ContentEditSegment, ...]:
        project = self.get_edit_project(project_id, owner_id)
        if project is None:
            raise PermissionError("An owned CRE editing project is required.")
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM content_edit_segments WHERE project_id = ? ORDER BY timeline_order",
                (project_id,),
            ).fetchall()
        return tuple(
            ContentEditSegment(
                source_asset_id=row["source_asset_id"],
                source_in_seconds=row["source_in_seconds"],
                source_out_seconds=row["source_out_seconds"],
                timeline_order=row["timeline_order"],
            )
            for row in rows
        )

    def save_edit_plan_in_transaction(
        self,
        connection: sqlite3.Connection,
        owner_id: str,
        plan: ContentCreativeEditPlanRecord,
    ) -> tuple[ContentEditSegment, ...]:
        if not isinstance(plan, (ContentCreativeEditPlan, ContentCreativeEditPlanV2)):
            raise TypeError("A supported structured CRE edit plan is required.")
        project = connection.execute(
            "SELECT owner_id, source_asset_ids_json FROM content_edit_projects "
            "WHERE project_id = ?",
            (plan.project_id,),
        ).fetchone()
        if project is None or project["owner_id"] != owner_id:
            raise PermissionError("An owned CRE editing project is required.")
        project_source_ids = tuple(json.loads(project["source_asset_ids_json"]))
        if tuple(source.source_asset_id for source in plan.sources) != project_source_ids:
            raise ValueError("Plan source identities must match the project's registered sources.")
        for source in plan.sources:
            asset = connection.execute(
                "SELECT owner_id, content_sha256 FROM content_assets WHERE asset_id = ?",
                (source.source_asset_id,),
            ).fetchone()
            if (
                asset is None
                or asset["owner_id"] != owner_id
                or asset["content_sha256"] != source.content_sha256
            ):
                raise ValueError("Plan source hash does not match the owner's registered asset.")
        latest = connection.execute(
            "SELECT MAX(revision) AS revision FROM content_edit_plan_revisions "
            "WHERE project_id = ?",
            (plan.project_id,),
        ).fetchone()["revision"]
        expected_revision = 1 if latest is None else latest + 1
        if plan.revision != expected_revision:
            raise ValueError("Creative plan revisions must be appended in sequence.")

        projection = tuple(
            ContentEditSegment(
                source_asset_id=segment.source_asset_id,
                source_in_seconds=segment.source_in_seconds,
                source_out_seconds=segment.source_out_seconds,
                timeline_order=segment.timeline_order,
            )
            for segment in plan.segments
        )
        self.replace_edit_segments_in_transaction(
            connection,
            plan.project_id,
            owner_id,
            projection,
            allow_structured_plan_revision=True,
        )
        canonical = self._canonical(plan)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        connection.execute(
            """
            INSERT INTO content_edit_plan_revisions (
                project_id, revision, plan_json, plan_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (plan.project_id, plan.revision, canonical, digest, self._now()),
        )
        return projection

    def get_latest_edit_plan(
        self,
        project_id: str,
        owner_id: str,
    ) -> ContentCreativeEditPlanRecord | None:
        project = self.get_edit_project(project_id, owner_id)
        if project is None:
            raise PermissionError("An owned CRE editing project is required.")
        with self._lock:
            row = self._connection.execute(
                """
                SELECT revision, plan_json, plan_sha256
                FROM content_edit_plan_revisions
                WHERE project_id = ?
                ORDER BY revision DESC
                LIMIT 1
                """,
                (project_id,),
            ).fetchone()
            if row is None:
                return None
            canonical = row["plan_json"]
            actual_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if actual_digest != row["plan_sha256"]:
                raise ValueError("Persisted creative plan digest is invalid.")
            try:
                payload = json.loads(canonical)
            except json.JSONDecodeError as exc:
                raise ValueError("Persisted creative plan JSON is invalid.") from exc
            schema_version = payload.get("schema_version") if isinstance(payload, dict) else None
            if schema_version == "cre-creative-plan-v1":
                plan = ContentCreativeEditPlan.model_validate_json(canonical)
            elif schema_version == "cre-creative-plan-v2":
                plan = ContentCreativeEditPlanV2.model_validate_json(canonical)
            else:
                raise ValueError("Persisted creative plan version is unsupported.")
            if plan.project_id != project_id or plan.revision != row["revision"]:
                raise ValueError("Persisted creative plan identity is inconsistent.")
            segment_rows = self._connection.execute(
                """
                SELECT source_asset_id, source_in_seconds, source_out_seconds,
                    timeline_order
                FROM content_edit_segments
                WHERE project_id = ?
                ORDER BY timeline_order
                """,
                (project_id,),
            ).fetchall()
        projection = tuple(
            ContentEditSegment(
                source_asset_id=segment.source_asset_id,
                source_in_seconds=segment.source_in_seconds,
                source_out_seconds=segment.source_out_seconds,
                timeline_order=segment.timeline_order,
            )
            for segment in plan.segments
        )
        stored_projection = tuple(
            ContentEditSegment(
                source_asset_id=row["source_asset_id"],
                source_in_seconds=row["source_in_seconds"],
                source_out_seconds=row["source_out_seconds"],
                timeline_order=row["timeline_order"],
            )
            for row in segment_rows
        )
        if stored_projection != projection:
            raise ValueError("Persisted edit timeline does not match its structured plan.")
        return plan

    def get_source_intake(self, intake_id: str) -> SourceReferenceRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM source_intakes WHERE intake_id = ?",
                (intake_id,),
            ).fetchone()
        if row is None:
            return None
        return SourceReferenceRecord(
            intake_id=row["intake_id"],
            asset_id=row["asset_id"],
            owner_id=row["owner_id"],
            source_reference=row["source_reference"],
            acquisition_method=row["acquisition_method"],
            provenance=json.loads(row["provenance_json"]),
            evidence_reference=row["evidence_reference"],
            claimed_source_sha256=row["claimed_source_sha256"],
            source_hash_status=SourceHashStatus(row["source_hash_status"]),
            access_permission_state=SourceAccessPermissionState(
                row["access_permission_state"]
            ),
            target_context=row["target_context"],
            rights_use=(
                None
                if row["rights_use_json"] is None
                else json.loads(row["rights_use_json"])
            ),
            created_at=row["created_at"],
        )

    def get_content_lifecycle(
        self,
        intake_id: str,
    ) -> ContentLifecycleRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM content_lifecycle WHERE intake_id = ?",
                (intake_id,),
            ).fetchone()
        if row is None:
            return None
        return ContentLifecycleRecord(
            intake_id=row["intake_id"],
            asset_id=row["asset_id"],
            asset_sha256=row["asset_sha256"],
            target_context=row["target_context"],
            state=row["state"],
            rights_decision_id=row["rights_decision_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _recover_interrupted_processing_jobs(self) -> None:
        now = self._now()
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT job_id FROM content_processing_jobs WHERE state = ?",
                (ProcessingJobState.RUNNING.value,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    UPDATE content_processing_jobs
                    SET state = ?, updated_at = ?
                    WHERE job_id = ? AND state = ?
                    """,
                    (
                        ProcessingJobState.UNCERTAIN.value,
                        now,
                        row["job_id"],
                        ProcessingJobState.RUNNING.value,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO content_processing_events (
                        job_id, from_state, to_state, actor, reason, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["job_id"],
                        ProcessingJobState.RUNNING.value,
                        ProcessingJobState.UNCERTAIN.value,
                        "system:startup_recovery",
                        "worker_interrupted_outcome_unknown",
                        now,
                    ),
                )
            schema = connection.execute(
                "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
            ).fetchone()
            if (
                schema is not None
                and schema["schema_version"]
                == self.PRIVATE_RENDER_OPERATION_SCHEMA_VERSION
            ):
                operations = connection.execute(
                    """
                    SELECT operation_id FROM content_private_render_operations
                    WHERE state = ?
                    """,
                    (PrivateRenderOperationState.RENDERING.value,),
                ).fetchall()
                for operation in operations:
                    connection.execute(
                        """
                        UPDATE content_private_render_operations
                        SET state = ?, failure_code = ?, failure_detail = ?,
                            updated_at = ?
                        WHERE operation_id = ? AND state = ?
                        """,
                        (
                            PrivateRenderOperationState.RECONCILIATION_REQUIRED.value,
                            "PROCESS_INTERRUPTED",
                            "The process stopped while rendering; output outcome requires reconciliation.",
                            now,
                            operation["operation_id"],
                            PrivateRenderOperationState.RENDERING.value,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO content_private_render_operation_events (
                            operation_id, from_state, to_state, actor, reason,
                            created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            operation["operation_id"],
                            PrivateRenderOperationState.RENDERING.value,
                            PrivateRenderOperationState.RECONCILIATION_REQUIRED.value,
                            "system:startup_recovery",
                            "worker_interrupted_outcome_unknown",
                            now,
                        ),
                    )
            clip_rows = connection.execute(
                "SELECT render_id FROM content_clip_renders WHERE state = ?",
                (ProcessingJobState.RUNNING.value,),
            ).fetchall()
            for row in clip_rows:
                connection.execute(
                    """
                    UPDATE content_clip_renders
                    SET state = ?, updated_at = ?
                    WHERE render_id = ? AND state = ?
                    """,
                    (
                        ProcessingJobState.UNCERTAIN.value,
                        now,
                        row["render_id"],
                        ProcessingJobState.RUNNING.value,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO content_clip_render_events (
                        render_id, from_state, to_state, actor, reason, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["render_id"],
                        ProcessingJobState.RUNNING.value,
                        ProcessingJobState.UNCERTAIN.value,
                        "system:startup_recovery",
                        "worker_interrupted_outcome_unknown",
                        now,
                    ),
                )
            publishing_rows = connection.execute(
                "SELECT job_id FROM content_publishing_jobs WHERE state = ?",
                (PublishingJobState.DISPATCHING.value,),
            ).fetchall()
            for row in publishing_rows:
                connection.execute(
                    """
                    UPDATE content_publishing_jobs
                    SET state = ?, updated_at = ?
                    WHERE job_id = ? AND state = ?
                    """,
                    (
                        PublishingJobState.UNCERTAIN.value,
                        now,
                        row["job_id"],
                        PublishingJobState.DISPATCHING.value,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO content_publishing_job_events (
                        job_id, from_state, to_state, actor, reason, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["job_id"],
                        PublishingJobState.DISPATCHING.value,
                        PublishingJobState.UNCERTAIN.value,
                        "system:startup_recovery",
                        "dispatch_interrupted_outcome_unknown",
                        now,
                    ),
                )
                recovery_fingerprint = self._fingerprint(
                    "content_recover_publishing_dispatch",
                    {
                        "job_id": row["job_id"],
                        "from_state": PublishingJobState.DISPATCHING.value,
                        "to_state": PublishingJobState.UNCERTAIN.value,
                    },
                )
                connection.execute(
                    """
                    INSERT INTO content_audit_events (
                        event_uuid, principal, session_id, action, subject_id,
                        decision, request_fingerprint, details_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        "system:startup_recovery",
                        "system",
                        "content_recover_publishing_dispatch",
                        row["job_id"],
                        "publishing_recovered_uncertain",
                        recovery_fingerprint,
                        self._canonical(
                            {
                                "from_state": PublishingJobState.DISPATCHING.value,
                                "to_state": PublishingJobState.UNCERTAIN.value,
                            }
                        ),
                        now,
                    ),
                )
            schema = connection.execute(
                "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
            ).fetchone()
            if (
                schema is not None
                and schema["schema_version"] == self.PUBLISHING_ATTEMPT_SCHEMA_VERSION
            ):
                attempts = connection.execute(
                    """
                    SELECT a.*,
                        EXISTS (
                            SELECT 1
                            FROM content_publishing_attempt_secret_refs r
                            WHERE r.attempt_id = a.attempt_id
                              AND r.purpose = ?
                              AND r.status = 'active'
                        ) AS has_active_session_ref
                    FROM content_publishing_attempts a
                    WHERE a.state IN (?, ?, ?)
                    """,
                    (
                        UploadSecretPurpose.RESUMABLE_UPLOAD_SESSION.value,
                        PublishingAttemptState.INITIATION_INTENT.value,
                        PublishingAttemptState.SESSION_READY.value,
                        PublishingAttemptState.UPLOADING.value,
                    ),
                ).fetchall()
                for attempt_row in attempts:
                    old_state = PublishingAttemptState(attempt_row["state"])
                    if old_state == PublishingAttemptState.INITIATION_INTENT:
                        new_state = PublishingAttemptState.INITIATION_UNCERTAIN
                        code = PublishingAttemptCode.INITIATION_OUTCOME_UNKNOWN
                    elif not attempt_row["has_active_session_ref"]:
                        new_state = PublishingAttemptState.OPERATOR_ACTION_REQUIRED
                        code = PublishingAttemptCode.SECRET_BINDING_MISMATCH
                    else:
                        new_state = PublishingAttemptState.RECONCILIATION_REQUIRED
                        code = PublishingAttemptCode.REMOTE_OUTCOME_UNKNOWN
                    revision = attempt_row["revision"] + 1
                    action = "content_recover_publishing_attempt"
                    command_id = f"startup-recovery:{attempt_row['attempt_id']}:{revision}"
                    fingerprint = self._fingerprint(
                        action,
                        {
                            "attempt_id": attempt_row["attempt_id"],
                            "from_state": old_state.value,
                            "to_state": new_state.value,
                            "code": code.value,
                        },
                    )
                    self._insert_publishing_attempt_event(
                        connection,
                        attempt_id=attempt_row["attempt_id"],
                        revision=revision,
                        event_type="transition",
                        from_state=old_state,
                        to_state=new_state,
                        code=code,
                        actor="system:startup_recovery",
                        command_id=command_id,
                        action=action,
                        request_fingerprint=fingerprint,
                        created_at=now,
                    )
                    connection.execute(
                        """
                        UPDATE content_publishing_attempts
                        SET state = ?, error_code = ?, revision = ?, updated_at = ?
                        WHERE attempt_id = ? AND revision = ?
                        """,
                        (
                            new_state.value,
                            code.value,
                            revision,
                            now,
                            attempt_row["attempt_id"],
                            attempt_row["revision"],
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO content_audit_events (
                            event_uuid, principal, session_id, action, subject_id,
                            decision, request_fingerprint, details_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid4()),
                            "system:startup_recovery",
                            "system",
                            action,
                            attempt_row["attempt_id"],
                            "publishing_attempt_recovered_unresolved",
                            fingerprint,
                            self._canonical(
                                {
                                    "from_state": old_state.value,
                                    "to_state": new_state.value,
                                    "code": code.value,
                                }
                            ),
                            now,
                        ),
                    )

    def get_clip_render(self, render_id: str) -> ContentClipRender | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM content_clip_renders WHERE render_id = ?",
                (render_id,),
            ).fetchone()
        return None if row is None else self._load_clip_render(row)

    def create_private_render_operation_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        operation_id: str,
        owner_id: str,
        project_id: str,
        request_fingerprint: str,
        input_snapshot_json: str,
    ) -> ContentPrivateRenderOperation:
        self._require_private_render_operation_schema(connection)
        now = self._now()
        connection.execute(
            """
            INSERT INTO content_private_render_operations (
                operation_id, owner_id, project_id, idempotency_key,
                request_fingerprint, input_snapshot_json, state, created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                operation_id,
                owner_id,
                project_id,
                operation_id,
                request_fingerprint,
                input_snapshot_json,
                PrivateRenderOperationState.QUEUED.value,
                now,
                now,
            ),
        )
        self._append_private_render_operation_event(
            connection,
            operation_id=operation_id,
            from_state=None,
            to_state=PrivateRenderOperationState.QUEUED,
            actor=owner_id,
            reason="render_operation_accepted",
            now=now,
        )
        row = connection.execute(
            """
            SELECT * FROM content_private_render_operations
            WHERE operation_id = ? AND owner_id = ?
            """,
            (operation_id, owner_id),
        ).fetchone()
        if row is None:
            raise RuntimeError("Private render operation disappeared after creation.")
        return self._load_private_render_operation(row)

    def claim_private_render_operation(
        self,
        operation_id: str,
        owner_id: str,
    ) -> bool:
        self._require_private_render_operation_schema(self._connection)
        now = self._now()
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE content_private_render_operations
                SET state = ?, updated_at = ?
                WHERE operation_id = ? AND owner_id = ? AND state = ?
                """,
                (
                    PrivateRenderOperationState.RENDERING.value,
                    now,
                    operation_id,
                    owner_id,
                    PrivateRenderOperationState.QUEUED.value,
                ),
            )
            if cursor.rowcount != 1:
                return False
            self._append_private_render_operation_event(
                connection,
                operation_id=operation_id,
                from_state=PrivateRenderOperationState.QUEUED,
                to_state=PrivateRenderOperationState.RENDERING,
                actor=owner_id,
                reason="renderer_claimed",
                now=now,
            )
            return True

    def complete_private_render_operation(
        self,
        operation_id: str,
        owner_id: str,
        *,
        artifact_key: str,
        artifact_sha256: str,
        artifact_byte_count: int,
        result_json: str,
    ) -> None:
        self._transition_private_render_operation(
            operation_id,
            owner_id,
            from_state=PrivateRenderOperationState.RENDERING,
            to_state=PrivateRenderOperationState.COMPLETED,
            reason="artifact_persisted_and_verified",
            artifact_key=artifact_key,
            artifact_sha256=artifact_sha256,
            artifact_byte_count=artifact_byte_count,
            result_json=result_json,
            completed_at=self._now(),
        )

    def fail_private_render_operation(
        self,
        operation_id: str,
        owner_id: str,
        *,
        reconciliation_required: bool,
        failure_code: str,
        failure_detail: str,
    ) -> None:
        operation = self.get_private_render_operation(operation_id, owner_id)
        if operation is None:
            raise PermissionError("Private render operation is unavailable.")
        if operation.state not in {
            PrivateRenderOperationState.QUEUED,
            PrivateRenderOperationState.RENDERING,
        }:
            return
        target = (
            PrivateRenderOperationState.RECONCILIATION_REQUIRED
            if reconciliation_required
            else PrivateRenderOperationState.FAILED
        )
        self._transition_private_render_operation(
            operation_id,
            owner_id,
            from_state=operation.state,
            to_state=target,
            reason=failure_code.lower(),
            failure_code=failure_code[:100],
            failure_detail=failure_detail[:1000],
        )

    def _transition_private_render_operation(
        self,
        operation_id: str,
        owner_id: str,
        *,
        from_state: PrivateRenderOperationState,
        to_state: PrivateRenderOperationState,
        reason: str,
        artifact_key: str | None = None,
        artifact_sha256: str | None = None,
        artifact_byte_count: int | None = None,
        result_json: str | None = None,
        failure_code: str | None = None,
        failure_detail: str | None = None,
        completed_at: str | None = None,
    ) -> None:
        now = self._now()
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE content_private_render_operations
                SET state = ?, artifact_key = COALESCE(?, artifact_key),
                    artifact_sha256 = COALESCE(?, artifact_sha256),
                    artifact_byte_count = COALESCE(?, artifact_byte_count),
                    result_json = COALESCE(?, result_json),
                    failure_code = ?, failure_detail = ?,
                    completed_at = COALESCE(?, completed_at), updated_at = ?
                WHERE operation_id = ? AND owner_id = ? AND state = ?
                """,
                (
                    to_state.value,
                    artifact_key,
                    artifact_sha256,
                    artifact_byte_count,
                    result_json,
                    failure_code,
                    failure_detail,
                    completed_at,
                    now,
                    operation_id,
                    owner_id,
                    from_state.value,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Private render operation state changed concurrently.")
            self._append_private_render_operation_event(
                connection,
                operation_id=operation_id,
                from_state=from_state,
                to_state=to_state,
                actor=owner_id,
                reason=reason,
                now=now,
            )

    def get_private_render_operation(
        self,
        operation_id: str,
        owner_id: str,
    ) -> ContentPrivateRenderOperation | None:
        self._require_private_render_operation_schema(self._connection)
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM content_private_render_operations
                WHERE operation_id = ? AND owner_id = ?
                """,
                (operation_id, owner_id),
            ).fetchone()
        return None if row is None else self._load_private_render_operation(row)

    def list_private_render_operation_events(
        self,
        operation_id: str,
        owner_id: str,
    ) -> list[ContentPrivateRenderOperationEvent]:
        if self.get_private_render_operation(operation_id, owner_id) is None:
            raise PermissionError("Private render operation is unavailable.")
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT e.* FROM content_private_render_operation_events e
                JOIN content_private_render_operations o
                    ON o.operation_id = e.operation_id
                WHERE e.operation_id = ? AND o.owner_id = ?
                ORDER BY e.sequence
                """,
                (operation_id, owner_id),
            ).fetchall()
        return [
            ContentPrivateRenderOperationEvent(
                sequence=row["sequence"],
                operation_id=row["operation_id"],
                from_state=(
                    None
                    if row["from_state"] is None
                    else PrivateRenderOperationState(row["from_state"])
                ),
                to_state=PrivateRenderOperationState(row["to_state"]),
                actor=row["actor"],
                reason=row["reason"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    @staticmethod
    def _require_private_render_operation_schema(
        connection: sqlite3.Connection,
    ) -> None:
        row = connection.execute(
            "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
        ).fetchone()
        if (
            row is None
            or row["schema_version"]
            != ContentStore.PRIVATE_RENDER_OPERATION_SCHEMA_VERSION
        ):
            raise RuntimeError(
                "Private render operations require explicit schema-v10 migration."
            )

    @staticmethod
    def _append_private_render_operation_event(
        connection: sqlite3.Connection,
        *,
        operation_id: str,
        from_state: PrivateRenderOperationState | None,
        to_state: PrivateRenderOperationState,
        actor: str,
        reason: str,
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO content_private_render_operation_events (
                operation_id, from_state, to_state, actor, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                operation_id,
                None if from_state is None else from_state.value,
                to_state.value,
                actor,
                reason[:200],
                now,
            ),
        )

    @staticmethod
    def _load_private_render_operation(
        row: sqlite3.Row,
    ) -> ContentPrivateRenderOperation:
        return ContentPrivateRenderOperation(
            operation_id=row["operation_id"],
            owner_id=row["owner_id"],
            project_id=row["project_id"],
            idempotency_key=row["idempotency_key"],
            request_fingerprint=row["request_fingerprint"],
            input_snapshot_json=row["input_snapshot_json"],
            state=PrivateRenderOperationState(row["state"]),
            artifact_key=row["artifact_key"],
            artifact_sha256=row["artifact_sha256"],
            artifact_byte_count=row["artifact_byte_count"],
            result_json=row["result_json"],
            failure_code=row["failure_code"],
            failure_detail=row["failure_detail"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            completed_at=(
                None
                if row["completed_at"] is None
                else datetime.fromisoformat(row["completed_at"])
            ),
        )

    def get_publishing_job(self, job_id: str) -> PublishingJob | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM content_publishing_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return PublishingJob(
            job_id=row["job_id"],
            owner_id=row["owner_id"],
            package_id=row["package_id"],
            package_version=row["package_version"],
            render_id=row["render_id"],
            output_sha256=row["output_sha256"],
            metadata_sha256=row["metadata_sha256"],
            platform=ContentPlatform(row["platform"]),
            account_id=row["account_id"],
            visibility=PublishingVisibility(row["visibility"]),
            intended_uses=tuple(
                RightsUse.model_validate(item)
                for item in json.loads(row["intended_uses_json"])
            ),
            synthetic_media_disclosed=bool(row["synthetic_media_disclosed"]),
            rights_decision_ids=tuple(json.loads(row["rights_decision_ids_json"])),
            rights_snapshot_sha256=row["rights_snapshot_sha256"],
            idempotency_key=row["idempotency_key"],
            request_fingerprint=row["request_fingerprint"],
            state=PublishingJobState(row["state"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def get_publishing_approval(
        self,
        job_id: str,
    ) -> PublishingReleaseApproval | None:
        with self._lock:
            row = self._connection.execute(
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
        if row is None:
            return None
        state = PublishingApprovalState(row["latest_state"])
        expires_at = datetime.fromisoformat(row["expires_at"])
        if state == PublishingApprovalState.APPROVED and expires_at <= datetime.now(UTC):
            state = PublishingApprovalState.EXPIRED
        return PublishingReleaseApproval(
            approval_id=row["approval_id"],
            job_id=row["job_id"],
            request_fingerprint=row["request_fingerprint"],
            package_id=row["package_id"],
            package_version=row["package_version"],
            output_sha256=row["output_sha256"],
            metadata_sha256=row["metadata_sha256"],
            platform=ContentPlatform(row["platform"]),
            account_id=row["account_id"],
            visibility=PublishingVisibility(row["visibility"]),
            intended_uses=tuple(
                RightsUse.model_validate(item)
                for item in json.loads(row["intended_uses_json"])
            ),
            synthetic_media_disclosed=bool(row["synthetic_media_disclosed"]),
            rights_decision_ids=tuple(json.loads(row["rights_decision_ids_json"])),
            rights_snapshot_sha256=row["rights_snapshot_sha256"],
            request_principal=row["request_principal"],
            approved_by=row["approved_by"],
            expires_at=expires_at,
            state=state,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def list_publishing_job_events(self, job_id: str) -> list[PublishingJobEvent]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM content_publishing_job_events
                WHERE job_id = ? ORDER BY sequence
                """,
                (job_id,),
            ).fetchall()
        return [
            PublishingJobEvent(
                sequence=row["sequence"],
                job_id=row["job_id"],
                from_state=(
                    None
                    if row["from_state"] is None
                    else PublishingJobState(row["from_state"])
                ),
                to_state=PublishingJobState(row["to_state"]),
                actor=row["actor"],
                reason=row["reason"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def is_publishing_kill_switch_enabled(self) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT kill_switch_enabled FROM content_publishing_controls "
                "WHERE singleton = 1"
            ).fetchone()
        if row is None:
            raise RuntimeError("Publishing kill-switch state is missing.")
        return bool(row["kill_switch_enabled"])

    @staticmethod
    def _load_clip_render(row: sqlite3.Row) -> ContentClipRender:
        return ContentClipRender(
            render_id=row["render_id"],
            owner_id=row["owner_id"],
            command_id=row["command_id"],
            candidate_id=row["candidate_id"],
            transcript_id=row["transcript_id"],
            asset_id=row["asset_id"],
            asset_sha256=row["asset_sha256"],
            target_context=ContentTargetContext(row["target_context"]),
            rights_use=RightsUse.model_validate_json(row["rights_use_json"]),
            start_seconds=row["start_seconds"],
            end_seconds=row["end_seconds"],
            source_duration_seconds=row["source_duration_seconds"],
            output_duration_seconds=row["output_duration_seconds"],
            has_video=(
                None if row["has_video"] is None else bool(row["has_video"])
            ),
            has_audio=(
                None if row["has_audio"] is None else bool(row["has_audio"])
            ),
            state=ProcessingJobState(row["state"]),
            output_key=row["output_key"],
            output_sha256=row["output_sha256"],
            output_byte_size=row["output_byte_size"],
            quality_output_sha256=row["quality_output_sha256"],
            quality_status=row["quality_status"],
            quality_black_frames=row["quality_black_frames"],
            quality_frozen_frames=row["quality_frozen_frames"],
            quality_black_ranges_json=row["quality_black_ranges_json"],
            quality_frozen_ranges_json=row["quality_frozen_ranges_json"],
            quality_revision_count=row["quality_revision_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_clip_render_events(self, render_id: str) -> list[ContentClipRenderEvent]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM content_clip_render_events
                WHERE render_id = ? ORDER BY sequence
                """,
                (render_id,),
            ).fetchall()
        return [
            ContentClipRenderEvent(
                sequence=row["sequence"],
                render_id=row["render_id"],
                from_state=(
                    None
                    if row["from_state"] is None
                    else ProcessingJobState(row["from_state"])
                ),
                to_state=ProcessingJobState(row["to_state"]),
                actor=row["actor"],
                reason=row["reason"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def get_processing_job(self, job_id: str) -> ContentProcessingJob | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM content_processing_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return None if row is None else self._load_processing_job(row)

    @staticmethod
    def _load_processing_job(row: sqlite3.Row) -> ContentProcessingJob:
        return ContentProcessingJob(
            job_id=row["job_id"],
            owner_id=row["owner_id"],
            session_id=row["session_id"],
            command_id=row["command_id"],
            asset_id=row["asset_id"],
            asset_sha256=row["asset_sha256"],
            target_context=ContentTargetContext(row["target_context"]),
            rights_use=RightsUse.model_validate_json(row["rights_use_json"]),
            state=ProcessingJobState(row["state"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_processing_output(self, job_id: str) -> ContentProcessingOutput | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM content_processing_outputs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return None if row is None else ContentProcessingOutput(
            job_id=row["job_id"],
            output_key=row["output_key"],
            content_sha256=row["content_sha256"],
            byte_size=row["byte_size"],
            created_at=row["created_at"],
        )

    def list_processing_events(self, job_id: str) -> list[ContentProcessingEvent]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM content_processing_events
                WHERE job_id = ? ORDER BY sequence
                """,
                (job_id,),
            ).fetchall()
        return [
            ContentProcessingEvent(
                sequence=row["sequence"],
                job_id=row["job_id"],
                from_state=(
                    None
                    if row["from_state"] is None
                    else ProcessingJobState(row["from_state"])
                ),
                to_state=ProcessingJobState(row["to_state"]),
                actor=row["actor"],
                reason=row["reason"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def get_operator_status(self, owner_id: str, *, limit: int) -> dict[str, Any]:
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("A CRE owner identity is required.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("CRE status limit must be between 1 and 100.")

        owner_id = owner_id.strip()
        with self._lock:
            counts = self._connection.execute(
                """
                SELECT job_type, state, COUNT(*) AS count
                FROM (
                    SELECT 'processing' AS job_type, state
                    FROM content_processing_jobs WHERE owner_id = ?
                    UNION ALL
                    SELECT 'rendering' AS job_type, state
                    FROM content_clip_renders WHERE owner_id = ?
                    UNION ALL
                    SELECT 'publishing' AS job_type, state
                    FROM content_publishing_jobs WHERE owner_id = ?
                )
                GROUP BY job_type, state
                """,
                (owner_id, owner_id, owner_id),
            ).fetchall()
            jobs = self._connection.execute(
                """
                SELECT job_id, job_type, state, created_at, updated_at
                FROM (
                    SELECT job_id, 'processing' AS job_type, state,
                           created_at, updated_at
                    FROM content_processing_jobs WHERE owner_id = ?
                    UNION ALL
                    SELECT render_id AS job_id, 'rendering' AS job_type, state,
                           created_at, updated_at
                    FROM content_clip_renders WHERE owner_id = ?
                    UNION ALL
                    SELECT job_id, 'publishing' AS job_type, state,
                           created_at, updated_at
                    FROM content_publishing_jobs WHERE owner_id = ?
                )
                ORDER BY updated_at DESC, job_id
                LIMIT ?
                """,
                (owner_id, owner_id, owner_id, limit),
            ).fetchall()
            lifecycle = self._connection.execute(
                """
                SELECT l.intake_id, l.state, l.created_at, l.updated_at
                FROM content_lifecycle l
                JOIN content_assets a ON a.asset_id = l.asset_id
                WHERE a.owner_id = ?
                ORDER BY l.updated_at DESC, l.intake_id
                LIMIT ?
                """,
                (owner_id, limit),
            ).fetchall()
            events = self._connection.execute(
                """
                SELECT event_id, event_type, state, created_at
                FROM (
                    SELECT 'processing:' || e.sequence AS event_id,
                           'processing' AS event_type, e.to_state AS state,
                           e.created_at
                    FROM content_processing_events e
                    JOIN content_processing_jobs j ON j.job_id = e.job_id
                    WHERE j.owner_id = ?
                    UNION ALL
                    SELECT 'rendering:' || e.sequence AS event_id,
                           'rendering' AS event_type, e.to_state AS state,
                           e.created_at
                    FROM content_clip_render_events e
                    JOIN content_clip_renders r ON r.render_id = e.render_id
                    WHERE r.owner_id = ?
                    UNION ALL
                    SELECT 'publishing:' || e.sequence AS event_id,
                           'publishing' AS event_type, e.to_state AS state,
                           e.created_at
                    FROM content_publishing_job_events e
                    JOIN content_publishing_jobs j ON j.job_id = e.job_id
                    WHERE j.owner_id = ?
                    UNION ALL
                    SELECT 'lifecycle:' || e.sequence AS event_id,
                           'lifecycle' AS event_type, e.to_state AS state,
                           e.created_at
                    FROM content_lifecycle_events e
                    JOIN content_lifecycle l ON l.intake_id = e.intake_id
                    JOIN content_assets a ON a.asset_id = l.asset_id
                    WHERE a.owner_id = ?
                )
                ORDER BY created_at DESC, event_id
                LIMIT ?
                """,
                (owner_id, owner_id, owner_id, owner_id, limit),
            ).fetchall()

        count_summary: dict[str, dict[str, int]] = {}
        total_jobs = 0
        for row in counts:
            count = int(row["count"])
            count_summary.setdefault(row["job_type"], {})[row["state"]] = count
            total_jobs += count

        return {
            "counts": {
                "total": total_jobs,
                "processing": count_summary.get("processing", {}),
                "rendering": count_summary.get("rendering", {}),
                "publishing": count_summary.get("publishing", {}),
                "pendingApprovals": count_summary.get("publishing", {}).get(
                    "awaiting_approval", 0
                ),
            },
            "jobs": [
                {
                    "id": row["job_id"],
                    "type": row["job_type"],
                    "state": row["state"],
                    "createdAt": row["created_at"],
                    "updatedAt": row["updated_at"],
                }
                for row in jobs
            ],
            "lifecycle": [
                {
                    "id": row["intake_id"],
                    "state": row["state"],
                    "createdAt": row["created_at"],
                    "updatedAt": row["updated_at"],
                }
                for row in lifecycle
            ],
            "events": [
                {
                    "id": row["event_id"],
                    "type": row["event_type"],
                    "state": row["state"],
                    "createdAt": row["created_at"],
                }
                for row in events
            ],
        }

    def processing_output_keys(self) -> frozenset[str]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT output_key FROM content_processing_outputs
                UNION
                SELECT output_key FROM content_clip_renders
                WHERE output_key IS NOT NULL
                """
            ).fetchall()
        return frozenset(row["output_key"] for row in rows)

    def get_transcript(self, transcript_id: str) -> TranscriptRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM content_transcripts WHERE transcript_id = ?",
                (transcript_id,),
            ).fetchone()
        return None if row is None else self._load_transcript(row)

    def get_transcript_for_job(self, job_id: str) -> TranscriptRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM content_transcripts WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return None if row is None else self._load_transcript(row)

    @staticmethod
    def _load_transcript(row: sqlite3.Row) -> TranscriptRecord:
        return TranscriptRecord(
            transcript_id=row["transcript_id"],
            job_id=row["job_id"],
            asset_id=row["asset_id"],
            asset_sha256=row["asset_sha256"],
            target_context=row["target_context"],
            duration_seconds=row["duration_seconds"],
            engine_id=row["engine_id"],
            engine_version=row["engine_version"],
            language=row["language"],
            raw_output_sha256=row["raw_output_sha256"],
            validation_version=row["validation_version"],
            segments=json.loads(row["segments_json"]),
            created_at=row["created_at"],
        )

    def get_highlight_candidates(
        self,
        run_id: str,
    ) -> tuple[HighlightCandidate, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM content_highlight_candidates
                WHERE run_id = ? ORDER BY rank
                """,
                (run_id,),
            ).fetchall()
        return tuple(
            HighlightCandidate(
                candidate_id=row["candidate_id"],
                run_id=row["run_id"],
                transcript_id=row["transcript_id"],
                asset_id=row["asset_id"],
                asset_sha256=row["asset_sha256"],
                start_seconds=row["start_seconds"],
                end_seconds=row["end_seconds"],
                duration_seconds=row["duration_seconds"],
                rank=row["rank"],
                score=row["score"],
                scores=json.loads(row["scores_json"]),
                evidence=json.loads(row["evidence_json"]),
                transcript_segment_ids=json.loads(row["segment_ids_json"]),
                explanation=row["explanation"],
                warnings=json.loads(row["warnings_json"]),
                scoring_version=row["scoring_version"],
                created_at=row["created_at"],
            )
            for row in rows
        )

    def get_highlight_config(self, run_id: str) -> HighlightConfig | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT config_json FROM content_highlight_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return (
            None
            if row is None
            else HighlightConfig.model_validate_json(row["config_json"])
        )

    def list_content_lifecycle_events(
        self,
        intake_id: str,
    ) -> list[ContentLifecycleEvent]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM content_lifecycle_events
                WHERE intake_id = ? ORDER BY sequence
                """,
                (intake_id,),
            ).fetchall()
        return [
            ContentLifecycleEvent(
                sequence=row["sequence"],
                intake_id=row["intake_id"],
                from_state=row["from_state"],
                to_state=row["to_state"],
                actor=row["actor"],
                reason=row["reason"],
                rights_decision_id=row["rights_decision_id"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    @staticmethod
    def _load_asset(row: sqlite3.Row) -> SourceAsset:
        return SourceAsset.model_validate(
            {
                "asset_id": row["asset_id"],
                "owner_id": row["owner_id"],
                "category": row["category"],
                "content_sha256": row["content_sha256"],
                "provenance": json.loads(row["provenance_json"]),
                "storage_key": row["storage_key"],
                "state": row["asset_state"],
                "created_at": row["created_at"],
            }
        )

    def get_ownership_claim(self, claim_id: str) -> OwnershipClaim | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT c.*,
                    (
                        SELECT er.decision
                        FROM rights_evidence e
                        LEFT JOIN evidence_reviews er ON er.review_id = (
                            SELECT review_id FROM evidence_reviews
                            WHERE evidence_id = e.evidence_id
                            ORDER BY review_sequence DESC LIMIT 1
                        )
                        WHERE e.claim_id = c.claim_id
                        ORDER BY er.review_sequence DESC LIMIT 1
                    ) AS latest_decision
                FROM ownership_claims c WHERE c.claim_id = ?
                """,
                (claim_id,),
            ).fetchone()
        if row is None:
            return None
        decision = row["latest_decision"]
        state = {
            EvidenceDecision.VERIFIED.value: OwnershipClaimState.VERIFIED,
            EvidenceDecision.REJECTED.value: OwnershipClaimState.REJECTED,
            EvidenceDecision.AMBIGUOUS.value: OwnershipClaimState.AMBIGUOUS,
        }.get(decision, OwnershipClaimState.REVIEW_REQUIRED)
        return OwnershipClaim.model_validate(
            {
                "claim_id": row["claim_id"],
                "asset_id": row["asset_id"],
                "claimant_id": row["claimant_id"],
                "rights_holder": row["rights_holder"],
                "authority_description": row["authority_description"],
                "state": state,
                "created_at": row["created_at"],
            }
        )

    def get_evidence(self, evidence_id: str) -> RightsEvidence | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT e.*, (
                    SELECT decision FROM evidence_reviews
                    WHERE evidence_id = e.evidence_id
                    ORDER BY review_sequence DESC LIMIT 1
                ) AS latest_decision
                FROM rights_evidence e WHERE e.evidence_id = ?
                """,
                (evidence_id,),
            ).fetchone()
        if row is None:
            return None
        return RightsEvidence.model_validate(
            {
                "evidence_id": row["evidence_id"],
                "asset_id": row["asset_id"],
                "claim_id": row["claim_id"],
                "submitted_by": row["submitted_by"],
                "evidence_kind": row["evidence_kind"],
                "evidence_bytes": bytes(row["evidence_blob"]),
                "evidence_sha256": row["evidence_sha256"],
                "grantor_identity": row["grantor_identity"],
                "grantor_authority": row["grantor_authority"],
                "claimed_terms": json.loads(row["claimed_terms_json"]),
                "created_at": row["created_at"],
            }
        )

    def evidence_decision(self, evidence_id: str) -> EvidenceDecision | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT decision FROM evidence_reviews
                WHERE evidence_id = ?
                ORDER BY review_sequence DESC LIMIT 1
                """,
                (evidence_id,),
            ).fetchone()
        return None if row is None else EvidenceDecision(row["decision"])

    def get_latest_evidence_review(
        self,
        evidence_id: str,
    ) -> RightsEvidenceReview | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM evidence_reviews
                WHERE evidence_id = ?
                ORDER BY review_sequence DESC LIMIT 1
                """,
                (evidence_id,),
            ).fetchone()
        if row is None:
            return None
        return RightsEvidenceReview(
            review_id=row["review_id"],
            evidence_id=row["evidence_id"],
            decision=row["decision"],
            reviewer_principal=row["reviewer_principal"],
            evidence_sha256=row["evidence_sha256"],
            verified_terms_sha256=row["verified_terms_sha256"],
            created_at=row["created_at"],
        )

    def get_grantor_identity(self, grantor_id: str) -> GrantorIdentity | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT i.*,
                    (SELECT decision FROM grantor_identity_reviews r
                     WHERE r.grantor_id = i.grantor_id
                     ORDER BY r.sequence DESC LIMIT 1) AS latest_decision
                FROM grantor_identities i WHERE i.grantor_id = ?
                """,
                (grantor_id,),
            ).fetchone()
        if row is None:
            return None
        return GrantorIdentity(
            grantor_id=row["grantor_id"],
            owner_id=row["owner_id"],
            claimed_identity=row["claimed_identity"],
            verification_method=row["verification_method"],
            evidence_reference=row["evidence_reference"],
            evidence_sha256=row["evidence_sha256"],
            status=_verification_status(row["latest_decision"]),
            created_at=row["created_at"],
        )

    def get_grantor_authority(self, authority_id: str) -> GrantorAuthority | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT a.*,
                    (SELECT decision FROM grantor_authority_reviews r
                     WHERE r.authority_id = a.authority_id
                     ORDER BY r.sequence DESC LIMIT 1) AS latest_decision
                FROM grantor_authorities a WHERE a.authority_id = ?
                """,
                (authority_id,),
            ).fetchone()
        if row is None:
            return None
        return GrantorAuthority(
            authority_id=row["authority_id"],
            grantor_id=row["grantor_id"],
            asset_id=row["asset_id"],
            rights_holder=row["rights_holder"],
            relationship=row["relationship"],
            scope_description=row["scope_description"],
            evidence_reference=row["evidence_reference"],
            evidence_sha256=row["evidence_sha256"],
            status=_verification_status(row["latest_decision"]),
            created_at=row["created_at"],
        )

    def get_rights_agreement(
        self,
        agreement_id: str,
        version: int | None = None,
    ) -> RightsAgreement | None:
        with self._lock:
            if version is None:
                row = self._connection.execute(
                    """
                    SELECT * FROM rights_agreements WHERE agreement_id = ?
                    ORDER BY version DESC LIMIT 1
                    """,
                    (agreement_id,),
                ).fetchone()
            else:
                row = self._connection.execute(
                    """
                    SELECT * FROM rights_agreements
                    WHERE agreement_id = ? AND version = ?
                    """,
                    (agreement_id, version),
                ).fetchone()
            if row is None:
                return None
            latest_event = self._connection.execute(
                """
                SELECT status FROM rights_agreement_events
                WHERE agreement_id = ? AND agreement_version = ?
                ORDER BY sequence DESC LIMIT 1
                """,
                (agreement_id, row["version"]),
            ).fetchone()
        agreement = self._load_rights_agreement(row)
        status = (
            AgreementStatus.PENDING_REVIEW
            if latest_event is None
            else AgreementStatus(latest_event["status"])
        )
        if (
            status == AgreementStatus.APPROVED
            and agreement.terms.valid_until <= datetime.now(UTC)
        ):
            status = AgreementStatus.EXPIRED
        return agreement.model_copy(update={"status": status})

    def list_rights_agreement_events(
        self,
        agreement_id: str,
    ) -> list[RightsAgreementEvent]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM rights_agreement_events
                WHERE agreement_id = ? ORDER BY sequence
                """,
                (agreement_id,),
            ).fetchall()
        return [
            RightsAgreementEvent(
                sequence=row["sequence"],
                agreement_id=row["agreement_id"],
                agreement_version=row["agreement_version"],
                status=row["status"],
                actor=row["actor"],
                reason=row["reason"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    @staticmethod
    def _load_rights_agreement(row: sqlite3.Row) -> RightsAgreement:
        return RightsAgreement(
            agreement_id=row["agreement_id"],
            version=row["version"],
            grantor_id=row["grantor_id"],
            authority_id=row["authority_id"],
            rights_holder=row["rights_holder"],
            grantee_owner_id=row["grantee_owner_id"],
            asset_ids=json.loads(row["asset_ids_json"]),
            terms=json.loads(row["terms_json"]),
            evidence_reference=row["evidence_reference"],
            evidence_sha256=row["evidence_sha256"],
            status=AgreementStatus.PENDING_REVIEW,
            created_at=row["created_at"],
        )

    def get_grant(self, grant_id: str) -> RightsGrant | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT g.*, r.revoked_at
                FROM rights_grants g
                LEFT JOIN (
                    SELECT grant_id, created_at AS revoked_at
                    FROM rights_grant_revocations
                ) r ON r.grant_id = g.grant_id
                WHERE g.grant_id = ?
                """,
                (grant_id,),
            ).fetchone()
        return None if row is None else self._load_grant(row)

    @staticmethod
    def _load_grant(row: sqlite3.Row) -> RightsGrant:
        return RightsGrant.model_validate(
            {
                "grant_id": row["grant_id"],
                "asset_id": row["asset_id"],
                "claim_id": row["claim_id"],
                "evidence_id": row["evidence_id"],
                "grantor_identity": row["grantor_identity"],
                "grantor_authority": row["grantor_authority"],
                "grantee_owner_id": row["grantee_owner_id"],
                "terms": json.loads(row["terms_json"]),
                "revoked": row["revoked_at"] is not None,
                "revoked_at": row["revoked_at"],
                "created_at": row["created_at"],
                "agreement_id": row["agreement_id"],
                "agreement_version": row["agreement_version"],
            }
        )

    @staticmethod
    def grant_records(
        connection: sqlite3.Connection,
        asset_id: str,
    ) -> tuple[GrantRecord, ...]:
        rows = connection.execute(
            """
            SELECT g.*, c.claimant_id, c.rights_holder, c.authority_description,
                e.evidence_blob, e.evidence_sha256, e.submitted_by,
                ca.owner_id AS asset_owner_id,
                (
                    SELECT decision FROM evidence_reviews
                    WHERE evidence_id = e.evidence_id
                    ORDER BY review_sequence DESC LIMIT 1
                ) AS evidence_decision,
                (
                    SELECT verified_terms_sha256 FROM evidence_reviews
                    WHERE evidence_id = e.evidence_id
                    ORDER BY review_sequence DESC LIMIT 1
                ) AS verified_terms_sha256,
                (
                    SELECT evidence_sha256 FROM evidence_reviews
                    WHERE evidence_id = e.evidence_id
                    ORDER BY review_sequence DESC LIMIT 1
                ) AS reviewed_evidence_sha256,
                (
                    SELECT decision FROM evidence_reviews er
                    JOIN rights_evidence re ON re.evidence_id = er.evidence_id
                    WHERE re.claim_id = c.claim_id
                    ORDER BY er.review_sequence DESC LIMIT 1
                ) AS claim_decision,
                r.created_at AS revoked_at
                , ra.grantor_id AS agreement_grantor_id,
                ra.version AS agreement_record_version,
                ra.authority_id AS agreement_authority_id,
                ra.rights_holder AS agreement_rights_holder,
                ra.grantee_owner_id AS agreement_grantee_owner_id,
                ra.asset_ids_json AS agreement_asset_ids_json,
                ra.terms_json AS agreement_terms_json,
                ra.evidence_reference AS agreement_evidence_reference,
                ra.evidence_sha256 AS agreement_evidence_sha256,
                ra.agreement_sha256 AS stored_agreement_sha256,
                ra.created_at AS agreement_created_at,
                (
                    SELECT status FROM rights_agreement_events ae
                    WHERE ae.agreement_id = ra.agreement_id
                      AND ae.agreement_version = ra.version
                    ORDER BY ae.sequence DESC LIMIT 1
                ) AS agreement_state,
                (
                    SELECT decision FROM grantor_identity_reviews ir
                    WHERE ir.grantor_id = ra.grantor_id
                    ORDER BY ir.sequence DESC LIMIT 1
                ) AS identity_state,
                (
                    SELECT evidence_sha256 FROM grantor_identity_reviews ir
                    WHERE ir.grantor_id = ra.grantor_id
                    ORDER BY ir.sequence DESC LIMIT 1
                ) AS reviewed_identity_evidence_sha256,
                gi.evidence_sha256 AS identity_evidence_sha256,
                gi.owner_id AS identity_owner_id,
                ga.grantor_id AS authority_grantor_id,
                ga.asset_id AS authority_asset_id,
                ga.rights_holder AS authority_rights_holder,
                ga.evidence_sha256 AS authority_evidence_sha256,
                (
                    SELECT decision FROM grantor_authority_reviews ar
                    WHERE ar.authority_id = ga.authority_id
                    ORDER BY ar.sequence DESC LIMIT 1
                ) AS authority_state,
                (
                    SELECT evidence_sha256 FROM grantor_authority_reviews ar
                    WHERE ar.authority_id = ga.authority_id
                    ORDER BY ar.sequence DESC LIMIT 1
                ) AS reviewed_authority_evidence_sha256,
                (
                    SELECT decision FROM rights_agreement_reviews gr
                    WHERE gr.agreement_id = ra.agreement_id
                      AND gr.agreement_version = ra.version
                    ORDER BY gr.sequence DESC LIMIT 1
                ) AS agreement_review_decision,
                (
                    SELECT agreement_sha256 FROM rights_agreement_reviews gr
                    WHERE gr.agreement_id = ra.agreement_id
                      AND gr.agreement_version = ra.version
                    ORDER BY gr.sequence DESC LIMIT 1
                ) AS reviewed_agreement_sha256,
                (
                    SELECT evidence_sha256 FROM rights_agreement_reviews gr
                    WHERE gr.agreement_id = ra.agreement_id
                      AND gr.agreement_version = ra.version
                    ORDER BY gr.sequence DESC LIMIT 1
                ) AS reviewed_agreement_evidence_sha256
            FROM rights_grants g
            LEFT JOIN content_assets ca ON ca.asset_id = g.asset_id
            LEFT JOIN ownership_claims c ON c.claim_id = g.claim_id
            LEFT JOIN rights_evidence e ON e.evidence_id = g.evidence_id
            LEFT JOIN rights_grant_revocations r ON r.grant_id = g.grant_id
            LEFT JOIN rights_agreements ra
              ON ra.agreement_id = g.agreement_id
             AND ra.version = g.agreement_version
            LEFT JOIN grantor_identities gi ON gi.grantor_id = ra.grantor_id
            LEFT JOIN grantor_authorities ga ON ga.authority_id = ra.authority_id
            WHERE g.asset_id = ?
            """,
            (asset_id,),
        ).fetchall()
        records: list[GrantRecord] = []
        for row in rows:
            grant = ContentStore._load_grant(row)
            evidence_integrity_valid = (
                row["evidence_blob"] is not None
                and row["claimant_id"] is not None
                and row["claimant_id"] == grant.grantee_owner_id
                and row["submitted_by"] == grant.grantee_owner_id
                and row["asset_owner_id"] == grant.grantee_owner_id
                and hashlib.sha256(bytes(row["evidence_blob"])).hexdigest()
                == row["evidence_sha256"]
                and row["reviewed_evidence_sha256"] == row["evidence_sha256"]
                and row["verified_terms_sha256"] == grant.terms.fingerprint()
                and row["grantor_identity"] == row["rights_holder"]
                and row["grantor_authority"] == row["authority_description"]
            )
            agreement_valid = False
            agreement_status = "unverified"
            if row["agreement_grantor_id"] is not None:
                agreement = ContentStore._load_rights_agreement(
                    {
                        "agreement_id": row["agreement_id"],
                        "version": row["agreement_record_version"],
                        "grantor_id": row["agreement_grantor_id"],
                        "authority_id": row["agreement_authority_id"],
                        "rights_holder": row["agreement_rights_holder"],
                        "grantee_owner_id": row["agreement_grantee_owner_id"],
                        "asset_ids_json": row["agreement_asset_ids_json"],
                        "terms_json": row["agreement_terms_json"],
                        "evidence_reference": row["agreement_evidence_reference"],
                        "evidence_sha256": row["agreement_evidence_sha256"],
                        "created_at": row["agreement_created_at"],
                    }
                )
                agreement_state = row["agreement_state"]
                if (
                    agreement_state == AgreementStatus.APPROVED.value
                    and agreement.terms.valid_until <= datetime.now(UTC)
                ):
                    agreement_state = AgreementStatus.EXPIRED.value
                agreement_status = agreement_state or "unverified"
                try:
                    agreement_terms = RightsTerms.model_validate(
                        json.loads(row["agreement_terms_json"])
                    )
                    agreement_asset_ids = json.loads(row["agreement_asset_ids_json"])
                    agreement_valid = (
                        agreement.fingerprint() == row["stored_agreement_sha256"]
                        and row["reviewed_agreement_sha256"]
                        == row["stored_agreement_sha256"]
                        and row["reviewed_agreement_evidence_sha256"]
                        == row["agreement_evidence_sha256"]
                        and row["agreement_review_decision"]
                        == EvidenceDecision.VERIFIED.value
                        and row["identity_state"] == EvidenceDecision.VERIFIED.value
                        and row["reviewed_identity_evidence_sha256"]
                        == row["identity_evidence_sha256"]
                        and row["identity_owner_id"]
                        == row["agreement_grantee_owner_id"]
                        and row["authority_grantor_id"]
                        == row["agreement_grantor_id"]
                        and row["authority_state"] == EvidenceDecision.VERIFIED.value
                        and row["reviewed_authority_evidence_sha256"]
                        == row["authority_evidence_sha256"]
                        and row["authority_asset_id"] == asset_id
                        and row["authority_rights_holder"]
                        == row["agreement_rights_holder"]
                        and row["agreement_grantee_owner_id"]
                        == row["grantee_owner_id"]
                        and row["agreement_rights_holder"] == row["grantor_identity"]
                        and asset_id in agreement_asset_ids
                        and agreement_terms.fingerprint() == grant.terms.fingerprint()
                    )
                except (KeyError, TypeError, ValueError):
                    agreement_valid = False
            records.append(
                GrantRecord(
                    grant=grant,
                    claim_state=(
                        "verified"
                        if row["claim_decision"] == EvidenceDecision.VERIFIED.value
                        else row["claim_decision"] or "review_required"
                    ),
                    evidence_decision=row["evidence_decision"],
                    evidence_integrity_valid=evidence_integrity_valid,
                    agreement_status=agreement_status,
                    agreement_integrity_valid=agreement_valid,
                )
            )
        return tuple(records)

    @staticmethod
    def evidence_states(
        connection: sqlite3.Connection,
        asset_id: str,
    ) -> tuple[str, ...]:
        rows = connection.execute(
            """
            SELECT (
                SELECT decision FROM evidence_reviews
                WHERE evidence_id = e.evidence_id
                ORDER BY review_sequence DESC LIMIT 1
            ) AS latest_decision
            FROM rights_evidence e
            WHERE e.asset_id = ?
            """,
            (asset_id,),
        ).fetchall()
        return tuple(
            row["latest_decision"] or "pending"
            for row in rows
        )

    def get_package(self, package_id: str) -> ContentPackage | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM content_packages WHERE package_id = ?",
                (package_id,),
            ).fetchone()
        if row is None:
            return None
        return ContentPackage(
            package_id=row["package_id"],
            owner_id=row["owner_id"],
            created_at=row["created_at"],
        )

    def get_package_version(
        self,
        package_id: str,
        version: int,
    ) -> ContentPackageVersion | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT v.*,
                    (SELECT state FROM content_review_events e
                     WHERE e.package_id = v.package_id
                       AND e.package_version = v.version
                     ORDER BY e.event_id DESC LIMIT 1) AS latest_review_state
                FROM content_package_versions v
                WHERE v.package_id = ? AND v.version = ?
                """,
                (package_id, version),
            ).fetchone()
        return None if row is None else self._load_package_version(row)

    @staticmethod
    def _load_package_version(row: sqlite3.Row) -> ContentPackageVersion:
        return ContentPackageVersion.model_validate(
            {
                "package_id": row["package_id"],
                "version": row["version"],
                "platform": row["platform"],
                "account_id": row["account_id"],
                "artifact_sha256": row["artifact_sha256"],
                "metadata_sha256": row["metadata_sha256"],
                "metadata": json.loads(row["metadata_json"]),
                "asset_uses": json.loads(row["asset_uses_json"]),
                "review_state": row["latest_review_state"] or row["review_state"],
                "created_at": row["created_at"],
            }
        )

    def get_approval(self, approval_id: str) -> ReviewApproval | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT a.*,
                    (SELECT state FROM approval_events
                     WHERE approval_id = a.approval_id
                     ORDER BY event_id DESC LIMIT 1) AS latest_state
                FROM review_approvals a WHERE a.approval_id = ?
                """,
                (approval_id,),
            ).fetchone()
        if row is None:
            return None
        return ReviewApproval.model_validate(
            {
                "approval_id": row["approval_id"],
                "package_id": row["package_id"],
                "package_version": row["package_version"],
                "artifact_sha256": row["artifact_sha256"],
                "metadata_sha256": row["metadata_sha256"],
                "platform": row["platform"],
                "account_id": row["account_id"],
                "permitted_action": row["permitted_action"],
                "principal": row["principal"],
                "session_id": row["session_id"],
                "rights_snapshot_sha256": row["rights_snapshot_sha256"],
                "expires_at": row["expires_at"],
                "nonce": row["nonce"],
                "review_state": (
                    ReviewState.EXPIRED.value
                    if row["latest_state"] == ReviewState.APPROVED.value
                    and datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC)
                    else row["latest_state"]
                ),
                "created_at": row["created_at"],
            }
        )

    def get_publication_record(
        self,
        package_id: str,
        version: int,
        platform: ContentPlatform,
        account_id: str,
    ) -> ContentPublicationRecord | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM content_publication_states
                WHERE package_id = ? AND package_version = ?
                  AND platform = ? AND account_id = ?
                """,
                (package_id, version, platform.value, account_id),
            ).fetchone()
        if row is None:
            return None
        return ContentPublicationRecord(
            package_id=row["package_id"],
            package_version=row["package_version"],
            platform=ContentPlatform(row["platform"]),
            account_id=row["account_id"],
            state=PublicationState(row["state"]),
        )

    def list_audit_events(self, subject_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if subject_id is None:
                rows = self._connection.execute(
                    "SELECT * FROM content_audit_events ORDER BY event_id"
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT * FROM content_audit_events WHERE subject_id = ? "
                    "ORDER BY event_id",
                    (subject_id,),
                ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "event_uuid": row["event_uuid"],
                "principal": row["principal"],
                "session_id": row["session_id"],
                "action": row["action"],
                "subject_id": row["subject_id"],
                "decision": row["decision"],
                "request_fingerprint": row["request_fingerprint"],
                "details": json.loads(row["details_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def close(self) -> None:
        with self._lock:
            if self._visual_analysis_active_claims:
                raise RuntimeError(
                    "Content store cannot close while visual analyses are claimed."
                )
            self._visual_analysis_worker_token = None
            self._visual_analysis_recovery_complete = False
            self._release_visual_analysis_worker_lease()
            self._connection.close()
