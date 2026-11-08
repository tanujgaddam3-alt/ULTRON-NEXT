from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import fcntl
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from ultron.domain.content_identity import source_deduplication_key
from ultron.domain.content_acquisition import (
    AcquisitionIntendedUse,
    AcquisitionRightsStatus,
    AcquisitionRequestState,
    ContentAcquisitionRequest,
    ContentAcquisitionRequestEvent,
)


class ContentTaskIdempotencyConflictError(ValueError):
    """A task idempotency key was reused for a different request."""

from ultron.application.content.maintenance import CreMaintenanceControl
from ultron.domain.content import (
    AcquisitionMethod,
    AssetLicenseRecord,
    AssetLicenseVerificationState,
    ContentPackage,
    ContentPackageVersion,
    ContentPlatform,
    ContentPublicationRecord,
    ContentLifecycleEvent,
    ContentLifecycleRecord,
    ContentWorkflowEvent,
    ContentWorkflowRecord,
    ContentWorkflowState,
    ContentWorkflowTask,
    ContentScriptArtifact,
    ContentProductionJob,
    ContentValidationReport,
    ContentApprovalDecision,
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
    ContentCreativeEditPlanV3,
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
    OwnerRightsAttestation,
    OwnerRightsAttestationStatus,
    RightsState,
    RightsTerms,
    RestrictedTechnicalTestPermit,
    RestrictedTechnicalTestStatus,
    VerificationStatus,
    validate_non_overlapping_source_intervals,
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
from ultron.domain.content_discovery import (
    ConfiguredFeed,
    ContentEvidenceRecord,
    ContentOpportunity,
    ContentOpportunityScore,
    ContentResearchReport,
    ContentSourceRecord,
    FeedHealth,
    OpportunityStatus,
    ResearchCompleteness,
    SourceReliability,
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
from ultron.domain.content_owner_processing import (
    OwnerLocalProcessingAuthorization,
    OwnerLocalProcessingOperation,
)
from ultron.domain.content_strategy import (
    ContentAngle,
    ContentMetadata,
    ContentStrategyBrief,
    EditorialPlan,
    FactualClaim,
    QualityEvaluation,
    ThumbnailBrief,
    ContentPackageCheckpoint,
    ContentPackageStage,
    AngleSelectionDecision,
    ProductionReadinessDecision,
    ProductionReadiness,
)
from ultron.domain.content_production import (
    MediaArtifact,
    MediaProductionPlan,
    MediaProductionState,
    MediaSubtitleArtifact,
    MediaValidationReport,
)
from ultron.domain.content_performance import (
    ContentPerformanceMeasurement,
    ContentPerformanceRecommendation,
    HumanInsightDecision,
    HumanInsightDecisionKind,
    PerformanceAnalysis,
    PerformanceStrategyRevision,
    ProductionCostReport,
)
from ultron.domain.content_distribution import (
    DistributionJob,
    DistributionJobState,
    DistributionRecord,
    PublicationPackage,
    PublicationPackageApproval,
    PublicationApprovalState,
)
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

    SCHEMA_VERSION = 10
    LEGACY_BASE_SCHEMA_VERSION = 7
    PUBLISHING_ATTEMPT_SCHEMA_VERSION = 9
    PRIVATE_RENDER_OPERATION_SCHEMA_VERSION = SCHEMA_VERSION
    RUNTIME_SCHEMA_VERSION = SCHEMA_VERSION
    VISUAL_ANALYSIS_SCHEMA_VERSION = 2
    ACQUISITION_REQUEST_SCHEMA_VERSION = 4
    OWNER_RIGHTS_ATTESTATION_SCHEMA_VERSION = 1
    OWNER_LOCAL_PROCESSING_EXPIRATION_SCHEMA_VERSION = 1
    OWNER_LOCAL_PROCESSING_AUTHORIZATION_TTL = timedelta(days=365)
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
        allow_legacy_v10_reconciliation: bool = False,
    ) -> None:
        self.allow_legacy_v10_reconciliation = allow_legacy_v10_reconciliation
        database_path = str(database)
        if database_path == ":memory:":
            path = None
            self.database_identity = ":memory:"
        else:
            path = Path(database_path)
            if path.is_symlink():
                raise ValueError("Content database path must not be a symbolic link.")
            self.database_identity = str(path.resolve())

        self._maintenance_control = (
            None
            if self.database_identity == ":memory:"
            else CreMaintenanceControl(self.database_identity)
        )
        self._writer_lease = (
            None
            if self._maintenance_control is None
            else self._maintenance_control.writer_lease()
        )
        if self._writer_lease is not None:
            self._writer_lease.__enter__()
        self._lock = threading.RLock()
        self._visual_analysis_recovery_complete = False
        self._visual_analysis_worker_lock_fd: int | None = None
        self._visual_analysis_worker_token: object | None = None
        self._visual_analysis_active_claims: set[tuple[str, str]] = set()
        self._transaction_savepoint_counter = 0
        self._authorization_authorities: list[WebSocketAuthenticator] = []
        self._authorization_authorities_sealed = False
        try:
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
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
            self._connection = sqlite3.connect(
                database_path,
                timeout=10.0,
                check_same_thread=False,
                isolation_level=None,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA busy_timeout = 10000")
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._enable_wal_mode(self._connection)
            self._connection.execute("PRAGMA synchronous = FULL")
            self._initialize_schema()
            self.migrate_owner_rights_attestation_schema()
            if recover_interrupted_jobs:
                self._recover_interrupted_processing_jobs()
        except BaseException:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
            if self._writer_lease is not None:
                self._writer_lease.__exit__(None, None, None)
                self._writer_lease = None
            raise

    @staticmethod
    def _enable_wal_mode(connection: sqlite3.Connection) -> None:
        deadline = time.monotonic() + 10.0
        while True:
            try:
                journal_mode = connection.execute(
                    "PRAGMA journal_mode = WAL"
                ).fetchone()[0]
            except sqlite3.OperationalError as error:
                error_code = getattr(error, "sqlite_errorcode", None)
                if (
                    type(error_code) is not int
                    or error_code & 0xFF
                    not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                    or time.monotonic() >= deadline
                ):
                    raise
                time.sleep(0.025)
                continue
            if journal_mode.lower() != "wal":
                raise RuntimeError("Content database could not enable WAL mode.")
            return

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
                self._create_current_runtime_schema(connection)
                self.verify_complete_runtime_schema(connection)
                self._verify_database_integrity(connection)
                return
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
            if row["schema_version"] == self.SCHEMA_VERSION:
                if self.allow_legacy_v10_reconciliation:
                    self._verify_supported_legacy_v10_reconciliation(connection)
                else:
                    self.verify_complete_runtime_schema(connection)
                self._verify_database_integrity(connection)
                return
            if (
                row is not None
                and row["schema_version"] > self.SCHEMA_VERSION
            ):
                raise RuntimeError(
                    "Content database schema is newer than this application supports."
                )
            self._create_tables(connection)
            self._create_strategy_tables(connection)
            self._ensure_workflow_task_columns(connection)
            self._ensure_configured_feed_columns(connection)
            self._ensure_clip_quality_columns(connection)
            if row is None:
                connection.execute(
                    "INSERT INTO content_schema_meta(singleton, schema_version) "
                    "VALUES (1, ?)",
                    (self.LEGACY_BASE_SCHEMA_VERSION,),
                )
            elif row["schema_version"] < self.LEGACY_BASE_SCHEMA_VERSION:
                if row["schema_version"] not in {1, 2, 3, 4, 5, 6}:
                    raise RuntimeError(
                        "Content database requires an explicit schema migration."
                    )
                self._migrate_schema(connection, row["schema_version"])
                connection.execute(
                    "UPDATE content_schema_meta SET schema_version = ? "
                    "WHERE singleton = 1",
                    (self.LEGACY_BASE_SCHEMA_VERSION,),
                )
            elif row["schema_version"] == self.PUBLISHING_ATTEMPT_SCHEMA_VERSION:
                self._verify_publishing_attempt_schema(connection)
            elif row["schema_version"] == self.SCHEMA_VERSION:
                self._verify_publishing_attempt_schema(connection)
                self._verify_private_render_operation_schema(connection)
            self._verify_database_integrity(connection)

    @classmethod
    def _create_current_runtime_schema(
        cls,
        connection: sqlite3.Connection,
    ) -> None:
        connection.execute(cls._CONTENT_SCHEMA_META_DDL)
        cls._create_tables(connection)
        cls._create_strategy_tables(connection)
        cls._ensure_workflow_task_columns(connection)
        cls._ensure_configured_feed_columns(connection)
        cls._ensure_clip_quality_columns(connection)
        cls._create_content_identity_schema(connection)
        cls._verify_content_identity_schema(connection)
        cls._create_publishing_attempt_tables(connection)
        cls._upgrade_publishing_attempt_schema_v9(connection)
        cls._create_private_render_operation_tables(connection)
        cls._verify_publishing_attempt_schema(connection)
        cls._verify_private_render_operation_schema(connection)
        cls._verify_database_integrity(connection)
        connection.execute(
            "INSERT INTO content_schema_meta(singleton, schema_version) VALUES (1, ?)",
            (cls.SCHEMA_VERSION,),
        )
        cls.verify_runtime_schema_metadata(connection)

    @staticmethod
    def _create_content_identity_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS content_source_identity_schema_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                schema_version INTEGER NOT NULL CHECK (schema_version > 0)
            )
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO content_source_identity_schema_meta
                (singleton, schema_version) VALUES (1, 1)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS content_source_identities (
                source_id TEXT PRIMARY KEY
                    REFERENCES content_sources(source_id) ON DELETE CASCADE,
                source_identity TEXT,
                provider TEXT,
                provider_content_id TEXT,
                original_url TEXT,
                resolved_url TEXT,
                metadata_fingerprint TEXT,
                legacy_metadata_digest TEXT,
                deduplication_key TEXT NOT NULL UNIQUE,
                CHECK (provider_content_id IS NULL OR provider IS NOT NULL)
            )
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS content_source_provider_item_identity
            ON content_source_identities(provider, provider_content_id)
            WHERE provider IS NOT NULL AND provider_content_id IS NOT NULL
            """
        )

    @staticmethod
    def _verify_database_integrity(connection: sqlite3.Connection) -> None:
        integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError("Content database failed SQLite integrity check.")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("Content database contains broken foreign keys.")

    @classmethod
    def verify_complete_runtime_schema(
        cls,
        connection: sqlite3.Connection,
    ) -> None:
        """Reject missing, extra, or altered schema objects without mutation."""
        expected_connection = sqlite3.connect(":memory:")
        expected_connection.row_factory = sqlite3.Row
        try:
            cls._create_current_runtime_schema(expected_connection)
            visual_schema_exists = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'content_visual_analysis_schema_meta'
                """
            ).fetchone()
            if visual_schema_exists is not None:
                cls._verify_visual_analysis_schema(connection)
                cls._create_visual_analysis_tables(expected_connection)
                expected_connection.execute(
                    """
                    INSERT INTO content_visual_analysis_schema_meta(
                        singleton, schema_version
                    ) VALUES (1, ?)
                    """,
                    (cls.VISUAL_ANALYSIS_SCHEMA_VERSION,),
                )
            acquisition_schema_exists = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'content_acquisition_request_schema_meta'
                """
            ).fetchone()
            if acquisition_schema_exists is not None:
                cls._verify_acquisition_request_schema(connection)
                cls._create_acquisition_request_tables(expected_connection)
                expected_connection.execute(
                    """
                    INSERT INTO content_acquisition_request_schema_meta(
                        singleton, schema_version
                    ) VALUES (1, ?)
                    """,
                    (cls.ACQUISITION_REQUEST_SCHEMA_VERSION,),
                )
            owner_attestation_schema_exists = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'owner_rights_attestation_schema_meta'
                """
            ).fetchone()
            if owner_attestation_schema_exists is not None:
                cls._verify_owner_rights_attestation_schema(connection)
                cls._create_owner_rights_attestation_tables(expected_connection)
                expected_connection.execute(
                    """
                    INSERT INTO owner_rights_attestation_schema_meta(
                        singleton, schema_version
                    ) VALUES (1, ?)
                    """,
                    (cls.OWNER_RIGHTS_ATTESTATION_SCHEMA_VERSION,),
                )
            owner_local_processing_schema_exists = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'content_owner_local_processing_schema_meta'
                """
            ).fetchone()
            if owner_local_processing_schema_exists is not None:
                cls._verify_owner_local_processing_schema(connection)
                cls._create_owner_local_processing_tables(expected_connection)
                expected_connection.execute(
                    """
                    INSERT INTO content_owner_local_processing_schema_meta(
                        singleton, schema_version
                    ) VALUES (1, 1)
                    """
                )
                owner_processing_expiration_schema_exists = connection.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type = 'table'
                      AND name = 'content_owner_local_processing_expiration_schema_meta'
                    """
                ).fetchone()
                if owner_processing_expiration_schema_exists is not None:
                    cls._verify_owner_local_processing_expiration_schema(connection)
                    cls._create_owner_local_processing_expiration_tables(
                        expected_connection
                    )
                    expected_connection.execute(
                        """
                        INSERT INTO content_owner_local_processing_expiration_schema_meta(
                            singleton, schema_version
                        ) VALUES (1, ?)
                        """,
                        (cls.OWNER_LOCAL_PROCESSING_EXPIRATION_SCHEMA_VERSION,),
                    )
            expected = {
                (row["type"], row["name"]): (
                    row["tbl_name"],
                    cls._normalize_schema_sql(row["sql"]),
                )
                for row in expected_connection.execute(
                    """
                    SELECT type, name, tbl_name, sql FROM sqlite_master
                    WHERE type IN ('table', 'index', 'trigger', 'view')
                      AND name NOT LIKE 'sqlite_%'
                    """
                )
            }
        finally:
            expected_connection.close()
        actual = {
            (row["type"], row["name"]): (
                row["tbl_name"],
                cls._normalize_schema_sql(row["sql"]),
            )
            for row in connection.execute(
                """
                SELECT type, name, tbl_name, sql FROM sqlite_master
                WHERE type IN ('table', 'index', 'trigger', 'view')
                  AND name NOT LIKE 'sqlite_%'
                """
            )
        }
        if actual != expected:
            missing = sorted(set(expected) - set(actual))
            unexpected = sorted(set(actual) - set(expected))
            changed = sorted(key for key in actual.keys() & expected.keys() if actual[key] != expected[key])
            raise RuntimeError(
                "Content runtime schema is incomplete or inconsistent "
                f"(missing={missing[:5]}, unexpected={unexpected[:5]}, "
                f"changed={changed[:5]})."
            )

    @staticmethod
    def _ensure_workflow_task_columns(connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(content_workflow_tasks)")
        }
        definitions = {
            "worker_id": "TEXT",
            "lease_token": "TEXT",
            "lease_expires_at": "TEXT",
            "started_at": "TEXT",
            "completed_at": "TEXT",
            "failure_class": "TEXT",
            "attempted_at": "TEXT",
            "next_attempt_at": "TEXT",
            "max_attempts": "INTEGER NOT NULL DEFAULT 3",
            "external_side_effect": "INTEGER NOT NULL DEFAULT 0",
            "input_json": "TEXT NOT NULL DEFAULT '{}'",
        }
        for column, definition in definitions.items():
            if column not in columns:
                connection.execute(
                    f"ALTER TABLE content_workflow_tasks ADD COLUMN {column} {definition}"
                )

    @staticmethod
    def _ensure_configured_feed_columns(connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(content_configured_feeds)")
        }
        definitions = {
            "last_ingestion_count": "INTEGER NOT NULL DEFAULT 0",
            "last_duplicate_count": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, definition in definitions.items():
            if column not in columns:
                connection.execute(
                    f"ALTER TABLE content_configured_feeds ADD COLUMN {column} {definition}"
                )

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
                self._verify_publishing_attempt_schema(connection)
                self._verify_private_render_operation_schema(connection)
                return version
            if version == self.LEGACY_BASE_SCHEMA_VERSION:
                self._create_publishing_attempt_tables(connection)
                connection.execute(
                    "UPDATE content_schema_meta SET schema_version = 8 "
                    "WHERE singleton = 1 AND schema_version = ?",
                    (self.LEGACY_BASE_SCHEMA_VERSION,),
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

    @classmethod
    def _verify_supported_legacy_v10_reconciliation(
        cls,
        connection: sqlite3.Connection,
    ) -> None:
        """Accept only the known legacy-v10 fixture shape or a reconciled v10 DB."""
        cls._verify_private_render_operation_schema(connection)

        unexpected_views = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'view' LIMIT 1
            """
        ).fetchone()
        if unexpected_views is not None:
            raise RuntimeError(
                "The schema-v10 database contains an unexpected view."
            )

        identity_meta = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table'
              AND name = 'content_source_identity_schema_meta'
            """
        ).fetchone()
        if identity_meta is not None:
            cls._verify_content_identity_schema(connection)
            cls.verify_complete_runtime_schema(connection)
            cls._verify_database_integrity(connection)
            return

        license_table = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table'
              AND name = 'content_asset_license_records'
            """
        ).fetchone()
        source_indexes = {
            tuple(
                row["name"]
                for row in connection.execute(
                    f"PRAGMA index_info({index['name']})"
                )
            )
            for index in connection.execute("PRAGMA index_list(content_sources)")
            if index["unique"]
        }
        opportunity_indexes = {
            tuple(
                row["name"]
                for row in connection.execute(
                    f"PRAGMA index_info({index['name']})"
                )
            )
            for index in connection.execute("PRAGMA index_list(content_opportunities)")
            if index["unique"]
        }
        legacy_source = ("canonical_url",) in source_indexes
        legacy_opportunity = (
            "normalized_topic",
            "niche",
            "target_language",
            "target_region",
        ) in opportunity_indexes

        source_columns = [
            row["name"]
            for row in connection.execute("PRAGMA table_info(content_sources)")
        ]
        opportunity_columns = [
            row["name"]
            for row in connection.execute("PRAGMA table_info(content_opportunities)")
        ]
        expected_source_columns = [
            "source_id", "canonical_url", "source_name", "publisher",
            "source_type", "published_at", "retrieved_at", "reliability",
            "content_sha256", "license_classification",
        ]
        expected_opportunity_columns = [
            "opportunity_id", "topic", "normalized_topic", "niche",
            "target_audience", "target_language", "target_region",
            "content_format", "discovered_at", "status",
            "relevance_assessment", "evidence_count", "research_status",
            "workflow_id", "version", "created_at", "updated_at",
        ]
        if source_columns != expected_source_columns:
            raise RuntimeError(
                "The schema-v10 database has an unexpected content_sources shape."
            )
        if opportunity_columns != expected_opportunity_columns:
            raise RuntimeError(
                "The schema-v10 database has an unexpected content_opportunities shape."
            )

        if license_table is not None and not legacy_source and not legacy_opportunity:
            raise RuntimeError(
                "The schema-v10 database is not the supported legacy reconciliation shape."
            )
        if not legacy_source and not legacy_opportunity:
            raise RuntimeError(
                "The schema-v10 database has an unsupported source/opportunity identity shape."
            )

    def migrate_v10_schema_reconciliation(self) -> int:
        """Explicitly reconcile the supported legacy-v10 snapshot shape."""
        with self._lock:
            if self._connection.in_transaction:
                raise RuntimeError(
                    "V10 schema reconciliation requires an idle connection."
                )
            foreign_keys_enabled = bool(
                self._connection.execute("PRAGMA foreign_keys").fetchone()[0]
            )
            if foreign_keys_enabled:
                self._connection.execute("PRAGMA foreign_keys = OFF")
            try:
                with self._transaction() as connection:
                    row = connection.execute(
                        "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
                    ).fetchone()
                    if row is None or row["schema_version"] != self.SCHEMA_VERSION:
                        raise RuntimeError(
                            "V10 schema reconciliation requires an exact schema-v10 database."
                        )

                    self._verify_supported_legacy_v10_reconciliation(connection)
                    if not self._table_exists(
                        connection, "content_asset_license_records"
                    ):
                        self._create_tables(connection)
                        self._verify_database_integrity(connection)

                    if not self._table_exists(
                        connection, "content_source_identity_schema_meta"
                    ):
                        self.migrate_source_identity_schema(standalone=False)

                    self.verify_complete_runtime_schema(connection)
                    self._verify_database_integrity(connection)
            finally:
                if foreign_keys_enabled:
                    self._connection.execute("PRAGMA foreign_keys = ON")
            return self.SCHEMA_VERSION

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

    def migrate_owner_local_processing_schema(self) -> int:
        """Explicitly add owner-authorized local-processing records to schema v10."""
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
            ).fetchone()
            if row is None or row["schema_version"] != self.SCHEMA_VERSION:
                raise RuntimeError(
                    "Owner local-processing migration requires an exact schema-v10 database."
                )
            meta_exists = self._table_exists(
                connection, "content_owner_local_processing_schema_meta"
            )
            auth_tables_exist = self._table_exists(
                connection, "content_owner_local_processing_authorizations"
            ) or self._table_exists(
                connection, "content_owner_local_processing_events"
            )
            expiration_tables_exist = self._table_exists(
                connection,
                "content_owner_local_processing_expirations",
            ) or self._table_exists(
                connection,
                "content_owner_local_processing_expiration_schema_meta",
            )
            if not meta_exists:
                if auth_tables_exist or expiration_tables_exist:
                    raise RuntimeError(
                        "Owner local-processing metadata is missing for existing tables."
                    )
                self._create_owner_local_processing_tables(connection)
                connection.execute(
                    """
                    INSERT INTO content_owner_local_processing_schema_meta
                        (singleton, schema_version)
                    VALUES (1, 1)
                    """
                )
            owner_local_processing_expiration_schema_exists = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'content_owner_local_processing_expiration_schema_meta'
                """
            ).fetchone()
            if owner_local_processing_expiration_schema_exists is not None:
                self._verify_owner_local_processing_expiration_schema(connection)
            else:
                columns = {
                    item["name"]
                    for item in connection.execute(
                        "PRAGMA table_info(content_owner_local_processing_schema_meta)"
                    )
                }
                if columns != {"singleton", "schema_version"}:
                    raise RuntimeError(
                        "Owner local-processing schema metadata is malformed."
                    )
                version = connection.execute(
                    """
                    SELECT schema_version
                    FROM content_owner_local_processing_schema_meta
                    WHERE singleton = 1
                    """
                ).fetchone()
                if version is None or version["schema_version"] != 1:
                    raise RuntimeError(
                        "Owner local-processing schema version is unsupported."
                    )
            self._verify_owner_local_processing_schema(connection)
            self._migrate_owner_local_processing_expiration_schema(connection)
            self._verify_owner_local_processing_expiration_schema(connection)
            self.verify_complete_runtime_schema(connection)
            return 1

    def _migrate_owner_local_processing_expiration_schema(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        meta_exists = self._table_exists(
            connection,
            "content_owner_local_processing_expiration_schema_meta",
        )
        table_exists = self._table_exists(
            connection,
            "content_owner_local_processing_expirations",
        )
        if not meta_exists:
            if table_exists:
                raise RuntimeError(
                    "Owner-processing expiration metadata is missing for existing records."
                )
            self._create_owner_local_processing_expiration_tables(connection)
            connection.execute(
                """
                INSERT INTO content_owner_local_processing_expiration_schema_meta
                    (singleton, schema_version)
                VALUES (1, ?)
                """,
                (self.OWNER_LOCAL_PROCESSING_EXPIRATION_SCHEMA_VERSION,),
            )
            rows = connection.execute(
                """
                SELECT authorization_id, authorized_at
                FROM content_owner_local_processing_authorizations
                ORDER BY authorization_id
                """
            ).fetchall()
            for row in rows:
                authorized_at = datetime.fromisoformat(row["authorized_at"])
                if authorized_at.tzinfo is None or authorized_at.utcoffset() is None:
                    raise RuntimeError(
                        "Existing owner-processing authorization has an invalid timestamp."
                    )
                expires_at = (
                    authorized_at
                    + self.OWNER_LOCAL_PROCESSING_AUTHORIZATION_TTL
                ).isoformat()
                connection.execute(
                    """
                    INSERT INTO content_owner_local_processing_expirations (
                        authorization_id, expires_at
                    ) VALUES (?, ?)
                    """,
                    (row["authorization_id"], expires_at),
                )
            return
        columns = {
            item["name"]
            for item in connection.execute(
                "PRAGMA table_info(content_owner_local_processing_expiration_schema_meta)"
            )
        }
        if columns != {"singleton", "schema_version"}:
            raise RuntimeError(
                "Owner-processing expiration metadata is malformed."
            )
        row = connection.execute(
            """
            SELECT schema_version
            FROM content_owner_local_processing_expiration_schema_meta
            WHERE singleton = 1
            """
        ).fetchone()
        if (
            row is None
            or row["schema_version"]
            != self.OWNER_LOCAL_PROCESSING_EXPIRATION_SCHEMA_VERSION
        ):
            raise RuntimeError(
                "Owner-processing expiration schema version is unsupported."
            )
        if not table_exists:
            raise RuntimeError("Owner-processing expiration records are missing.")
        missing_expirations = connection.execute(
            """
            SELECT 1
            FROM content_owner_local_processing_authorizations a
            LEFT JOIN content_owner_local_processing_expirations e
              ON e.authorization_id = a.authorization_id
            WHERE e.authorization_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        if missing_expirations is not None:
            raise RuntimeError(
                "Owner-processing authorization is missing its expiration."
            )

    @staticmethod
    def _create_owner_local_processing_tables(
        connection: sqlite3.Connection,
    ) -> None:
        statements = (
            """
            CREATE TABLE content_owner_local_processing_schema_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                schema_version INTEGER NOT NULL CHECK (schema_version = 1)
            )
            """,
            """
            CREATE TABLE content_owner_local_processing_authorizations (
                authorization_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                asset_id TEXT NOT NULL REFERENCES content_assets(asset_id),
                asset_sha256 TEXT NOT NULL CHECK (
                    length(asset_sha256) = 64
                    AND asset_sha256 NOT GLOB '*[^0-9a-f]*'
                ),
                operations_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
                authorized_at TEXT NOT NULL,
                revoked_at TEXT,
                revocation_reason TEXT
            )
            """,
            """
            CREATE INDEX content_owner_local_processing_asset
            ON content_owner_local_processing_authorizations(
                owner_id, asset_id, asset_sha256, status, authorized_at
            )
            """,
            """
            CREATE TABLE content_owner_local_processing_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                authorization_id TEXT NOT NULL
                    REFERENCES content_owner_local_processing_authorizations(
                        authorization_id
                    ),
                owner_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                asset_sha256 TEXT NOT NULL,
                event_type TEXT NOT NULL CHECK (
                    event_type IN ('authorized', 'operation_authorized', 'revoked')
                ),
                operation TEXT,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX content_owner_local_processing_events_order
            ON content_owner_local_processing_events(authorization_id, sequence)
            """,
            """
            CREATE TRIGGER content_owner_local_processing_identity_immutable
            BEFORE UPDATE ON content_owner_local_processing_authorizations
            WHEN OLD.authorization_id != NEW.authorization_id
              OR OLD.owner_id != NEW.owner_id
              OR OLD.asset_id != NEW.asset_id
              OR OLD.asset_sha256 != NEW.asset_sha256
              OR OLD.operations_json != NEW.operations_json
              OR OLD.authorized_at != NEW.authorized_at
              OR OLD.status != 'active'
              OR NEW.status != 'revoked'
              OR OLD.revoked_at IS NOT NULL
              OR NEW.revoked_at IS NULL
              OR NEW.revocation_reason IS NULL
            BEGIN
                SELECT RAISE(ABORT, 'owner local-processing scope is immutable');
            END
            """,
            """
            CREATE TRIGGER content_owner_local_processing_events_immutable_update
            BEFORE UPDATE ON content_owner_local_processing_events
            BEGIN
                SELECT RAISE(ABORT, 'owner local-processing events are append-only');
            END
            """,
            """
            CREATE TRIGGER content_owner_local_processing_events_immutable_delete
            BEFORE DELETE ON content_owner_local_processing_events
            BEGIN
                SELECT RAISE(ABORT, 'owner local-processing events are append-only');
            END
            """,
        )
        for statement in statements:
            connection.execute(statement)

    @staticmethod
    def _create_owner_local_processing_expiration_tables(
        connection: sqlite3.Connection,
    ) -> None:
        statements = (
            """
            CREATE TABLE content_owner_local_processing_expiration_schema_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                schema_version INTEGER NOT NULL CHECK (schema_version = 1)
            )
            """,
            """
            CREATE TABLE content_owner_local_processing_expirations (
                authorization_id TEXT PRIMARY KEY
                    REFERENCES content_owner_local_processing_authorizations(
                        authorization_id
                    ),
                expires_at TEXT NOT NULL
            )
            """,
            """
            CREATE TRIGGER content_owner_local_processing_expirations_immutable_update
            BEFORE UPDATE ON content_owner_local_processing_expirations
            BEGIN
                SELECT RAISE(ABORT, 'owner-processing expiration is immutable');
            END
            """,
            """
            CREATE TRIGGER content_owner_local_processing_expirations_immutable_delete
            BEFORE DELETE ON content_owner_local_processing_expirations
            BEGIN
                SELECT RAISE(ABORT, 'owner-processing expiration is immutable');
            END
            """,
        )
        for statement in statements:
            connection.execute(statement)

    @staticmethod
    def _verify_owner_local_processing_expiration_schema(
        connection: sqlite3.Connection,
    ) -> None:
        expected = {
            "content_owner_local_processing_expiration_schema_meta": {
                "singleton",
                "schema_version",
            },
            "content_owner_local_processing_expirations": {
                "authorization_id",
                "expires_at",
            },
        }
        for table, columns in expected.items():
            if not ContentStore._table_exists(connection, table):
                raise RuntimeError(
                    f"Owner local-processing expiration schema is missing {table}."
                )
            actual = {
                item["name"]
                for item in connection.execute(f"PRAGMA table_info({table})")
            }
            if actual != columns:
                raise RuntimeError(
                    f"Owner local-processing expiration table {table} has unexpected columns."
                )
        row = connection.execute(
            """
            SELECT schema_version
            FROM content_owner_local_processing_expiration_schema_meta
            WHERE singleton = 1
            """
        ).fetchone()
        if (
            row is None
            or row["schema_version"]
            != ContentStore.OWNER_LOCAL_PROCESSING_EXPIRATION_SCHEMA_VERSION
        ):
            raise RuntimeError(
                "Owner local-processing expiration schema version is unsupported."
            )
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError(
                "Owner local-processing expiration schema has foreign-key violations."
            )

    @staticmethod
    def _verify_owner_local_processing_schema(
        connection: sqlite3.Connection,
    ) -> None:
        expected = {
            "content_owner_local_processing_schema_meta": {
                "singleton",
                "schema_version",
            },
            "content_owner_local_processing_authorizations": {
                "authorization_id",
                "owner_id",
                "asset_id",
                "asset_sha256",
                "operations_json",
                "status",
                "authorized_at",
                "revoked_at",
                "revocation_reason",
            },
            "content_owner_local_processing_events": {
                "sequence",
                "authorization_id",
                "owner_id",
                "asset_id",
                "asset_sha256",
                "event_type",
                "operation",
                "actor",
                "reason",
                "created_at",
            },
        }
        for table, columns in expected.items():
            if not ContentStore._table_exists(connection, table):
                raise RuntimeError(
                    f"Owner local-processing schema is missing {table}."
                )
            actual = {
                item["name"]
                for item in connection.execute(f"PRAGMA table_info({table})")
            }
            if actual != columns:
                raise RuntimeError(
                    f"Owner local-processing table {table} has unexpected columns."
                )
        foreign_keys = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if foreign_keys:
            raise RuntimeError("Owner local-processing schema has foreign-key violations.")

    def create_owner_local_processing_authorization_in_transaction(
        self,
        connection: sqlite3.Connection,
        authorization: OwnerLocalProcessingAuthorization,
        *,
        actor: str,
        audit_reason: str = "explicit_owner_local_processing_authorization",
    ) -> OwnerLocalProcessingAuthorization:
        self._require_owner_local_processing_schema(connection)
        if type(authorization) is not OwnerLocalProcessingAuthorization:
            raise TypeError("A validated owner local-processing authorization is required.")
        if actor != authorization.owner_id:
            raise PermissionError("Only the authenticated asset owner can authorize processing.")
        if (
            authorization.expires_at
            != authorization.authorized_at
            + self.OWNER_LOCAL_PROCESSING_AUTHORIZATION_TTL
        ):
            raise ValueError(
                "Owner local-processing authorization must use the 365-day lifetime."
            )
        if not audit_reason.strip() or len(audit_reason.strip()) > 1000:
            raise ValueError("Owner authorization requires a bounded audit reason.")
        self._require_owner_created_asset_in_transaction(
            connection,
            owner_id=authorization.owner_id,
            asset_id=authorization.asset_id,
            asset_sha256=authorization.asset_sha256,
        )
        existing = connection.execute(
            """
            SELECT a.*, e.expires_at
            FROM content_owner_local_processing_authorizations a
            JOIN content_owner_local_processing_expirations e
              ON e.authorization_id = a.authorization_id
            WHERE a.authorization_id = ?
            """,
            (authorization.authorization_id,),
        ).fetchone()
        operations_json = self._canonical(
            sorted(operation.value for operation in authorization.operations)
        )
        if existing is not None:
            if (
                existing["owner_id"] == authorization.owner_id
                and existing["asset_id"] == authorization.asset_id
                and existing["asset_sha256"] == authorization.asset_sha256
                and existing["operations_json"] == operations_json
                and existing["authorized_at"] == authorization.authorized_at.isoformat()
                and existing["expires_at"] == authorization.expires_at.isoformat()
            ):
                return self._load_owner_local_processing_authorization(existing)
            raise ValueError("Owner authorization ID conflicts with an existing record.")
        active = connection.execute(
            """
            SELECT 1
            FROM content_owner_local_processing_authorizations a
            JOIN content_owner_local_processing_expirations e
              ON e.authorization_id = a.authorization_id
            WHERE a.owner_id = ? AND a.asset_id = ? AND a.status = 'active'
              AND e.expires_at > ?
            LIMIT 1
            """,
            (
                authorization.owner_id,
                authorization.asset_id,
                self._now(),
            ),
        ).fetchone()
        if active is not None:
            raise ValueError(
                "An active owner authorization must be revoked before replacement."
            )
        connection.execute(
            """
            INSERT INTO content_owner_local_processing_authorizations (
                authorization_id, owner_id, asset_id, asset_sha256,
                operations_json, status, authorized_at, revoked_at,
                revocation_reason
            ) VALUES (?, ?, ?, ?, ?, 'active', ?, NULL, NULL)
            """,
            (
                authorization.authorization_id,
                authorization.owner_id,
                authorization.asset_id,
                authorization.asset_sha256,
                operations_json,
                authorization.authorized_at.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO content_owner_local_processing_expirations (
                authorization_id, expires_at
            ) VALUES (?, ?)
            """,
            (
                authorization.authorization_id,
                authorization.expires_at.isoformat(),
            ),
        )
        self._append_owner_local_processing_event(
            connection,
            authorization=authorization,
            event_type="authorized",
            operation=None,
            actor=actor,
            reason=audit_reason.strip(),
            created_at=authorization.authorized_at.isoformat(),
        )
        return authorization

    def require_owner_local_processing_authorization_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        owner_id: str,
        asset_id: str,
        asset_sha256: str,
        operation: OwnerLocalProcessingOperation,
        actor: str,
    ) -> OwnerLocalProcessingAuthorization:
        self._require_owner_local_processing_schema(connection)
        if type(operation) is not OwnerLocalProcessingOperation:
            raise TypeError("A supported local-processing operation is required.")
        if actor not in {owner_id, "system:visual_analysis_worker"}:
            raise PermissionError("Owner local-processing authorization is account-scoped.")
        self._require_owner_created_asset_in_transaction(
            connection,
            owner_id=owner_id,
            asset_id=asset_id,
            asset_sha256=asset_sha256,
        )
        row = connection.execute(
            """
            SELECT a.*, e.expires_at
            FROM content_owner_local_processing_authorizations a
            JOIN content_owner_local_processing_expirations e
              ON e.authorization_id = a.authorization_id
            WHERE a.owner_id = ? AND a.asset_id = ? AND a.asset_sha256 = ?
              AND a.status = 'active'
            ORDER BY a.authorized_at DESC, a.authorization_id DESC LIMIT 1
            """,
            (owner_id, asset_id, asset_sha256),
        ).fetchone()
        if row is None:
            raise PermissionError(
                "No active owner authorization matches this asset and hash."
            )
        authorization = self._load_owner_local_processing_authorization(row)
        now = datetime.fromisoformat(self._now())
        if now >= authorization.expires_at:
            raise PermissionError("Owner local-processing authorization has expired.")
        if operation not in authorization.operations:
            raise PermissionError(
                "Owner authorization does not permit this local-processing operation."
            )
        self._append_owner_local_processing_event(
            connection,
            authorization=authorization,
            event_type="operation_authorized",
            operation=operation,
            actor=actor,
            reason="owner_local_processing_authorized",
            created_at=self._now(),
        )
        return authorization

    def revoke_owner_local_processing_authorization_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        authorization_id: str,
        owner_id: str,
        reason: str,
    ) -> OwnerLocalProcessingAuthorization:
        self._require_owner_local_processing_schema(connection)
        row = connection.execute(
            """
            SELECT a.*, e.expires_at
            FROM content_owner_local_processing_authorizations a
            JOIN content_owner_local_processing_expirations e
              ON e.authorization_id = a.authorization_id
            WHERE a.authorization_id = ? AND a.owner_id = ?
            """,
            (authorization_id, owner_id),
        ).fetchone()
        if row is None:
            raise PermissionError(
                "Owner local-processing authorization is outside the account."
            )
        if row["status"] == "revoked":
            return self._load_owner_local_processing_authorization(row)
        revoked_at = self._now()
        connection.execute(
            """
            UPDATE content_owner_local_processing_authorizations
            SET status = 'revoked', revoked_at = ?, revocation_reason = ?
            WHERE authorization_id = ? AND owner_id = ? AND status = 'active'
            """,
            (revoked_at, reason, authorization_id, owner_id),
        )
        authorization = self._load_owner_local_processing_authorization(
            connection.execute(
                """
                SELECT a.*, e.expires_at
                FROM content_owner_local_processing_authorizations a
                JOIN content_owner_local_processing_expirations e
                  ON e.authorization_id = a.authorization_id
                WHERE a.authorization_id = ?
                """,
                (authorization_id,),
            ).fetchone()
        )
        self._append_owner_local_processing_event(
            connection,
            authorization=authorization,
            event_type="revoked",
            operation=None,
            actor=owner_id,
            reason=reason,
            created_at=revoked_at,
        )
        return authorization

    def get_owner_local_processing_authorization(
        self,
        authorization_id: str,
        owner_id: str,
    ) -> OwnerLocalProcessingAuthorization | None:
        self._require_owner_local_processing_schema(self._connection)
        row = self._connection.execute(
            """
            SELECT a.*, e.expires_at
            FROM content_owner_local_processing_authorizations a
            JOIN content_owner_local_processing_expirations e
              ON e.authorization_id = a.authorization_id
            WHERE a.authorization_id = ? AND a.owner_id = ?
            """,
            (authorization_id, owner_id),
        ).fetchone()
        return (
            None
            if row is None
            else self._load_owner_local_processing_authorization(row)
        )

    def get_owner_local_processing_authorization_for_asset(
        self,
        asset_id: str,
        asset_sha256: str,
        owner_id: str,
        *,
        active_only: bool = True,
    ) -> OwnerLocalProcessingAuthorization | None:
        self._require_owner_local_processing_schema(self._connection)
        row = self._connection.execute(
            """
            SELECT a.*, e.expires_at
            FROM content_owner_local_processing_authorizations a
            JOIN content_owner_local_processing_expirations e
              ON e.authorization_id = a.authorization_id
            WHERE a.asset_id = ? AND a.asset_sha256 = ? AND a.owner_id = ?
              AND (? = 0 OR (a.status = 'active' AND e.expires_at > ?))
            ORDER BY a.authorized_at DESC, a.authorization_id DESC LIMIT 1
            """,
            (
                asset_id,
                asset_sha256,
                owner_id,
                int(active_only),
                self._now(),
            ),
        ).fetchone()
        return (
            None
            if row is None
            else self._load_owner_local_processing_authorization(row)
        )

    def has_owner_local_processing_authorization_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        owner_id: str,
        asset_id: str,
    ) -> bool:
        if not self._table_exists(
            connection,
            "content_owner_local_processing_authorizations",
        ):
            return False
        self._require_owner_local_processing_schema(connection)
        return (
            connection.execute(
                """
                SELECT 1 FROM content_owner_local_processing_authorizations
                WHERE owner_id = ? AND asset_id = ? LIMIT 1
                """,
                (owner_id, asset_id),
            ).fetchone()
            is not None
        )

    def list_owner_local_processing_events(
        self,
        authorization_id: str,
        owner_id: str,
    ) -> list[dict[str, object]]:
        authorization = self.get_owner_local_processing_authorization(
            authorization_id,
            owner_id,
        )
        if authorization is None:
            raise PermissionError(
                "Owner local-processing authorization is outside the account."
            )
        rows = self._connection.execute(
            """
            SELECT sequence, event_type, operation, actor, reason, created_at
            FROM content_owner_local_processing_events
            WHERE authorization_id = ? AND owner_id = ?
            ORDER BY sequence
            """,
            (authorization_id, owner_id),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _require_owner_local_processing_schema(
        connection: sqlite3.Connection,
    ) -> None:
        if not ContentStore._table_exists(
            connection,
            "content_owner_local_processing_schema_meta",
        ):
            raise RuntimeError(
                "Owner local-processing schema is unavailable; apply its explicit "
                "schema-v10 migration to this database."
            )
        row = connection.execute(
            """
            SELECT schema_version FROM content_owner_local_processing_schema_meta
            WHERE singleton = 1
            """
        ).fetchone()
        if row is None or row["schema_version"] != 1:
            raise RuntimeError("Owner local-processing schema version is unsupported.")
        ContentStore._verify_owner_local_processing_expiration_schema(connection)

    @staticmethod
    def _require_owner_created_asset_in_transaction(
        connection: sqlite3.Connection,
        *,
        owner_id: str,
        asset_id: str,
        asset_sha256: str,
    ) -> None:
        row = connection.execute(
            """
            SELECT a.owner_id, a.content_sha256, a.provenance_json,
                   s.acquisition_method, s.claimed_source_sha256
            FROM content_assets a
            JOIN source_intakes s ON s.asset_id = a.asset_id
            WHERE a.asset_id = ? AND a.owner_id = ? AND s.owner_id = ?
            ORDER BY s.created_at DESC, s.intake_id DESC LIMIT 1
            """,
            (asset_id, owner_id, owner_id),
        ).fetchone()
        if row is None:
            raise PermissionError(
                "Owner local-processing authorization requires a registered owner upload."
            )
        try:
            provenance = json.loads(row["provenance_json"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("Registered asset provenance is malformed.") from exc
        if (
            row["owner_id"] != owner_id
            or row["content_sha256"] != asset_sha256
            or row["acquisition_method"] != AcquisitionMethod.OWNER_CREATED.value
            or provenance.get("acquisition_method")
            != AcquisitionMethod.OWNER_CREATED.value
            or row["claimed_source_sha256"] != asset_sha256
        ):
            raise PermissionError(
                "Owner authorization requires an exact owner-created asset and SHA-256."
            )
        if ContentStore._table_exists(connection, "content_acquisition_requests"):
            acquired = connection.execute(
                """
                SELECT 1 FROM content_acquisition_requests
                WHERE asset_id = ? LIMIT 1
                """,
                (asset_id,),
            ).fetchone()
            if acquired is not None:
                raise PermissionError(
                    "Internet-acquired assets cannot use owner local-processing authorization."
                )

    @staticmethod
    def _append_owner_local_processing_event(
        connection: sqlite3.Connection,
        *,
        authorization: OwnerLocalProcessingAuthorization,
        event_type: str,
        operation: OwnerLocalProcessingOperation | None,
        actor: str,
        reason: str,
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO content_owner_local_processing_events (
                authorization_id, owner_id, asset_id, asset_sha256,
                event_type, operation, actor, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                authorization.authorization_id,
                authorization.owner_id,
                authorization.asset_id,
                authorization.asset_sha256,
                event_type,
                None if operation is None else operation.value,
                actor,
                reason,
                created_at,
            ),
        )

    @staticmethod
    def _load_owner_local_processing_authorization(
        row: sqlite3.Row,
    ) -> OwnerLocalProcessingAuthorization:
        try:
            operations_data = json.loads(row["operations_json"])
            operations = frozenset(
                OwnerLocalProcessingOperation(value)
                for value in operations_data
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "Owner local-processing authorization has malformed operation scope."
            ) from exc
        return OwnerLocalProcessingAuthorization(
            authorization_id=row["authorization_id"],
            owner_id=row["owner_id"],
            asset_id=row["asset_id"],
            asset_sha256=row["asset_sha256"],
            operations=operations,
            authorized_at=datetime.fromisoformat(row["authorized_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            revoked_at=(
                None
                if row["revoked_at"] is None
                else datetime.fromisoformat(row["revoked_at"])
            ),
            revocation_reason=row["revocation_reason"],
        )

    def migrate_acquisition_request_schema(self) -> int:
        """Explicitly migrate durable acquisition requests on a schema-v10 database."""
        with self._transaction() as connection:
            main = connection.execute(
                "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
            ).fetchone()
            if main is None or main["schema_version"] != self.SCHEMA_VERSION:
                raise RuntimeError(
                    "Acquisition requests require the content schema-v10 foundation."
                )
            self._require_content_identity_schema(connection)
            meta_exists = self._table_exists(
                connection, "content_acquisition_request_schema_meta"
            )
            request_tables_exist = self._table_exists(
                connection, "content_acquisition_requests"
            ) or self._table_exists(
                connection, "content_acquisition_request_events"
            )
            if meta_exists:
                version = connection.execute(
                    """
                    SELECT schema_version FROM content_acquisition_request_schema_meta
                    WHERE singleton = 1
                    """
                ).fetchone()
                if version is None:
                    raise RuntimeError("Acquisition request schema metadata is missing.")
                if version["schema_version"] == 1:
                    self._upgrade_acquisition_request_schema_v1(connection)
                    version_number = 2
                else:
                    version_number = version["schema_version"]
                if version_number == 2:
                    self._upgrade_acquisition_request_schema_v2(connection)
                    version_number = 3
                if version_number == 3:
                    self._upgrade_acquisition_request_schema_v3(connection)
                elif version_number != self.ACQUISITION_REQUEST_SCHEMA_VERSION:
                    raise RuntimeError("Unsupported acquisition request schema version.")
                self._verify_acquisition_request_schema(connection)
                return self.ACQUISITION_REQUEST_SCHEMA_VERSION
            if request_tables_exist:
                raise RuntimeError(
                    "Acquisition request schema metadata is missing for existing tables."
                )
            self._create_acquisition_request_tables(connection)
            connection.execute(
                """
                INSERT INTO content_acquisition_request_schema_meta(
                    singleton, schema_version
                ) VALUES (1, ?)
                """,
                (self.ACQUISITION_REQUEST_SCHEMA_VERSION,),
            )
            self._verify_acquisition_request_schema(connection)
            return self.ACQUISITION_REQUEST_SCHEMA_VERSION

    def migrate_owner_rights_attestation_schema(self) -> int:
        """Create the additive, append-only owner self-attestation schema."""
        with self._transaction() as connection:
                main = connection.execute(
                    "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
                ).fetchone()
                if main is None:
                    raise RuntimeError(
                        "Owner rights attestations require content schema metadata."
                    )
                meta_exists = self._table_exists(
                    connection,
                    "owner_rights_attestation_schema_meta",
                )
                if main["schema_version"] == 8 and not meta_exists:
                    return 0
                supported_content_versions = {
                    self.LEGACY_BASE_SCHEMA_VERSION,
                    8,
                    self.PUBLISHING_ATTEMPT_SCHEMA_VERSION,
                    self.SCHEMA_VERSION,
                }
                if main["schema_version"] not in supported_content_versions:
                    raise RuntimeError(
                        "Owner rights attestations require a supported content schema."
                    )
                if meta_exists:
                    version = connection.execute(
                        """
                        SELECT schema_version FROM owner_rights_attestation_schema_meta
                        WHERE singleton = 1
                        """
                    ).fetchone()
                    if (
                        version is None
                        or version["schema_version"]
                        != self.OWNER_RIGHTS_ATTESTATION_SCHEMA_VERSION
                    ):
                        raise RuntimeError(
                            "Owner rights attestation schema version is unsupported."
                        )
                    self._verify_owner_rights_attestation_schema(connection)
                    return self.OWNER_RIGHTS_ATTESTATION_SCHEMA_VERSION
                if self._table_exists(connection, "owner_rights_attestation_events"):
                    raise RuntimeError(
                        "Owner rights attestation schema metadata is missing."
                    )
                self._create_owner_rights_attestation_tables(connection)
                connection.execute(
                    """
                    INSERT INTO owner_rights_attestation_schema_meta(
                        singleton, schema_version
                    ) VALUES (1, ?)
                    """,
                    (self.OWNER_RIGHTS_ATTESTATION_SCHEMA_VERSION,),
                )
                self._verify_owner_rights_attestation_schema(connection)
                return self.OWNER_RIGHTS_ATTESTATION_SCHEMA_VERSION

    @staticmethod
    def _create_owner_rights_attestation_tables(
        connection: sqlite3.Connection,
    ) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS owner_rights_attestation_schema_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                schema_version INTEGER NOT NULL CHECK (schema_version > 0)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS owner_rights_attestations (
                attestation_id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL REFERENCES content_assets(asset_id),
                asset_sha256 TEXT NOT NULL CHECK (length(asset_sha256) = 64),
                owner_id TEXT NOT NULL,
                ownership_declaration INTEGER NOT NULL CHECK (
                    ownership_declaration = 1
                ),
                ownership_basis TEXT NOT NULL,
                evidence_reference TEXT,
                platform TEXT NOT NULL CHECK (platform IN ('youtube', 'instagram')),
                account_scope TEXT NOT NULL,
                territories_json TEXT NOT NULL,
                permitted_actions_json TEXT NOT NULL,
                max_duration_seconds INTEGER NOT NULL CHECK (
                    max_duration_seconds BETWEEN 1 AND 86400
                ),
                limitations TEXT NOT NULL,
                valid_from TEXT NOT NULL,
                valid_until TEXT NOT NULL,
                transfer_channel TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS owner_rights_attestation_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                attestation_id TEXT NOT NULL
                    REFERENCES owner_rights_attestations(attestation_id),
                event_type TEXT NOT NULL CHECK (
                    event_type IN ('attested', 'revoked')
                ),
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(attestation_id, event_type)
            )
            """
        )
        for definition in ContentStore._owner_rights_attestation_triggers().values():
            connection.execute(definition)

    @classmethod
    def _verify_owner_rights_attestation_schema(
        cls,
        connection: sqlite3.Connection,
    ) -> None:
        meta = connection.execute(
                """
                SELECT schema_version FROM owner_rights_attestation_schema_meta
                WHERE singleton = 1
                """
        ).fetchone()
        if (
                meta is None
                or meta["schema_version"] != cls.OWNER_RIGHTS_ATTESTATION_SCHEMA_VERSION
        ):
                raise RuntimeError("Owner rights attestation schema metadata is inconsistent.")
        expected_columns = {
                "owner_rights_attestation_schema_meta": {
                    "singleton", "schema_version",
                },
                "owner_rights_attestations": {
                    "attestation_id", "asset_id", "asset_sha256", "owner_id",
                    "ownership_declaration", "ownership_basis", "evidence_reference",
                    "platform", "account_scope", "territories_json",
                    "permitted_actions_json", "max_duration_seconds", "limitations",
                    "valid_from", "valid_until", "transfer_channel", "created_at",
                },
                "owner_rights_attestation_events": {
                    "sequence", "attestation_id", "event_type", "actor",
                    "reason", "created_at",
                },
        }
        for table, expected in expected_columns.items():
                actual = {
                    row["name"]
                    for row in connection.execute(f"PRAGMA table_info({table})")
                }
                if actual != expected:
                    raise RuntimeError(
                        f"Owner rights attestation table {table} is incomplete."
                    )
        definitions = ContentStore._owner_rights_attestation_triggers()
        actual_triggers = {
                row["name"]: row["sql"]
                for row in connection.execute(
                    """
                    SELECT name, sql FROM sqlite_master
                    WHERE type = 'trigger' AND name LIKE 'owner_rights_attestation%'
                    """
                )
        }
        for name, definition in definitions.items():
                if (
                    name not in actual_triggers
                    or cls._normalize_schema_sql(actual_triggers[name])
                    != cls._normalize_schema_sql(definition)
                ):
                    raise RuntimeError(
                        f"Owner rights attestation trigger {name} is inconsistent."
                    )

    @staticmethod
    def _owner_rights_attestation_triggers() -> dict[str, str]:
        return {
                "owner_rights_attestations_no_update": """
                    CREATE TRIGGER IF NOT EXISTS owner_rights_attestations_no_update
                    BEFORE UPDATE ON owner_rights_attestations
                    BEGIN
                        SELECT RAISE(ABORT, 'owner_rights_attestations are append-only');
                    END
                """,
                "owner_rights_attestations_no_delete": """
                    CREATE TRIGGER IF NOT EXISTS owner_rights_attestations_no_delete
                    BEFORE DELETE ON owner_rights_attestations
                    BEGIN
                        SELECT RAISE(ABORT, 'owner_rights_attestations are append-only');
                    END
                """,
                "owner_rights_attestation_events_no_update": """
                    CREATE TRIGGER IF NOT EXISTS owner_rights_attestation_events_no_update
                    BEFORE UPDATE ON owner_rights_attestation_events
                    BEGIN
                        SELECT RAISE(ABORT, 'owner_rights_attestation_events are append-only');
                    END
                """,
                "owner_rights_attestation_events_no_delete": """
                    CREATE TRIGGER IF NOT EXISTS owner_rights_attestation_events_no_delete
                    BEFORE DELETE ON owner_rights_attestation_events
                    BEGIN
                        SELECT RAISE(ABORT, 'owner_rights_attestation_events are append-only');
                    END
                """,
                "owner_rights_attestation_events_valid_transition": """
                    CREATE TRIGGER IF NOT EXISTS
                        owner_rights_attestation_events_valid_transition
                    BEFORE INSERT ON owner_rights_attestation_events
                    WHEN (
                        (NEW.event_type = 'attested' AND EXISTS (
                            SELECT 1 FROM owner_rights_attestation_events
                            WHERE attestation_id = NEW.attestation_id
                        ))
                        OR
                        (NEW.event_type = 'revoked' AND NOT EXISTS (
                            SELECT 1 FROM owner_rights_attestation_events
                            WHERE attestation_id = NEW.attestation_id
                              AND event_type = 'attested'
                        ))
                    )
                    BEGIN
                        SELECT RAISE(ABORT, 'invalid owner attestation event transition');
                    END
                """,
        }
    @classmethod
    def _upgrade_acquisition_request_schema_v1(
        cls,
        connection: sqlite3.Connection,
    ) -> None:
        version = connection.execute(
            """
            SELECT schema_version FROM content_acquisition_request_schema_meta
            WHERE singleton = 1
            """
        ).fetchone()
        if version is None or version["schema_version"] != 1:
            raise RuntimeError("Only acquisition request schema v1 can be upgraded.")
        for trigger in cls._acquisition_request_triggers():
            connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        connection.execute(
            "ALTER TABLE content_acquisition_request_events "
            "RENAME TO content_acquisition_request_events_v1"
        )
        connection.execute(
            "ALTER TABLE content_acquisition_requests "
            "RENAME TO content_acquisition_requests_v1"
        )
        cls._create_acquisition_request_tables(connection)
        connection.execute(
            """
            INSERT INTO content_acquisition_requests (
                request_id, opportunity_id, source_id, source_identity,
                provider, provider_content_id, original_url, canonical_url,
                resolved_url, owner_id, intended_use, authorization_reference,
                asset_id, rights_status, rights_decision_id, idempotency_key,
                request_fingerprint, state, last_error_code, workflow_id, task_id,
                version, created_at, updated_at
            )
            SELECT request_id, opportunity_id, source_id, source_identity,
                provider, provider_content_id, original_url, canonical_url,
                resolved_url, owner_id, intended_use, authorization_reference,
                NULL, rights_status, rights_decision_id, idempotency_key,
                request_fingerprint, state, last_error_code, workflow_id, task_id,
                version, created_at, updated_at
            FROM content_acquisition_requests_v1
            """
        )
        connection.execute(
            """
            INSERT INTO content_acquisition_request_events (
                sequence, request_id, from_state, to_state, actor, reason, created_at
            )
            SELECT sequence, request_id, from_state, to_state, actor, reason, created_at
            FROM content_acquisition_request_events_v1
            """
        )
        connection.execute("DROP TABLE content_acquisition_request_events_v1")
        connection.execute("DROP TABLE content_acquisition_requests_v1")
        connection.execute(
            """
            UPDATE content_acquisition_request_schema_meta
            SET schema_version = ?
            WHERE singleton = 1
            """,
            (2,),
        )

    @classmethod
    def _upgrade_acquisition_request_schema_v2(
        cls,
        connection: sqlite3.Connection,
    ) -> None:
        version = connection.execute(
            """
            SELECT schema_version FROM content_acquisition_request_schema_meta
            WHERE singleton = 1
            """
        ).fetchone()
        if version is None or version["schema_version"] != 2:
            raise RuntimeError("Only acquisition request schema v2 can be upgraded.")
        connection.execute(
            "DROP TRIGGER IF EXISTS content_acquisition_request_state_transition"
        )
        connection.execute(
            cls._acquisition_request_triggers()[
                "content_acquisition_request_state_transition"
            ]
        )
        connection.execute(
            """
            UPDATE content_acquisition_request_schema_meta
            SET schema_version = ?
            WHERE singleton = 1
            """,
            (3,),
        )

    @classmethod
    def _upgrade_acquisition_request_schema_v3(
        cls,
        connection: sqlite3.Connection,
    ) -> None:
        version = connection.execute(
            """
            SELECT schema_version FROM content_acquisition_request_schema_meta
            WHERE singleton = 1
            """
        ).fetchone()
        if version is None or version["schema_version"] != 3:
            raise RuntimeError("Only acquisition request schema v3 can be upgraded.")
        cls._create_acquisition_evidence_assessment_table(connection)
        for definition in cls._acquisition_request_triggers().values():
            connection.execute(definition)
        connection.execute(
            """
            UPDATE content_acquisition_request_schema_meta
            SET schema_version = ?
            WHERE singleton = 1
            """,
            (cls.ACQUISITION_REQUEST_SCHEMA_VERSION,),
        )

    @staticmethod
    def _create_acquisition_request_tables(
        connection: sqlite3.Connection,
    ) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS content_acquisition_request_schema_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                schema_version INTEGER NOT NULL CHECK (schema_version > 0)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS content_acquisition_requests (
                request_id TEXT PRIMARY KEY,
                opportunity_id TEXT NOT NULL UNIQUE
                    REFERENCES content_opportunities(opportunity_id),
                source_id TEXT NOT NULL
                    REFERENCES content_source_identities(source_id),
                source_identity TEXT NOT NULL,
                provider TEXT,
                provider_content_id TEXT,
                original_url TEXT NOT NULL,
                canonical_url TEXT NOT NULL,
                resolved_url TEXT,
                owner_id TEXT NOT NULL,
                intended_use TEXT NOT NULL CHECK (intended_use IN (
                    'internal_research', 'editorial_development',
                    'commercial_publication', 'monetized_publication'
                )),
                authorization_reference TEXT NOT NULL,
                asset_id TEXT UNIQUE REFERENCES content_assets(asset_id),
                rights_status TEXT NOT NULL CHECK (rights_status IN (
                    'pending_asset_evaluation', 'cleared', 'blocked'
                )),
                rights_decision_id TEXT REFERENCES rights_decisions(decision_id),
                idempotency_key TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL CHECK (length(request_fingerprint) = 64),
                state TEXT NOT NULL CHECK (state IN (
                    'requested', 'validating', 'awaiting_adapter',
                    'awaiting_rights', 'completed',
                    'rejected', 'cancelled', 'failed'
                )),
                last_error_code TEXT,
                workflow_id TEXT NOT NULL UNIQUE
                    REFERENCES content_workflows(workflow_id),
                task_id TEXT UNIQUE REFERENCES content_workflow_tasks(task_id),
                version INTEGER NOT NULL CHECK (version > 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(owner_id, idempotency_key),
                CHECK (provider_content_id IS NULL OR provider IS NOT NULL),
                CHECK (
                    rights_status != 'cleared' OR rights_decision_id IS NOT NULL
                )
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS content_acquisition_request_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL
                    REFERENCES content_acquisition_requests(request_id),
                from_state TEXT,
                to_state TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        ContentStore._create_acquisition_evidence_assessment_table(connection)
        for definition in ContentStore._acquisition_request_triggers().values():
            connection.execute(definition)

    @staticmethod
    def _create_acquisition_evidence_assessment_table(
        connection: sqlite3.Connection,
    ) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS content_acquisition_evidence_assessments (
                assessment_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL
                    REFERENCES content_acquisition_requests(request_id),
                provider TEXT NOT NULL,
                source_identity TEXT NOT NULL,
                work_identity TEXT NOT NULL,
                provider_identity TEXT NOT NULL,
                verification_decision TEXT NOT NULL,
                policy_decision TEXT NOT NULL CHECK (policy_decision IN (
                    'VERIFIED_APPROVED',
                    'POLICY_ACCEPTED_WITH_RESIDUAL_RISK',
                    'CONDITIONAL',
                    'REJECTED',
                    'UNKNOWN'
                )),
                intended_use TEXT NOT NULL,
                operation TEXT NOT NULL,
                policy_id TEXT NOT NULL,
                territory TEXT NOT NULL,
                duration_seconds REAL NOT NULL CHECK (duration_seconds > 0),
                reasons_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                evaluated_at TEXT NOT NULL
            )
            """
        )

    @staticmethod
    def _acquisition_request_triggers() -> dict[str, str]:
        return {
            "content_acquisition_request_events_no_update": """
                CREATE TRIGGER IF NOT EXISTS content_acquisition_request_events_no_update
                BEFORE UPDATE ON content_acquisition_request_events
                BEGIN
                    SELECT RAISE(ABORT, 'acquisition request events are immutable');
                END
            """,
            "content_acquisition_request_events_no_delete": """
                CREATE TRIGGER IF NOT EXISTS content_acquisition_request_events_no_delete
                BEFORE DELETE ON content_acquisition_request_events
                BEGIN
                    SELECT RAISE(ABORT, 'acquisition request events are immutable');
                END
            """,
            "content_acquisition_request_identity_immutable": """
                CREATE TRIGGER IF NOT EXISTS content_acquisition_request_identity_immutable
                BEFORE UPDATE OF request_id, opportunity_id, source_id, source_identity,
                    provider, provider_content_id, original_url, canonical_url,
                    resolved_url, owner_id, intended_use, authorization_reference,
                    idempotency_key, request_fingerprint, workflow_id, task_id,
                    created_at ON content_acquisition_requests
                BEGIN
                    SELECT RAISE(ABORT, 'acquisition request identity is immutable');
                END
            """,
            "content_acquisition_request_asset_immutable": """
                CREATE TRIGGER IF NOT EXISTS content_acquisition_request_asset_immutable
                BEFORE UPDATE OF asset_id ON content_acquisition_requests
                WHEN OLD.asset_id IS NOT NULL AND NEW.asset_id IS NOT OLD.asset_id
                BEGIN
                    SELECT RAISE(ABORT, 'acquisition request asset binding is immutable');
                END
            """,
            "content_acquisition_request_state_transition": """
                CREATE TRIGGER IF NOT EXISTS content_acquisition_request_state_transition
                BEFORE UPDATE OF state ON content_acquisition_requests
                WHEN NOT (
                    (OLD.state = 'requested' AND NEW.state IN (
                        'validating', 'rejected', 'cancelled', 'failed'
                    ))
                    OR (OLD.state = 'validating' AND NEW.state IN (
                        'awaiting_adapter', 'rejected', 'cancelled', 'failed'
                    ))
                    OR (OLD.state = 'awaiting_adapter' AND NEW.state IN (
                        'validating', 'awaiting_rights', 'completed',
                        'rejected', 'cancelled', 'failed'
                    ))
                    OR (OLD.state = 'awaiting_rights' AND NEW.state IN (
                        'completed', 'rejected', 'cancelled', 'failed'
                    ))
                    OR (OLD.state = 'completed' AND NEW.state IN (
                        'awaiting_rights', 'rejected'
                    ))
                    OR (OLD.state = 'failed' AND NEW.state IN (
                        'validating', 'cancelled'
                    ))
                )
                BEGIN
                    SELECT RAISE(ABORT, 'invalid acquisition request state transition');
                END
            """,
            "content_acquisition_request_completion_guard": """
                CREATE TRIGGER IF NOT EXISTS content_acquisition_request_completion_guard
                BEFORE UPDATE OF state ON content_acquisition_requests
                WHEN NEW.state = 'completed' AND (
                    NEW.asset_id IS NULL
                    OR NEW.rights_status != 'cleared'
                    OR NEW.rights_decision_id IS NULL
                    OR NOT EXISTS (
                        SELECT 1 FROM content_assets a
                        WHERE a.asset_id = NEW.asset_id
                          AND a.owner_id = NEW.owner_id
                    )
                    OR NOT EXISTS (
                        SELECT 1 FROM rights_decisions d
                        WHERE d.decision_id = NEW.rights_decision_id
                          AND d.asset_id = NEW.asset_id
                    )
                )
                BEGIN
                    SELECT RAISE(ABORT, 'completed acquisition requires a registered asset and cleared rights');
                END
            """,
            "content_acquisition_request_reopened_rights_guard": """
                CREATE TRIGGER IF NOT EXISTS content_acquisition_request_reopened_rights_guard
                BEFORE UPDATE OF state ON content_acquisition_requests
                WHEN OLD.state = 'completed' AND NEW.state = 'awaiting_rights'
                  AND (
                    NEW.rights_status != 'pending_asset_evaluation'
                    OR NEW.rights_decision_id IS NULL
                  )
                BEGIN
                    SELECT RAISE(ABORT, 'reopened acquisition requires a new pending rights decision');
                END
            """,
            "content_acquisition_evidence_assessments_no_update": """
                CREATE TRIGGER IF NOT EXISTS content_acquisition_evidence_assessments_no_update
                BEFORE UPDATE ON content_acquisition_evidence_assessments
                BEGIN
                    SELECT RAISE(ABORT, 'acquisition evidence assessments are immutable');
                END
            """,
            "content_acquisition_evidence_assessments_no_delete": """
                CREATE TRIGGER IF NOT EXISTS content_acquisition_evidence_assessments_no_delete
                BEFORE DELETE ON content_acquisition_evidence_assessments
                BEGIN
                    SELECT RAISE(ABORT, 'acquisition evidence assessments are immutable');
                END
            """,
        }

    @classmethod
    def _verify_acquisition_request_schema(
        cls,
        connection: sqlite3.Connection,
    ) -> None:
        meta_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(content_acquisition_request_schema_meta)"
            )
        }
        meta = connection.execute(
            """
            SELECT schema_version FROM content_acquisition_request_schema_meta
            WHERE singleton = 1
            """
        ).fetchone()
        if (
            meta_columns != {"singleton", "schema_version"}
            or meta is None
            or meta["schema_version"] != cls.ACQUISITION_REQUEST_SCHEMA_VERSION
        ):
            raise RuntimeError("Acquisition request schema metadata is inconsistent.")
        expected_columns = {
            "content_acquisition_requests": {
                "request_id", "opportunity_id", "source_id", "source_identity",
                "provider", "provider_content_id", "original_url", "canonical_url",
                "resolved_url", "owner_id", "intended_use", "authorization_reference",
                "rights_status", "rights_decision_id", "idempotency_key",
                "asset_id", "request_fingerprint", "state", "last_error_code", "workflow_id",
                "task_id", "version", "created_at", "updated_at",
            },
            "content_acquisition_request_events": {
                "sequence", "request_id", "from_state", "to_state", "actor",
                "reason", "created_at",
            },
            "content_acquisition_evidence_assessments": {
                "assessment_id", "request_id", "provider", "source_identity",
                "work_identity", "provider_identity", "verification_decision",
                "policy_decision", "intended_use", "reasons_json", "evidence_json",
                "operation", "policy_id", "territory", "duration_seconds",
                "evaluated_at",
            },
        }
        for table, expected in expected_columns.items():
            actual = {
                row["name"]
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
            if actual != expected:
                raise RuntimeError(f"Acquisition request table {table} is incomplete.")
        expected_triggers = cls._acquisition_request_triggers()
        actual_triggers = {
            row["name"]: row["sql"]
            for row in connection.execute(
                """
                SELECT name, sql FROM sqlite_master
                WHERE type = 'trigger' AND name IN (
                    'content_acquisition_request_events_no_update',
                    'content_acquisition_request_events_no_delete',
                    'content_acquisition_request_identity_immutable',
                    'content_acquisition_request_asset_immutable',
                    'content_acquisition_request_state_transition',
                    'content_acquisition_request_completion_guard',
                    'content_acquisition_request_reopened_rights_guard',
                    'content_acquisition_evidence_assessments_no_update',
                    'content_acquisition_evidence_assessments_no_delete'
                )
                """
            )
        }
        for name, definition in expected_triggers.items():
            if (
                name not in actual_triggers
                or cls._normalize_schema_sql(actual_triggers[name])
                != cls._normalize_schema_sql(definition)
            ):
                raise RuntimeError(
                    f"Acquisition request trigger {name} is missing or malformed."
                )

    def migrate_source_identity_schema(self, *, standalone: bool = True) -> int:
        """Explicitly migrate schema-v10 source identity records without merging rows."""
        with self._lock:
            has_identity_meta = self._table_exists(
                self._connection, "content_source_identity_schema_meta"
            )
            identity_meta = (
                self._connection.execute(
                    """
                    SELECT schema_version FROM content_source_identity_schema_meta
                    WHERE singleton = 1
                    """
                ).fetchone()
                if has_identity_meta
                else None
            )
            if has_identity_meta:
                if identity_meta is None:
                    raise RuntimeError(
                        "Content source identity schema metadata is inconsistent."
                    )
                if identity_meta["schema_version"] != 1:
                    raise RuntimeError("Source identity schema version is unsupported.")
                self._verify_content_identity_schema(self._connection)
                return 1
            if standalone and self._connection.in_transaction:
                raise RuntimeError("Source identity migration requires an idle connection.")
            version = self._connection.execute(
                "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
            ).fetchone()
            if version is None or version["schema_version"] != self.SCHEMA_VERSION:
                raise RuntimeError(
                    "Source identity migration requires an exact schema-v10 database."
                )

            foreign_keys_enabled = bool(
                self._connection.execute("PRAGMA foreign_keys").fetchone()[0]
            )
            if foreign_keys_enabled:
                self._connection.execute("PRAGMA foreign_keys = OFF")
            try:
                if standalone:
                    self._connection.execute("BEGIN IMMEDIATE")
                source_rows = self._connection.execute(
                    "SELECT * FROM content_sources ORDER BY source_id"
                ).fetchall()
                self._connection.execute(
                    """
                    CREATE TABLE content_opportunities_identity_new (
                        opportunity_id TEXT PRIMARY KEY,
                        topic TEXT NOT NULL,
                        normalized_topic TEXT NOT NULL,
                        niche TEXT NOT NULL,
                        target_audience TEXT NOT NULL,
                        target_language TEXT NOT NULL,
                        target_region TEXT,
                        content_format TEXT NOT NULL,
                        discovered_at TEXT NOT NULL,
                        status TEXT NOT NULL,
                        relevance_assessment TEXT,
                        evidence_count INTEGER NOT NULL DEFAULT 0,
                        research_status TEXT NOT NULL,
                        workflow_id TEXT REFERENCES content_workflows(workflow_id),
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(
                            normalized_topic, niche, target_audience,
                            target_language, target_region, content_format
                        )
                    )
                    """
                )
                opportunity_columns = (
                    "opportunity_id, topic, normalized_topic, niche, target_audience, "
                    "target_language, target_region, content_format, discovered_at, "
                    "status, relevance_assessment, evidence_count, research_status, "
                    "workflow_id, version, created_at, updated_at"
                )
                self._connection.execute(
                    f"INSERT INTO content_opportunities_identity_new ({opportunity_columns}) "
                    f"SELECT {opportunity_columns} FROM content_opportunities"
                )
                self._connection.execute("DROP TABLE content_opportunities")
                self._connection.execute(
                    "ALTER TABLE content_opportunities_identity_new "
                    "RENAME TO content_opportunities"
                )

                self._connection.execute(
                    """
                    CREATE TABLE content_sources_identity_new (
                        source_id TEXT PRIMARY KEY,
                        canonical_url TEXT NOT NULL,
                        source_name TEXT NOT NULL,
                        publisher TEXT,
                        source_type TEXT NOT NULL,
                        published_at TEXT,
                        retrieved_at TEXT NOT NULL,
                        reliability TEXT NOT NULL,
                        content_sha256 TEXT,
                        license_classification TEXT
                    )
                    """
                )
                self._connection.executemany(
                    """
                    INSERT INTO content_sources_identity_new (
                        source_id, canonical_url, source_name, publisher,
                        source_type, published_at, retrieved_at, reliability,
                        content_sha256, license_classification
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                    """,
                    (
                        (
                            row["source_id"],
                            row["canonical_url"],
                            row["source_name"],
                            row["publisher"],
                            row["source_type"],
                            row["published_at"],
                            row["retrieved_at"],
                            row["reliability"],
                            row["license_classification"],
                        )
                        for row in source_rows
                    ),
                )
                self._connection.execute("DROP TABLE content_sources")
                self._connection.execute(
                    "ALTER TABLE content_sources_identity_new RENAME TO content_sources"
                )
                self._create_content_identity_schema(self._connection)
                self._connection.executemany(
                    """
                    INSERT INTO content_source_identities (
                        source_id, source_identity, provider, provider_content_id,
                        original_url, resolved_url, metadata_fingerprint,
                        legacy_metadata_digest, deduplication_key
                    ) VALUES (?, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?)
                    """,
                    (
                        (
                            row["source_id"],
                            row["content_sha256"],
                            f"url:{row['canonical_url']}",
                        )
                        for row in source_rows
                    ),
                )
                violations = self._connection.execute(
                    "PRAGMA foreign_key_check"
                ).fetchall()
                if violations:
                    raise RuntimeError(
                        "Source identity migration found broken content references."
                    )
                if standalone:
                    self._connection.commit()
            except Exception:
                if standalone:
                    self._connection.rollback()
                raise
            finally:
                if foreign_keys_enabled:
                    self._connection.execute("PRAGMA foreign_keys = ON")
            self._verify_content_identity_schema(self._connection)
            self._verify_database_integrity(self._connection)
            return 1

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone() is not None

    @classmethod
    def _verify_content_identity_schema(
        cls,
        connection: sqlite3.Connection,
    ) -> None:
        meta = connection.execute(
            """
            SELECT schema_version FROM content_source_identity_schema_meta
            WHERE singleton = 1
            """
        ).fetchone()
        if meta is None or meta["schema_version"] != 1:
            raise RuntimeError("Content source identity schema metadata is invalid.")
        expected = {
            "source_id",
            "source_identity",
            "provider",
            "provider_content_id",
            "original_url",
            "resolved_url",
            "metadata_fingerprint",
            "legacy_metadata_digest",
            "deduplication_key",
        }
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(content_source_identities)")
        }
        if not expected.issubset(columns):
            raise RuntimeError("Content source identity schema is incomplete.")
        unique_indexes: dict[str, set[tuple[str, ...]]] = {}
        for table in (
            "content_source_identities",
            "content_opportunities",
            "content_sources",
        ):
            unique_indexes[table] = set()
            for index in connection.execute(f"PRAGMA index_list({table})"):
                if index["unique"]:
                    index_columns = tuple(
                        item["name"]
                        for item in connection.execute(
                            f"PRAGMA index_info({index['name']})"
                        )
                    )
                    unique_indexes[table].add(index_columns)
        if ("deduplication_key",) not in unique_indexes["content_source_identities"]:
            raise RuntimeError("Content source identity deduplication is incomplete.")
        if (
            ("provider", "provider_content_id")
            not in unique_indexes["content_source_identities"]
        ):
            raise RuntimeError("Provider content identity uniqueness is incomplete.")
        if (
            "canonical_url",
        ) in unique_indexes["content_sources"]:
            raise RuntimeError("Source URL aliases cannot be represented by this schema.")
        if not any(
            set(index) == {
                "normalized_topic",
                "niche",
                "target_audience",
                "target_language",
                "target_region",
                "content_format",
            }
            for index in unique_indexes["content_opportunities"]
        ):
            raise RuntimeError("Content opportunity identity dimensions are incomplete.")

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
        result: list[str] = []
        quote: str | None = None
        quote_is_string = False
        pending_space = False
        index = 0
        while index < len(sql):
            character = sql[index]
            if quote is not None:
                if character == quote:
                    if index + 1 < len(sql) and sql[index + 1] == quote:
                        result.append(sql[index + 1])
                        index += 1
                    else:
                        quote = None
                        quote_is_string = False
                elif quote_is_string:
                    result.append(character)
                else:
                    result.append(character.casefold())
            elif character in ("'", "`", '"', "["):
                if pending_space and result and result[-1] != " ":
                    result.append(" ")
                pending_space = False
                quote = "]" if character == "[" else character
                quote_is_string = character == "'"
                if character == "'":
                    result.append(character)
            elif character.isspace():
                pending_space = True
            else:
                if pending_space and result and result[-1] != " ":
                    result.append(" ")
                pending_space = False
                result.append(character.casefold())
            index += 1
        normalized = "".join(result).strip()
        return normalized.replace(
            "create trigger if not exists ", "create trigger "
        )

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
                cls.SCHEMA_VERSION,
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

    def _require_acquisition_request_schema(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        main = connection.execute(
            "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
        ).fetchone()
        if main is None or main["schema_version"] != self.SCHEMA_VERSION:
            raise RuntimeError(
                "Acquisition requests require the content schema-v10 foundation."
            )
        if not self._table_exists(
            connection, "content_acquisition_request_schema_meta"
        ):
            raise RuntimeError(
                "Acquisition requests require their explicit schema migration."
            )
        self._verify_acquisition_request_schema(connection)

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
        *,
        authorization_check: Callable[
            [sqlite3.Connection, VisualAnalysisRequest], None
        ]
        | None = None,
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
            if authorization_check is not None:
                authorization_check(connection, request)
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
        authorization_check: Callable[
            [sqlite3.Connection, VisualAnalysisRequest], None
        ]
        | None = None,
    ) -> bool:
        with self._lock:
            self._require_visual_analysis_worker(worker_token)
            now = self._now()
            with self._transaction() as connection:
                self._require_visual_analysis_schema(connection)
                row = connection.execute(
                    """
                    SELECT request_json FROM content_visual_analyses
                    WHERE analysis_id = ? AND owner_id = ? AND state = ?
                    """,
                    (
                        analysis_id,
                        owner_id,
                        VisualAnalysisState.QUEUED.value,
                    ),
                ).fetchone()
                if row is None:
                    return False
                if authorization_check is None:
                    raise RuntimeError(
                        "Visual analysis claims require an authorization check."
                    )
                request = VisualAnalysisRequest.model_validate_json(
                    row["request_json"]
                )
                authorization_check(connection, request)
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
        authorization_check: Callable[
            [sqlite3.Connection, VisualAnalysisRequest], None
        ]
        | None = None,
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
                if authorization_check is None:
                    raise RuntimeError(
                        "Visual analysis completion requires an authorization check."
                    )
                authorization_check(connection, request)
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
                WHERE type IN ('table', 'index', 'trigger', 'view')
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
                    WHERE type IN ('table', 'index', 'trigger', 'view')
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
                WHERE type IN ('table', 'index', 'trigger', 'view')
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
            CREATE TABLE IF NOT EXISTS content_asset_license_records (
                record_id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL REFERENCES content_assets(asset_id),
                asset_sha256 TEXT NOT NULL CHECK (length(asset_sha256) = 64),
                owner_id TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS content_asset_license_history
            ON content_asset_license_records(asset_id, created_at, record_id)
            """,
            """
            CREATE TRIGGER IF NOT EXISTS content_asset_license_records_immutable_update
            BEFORE UPDATE ON content_asset_license_records
            BEGIN
                SELECT RAISE(ABORT, 'asset license records are append-only');
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS content_asset_license_records_immutable_delete
            BEFORE DELETE ON content_asset_license_records
            BEGIN
                SELECT RAISE(ABORT, 'asset license records are append-only');
            END
            """,
            """
            CREATE TABLE IF NOT EXISTS content_restricted_technical_tests (
                permit_id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL REFERENCES content_assets(asset_id),
                asset_sha256 TEXT NOT NULL CHECK (length(asset_sha256) = 64),
                owner_id TEXT NOT NULL,
                license_record_id TEXT NOT NULL
                    REFERENCES content_asset_license_records(record_id),
                allowed_operations_json TEXT NOT NULL,
                commercial_use INTEGER NOT NULL CHECK (commercial_use = 0),
                external_distribution INTEGER NOT NULL
                    CHECK (external_distribution = 0),
                status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS content_restricted_test_asset
            ON content_restricted_technical_tests(
                asset_id, asset_sha256, owner_id, status, expires_at
            )
            """,
            """
            CREATE TRIGGER IF NOT EXISTS content_restricted_test_scope_immutable
            BEFORE UPDATE ON content_restricted_technical_tests
            WHEN OLD.permit_id != NEW.permit_id
              OR OLD.asset_id != NEW.asset_id
              OR OLD.asset_sha256 != NEW.asset_sha256
              OR OLD.owner_id != NEW.owner_id
              OR OLD.license_record_id != NEW.license_record_id
              OR OLD.allowed_operations_json != NEW.allowed_operations_json
              OR OLD.commercial_use != NEW.commercial_use
              OR OLD.external_distribution != NEW.external_distribution
              OR OLD.created_at != NEW.created_at
              OR OLD.expires_at != NEW.expires_at
              OR OLD.status != 'active'
              OR NEW.status != 'revoked'
            BEGIN
                SELECT RAISE(ABORT, 'restricted test scope is immutable');
            END
            """,
            """
            CREATE TABLE IF NOT EXISTS content_restricted_technical_test_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                permit_id TEXT NOT NULL
                    REFERENCES content_restricted_technical_tests(permit_id),
                asset_id TEXT NOT NULL,
                asset_sha256 TEXT NOT NULL,
                actor TEXT NOT NULL,
                event_type TEXT NOT NULL,
                operation TEXT,
                rights_state TEXT NOT NULL,
                detail_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS content_restricted_test_events_order
            ON content_restricted_technical_test_events(permit_id, sequence)
            """,
            """
            CREATE TRIGGER IF NOT EXISTS content_restricted_test_events_immutable_update
            BEFORE UPDATE ON content_restricted_technical_test_events
            BEGIN
                SELECT RAISE(ABORT, 'restricted test events are append-only');
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS content_restricted_test_events_immutable_delete
            BEFORE DELETE ON content_restricted_technical_test_events
            BEGIN
                SELECT RAISE(ABORT, 'restricted test events are append-only');
            END
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
            CREATE TABLE IF NOT EXISTS content_workflows (
                workflow_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                title TEXT NOT NULL,
                state TEXT NOT NULL,
                version INTEGER NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                request_fingerprint TEXT NOT NULL,
                execution_attempt INTEGER NOT NULL DEFAULT 0,
                provider TEXT,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_workflow_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL REFERENCES content_workflows(workflow_id),
                from_state TEXT,
                to_state TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_workflow_tasks (
                task_id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL REFERENCES content_workflows(workflow_id),
                idempotency_key TEXT NOT NULL UNIQUE,
                task_type TEXT NOT NULL,
                state TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 0,
                provider TEXT,
                result_json TEXT,
                error_code TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS content_workflow_tasks_ready
            ON content_workflow_tasks(workflow_id, state, updated_at)
            """,
            """
            CREATE TABLE IF NOT EXISTS content_workflow_task_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL REFERENCES content_workflow_tasks(task_id),
                state TEXT NOT NULL,
                worker_id TEXT,
                failure_class TEXT,
                detail TEXT,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_workflow_reconciliations (
                reconciliation_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES content_workflow_tasks(task_id),
                operator_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                safe_to_retry INTEGER NOT NULL,
                external_operation_id TEXT,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_script_artifacts (
                artifact_id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL REFERENCES content_workflows(workflow_id),
                version INTEGER NOT NULL,
                content TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                provenance_json TEXT NOT NULL,
                generation_metadata_json TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(workflow_id, version)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_production_jobs (
                job_id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL REFERENCES content_workflows(workflow_id),
                script_artifact_id TEXT NOT NULL REFERENCES content_script_artifacts(artifact_id),
                script_version INTEGER NOT NULL,
                script_sha256 TEXT NOT NULL,
                configuration_json TEXT NOT NULL,
                state TEXT NOT NULL,
                output_asset_id TEXT,
                output_sha256 TEXT,
                failure_class TEXT,
                failure_detail TEXT,
                started_at TEXT,
                completed_at TEXT,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_media_production_plans (
                job_id TEXT PRIMARY KEY,
                package_id TEXT NOT NULL,
                script_artifact_id TEXT NOT NULL,
                script_version INTEGER NOT NULL,
                script_sha256 TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                state TEXT NOT NULL,
                current_stage TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                failure_reason TEXT,
                failure_class TEXT
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS content_media_production_package
            ON content_media_production_plans(package_id, created_at)
            """,
            """
            CREATE TABLE IF NOT EXISTS content_media_scene_assets (
                asset_reference TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES content_media_production_plans(job_id),
                scene_id TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_media_production_stage_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES content_media_production_plans(job_id),
                state TEXT NOT NULL,
                stage TEXT NOT NULL,
                detail TEXT,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_media_validation_reports (
                report_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES content_media_production_plans(job_id),
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_media_subtitle_artifacts (
                artifact_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES content_media_production_plans(job_id),
                subtitle_format TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(job_id, subtitle_format)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_media_artifacts (
                artifact_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL UNIQUE
                    REFERENCES content_media_production_plans(job_id),
                package_id TEXT NOT NULL,
                script_artifact_id TEXT NOT NULL,
                script_version INTEGER NOT NULL,
                script_sha256 TEXT NOT NULL,
                output_sha256 TEXT NOT NULL,
                storage_reference TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_publication_packages (
                publication_package_id TEXT PRIMARY KEY,
                content_package_id TEXT NOT NULL,
                approval_fingerprint TEXT NOT NULL UNIQUE
                    CHECK (length(approval_fingerprint) = 64),
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS content_publication_packages_source
            ON content_publication_packages(content_package_id, created_at)
            """,
            """
            CREATE TABLE IF NOT EXISTS content_publication_approvals (
                approval_id TEXT PRIMARY KEY,
                publication_package_id TEXT NOT NULL
                    REFERENCES content_publication_packages(publication_package_id),
                approval_fingerprint TEXT NOT NULL
                    CHECK (length(approval_fingerprint) = 64),
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS content_publication_approvals_latest
            ON content_publication_approvals(publication_package_id, created_at)
            """,
            """
            CREATE TABLE IF NOT EXISTS content_distribution_jobs (
                job_id TEXT PRIMARY KEY,
                publication_package_id TEXT NOT NULL UNIQUE
                    REFERENCES content_publication_packages(publication_package_id),
                approval_id TEXT NOT NULL
                    REFERENCES content_publication_approvals(approval_id),
                state TEXT NOT NULL CHECK (state IN (
                    'created', 'awaiting_approval', 'approved', 'scheduled',
                    'uploading', 'processing', 'published', 'failed',
                    'uncertain', 'cancelled'
                )),
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_distribution_job_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES content_distribution_jobs(job_id),
                from_state TEXT,
                to_state TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS content_distribution_job_events_order
            ON content_distribution_job_events(job_id, sequence)
            """,
            """
            CREATE TABLE IF NOT EXISTS content_distribution_records (
                publication_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL UNIQUE
                    REFERENCES content_distribution_jobs(job_id),
                publication_package_id TEXT NOT NULL
                    REFERENCES content_publication_packages(publication_package_id),
                remote_publication_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_youtube_upload_sessions (
                job_id TEXT PRIMARY KEY,
                reference_id TEXT UNIQUE,
                binding_json TEXT,
                generation INTEGER,
                nonce BLOB,
                encrypted_session_uri BLOB,
                expires_at TEXT,
                invalidated INTEGER NOT NULL DEFAULT 0
                    CHECK (invalidated IN (0, 1)),
                progress_json TEXT,
                updated_at TEXT NOT NULL,
                CHECK (
                    (reference_id IS NULL AND binding_json IS NULL
                     AND generation IS NULL AND nonce IS NULL
                     AND encrypted_session_uri IS NULL AND expires_at IS NULL)
                    OR
                    (reference_id IS NOT NULL AND binding_json IS NOT NULL
                     AND generation IS NOT NULL AND nonce IS NOT NULL
                     AND encrypted_session_uri IS NOT NULL AND expires_at IS NOT NULL)
                )
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS content_youtube_upload_sessions_expiry
            ON content_youtube_upload_sessions(expires_at)
            """,
            """
            CREATE TABLE IF NOT EXISTS content_validation_reports (
                report_id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL REFERENCES content_workflows(workflow_id),
                artifact_type TEXT NOT NULL,
                artifact_id TEXT NOT NULL,
                artifact_version INTEGER NOT NULL,
                artifact_sha256 TEXT NOT NULL,
                rules_json TEXT NOT NULL,
                results_json TEXT NOT NULL,
                failure_reasons_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_approval_decisions (
                decision_id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL REFERENCES content_workflows(workflow_id),
                artifact_type TEXT NOT NULL,
                artifact_id TEXT NOT NULL,
                artifact_version INTEGER NOT NULL,
                artifact_sha256 TEXT NOT NULL,
                decision TEXT NOT NULL,
                actor TEXT NOT NULL,
                comments TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_opportunities (
                opportunity_id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                normalized_topic TEXT NOT NULL,
                niche TEXT NOT NULL,
                target_audience TEXT NOT NULL,
                target_language TEXT NOT NULL,
                target_region TEXT,
                content_format TEXT NOT NULL,
                discovered_at TEXT NOT NULL,
                status TEXT NOT NULL,
                relevance_assessment TEXT,
                evidence_count INTEGER NOT NULL DEFAULT 0,
                research_status TEXT NOT NULL,
                workflow_id TEXT REFERENCES content_workflows(workflow_id),
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(
                    normalized_topic, niche, target_audience, target_language,
                    target_region, content_format
                )
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_sources (
                source_id TEXT PRIMARY KEY,
                canonical_url TEXT NOT NULL,
                source_name TEXT NOT NULL,
                publisher TEXT,
                source_type TEXT NOT NULL,
                published_at TEXT,
                retrieved_at TEXT NOT NULL,
                reliability TEXT NOT NULL,
                content_sha256 TEXT,
                license_classification TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_configured_feeds (
                feed_id TEXT PRIMARY KEY,
                feed_name TEXT NOT NULL,
                canonical_url TEXT NOT NULL UNIQUE,
                allowed_hostname TEXT NOT NULL,
                allow_http INTEGER NOT NULL,
                enabled INTEGER NOT NULL,
                source_category TEXT NOT NULL,
                niches_json TEXT NOT NULL,
                keywords_json TEXT NOT NULL,
                retrieval_interval_seconds INTEGER NOT NULL,
                last_successful_retrieval TEXT,
                last_attempted_retrieval TEXT,
                etag TEXT,
                last_modified TEXT,
                health TEXT NOT NULL,
                consecutive_failures INTEGER NOT NULL,
                last_response_status INTEGER,
                last_error TEXT,
                next_refresh_at TEXT,
                configuration_version INTEGER NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_feed_host_rate_limits (
                hostname TEXT PRIMARY KEY,
                last_reserved_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_evidence (
                evidence_id TEXT PRIMARY KEY,
                opportunity_id TEXT NOT NULL REFERENCES content_opportunities(opportunity_id),
                source_id TEXT NOT NULL REFERENCES content_sources(source_id),
                claim TEXT NOT NULL,
                observation TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                published_at TEXT,
                extraction_method TEXT NOT NULL,
                confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
                evidence_sha256 TEXT NOT NULL,
                is_inference INTEGER NOT NULL DEFAULT 0,
                contradictory INTEGER NOT NULL DEFAULT 0,
                UNIQUE(opportunity_id, source_id, evidence_sha256)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_research_reports (
                report_id TEXT PRIMARY KEY,
                opportunity_id TEXT NOT NULL REFERENCES content_opportunities(opportunity_id),
                summary TEXT NOT NULL,
                report_json TEXT NOT NULL,
                research_timestamp TEXT NOT NULL,
                completeness TEXT NOT NULL,
                report_sha256 TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_opportunity_scores (
                opportunity_id TEXT NOT NULL REFERENCES content_opportunities(opportunity_id),
                score_version TEXT NOT NULL,
                score REAL NOT NULL CHECK (score >= 0 AND score <= 100),
                dimensions_json TEXT NOT NULL,
                evidence_coverage REAL NOT NULL CHECK (evidence_coverage >= 0 AND evidence_coverage <= 1),
                confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
                explanation_json TEXT NOT NULL,
                input_snapshot_sha256 TEXT NOT NULL,
                scored_at TEXT NOT NULL,
                PRIMARY KEY (opportunity_id, score_version)
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS content_approval_lookup
            ON content_approval_decisions(workflow_id, artifact_id, artifact_version, status)
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

    @staticmethod
    def _create_strategy_tables(connection: sqlite3.Connection) -> None:
        ContentStore._execute_sql_script(
            connection,
            """
            CREATE TABLE IF NOT EXISTS content_strategy_briefs (
                strategy_id TEXT PRIMARY KEY,
                opportunity_id TEXT NOT NULL,
                research_report_id TEXT NOT NULL,
                strategy_version INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS content_strategy_angles (
                angle_id TEXT PRIMARY KEY,
                strategy_id TEXT NOT NULL REFERENCES content_strategy_briefs(strategy_id),
                payload_json TEXT NOT NULL,
                UNIQUE(strategy_id, angle_id)
            );
            CREATE TABLE IF NOT EXISTS content_editorial_plans (
                plan_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                strategy_id TEXT NOT NULL REFERENCES content_strategy_briefs(strategy_id),
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS content_script_claims (
                claim_id TEXT NOT NULL,
                artifact_id TEXT NOT NULL REFERENCES content_script_artifacts(artifact_id),
                artifact_version INTEGER NOT NULL,
                artifact_sha256 TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (artifact_id, artifact_version, claim_id)
            );
            CREATE TABLE IF NOT EXISTS content_metadata_records (
                metadata_id TEXT PRIMARY KEY,
                script_artifact_id TEXT REFERENCES content_script_artifacts(artifact_id),
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS content_thumbnail_briefs (
                thumbnail_id TEXT PRIMARY KEY,
                script_artifact_id TEXT REFERENCES content_script_artifacts(artifact_id),
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS content_quality_evaluations (
                evaluation_id TEXT PRIMARY KEY,
                script_artifact_id TEXT REFERENCES content_script_artifacts(artifact_id),
                script_artifact_version INTEGER,
                script_artifact_sha256 TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS content_package_checkpoints (
                package_id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                opportunity_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS content_package_stages (
                stage_id TEXT PRIMARY KEY,
                package_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                UNIQUE(package_id, stage_id)
            );
            CREATE TABLE IF NOT EXISTS content_angle_selections (
                selection_id TEXT PRIMARY KEY,
                package_id TEXT NOT NULL,
                selection_version INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                UNIQUE(package_id, selection_version)
            );
            CREATE TABLE IF NOT EXISTS content_readiness_decisions (
                decision_id TEXT PRIMARY KEY,
                package_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS content_performance_measurements (
                measurement_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                content_package_id TEXT NOT NULL,
                media_artifact_id TEXT NOT NULL,
                reporting_start TEXT NOT NULL,
                reporting_end TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS content_performance_package_period
            ON content_performance_measurements(
                content_package_id, reporting_start, reporting_end
            );
            CREATE TABLE IF NOT EXISTS content_production_cost_reports (
                report_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                content_package_id TEXT NOT NULL,
                media_artifact_id TEXT NOT NULL,
                reporting_start TEXT NOT NULL,
                reporting_end TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS content_cost_package_period
            ON content_production_cost_reports(
                content_package_id, reporting_start, reporting_end
            );
            CREATE TABLE IF NOT EXISTS content_performance_analyses (
                analysis_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS content_performance_recommendations (
                recommendation_id TEXT PRIMARY KEY,
                analysis_id TEXT NOT NULL
                    REFERENCES content_performance_analyses(analysis_id),
                idempotency_key TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS content_performance_decisions (
                decision_id TEXT PRIMARY KEY,
                recommendation_id TEXT NOT NULL UNIQUE
                    REFERENCES content_performance_recommendations(recommendation_id),
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS content_performance_strategy_revisions (
                revision_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                recommendation_id TEXT NOT NULL
                    REFERENCES content_performance_recommendations(recommendation_id),
                decision_id TEXT NOT NULL
                    REFERENCES content_performance_decisions(decision_id),
                strategy_id TEXT NOT NULL
                    REFERENCES content_strategy_briefs(strategy_id),
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )

    @staticmethod
    def _execute_sql_script(
        connection: sqlite3.Connection,
        script: str,
    ) -> None:
        statement_buffer = ""
        for line in script.splitlines():
            statement_buffer += f"{line}\n"
            if sqlite3.complete_statement(statement_buffer):
                statement = statement_buffer.strip()
                if statement:
                    connection.execute(statement)
                statement_buffer = ""
        if statement_buffer.strip():
            raise RuntimeError("Content schema SQL is incomplete.")
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
            or row["schema_version"] != ContentStore.SCHEMA_VERSION
        ):
            raise RuntimeError("Publishing attempt schema v10 is not enabled.")

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
                f"{context.grant_id}:{action}:{decision}:{uuid4()}",
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

    def record_asset_license_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        record: AssetLicenseRecord,
    ) -> None:
        asset_row = connection.execute(
            "SELECT * FROM content_assets WHERE asset_id = ?",
            (record.asset_id,),
        ).fetchone()
        asset = None if asset_row is None else self._load_asset(asset_row)
        if (
            asset is None
            or asset.owner_id != record.owner_id
            or asset.content_sha256 != record.asset_sha256
            or record.submitted_by != record.owner_id
        ):
            raise PermissionError(
                "License metadata must be bound to the authenticated asset owner and hash."
            )
        connection.execute(
            """
            INSERT INTO content_asset_license_records (
                record_id, asset_id, asset_sha256, owner_id,
                metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                record.record_id,
                record.asset_id,
                record.asset_sha256,
                record.owner_id,
                self._canonical(record.model_dump(mode="json")),
                record.created_at.isoformat(),
            ),
        )

    def create_restricted_technical_test_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        permit: RestrictedTechnicalTestPermit,
        license_record: AssetLicenseRecord,
        actor: str,
    ) -> None:
        if (
            actor != permit.owner_id
            or permit.license_record_id != license_record.record_id
            or license_record.asset_id != permit.asset_id
            or license_record.asset_sha256 != permit.asset_sha256
            or license_record.owner_id != permit.owner_id
            or license_record.verification_state
            not in {
                AssetLicenseVerificationState.UNKNOWN,
                AssetLicenseVerificationState.PENDING_REVIEW,
            }
        ):
            raise PermissionError(
                "Restricted technical tests require the owner's unverified license record."
            )
        asset_row = connection.execute(
            "SELECT * FROM content_assets WHERE asset_id = ?",
            (permit.asset_id,),
        ).fetchone()
        asset = None if asset_row is None else self._load_asset(asset_row)
        if (
            asset is None
            or asset.owner_id != actor
            or asset.content_sha256 != permit.asset_sha256
            or asset.provenance.acquisition_method.value == "owner_created"
        ):
            raise PermissionError(
                "Restricted technical tests require the exact non-owner-created asset."
            )
        active = connection.execute(
            """
            SELECT 1 FROM content_restricted_technical_tests
            WHERE asset_id = ? AND asset_sha256 = ? AND owner_id = ?
              AND status = 'active' AND expires_at > ?
            LIMIT 1
            """,
            (
                permit.asset_id,
                permit.asset_sha256,
                actor,
                datetime.now(UTC).isoformat(),
            ),
        ).fetchone()
        if active is not None:
            raise ValueError("An active restricted technical-test permit already exists.")
        self.record_asset_license_in_transaction(connection, record=license_record)
        connection.execute(
            """
            INSERT INTO content_restricted_technical_tests (
                permit_id, asset_id, asset_sha256, owner_id, license_record_id,
                allowed_operations_json, commercial_use, external_distribution,
                status, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?)
            """,
            (
                permit.permit_id,
                permit.asset_id,
                permit.asset_sha256,
                permit.owner_id,
                permit.license_record_id,
                self._canonical(sorted(permit.allowed_operations)),
                permit.status.value,
                permit.created_at.isoformat(),
                permit.expires_at.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO content_restricted_technical_test_events (
                permit_id, asset_id, asset_sha256, actor, event_type,
                operation, rights_state, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (
                permit.permit_id,
                permit.asset_id,
                permit.asset_sha256,
                actor,
                "permit_created",
                "unknown",
                self._canonical(
                    {
                        "license_record_id": license_record.record_id,
                        "allowed_operations": sorted(permit.allowed_operations),
                        "commercial_use": False,
                        "external_distribution": False,
                        "expires_at": permit.expires_at.isoformat(),
                    }
                ),
                permit.created_at.isoformat(),
            ),
        )

    def record_restricted_technical_test_use_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        asset_id: str,
        asset_sha256: str,
        owner_id: str,
        actions: frozenset[RightsAction],
        commercial_intent: bool,
        monetization_intent: bool,
        operation: str,
        decision_id: str,
    ) -> bool:
        asset_row = connection.execute(
            "SELECT * FROM content_assets WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()
        asset = None if asset_row is None else self._load_asset(asset_row)
        if (
            asset is None
            or asset.owner_id != owner_id
            or asset.content_sha256 != asset_sha256
        ):
            return False
        self.require_cleared_acquisition_rights_in_transaction(
            connection,
            asset_id=asset_id,
            owner_id=owner_id,
        )
        license_row = connection.execute(
            """
            SELECT metadata_json FROM content_asset_license_records
            WHERE asset_id = ? AND asset_sha256 = ? AND owner_id = ?
            ORDER BY created_at DESC, record_id DESC LIMIT 1
            """,
            (asset_id, asset_sha256, owner_id),
        ).fetchone()
        if license_row is None:
            return False
        try:
            license_state = AssetLicenseRecord.model_validate_json(
                license_row["metadata_json"]
            ).verification_state
        except ValidationError as exc:
            raise RuntimeError(
                "Restricted technical-test license record is invalid."
            ) from exc
        if license_state not in {
            AssetLicenseVerificationState.UNKNOWN,
            AssetLicenseVerificationState.PENDING_REVIEW,
        } or EvidenceDecision.REJECTED.value in self.evidence_states(
            connection,
            asset_id,
        ):
            return False
        required_actions = self.restricted_technical_test_required_actions(
            operation
        )
        if required_actions is None:
            return False
        if (
            not required_actions.issubset(actions)
            or not actions.issubset(
                {
                    RightsAction.ANALYZE,
                    RightsAction.EDIT,
                    RightsAction.CREATE_DERIVATIVE,
                }
            )
            or commercial_intent
            or monetization_intent
        ):
            return False
        row = connection.execute(
            """
            SELECT permit_id, allowed_operations_json FROM content_restricted_technical_tests
            WHERE asset_id = ? AND asset_sha256 = ? AND owner_id = ?
              AND status = 'active' AND expires_at > ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (
                asset_id,
                asset_sha256,
                owner_id,
                datetime.now(UTC).isoformat(),
            ),
        ).fetchone()
        if row is None:
            return False
        try:
            allowed_operations = frozenset(
                json.loads(row["allowed_operations_json"])
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "Restricted technical-test permit has invalid operation scope."
            ) from exc
        if operation not in allowed_operations:
            return False
        connection.execute(
            """
            INSERT INTO content_restricted_technical_test_events (
                permit_id, asset_id, asset_sha256, actor, event_type,
                operation, rights_state, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["permit_id"],
                asset_id,
                asset_sha256,
                owner_id,
                "restricted_local_operation",
                operation,
                "unknown",
                self._canonical(
                    {
                        "decision_id": decision_id,
                        "actions": sorted(action.value for action in actions),
                        "commercial_intent": False,
                        "monetization_intent": False,
                    }
                ),
                datetime.now(UTC).isoformat(),
            ),
        )
        return True

    @staticmethod
    def restricted_technical_test_required_actions(
        operation: str,
    ) -> frozenset[RightsAction] | None:
        return {
            "transcription": frozenset({RightsAction.ANALYZE}),
            "transcript_read": frozenset({RightsAction.ANALYZE}),
            "highlight": frozenset({RightsAction.ANALYZE}),
            "visual_analysis": frozenset({RightsAction.ANALYZE}),
            "source_planning": frozenset({RightsAction.ANALYZE}),
            "private_render": frozenset({RightsAction.CREATE_DERIVATIVE}),
            "project_render": frozenset(
                {RightsAction.EDIT, RightsAction.CREATE_DERIVATIVE}
            ),
        }.get(operation)

    def revoke_restricted_technical_test_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        permit_id: str,
        owner_id: str,
    ) -> bool:
        row = connection.execute(
            """
            SELECT asset_id, asset_sha256, status
            FROM content_restricted_technical_tests
            WHERE permit_id = ? AND owner_id = ?
            """,
            (permit_id, owner_id),
        ).fetchone()
        if row is None:
            return False
        if row["status"] == "revoked":
            return True
        now = datetime.now(UTC).isoformat()
        connection.execute(
            """
            UPDATE content_restricted_technical_tests
            SET status = 'revoked'
            WHERE permit_id = ? AND owner_id = ? AND status = 'active'
            """,
            (permit_id, owner_id),
        )
        connection.execute(
            """
            INSERT INTO content_restricted_technical_test_events (
                permit_id, asset_id, asset_sha256, actor, event_type,
                operation, rights_state, detail_json, created_at
            ) VALUES (?, ?, ?, ?, 'permit_revoked', NULL, 'unknown', '{}', ?)
            """,
            (
                permit_id,
                row["asset_id"],
                row["asset_sha256"],
                owner_id,
                now,
            ),
        )
        return True

    def get_restricted_technical_test(
        self,
        permit_id: str,
        owner_id: str,
    ) -> RestrictedTechnicalTestPermit | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM content_restricted_technical_tests
                WHERE permit_id = ? AND owner_id = ?
                """,
                (permit_id, owner_id),
            ).fetchone()
        if row is None:
            return None
        try:
            allowed_operations = frozenset(
                json.loads(row["allowed_operations_json"])
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "Restricted technical-test permit has invalid operation scope."
            ) from exc
        return RestrictedTechnicalTestPermit(
            permit_id=row["permit_id"],
            asset_id=row["asset_id"],
            asset_sha256=row["asset_sha256"],
            owner_id=row["owner_id"],
            license_record_id=row["license_record_id"],
            allowed_operations=allowed_operations,
            commercial_use=bool(row["commercial_use"]),
            external_distribution=bool(row["external_distribution"]),
            status=RestrictedTechnicalTestStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
        )

    def get_latest_asset_license_record(
        self,
        asset_id: str,
        owner_id: str,
    ) -> AssetLicenseRecord | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT metadata_json FROM content_asset_license_records
                WHERE asset_id = ? AND owner_id = ?
                ORDER BY created_at DESC, record_id DESC LIMIT 1
                """,
                (asset_id, owner_id),
            ).fetchone()
        return (
            None
            if row is None
            else AssetLicenseRecord.model_validate_json(row["metadata_json"])
        )

    def create_owner_rights_attestation_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        attestation: OwnerRightsAttestation,
        actor: str,
    ) -> OwnerRightsAttestation:
        self._require_owner_rights_attestation_schema(connection)
        if attestation.owner_id != actor:
            raise PermissionError("Only the authenticated asset owner may self-attest.")
        row = connection.execute(
            "SELECT * FROM content_assets WHERE asset_id = ?",
            (attestation.asset_id,),
        ).fetchone()
        if row is None:
            raise KeyError("Owner attestation references an unknown asset.")
        asset = self._load_asset(row)
        if (
            asset.owner_id != actor
            or asset.content_sha256 != attestation.asset_sha256
            or asset.provenance.acquisition_method.value != "owner_created"
        ):
            raise PermissionError(
                "Owner self-attestation requires the authenticated owner and exact "
                "hash of an owner-created asset."
            )
        existing = connection.execute(
            "SELECT * FROM owner_rights_attestations WHERE attestation_id = ?",
            (attestation.attestation_id,),
        ).fetchone()
        if existing is not None:
            stored = self._load_owner_rights_attestation(
                existing,
                OwnerRightsAttestationStatus.ACTIVE,
            )
            if stored.model_copy(update={"status": attestation.status}) != attestation:
                raise ValueError("Owner attestation ID conflicts with existing scope.")
            return self._load_owner_rights_attestation(
                existing,
                self._owner_attestation_status(connection, attestation.attestation_id),
            )

        connection.execute(
            """
            INSERT INTO owner_rights_attestations (
                attestation_id, asset_id, asset_sha256, owner_id,
                ownership_declaration, ownership_basis, evidence_reference,
                platform, account_scope, territories_json,
                permitted_actions_json, max_duration_seconds, limitations,
                valid_from, valid_until, transfer_channel, created_at
            ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attestation.attestation_id,
                attestation.asset_id,
                attestation.asset_sha256,
                attestation.owner_id,
                attestation.ownership_basis,
                attestation.evidence_reference,
                attestation.platform.value,
                attestation.account_scope,
                self._canonical(list(attestation.territories)),
                self._canonical(
                    sorted(action.value for action in attestation.permitted_actions)
                ),
                attestation.max_duration_seconds,
                attestation.limitations,
                attestation.valid_from.isoformat(),
                attestation.valid_until.isoformat(),
                attestation.transfer_channel,
                attestation.created_at.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO owner_rights_attestation_events (
                attestation_id, event_type, actor, reason, created_at
            ) VALUES (?, 'attested', ?, 'owner_original_content_self_attestation', ?)
            """,
            (
                attestation.attestation_id,
                actor,
                attestation.created_at.isoformat(),
            ),
        )
        row = connection.execute(
            "SELECT * FROM owner_rights_attestations WHERE attestation_id = ?",
            (attestation.attestation_id,),
        ).fetchone()
        return self._load_owner_rights_attestation(
            row,
            OwnerRightsAttestationStatus.ACTIVE,
        )

    def revoke_owner_rights_attestation_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        attestation_id: str,
        owner_id: str,
        reason: str,
    ) -> OwnerRightsAttestation:
        self._require_owner_rights_attestation_schema(connection)
        row = connection.execute(
            """
            SELECT * FROM owner_rights_attestations
            WHERE attestation_id = ? AND owner_id = ?
            """,
            (attestation_id, owner_id),
        ).fetchone()
        if row is None:
            raise PermissionError("Owner attestation is unavailable to this account.")
        status = self._owner_attestation_status(connection, attestation_id)
        if status is OwnerRightsAttestationStatus.REVOKED:
            return self._load_owner_rights_attestation(row, status)
        connection.execute(
            """
            INSERT INTO owner_rights_attestation_events (
                attestation_id, event_type, actor, reason, created_at
            ) VALUES (?, 'revoked', ?, ?, ?)
            """,
            (
                attestation_id,
                owner_id,
                reason,
                self._now(),
            ),
        )
        return self._load_owner_rights_attestation(
            row,
            OwnerRightsAttestationStatus.REVOKED,
        )

    def list_owner_rights_attestations_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        asset_id: str,
        owner_id: str,
    ) -> tuple[OwnerRightsAttestation, ...]:
        if not self._table_exists(
            connection,
            "owner_rights_attestation_schema_meta",
        ):
            return ()
        self._require_owner_rights_attestation_schema(connection)
        rows = connection.execute(
            """
            SELECT a.*,
                (SELECT e.event_type
                 FROM owner_rights_attestation_events e
                 WHERE e.attestation_id = a.attestation_id
                 ORDER BY e.sequence DESC LIMIT 1) AS latest_event
            FROM owner_rights_attestations a
            WHERE a.asset_id = ? AND a.owner_id = ?
            ORDER BY a.created_at, a.attestation_id
            """,
            (asset_id, owner_id),
        ).fetchall()
        return tuple(
            self._load_owner_rights_attestation(
                row,
                (
                    OwnerRightsAttestationStatus.REVOKED
                    if row["latest_event"] == "revoked"
                    else OwnerRightsAttestationStatus.ACTIVE
                ),
            )
            for row in rows
        )

    def get_owner_rights_attestation(
        self,
        attestation_id: str,
        owner_id: str,
    ) -> OwnerRightsAttestation | None:
        with self._lock:
            if not self._table_exists(
                self._connection,
                "owner_rights_attestation_schema_meta",
            ):
                return None
            self._require_owner_rights_attestation_schema(self._connection)
            row = self._connection.execute(
                """
                SELECT * FROM owner_rights_attestations
                WHERE attestation_id = ? AND owner_id = ?
                """,
                (attestation_id, owner_id),
            ).fetchone()
            if row is None:
                return None
            status = self._owner_attestation_status(
                self._connection,
                attestation_id,
            )
        return self._load_owner_rights_attestation(row, status)

    @staticmethod
    def _owner_attestation_status(
        connection: sqlite3.Connection,
        attestation_id: str,
    ) -> OwnerRightsAttestationStatus:
        row = connection.execute(
            """
            SELECT event_type FROM owner_rights_attestation_events
            WHERE attestation_id = ?
            ORDER BY sequence DESC LIMIT 1
            """,
            (attestation_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Owner attestation has no append-only audit event.")
        return (
            OwnerRightsAttestationStatus.REVOKED
            if row["event_type"] == "revoked"
            else OwnerRightsAttestationStatus.ACTIVE
        )

    @staticmethod
    def _load_owner_rights_attestation(
        row: sqlite3.Row,
        status: OwnerRightsAttestationStatus,
    ) -> OwnerRightsAttestation:
        payload = dict(row)
        payload["ownership_declaration"] = bool(payload["ownership_declaration"])
        payload["territories"] = json.loads(payload.pop("territories_json"))
        payload["permitted_actions"] = json.loads(
            payload.pop("permitted_actions_json")
        )
        payload["platform"] = payload["platform"]
        payload["valid_from"] = datetime.fromisoformat(payload["valid_from"])
        payload["valid_until"] = datetime.fromisoformat(payload["valid_until"])
        payload["created_at"] = datetime.fromisoformat(payload["created_at"])
        payload["status"] = status
        payload.pop("latest_event", None)
        return OwnerRightsAttestation.model_validate(payload)

    @classmethod
    def _require_owner_rights_attestation_schema(
        cls,
        connection: sqlite3.Connection,
    ) -> None:
        if not cls._table_exists(
            connection,
            "owner_rights_attestation_schema_meta",
        ):
            raise RuntimeError(
                "Owner rights attestation schema requires its explicit migration."
            )
        meta = connection.execute(
            """
            SELECT schema_version FROM owner_rights_attestation_schema_meta
            WHERE singleton = 1
            """
        ).fetchone()
        if (
            meta is None
            or meta["schema_version"] != cls.OWNER_RIGHTS_ATTESTATION_SCHEMA_VERSION
        ):
            raise RuntimeError("Owner rights attestation schema is inconsistent.")
        cls._verify_owner_rights_attestation_schema(connection)

    def list_owned_source_asset_records(
        self,
        owner_id: str,
        *,
        limit: int = 100,
    ) -> tuple[dict[str, Any], ...]:
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("A source asset owner is required.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("Source asset limit must be between 1 and 100.")
        with self._lock:
            assets = self._connection.execute(
                """
                SELECT * FROM content_assets
                WHERE owner_id = ?
                ORDER BY created_at DESC, asset_id
                LIMIT ?
                """,
                (owner_id, limit),
            ).fetchall()
            results = []
            for row in assets:
                claims = self._connection.execute(
                    """
                    SELECT c.claim_id, c.rights_holder, c.authority_description,
                        c.created_at,
                        (SELECT e.evidence_id FROM rights_evidence e
                         WHERE e.claim_id = c.claim_id
                         ORDER BY e.created_at DESC LIMIT 1) AS evidence_id,
                        (SELECT e.evidence_kind FROM rights_evidence e
                         WHERE e.claim_id = c.claim_id
                         ORDER BY e.created_at DESC LIMIT 1) AS evidence_kind,
                        (SELECT e.evidence_sha256 FROM rights_evidence e
                         WHERE e.claim_id = c.claim_id
                         ORDER BY e.created_at DESC LIMIT 1) AS evidence_sha256,
                        (SELECT r.decision FROM rights_evidence e
                         JOIN evidence_reviews r ON r.review_sequence = (
                           SELECT MAX(r2.review_sequence) FROM evidence_reviews r2
                           WHERE r2.evidence_id = e.evidence_id
                         )
                         WHERE e.claim_id = c.claim_id
                         ORDER BY e.created_at DESC LIMIT 1) AS review_decision,
                        (SELECT r.reviewer_principal FROM rights_evidence e
                         JOIN evidence_reviews r ON r.review_sequence = (
                           SELECT MAX(r2.review_sequence) FROM evidence_reviews r2
                           WHERE r2.evidence_id = e.evidence_id
                         )
                         WHERE e.claim_id = c.claim_id
                         ORDER BY e.created_at DESC LIMIT 1) AS reviewer_principal,
                        (SELECT r.created_at FROM rights_evidence e
                         JOIN evidence_reviews r ON r.review_sequence = (
                           SELECT MAX(r2.review_sequence) FROM evidence_reviews r2
                           WHERE r2.evidence_id = e.evidence_id
                         )
                         WHERE e.claim_id = c.claim_id
                         ORDER BY e.created_at DESC LIMIT 1) AS reviewed_at,
                        EXISTS (
                          SELECT 1 FROM rights_grants g
                          WHERE g.claim_id = c.claim_id
                            AND NOT EXISTS (
                              SELECT 1 FROM rights_grant_revocations x
                              WHERE x.grant_id = g.grant_id
                            )
                        ) AS active_grant
                    FROM ownership_claims c
                    WHERE c.asset_id = ?
                    ORDER BY c.created_at DESC, c.claim_id
                    """,
                    (row["asset_id"],),
                ).fetchall()
                claim_records = []
                for claim in claims:
                    decision = claim["review_decision"]
                    evidence_history = []
                    evidence_rows = self._connection.execute(
                        """
                        SELECT evidence_id, evidence_kind, evidence_sha256, created_at
                        FROM rights_evidence
                        WHERE claim_id = ?
                        ORDER BY created_at DESC, evidence_id
                        LIMIT 50
                        """,
                        (claim["claim_id"],),
                    ).fetchall()
                    for evidence_row in evidence_rows:
                        reviews = self._connection.execute(
                            """
                            SELECT review_id, decision, reviewer_principal,
                                evidence_sha256, verified_terms_sha256, created_at
                            FROM evidence_reviews
                            WHERE evidence_id = ?
                            ORDER BY review_sequence DESC
                            LIMIT 50
                            """,
                            (evidence_row["evidence_id"],),
                        ).fetchall()
                        evidence_history.append(
                            {
                                "evidenceId": evidence_row["evidence_id"],
                                "kind": evidence_row["evidence_kind"],
                                "sha256": evidence_row["evidence_sha256"],
                                "createdAt": evidence_row["created_at"],
                                "reviewHistory": tuple(
                                    {
                                        "reviewId": review["review_id"],
                                        "decision": review["decision"],
                                        "reviewer": review["reviewer_principal"],
                                        "evidenceSha256": review["evidence_sha256"],
                                        "verifiedTermsSha256": review[
                                            "verified_terms_sha256"
                                        ],
                                        "createdAt": review["created_at"],
                                    }
                                    for review in reviews
                                ),
                            }
                        )
                    claim_records.append(
                        {
                            "claimId": claim["claim_id"],
                            "rightsHolder": claim["rights_holder"],
                            "authorityDescription": claim["authority_description"],
                            "createdAt": claim["created_at"],
                            "reviewStatus": decision or "REVIEW_REQUIRED",
                            "reviewedAt": claim["reviewed_at"],
                            "reviewer": claim["reviewer_principal"],
                            "evidence": (
                                None
                                if claim["evidence_id"] is None
                                else {
                                    "evidenceId": claim["evidence_id"],
                                    "kind": claim["evidence_kind"],
                                    "sha256": claim["evidence_sha256"],
                                }
                            ),
                            "evidenceHistory": tuple(evidence_history),
                            "activeGrant": bool(claim["active_grant"]),
                        }
                    )
                asset = self._load_asset(row)
                results.append(
                    {
                        "assetId": asset.asset_id,
                        "category": asset.category.value,
                        "contentSha256": asset.content_sha256,
                        "state": asset.state.value,
                        "provenance": {
                            "acquisition_method": asset.provenance.acquisition_method.value,
                            "acquired_at": asset.provenance.acquired_at.isoformat(),
                            "source_reference": asset.provenance.source_reference,
                            "provider": asset.provenance.provider,
                            "provider_record_id": asset.provenance.provider_record_id,
                        },
                        "createdAt": asset.created_at.isoformat(),
                        "claims": tuple(claim_records),
                        "rightsStatus": (
                            "AUTHORIZED"
                            if any(item["activeGrant"] for item in claim_records)
                            else "REVIEW_REQUIRED"
                            if claim_records
                            else "NOT_REVIEWED"
                        ),
                    }
                )
        return tuple(results)

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
        validate_non_overlapping_source_intervals(
            tuple(
                (
                    segment.source_asset_id,
                    segment.source_in_seconds,
                    segment.source_out_seconds,
                )
                for segment in plan.segments
            ),
            source_kind="video",
        )
        if isinstance(plan, ContentCreativeEditPlanV2):
            source_audio_intervals: list[tuple[str, float, float]] = []
            bed_audio_intervals: list[tuple[str, float, float]] = []
            for segment in plan.segments:
                audio = segment.audio
                if audio.mode == "source_audio":
                    if (
                        audio.source_asset_id is None
                        or audio.source_in_seconds is None
                        or audio.source_out_seconds is None
                    ):
                        raise ValueError(
                            "Creative plan source audio interval is incomplete."
                        )
                    source_audio_intervals.append(
                        (
                            audio.source_asset_id,
                            audio.source_in_seconds,
                            audio.source_out_seconds,
                        )
                    )
                if audio.bed_source_asset_id is not None:
                    if (
                        audio.bed_source_in_seconds is None
                        or audio.bed_source_out_seconds is None
                    ):
                        raise ValueError(
                            "Creative plan bed audio interval is incomplete."
                        )
                    bed_audio_intervals.append(
                        (
                            audio.bed_source_asset_id,
                            audio.bed_source_in_seconds,
                            audio.bed_source_out_seconds,
                        )
                    )
            validate_non_overlapping_source_intervals(
                tuple(source_audio_intervals),
                source_kind="audio",
            )
            validate_non_overlapping_source_intervals(
                tuple(bed_audio_intervals),
                source_kind="bed audio",
            )
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
            elif schema_version == "cre-creative-plan-v3":
                plan = ContentCreativeEditPlanV3.model_validate_json(canonical)
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
                and schema["schema_version"]
                in {
                    self.PUBLISHING_ATTEMPT_SCHEMA_VERSION,
                    self.SCHEMA_VERSION,
                }
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
            institutional_usage_valid = False
            if (
                grant.agreement_id is None
                and evidence_integrity_valid
                and row["evidence_decision"] == EvidenceDecision.VERIFIED.value
                and row["evidence_blob"] is not None
            ):
                try:
                    institutional_blob = json.loads(bytes(row["evidence_blob"]).decode("utf-8"))
                    institutional_usage_valid = (
                        isinstance(institutional_blob, dict)
                        and institutional_blob.get("institutional_usage_authority") == "nasa-official-media"
                        and institutional_blob.get("asset_id") == asset_id
                        and institutional_blob.get("authority_scope") == "nasa-origin-editorial-media"
                        and institutional_blob.get("commercial_use") is False
                        and institutional_blob.get("monetization") is False
                    )
                except (UnicodeDecodeError, json.JSONDecodeError):
                    institutional_usage_valid = False
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
                    institutional_usage_valid=institutional_usage_valid,
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

    def create_content_workflow(
        self,
        *,
        workflow_id: str,
        owner_id: str,
        title: str,
        idempotency_key: str,
        request_fingerprint: str,
        metadata: dict[str, Any] | None = None,
    ) -> ContentWorkflowRecord:
        now = datetime.now(UTC).isoformat()
        metadata_json = json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"))
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM content_workflows WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing["request_fingerprint"] != request_fingerprint:
                    raise ValueError("Workflow idempotency key conflicts with the request.")
                return self._workflow_record(existing)
            connection.execute(
                """
                INSERT INTO content_workflows (
                    workflow_id, owner_id, title, state, version,
                    idempotency_key, request_fingerprint, execution_attempt,
                    provider, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?, 0, NULL, ?, ?, ?)
                """,
                (
                    workflow_id,
                    owner_id,
                    title,
                    ContentWorkflowState.DISCOVERED.value,
                    idempotency_key,
                    request_fingerprint,
                    metadata_json,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO content_workflow_events
                    (workflow_id, from_state, to_state, actor, reason, created_at)
                VALUES (?, NULL, ?, 'system:content_workflow', 'workflow_created', ?)
                """,
                (workflow_id, ContentWorkflowState.DISCOVERED.value, now),
            )
            row = connection.execute(
                "SELECT * FROM content_workflows WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
        return self._workflow_record(row)

    def get_content_workflow(self, workflow_id: str) -> ContentWorkflowRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM content_workflows WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
        return None if row is None else self._workflow_record(row)

    def transition_content_workflow(
        self,
        workflow_id: str,
        *,
        expected_version: int,
        target_state: ContentWorkflowState,
        actor: str,
        reason: str,
    ) -> ContentWorkflowRecord:
        transitions = {
            ContentWorkflowState.DISCOVERED: {
                ContentWorkflowState.RESEARCHING,
                ContentWorkflowState.CANCELLED,
            },
            ContentWorkflowState.RESEARCHING: {
                ContentWorkflowState.PLANNED,
                ContentWorkflowState.FAILED,
                ContentWorkflowState.CANCELLED,
            },
            ContentWorkflowState.PLANNED: {
                ContentWorkflowState.SCRIPTING,
                ContentWorkflowState.CANCELLED,
            },
            ContentWorkflowState.SCRIPTING: {
                ContentWorkflowState.SCRIPT_READY,
                ContentWorkflowState.FAILED,
                ContentWorkflowState.CANCELLED,
            },
            ContentWorkflowState.SCRIPT_READY: {
                ContentWorkflowState.PRODUCING,
                ContentWorkflowState.CANCELLED,
            },
            ContentWorkflowState.PRODUCING: {
                ContentWorkflowState.PRODUCTION_READY,
                ContentWorkflowState.FAILED,
                ContentWorkflowState.NEEDS_RECONCILIATION,
                ContentWorkflowState.CANCELLED,
            },
            ContentWorkflowState.PRODUCTION_READY: {
                ContentWorkflowState.VALIDATING,
                ContentWorkflowState.CANCELLED,
            },
            ContentWorkflowState.VALIDATING: {
                ContentWorkflowState.AWAITING_APPROVAL,
                ContentWorkflowState.FAILED,
                ContentWorkflowState.CANCELLED,
            },
            ContentWorkflowState.AWAITING_APPROVAL: {
                ContentWorkflowState.APPROVED,
                ContentWorkflowState.CANCELLED,
            },
            ContentWorkflowState.APPROVED: {ContentWorkflowState.SCHEDULED},
            ContentWorkflowState.SCHEDULED: {
                ContentWorkflowState.PUBLISHING,
                ContentWorkflowState.CANCELLED,
            },
            ContentWorkflowState.PUBLISHING: {
                ContentWorkflowState.PUBLISHED,
                ContentWorkflowState.NEEDS_RECONCILIATION,
                ContentWorkflowState.FAILED,
            },
            ContentWorkflowState.PUBLISHED: {ContentWorkflowState.MEASURING},
            ContentWorkflowState.MEASURING: {
                ContentWorkflowState.COMPLETED,
                ContentWorkflowState.NEEDS_RECONCILIATION,
            },
            ContentWorkflowState.NEEDS_RECONCILIATION: {
                ContentWorkflowState.PUBLISHING,
                ContentWorkflowState.PRODUCING,
                ContentWorkflowState.FAILED,
                ContentWorkflowState.CANCELLED,
            },
            ContentWorkflowState.FAILED: {ContentWorkflowState.RESEARCHING},
            ContentWorkflowState.CANCELLED: set(),
            ContentWorkflowState.COMPLETED: set(),
        }
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM content_workflows WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown content workflow: {workflow_id}")
            current = ContentWorkflowState(row["state"])
            if row["version"] != expected_version:
                raise ValueError("Content workflow version is stale.")
            if target_state == current:
                return self._workflow_record(row)
            if target_state not in transitions[current]:
                raise ValueError(f"Invalid workflow transition: {current.value} -> {target_state.value}.")
            now = datetime.now(UTC).isoformat()
            connection.execute(
                """
                UPDATE content_workflows
                SET state = ?, version = version + 1, updated_at = ?
                WHERE workflow_id = ? AND version = ?
                """,
                (target_state.value, now, workflow_id, expected_version),
            )
            connection.execute(
                """
                INSERT INTO content_workflow_events
                    (workflow_id, from_state, to_state, actor, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (workflow_id, current.value, target_state.value, actor, reason, now),
            )
            updated = connection.execute(
                "SELECT * FROM content_workflows WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
        return self._workflow_record(updated)

    def create_content_workflow_task(
        self,
        *,
        task_id: str,
        workflow_id: str,
        task_type: str,
        idempotency_key: str,
        provider: str | None = None,
        external_side_effect: bool = False,
        max_attempts: int = 3,
        input_data: dict[str, Any] | None = None,
    ) -> ContentWorkflowTask:
        if max_attempts <= 0:
            raise ValueError("Maximum task attempts must be positive.")
        now = datetime.now(UTC).isoformat()
        canonical_input = self._canonical(input_data or {})
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM content_workflow_tasks WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["workflow_id"] != workflow_id
                    or existing["task_type"] != task_type
                    or existing["provider"] != provider
                    or existing["input_json"] != canonical_input
                    or bool(existing["external_side_effect"]) != external_side_effect
                    or existing["max_attempts"] != max_attempts
                ):
                    raise ContentTaskIdempotencyConflictError(
                        "Workflow task idempotency key conflicts with the request."
                    )
                return self._workflow_task(existing)
            connection.execute(
                """
                INSERT INTO content_workflow_tasks (
                    task_id, workflow_id, idempotency_key, task_type, state,
                    attempt, provider, result_json, error_code, created_at, updated_at,
                    external_side_effect, max_attempts
                    , input_json
                ) VALUES (?, ?, ?, ?, 'pending', 0, ?, NULL, NULL, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    workflow_id,
                    idempotency_key,
                    task_type,
                    provider,
                    now,
                    now,
                    int(external_side_effect),
                    max_attempts,
                    canonical_input,
                ),
            )
            row = connection.execute(
                "SELECT * FROM content_workflow_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            self._append_workflow_task_event(
                connection,
                task_id=task_id,
                from_state=None,
                to_state="pending",
                event_type="task_created",
                attempt=0,
                timestamp=now,
            )
        return self._workflow_task(row)

    def get_content_workflow_task(self, task_id: str) -> ContentWorkflowTask | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM content_workflow_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return None if row is None else self._workflow_task(row)

    def list_content_workflow_tasks(self) -> tuple[ContentWorkflowTask, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM content_workflow_tasks ORDER BY created_at"
            ).fetchall()
        return tuple(self._workflow_task(row) for row in rows)

    def update_content_workflow_task(
        self,
        task_id: str,
        *,
        state: str,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> ContentWorkflowTask:
        if state not in {"pending", "running", "completed", "failed", "cancelled", "uncertain"}:
            raise ValueError(f"Invalid workflow task state: {state}")
        now = datetime.now(UTC).isoformat()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM content_workflow_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if existing is None:
                raise KeyError(f"Unknown content workflow task: {task_id}")
            previous_state = existing["state"]
            if previous_state in {"running", "cancellation_requested"}:
                raise ValueError(
                    "Active tasks must be finalized by their lease owner or "
                    "the cancellation lifecycle."
                )
            if state == "running":
                raise ValueError("Tasks may enter running only through an atomic lease claim.")
            if state == "cancelled":
                raise ValueError(
                    "Task cancellation must use request_content_workflow_task_cancellation."
                )
            if previous_state in {"completed", "failed", "cancelled", "uncertain"} and (
                state != previous_state
            ):
                raise ValueError(
                    "Terminal tasks cannot be reopened administratively; "
                    "unresolved tasks require reconciliation."
                )
            changed = connection.execute(
                """
                UPDATE content_workflow_tasks
                SET state = ?, attempt = attempt + CASE WHEN ? = 'running' THEN 1 ELSE 0 END,
                    result_json = ?, error_code = ?, updated_at = ?
                WHERE task_id = ? AND state = ?
                  AND (state NOT IN ('running', 'cancellation_requested'))
                """,
                (
                    state,
                    state,
                    None if result is None else json.dumps(result, sort_keys=True),
                    error_code,
                    now,
                    task_id,
                    previous_state,
                ),
            )
            if changed.rowcount != 1:
                raise RuntimeError("Content workflow task changed during administrative update.")
            row = connection.execute(
                "SELECT * FROM content_workflow_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            self._append_workflow_task_event(
                connection,
                task_id=task_id,
                from_state=previous_state,
                to_state=state,
                event_type="administrative_state_update",
                attempt=row["attempt"],
                worker_id=row["worker_id"],
                failure_class=row["failure_class"],
                detail=error_code,
                timestamp=now,
            )
        return self._workflow_task(row)

    @staticmethod
    def _append_workflow_task_event(
        connection: sqlite3.Connection,
        *,
        task_id: str,
        from_state: str | None,
        to_state: str,
        event_type: str,
        attempt: int,
        worker_id: str | None = None,
        failure_class: str | None = None,
        detail: str | None = None,
        timestamp: str | None = None,
    ) -> None:
        payload = json.dumps(
            {
                "event_type": event_type,
                "from_state": from_state,
                "to_state": to_state,
                "attempt": attempt,
                "detail": detail,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        connection.execute(
            """
            INSERT INTO content_workflow_task_events
                (task_id, state, worker_id, failure_class, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                to_state,
                worker_id,
                failure_class,
                payload,
                timestamp or datetime.now(UTC).isoformat(),
            ),
        )

    def request_content_workflow_task_cancellation(
        self,
        task_id: str,
        *,
        actor: str = "operator",
    ) -> ContentWorkflowTask:
        if not actor.strip():
            raise ValueError("Cancellation actor must not be blank.")
        now = datetime.now(UTC).isoformat()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM content_workflow_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown content workflow task: {task_id}")
            if row["state"] == "pending":
                target_state = "cancelled"
                event_type = "cancellation_acknowledged"
                self._append_workflow_task_event(
                    connection,
                    task_id=task_id,
                    from_state="pending",
                    to_state="cancellation_requested",
                    event_type="cancellation_requested",
                    attempt=row["attempt"],
                    detail=f"requested_by:{actor}",
                    timestamp=now,
                )
                connection.execute(
                    """
                    UPDATE content_workflow_tasks
                    SET state = ?, error_code = 'CANCELLED', completed_at = ?,
                        next_attempt_at = NULL, updated_at = ?
                    WHERE task_id = ? AND state = 'pending'
                    """,
                    (target_state, now, now, task_id),
                )
            elif row["state"] == "running":
                target_state = "cancellation_requested"
                event_type = "cancellation_requested"
                connection.execute(
                    """
                    UPDATE content_workflow_tasks
                    SET state = ?, error_code = 'CANCELLATION_REQUESTED', updated_at = ?
                    WHERE task_id = ? AND state = 'running'
                      AND worker_id = ? AND lease_token = ?
                    """,
                    (target_state, now, task_id, row["worker_id"], row["lease_token"]),
                )
            elif row["state"] == "cancellation_requested":
                return self._workflow_task(row)
            else:
                raise ValueError("Only pending or running tasks may be cancelled.")
            updated = connection.execute(
                "SELECT * FROM content_workflow_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            self._append_workflow_task_event(
                connection,
                task_id=task_id,
                from_state=row["state"],
                to_state=target_state,
                event_type=event_type,
                attempt=updated["attempt"],
                worker_id=None if row["state"] == "pending" else row["worker_id"],
                detail=f"requested_by:{actor}",
                timestamp=now,
            )
        return self._workflow_task(updated)

    def confirm_content_workflow_task_external_effects(
        self,
        task_id: str,
        *,
        worker_id: str,
        lease_token: str,
    ) -> ContentWorkflowTask:
        now = datetime.now(UTC)
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM content_workflow_tasks
                WHERE task_id = ? AND state = 'running'
                  AND worker_id = ? AND lease_token = ?
                  AND lease_expires_at IS NOT NULL AND lease_expires_at > ?
                """,
                (task_id, worker_id, lease_token, now.isoformat()),
            ).fetchone()
            if row is None:
                raise RuntimeError("Task lease is stale, expired, or owned by another worker.")
            if not row["external_side_effect"]:
                connection.execute(
                    """
                    UPDATE content_workflow_tasks
                    SET external_side_effect = 1, updated_at = ?
                    WHERE task_id = ? AND state = 'running'
                      AND worker_id = ? AND lease_token = ?
                      AND lease_expires_at > ?
                    """,
                    (
                        now.isoformat(),
                        task_id,
                        worker_id,
                        lease_token,
                        now.isoformat(),
                    ),
                )
                self._append_workflow_task_event(
                    connection,
                    task_id=task_id,
                    from_state="running",
                    to_state="running",
                    event_type="external_effect_policy_confirmed",
                    attempt=row["attempt"],
                    worker_id=worker_id,
                    timestamp=now.isoformat(),
                )
            updated = connection.execute(
                "SELECT * FROM content_workflow_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return self._workflow_task(updated)

    def create_script_artifact(
        self,
        *,
        artifact_id: str,
        workflow_id: str,
        content: str,
        provenance: dict[str, Any],
        generation_metadata: dict[str, Any] | None = None,
        state: str = "draft",
    ) -> ContentScriptArtifact:
        if not content:
            raise ValueError("Script artifact content must not be empty.")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        provenance_json = self._canonical(provenance)
        generation_metadata_json = self._canonical(generation_metadata or {})
        now = self._now()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM content_script_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["content_sha256"] != digest
                    or existing["workflow_id"] != workflow_id
                    or existing["provenance_json"] != provenance_json
                    or existing["generation_metadata_json"] != generation_metadata_json
                ):
                    raise ValueError("Script artifact identity conflicts with existing content.")
                return self._script_artifact(existing)
            current = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS next_version "
                "FROM content_script_artifacts WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()["next_version"]
            connection.execute(
                """
                INSERT INTO content_script_artifacts (
                    artifact_id, workflow_id, version, content, content_sha256,
                    provenance_json, generation_metadata_json, state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    workflow_id,
                    current,
                    content,
                    digest,
                    provenance_json,
                    generation_metadata_json,
                    state,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM content_script_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        return self._script_artifact(row)

    def get_script_artifact(self, artifact_id: str) -> ContentScriptArtifact | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM content_script_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        return None if row is None else self._script_artifact(row)

    def create_production_job(
        self,
        *,
        job_id: str,
        workflow_id: str,
        script_artifact_id: str,
        configuration: dict[str, Any],
        state: str = "queued",
    ) -> ContentProductionJob:
        now = self._now()
        with self._transaction() as connection:
            script = connection.execute(
                "SELECT version, content_sha256 FROM content_script_artifacts "
                "WHERE artifact_id = ? AND workflow_id = ?",
                (script_artifact_id, workflow_id),
            ).fetchone()
            if script is None:
                raise ValueError("Production job references an unknown workflow script.")
            connection.execute(
                """
                INSERT INTO content_production_jobs (
                    job_id, workflow_id, script_artifact_id, script_version,
                    script_sha256, configuration_json, state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    workflow_id,
                    script_artifact_id,
                    script["version"],
                    script["content_sha256"],
                    self._canonical(configuration),
                    state,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM content_production_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._production_job(row)

    def create_media_production_plan(
        self,
        plan: MediaProductionPlan,
    ) -> MediaProductionPlan:
        with self._transaction() as connection:
            script = connection.execute(
                "SELECT version, content_sha256, workflow_id "
                "FROM content_script_artifacts WHERE artifact_id = ?",
                (plan.script_artifact_id,),
            ).fetchone()
            package_row = connection.execute(
                "SELECT workflow_id, payload_json FROM content_package_checkpoints "
                "WHERE package_id = ?",
                (plan.package_id,),
            ).fetchone()
            if (
                script is None
                or package_row is None
                or script["version"] != plan.script_version
                or script["content_sha256"] != plan.script_sha256
                or script["workflow_id"] != plan.workflow_id
                or package_row["workflow_id"] != plan.workflow_id
            ):
                raise ValueError("Media production plan does not match its stored package and script.")

            existing = connection.execute(
                "SELECT payload_json FROM content_media_production_plans WHERE job_id = ?",
                (plan.job_id,),
            ).fetchone()
            if existing is not None:
                saved = MediaProductionPlan.model_validate_json(existing["payload_json"])
                if saved.model_dump(
                    mode="json",
                    exclude={
                        "created_at",
                        "updated_at",
                        "state",
                        "current_stage",
                        "task_id",
                    },
                ) != plan.model_dump(
                    mode="json",
                    exclude={
                        "created_at",
                        "updated_at",
                        "state",
                        "current_stage",
                        "task_id",
                    },
                ):
                    raise ValueError("Media production job identity conflicts with its existing plan.")
                return saved

            payload = self._canonical(plan.model_dump(mode="json"))
            connection.execute(
                """
                INSERT INTO content_media_production_plans (
                    job_id, package_id, script_artifact_id, script_version,
                    script_sha256, payload_json, state, current_stage,
                    created_at, updated_at, completed_at, failure_reason, failure_class
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
                """,
                (
                    plan.job_id,
                    plan.package_id,
                    plan.script_artifact_id,
                    plan.script_version,
                    plan.script_sha256,
                    payload,
                    plan.state.value,
                    plan.current_stage,
                    plan.created_at.isoformat(),
                    plan.updated_at.isoformat(),
                ),
            )
            for scene in plan.scenes:
                for asset_reference in scene.asset_references:
                    self._insert_media_scene_asset(
                        connection,
                        job_id=plan.job_id,
                        asset_reference=asset_reference,
                        scene_id=scene.scene_id,
                        payload={
                            "asset_type": "presentation_scene_card",
                            "label": "Deterministic local scene card; not generated imagery.",
                            "sequence": scene.sequence,
                            "heading": scene.heading,
                            "narration": scene.narration,
                            "visual_description": scene.visual_description,
                            "subtitle_text": scene.subtitle_text,
                        },
                    )
            connection.execute(
                """
                INSERT INTO content_media_production_stage_events
                    (job_id, state, stage, detail, created_at)
                VALUES (?, ?, ?, NULL, ?)
                """,
                (
                    plan.job_id,
                    plan.state.value,
                    plan.current_stage,
                    plan.created_at.isoformat(),
                ),
            )
        return plan

    @staticmethod
    def _insert_media_scene_asset(
        connection: sqlite3.Connection,
        *,
        job_id: str,
        asset_reference: str,
        scene_id: str,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO content_media_scene_assets
                (asset_reference, job_id, scene_id, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                asset_reference,
                job_id,
                scene_id,
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
            ),
        )

    def get_media_production_plan(self, job_id: str) -> MediaProductionPlan | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM content_media_production_plans WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return None if row is None else MediaProductionPlan.model_validate_json(row["payload_json"])

    def list_media_production_plans(
        self,
        *,
        package_id: str | None = None,
        limit: int = 100,
    ) -> tuple[MediaProductionPlan, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("Media production plan limit must be positive.")
        query = "SELECT payload_json FROM content_media_production_plans"
        parameters: tuple[Any, ...] = ()
        if package_id is not None:
            query += " WHERE package_id = ?"
            parameters = (package_id,)
        query += " ORDER BY updated_at DESC LIMIT ?"
        parameters += (min(limit, 1000),)
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return tuple(MediaProductionPlan.model_validate_json(row["payload_json"]) for row in rows)

    def list_media_scene_assets(self, job_id: str) -> tuple[dict[str, Any], ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT asset_reference, scene_id, payload_json
                FROM content_media_scene_assets WHERE job_id = ?
                ORDER BY rowid
                """,
                (job_id,),
            ).fetchall()
        return tuple(
            {
                "asset_reference": row["asset_reference"],
                "scene_id": row["scene_id"],
                **json.loads(row["payload_json"]),
            }
            for row in rows
        )

    def update_media_production_plan(
        self,
        job_id: str,
        *,
        state: MediaProductionState,
        current_stage: str,
        detail: str | None = None,
        failure_reason: str | None = None,
        failure_class: str | None = None,
        retry_count: int | None = None,
    ) -> MediaProductionPlan:
        transitions = {
            MediaProductionState.PLANNED: {
                MediaProductionState.PREPARING,
                MediaProductionState.CANCELLED,
                MediaProductionState.FAILED,
            },
            MediaProductionState.PREPARING: {
                MediaProductionState.RENDERING,
                MediaProductionState.CANCELLED,
                MediaProductionState.FAILED,
            },
            MediaProductionState.RENDERING: {
                MediaProductionState.VALIDATING,
                MediaProductionState.PREPARING,
                MediaProductionState.CANCELLED,
                MediaProductionState.FAILED,
                MediaProductionState.UNCERTAIN,
            },
            MediaProductionState.VALIDATING: {
                MediaProductionState.COMPLETED,
                MediaProductionState.PREPARING,
                MediaProductionState.CANCELLED,
                MediaProductionState.FAILED,
                MediaProductionState.UNCERTAIN,
            },
            MediaProductionState.UNCERTAIN: {
                MediaProductionState.PREPARING,
                MediaProductionState.COMPLETED,
                MediaProductionState.CANCELLED,
                MediaProductionState.FAILED,
            },
            MediaProductionState.FAILED: {
                MediaProductionState.PREPARING,
                MediaProductionState.COMPLETED,
            },
            MediaProductionState.COMPLETED: {MediaProductionState.UNCERTAIN},
            MediaProductionState.CANCELLED: set(),
        }
        now = datetime.now(UTC)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT payload_json, state FROM content_media_production_plans "
                "WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown media production job: {job_id}")
            plan = MediaProductionPlan.model_validate_json(row["payload_json"])
            current_state = MediaProductionState(row["state"])
            if state != current_state and state not in transitions[current_state]:
                raise ValueError(
                    f"Invalid media production transition: {current_state.value} -> {state.value}."
                )
            updated = plan.model_copy(
                update={
                    "state": state,
                    "current_stage": current_stage,
                    "updated_at": now,
                    "completed_at": now if state == MediaProductionState.COMPLETED else None,
                    "failure_reason": failure_reason,
                    "failure_class": failure_class,
                    "retry_count": (
                        plan.retry_count if retry_count is None else retry_count
                    ),
                }
            )
            connection.execute(
                """
                UPDATE content_media_production_plans
                SET payload_json = ?, state = ?, current_stage = ?,
                    updated_at = ?, completed_at = ?, failure_reason = ?,
                    failure_class = ?
                WHERE job_id = ?
                """,
                (
                    self._canonical(updated.model_dump(mode="json")),
                    state.value,
                    current_stage,
                    now.isoformat(),
                    None if updated.completed_at is None else updated.completed_at.isoformat(),
                    failure_reason,
                    failure_class,
                    job_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO content_media_production_stage_events
                    (job_id, state, stage, detail, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, state.value, current_stage, detail, now.isoformat()),
            )
        return updated

    def set_media_production_task(self, job_id: str, task_id: str) -> MediaProductionPlan:
        now = datetime.now(UTC)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT payload_json FROM content_media_production_plans WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown media production job: {job_id}")
            plan = MediaProductionPlan.model_validate_json(row["payload_json"])
            if plan.task_id is not None and plan.task_id != task_id:
                raise ValueError("Media production plan already references a different worker task.")
            updated = plan.model_copy(update={"task_id": task_id, "updated_at": now})
            connection.execute(
                "UPDATE content_media_production_plans SET payload_json = ?, updated_at = ? "
                "WHERE job_id = ?",
                (
                    self._canonical(updated.model_dump(mode="json")),
                    now.isoformat(),
                    job_id,
                ),
            )
        return updated

    def list_media_production_stage_events(self, job_id: str) -> tuple[dict[str, Any], ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT sequence, state, stage, detail, created_at
                FROM content_media_production_stage_events
                WHERE job_id = ? ORDER BY sequence
                """,
                (job_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def create_media_subtitle_artifact(
        self,
        artifact: MediaSubtitleArtifact,
    ) -> MediaSubtitleArtifact:
        payload = self._canonical(artifact.model_dump(mode="json"))
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT payload_json FROM content_media_production_plans WHERE job_id = ?",
                (artifact.job_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Subtitle artifact requires an existing production plan.")
            plan = MediaProductionPlan.model_validate_json(row["payload_json"])
            if (
                plan.script_artifact_id != artifact.script_artifact_id
                or plan.script_version != artifact.script_version
                or plan.script_sha256 != artifact.script_sha256
                or artifact.subtitle_format not in {"srt", "ass"}
                or not artifact.timing_method
            ):
                raise ValueError("Subtitle artifact does not match the pinned script.")
            scene_intervals = {}
            scene_start = 0.0
            for scene in plan.scenes:
                scene_intervals[scene.scene_id] = (
                    scene_start,
                    scene_start + scene.duration_seconds,
                )
                scene_start += scene.duration_seconds
            previous_start = -1.0
            previous_end = 0.0
            for segment in artifact.segments:
                scene_interval = scene_intervals.get(segment.scene_id)
                if (
                    scene_interval is None
                    or not segment.text.strip()
                    or segment.end_seconds <= segment.start_seconds
                    or segment.start_seconds < previous_start
                    or segment.start_seconds < previous_end - 0.001
                    or segment.start_seconds < scene_interval[0] - 0.001
                    or segment.end_seconds > scene_interval[1] + 0.001
                    or segment.sequence <= 0
                ):
                    raise ValueError("Subtitle segments have invalid ordering or duration.")
                previous_start = segment.start_seconds
                previous_end = segment.end_seconds
            if artifact.segments != plan.subtitle_segments:
                raise ValueError("Subtitle artifact segments differ from the production plan.")
            existing = connection.execute(
                "SELECT payload_json FROM content_media_subtitle_artifacts "
                "WHERE job_id = ? AND subtitle_format = ?",
                (artifact.job_id, artifact.subtitle_format),
            ).fetchone()
            if existing is not None:
                saved = MediaSubtitleArtifact.model_validate_json(existing["payload_json"])
                if (
                    saved.content_sha256 != artifact.content_sha256
                    or saved.script_sha256 != artifact.script_sha256
                    or saved.storage_reference != artifact.storage_reference
                ):
                    raise ValueError("Subtitle artifact identity conflicts with persisted data.")
                return saved
            connection.execute(
                """
                INSERT INTO content_media_subtitle_artifacts
                    (artifact_id, job_id, subtitle_format, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    artifact.artifact_id,
                    artifact.job_id,
                    artifact.subtitle_format,
                    payload,
                    artifact.created_at.isoformat(),
                ),
            )
        return artifact

    def get_media_subtitle_artifact(
        self,
        artifact_id: str,
    ) -> MediaSubtitleArtifact | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM content_media_subtitle_artifacts "
                "WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        return (
            None
            if row is None
            else MediaSubtitleArtifact.model_validate_json(row["payload_json"])
        )

    def list_media_subtitle_artifacts(
        self,
        job_id: str,
    ) -> tuple[MediaSubtitleArtifact, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload_json FROM content_media_subtitle_artifacts "
                "WHERE job_id = ? ORDER BY subtitle_format",
                (job_id,),
            ).fetchall()
        return tuple(
            MediaSubtitleArtifact.model_validate_json(row["payload_json"])
            for row in rows
        )

    def create_media_validation_report(
        self,
        report: MediaValidationReport,
    ) -> MediaValidationReport:
        payload = self._canonical(report.model_dump(mode="json"))
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM content_media_validation_reports "
                "WHERE report_id = ?",
                (report.report_id,),
            ).fetchone()
            if existing is not None:
                saved = MediaValidationReport.model_validate_json(existing["payload_json"])
                if saved.model_dump(mode="json", exclude={"created_at"}) != report.model_dump(
                    mode="json",
                    exclude={"created_at"},
                ):
                    raise ValueError("Media validation report identity conflicts with stored data.")
                return saved
            connection.execute(
                """
                INSERT INTO content_media_validation_reports
                    (report_id, job_id, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    report.report_id,
                    report.job_id,
                    payload,
                    report.created_at.isoformat(),
                ),
            )
        return report

    def get_media_validation_report(self, report_id: str) -> MediaValidationReport | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM content_media_validation_reports "
                "WHERE report_id = ?",
                (report_id,),
            ).fetchone()
        return (
            None
            if row is None
            else MediaValidationReport.model_validate_json(row["payload_json"])
        )

    def get_media_validation_report_for_job(
        self,
        job_id: str,
    ) -> MediaValidationReport | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT payload_json FROM content_media_validation_reports
                WHERE job_id = ? ORDER BY created_at DESC LIMIT 1
                """,
                (job_id,),
            ).fetchone()
        return (
            None
            if row is None
            else MediaValidationReport.model_validate_json(row["payload_json"])
        )

    def create_media_artifact(self, artifact: MediaArtifact) -> MediaArtifact:
        payload = self._canonical(artifact.model_dump(mode="json"))
        with self._transaction() as connection:
            plan = connection.execute(
                "SELECT state, package_id, script_artifact_id, script_version, script_sha256 "
                "FROM content_media_production_plans WHERE job_id = ?",
                (artifact.job_id,),
            ).fetchone()
            if (
                plan is None
                or plan["state"] not in {
                    MediaProductionState.VALIDATING.value,
                    MediaProductionState.COMPLETED.value,
                }
                or plan["package_id"] != artifact.package_id
                or plan["script_artifact_id"] != artifact.script_artifact_id
                or plan["script_version"] != artifact.script_version
                or plan["script_sha256"] != artifact.script_sha256
            ):
                raise ValueError("Media artifact does not match a validating production plan.")
            existing = connection.execute(
                "SELECT payload_json FROM content_media_artifacts WHERE job_id = ?",
                (artifact.job_id,),
            ).fetchone()
            if existing is not None:
                saved = MediaArtifact.model_validate_json(existing["payload_json"])
                if (
                    saved.artifact_id != artifact.artifact_id
                    or saved.output_sha256 != artifact.output_sha256
                    or saved.storage_reference != artifact.storage_reference
                ):
                    raise ValueError("Production job already has a different media artifact.")
                return saved
            connection.execute(
                """
                INSERT INTO content_media_artifacts (
                    artifact_id, job_id, package_id, script_artifact_id,
                    script_version, script_sha256, output_sha256,
                    storage_reference, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.artifact_id,
                    artifact.job_id,
                    artifact.package_id,
                    artifact.script_artifact_id,
                    artifact.script_version,
                    artifact.script_sha256,
                    artifact.output_sha256,
                    artifact.storage_reference,
                    payload,
                    artifact.created_at.isoformat(),
                ),
            )
        return artifact

    def get_media_artifact(self, artifact_id: str) -> MediaArtifact | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM content_media_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        return None if row is None else MediaArtifact.model_validate_json(row["payload_json"])

    def get_media_artifact_for_job(self, job_id: str) -> MediaArtifact | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM content_media_artifacts WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return None if row is None else MediaArtifact.model_validate_json(row["payload_json"])

    def create_publication_package(
        self,
        publication_package: PublicationPackage,
    ) -> PublicationPackage:
        payload = self._canonical(publication_package.model_dump(mode="json"))
        with self._transaction() as connection:
            existing = connection.execute(
                """
                SELECT payload_json FROM content_publication_packages
                WHERE approval_fingerprint = ?
                """,
                (publication_package.approval_fingerprint,),
            ).fetchone()
            if existing is not None:
                saved = PublicationPackage.model_validate_json(existing["payload_json"])
                saved_data = saved.model_dump(mode="json")
                proposed_data = publication_package.model_dump(mode="json")
                saved_data.pop("created_at")
                proposed_data.pop("created_at")
                if saved_data != proposed_data:
                    raise IdempotencyConflictError(
                        "Publication fingerprint already exists with different content."
                    )
                return saved
            connection.execute(
                """
                INSERT INTO content_publication_packages (
                    publication_package_id, content_package_id,
                    approval_fingerprint, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    publication_package.publication_package_id,
                    publication_package.content_package_id,
                    publication_package.approval_fingerprint,
                    payload,
                    publication_package.created_at.isoformat(),
                ),
            )
        return publication_package

    def get_publication_package(
        self,
        publication_package_id: str,
    ) -> PublicationPackage | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT payload_json FROM content_publication_packages
                WHERE publication_package_id = ?
                """,
                (publication_package_id,),
            ).fetchone()
        return (
            None
            if row is None
            else PublicationPackage.model_validate_json(row["payload_json"])
        )

    def list_publication_packages(
        self,
        *,
        content_package_id: str,
        limit: int = 50,
    ) -> tuple[PublicationPackage, ...]:
        if not content_package_id.strip():
            raise ValueError("A source content package ID is required.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("Publication package limit must be between 1 and 100.")
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT payload_json FROM content_publication_packages
                WHERE content_package_id = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (content_package_id, limit),
            ).fetchall()
        return tuple(
            PublicationPackage.model_validate_json(row["payload_json"])
            for row in rows
        )

    @staticmethod
    def _publication_package_binding_is_current(
        connection: sqlite3.Connection,
        publication_package: PublicationPackage,
    ) -> bool:
        source_row = connection.execute(
            """
            SELECT payload_json FROM content_package_checkpoints
            WHERE package_id = ?
            """,
            (publication_package.content_package_id,),
        ).fetchone()
        media_row = connection.execute(
            """
            SELECT payload_json FROM content_media_artifacts
            WHERE artifact_id = ?
            """,
            (publication_package.media_artifact_id,),
        ).fetchone()
        script_row = connection.execute(
            """
            SELECT * FROM content_script_artifacts
            WHERE artifact_id = ?
            """,
            (publication_package.script_artifact_id,),
        ).fetchone()
        if source_row is None or media_row is None or script_row is None:
            return False
        source = ContentPackageCheckpoint.model_validate_json(source_row["payload_json"])
        media = MediaArtifact.model_validate_json(media_row["payload_json"])
        script = ContentStore._script_artifact(script_row)
        report_row = connection.execute(
            """
            SELECT payload_json FROM content_media_validation_reports
            WHERE job_id = ? ORDER BY created_at DESC LIMIT 1
            """,
            (media.job_id,),
        ).fetchone()
        approval_row = connection.execute(
            """
            SELECT actor, decision FROM content_approval_decisions
            WHERE workflow_id = ? AND artifact_id = ? AND artifact_version = ?
              AND artifact_sha256 = ? AND status = 'active'
            ORDER BY created_at DESC LIMIT 1
            """,
            (
                script.workflow_id,
                script.artifact_id,
                script.version,
                script.content_sha256,
            ),
        ).fetchone()
        if report_row is None or approval_row is None:
            return False
        report = MediaValidationReport.model_validate_json(report_row["payload_json"])
        return bool(
            source.readiness == ProductionReadiness.PRODUCTION_READY
            and source.script_artifact_id == publication_package.script_artifact_id
            and source.script_artifact_version == publication_package.script_version
            and source.script_artifact_sha256 == publication_package.script_sha256
            and script.version == publication_package.script_version
            and script.content_sha256 == publication_package.script_sha256
            and media.package_id == publication_package.content_package_id
            and media.artifact_id == publication_package.media_artifact_id
            and media.output_sha256 == publication_package.media_sha256
            and media.script_artifact_id == publication_package.script_artifact_id
            and media.script_version == publication_package.script_version
            and media.script_sha256 == publication_package.script_sha256
            and media.validation_passed
            and report.passed
            and report.artifact_id == media.artifact_id
            and approval_row["decision"] == "approved"
            and approval_row["actor"] != publication_package.requested_by
        )

    def get_publication_approval(
        self,
        publication_package_id: str,
    ) -> PublicationPackageApproval | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT payload_json FROM content_publication_approvals
                WHERE publication_package_id = ?
                ORDER BY created_at DESC, rowid DESC LIMIT 1
                """,
                (publication_package_id,),
            ).fetchone()
        if row is None:
            return None
        approval = PublicationPackageApproval.model_validate_json(row["payload_json"])
        if (
            approval.state == PublicationApprovalState.APPROVED
            and approval.expires_at <= datetime.now(UTC)
        ):
            return approval.model_copy(update={"state": PublicationApprovalState.EXPIRED})
        if approval.state == PublicationApprovalState.APPROVED:
            package = self.get_publication_package(publication_package_id)
            if package is None:
                return approval.model_copy(update={"state": PublicationApprovalState.INVALIDATED})
            with self._lock:
                current = self._publication_package_binding_is_current(
                    self._connection,
                    package,
                )
            if (
                not current
                or approval.approval_fingerprint != package.approval_fingerprint
                or approval.requested_by != package.requested_by
            ):
                return approval.model_copy(update={"state": PublicationApprovalState.INVALIDATED})
        return approval

    def get_distribution_job(self, job_id: str) -> DistributionJob | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM content_distribution_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return None if row is None else DistributionJob.model_validate_json(row["payload_json"])

    def get_distribution_job_for_package(
        self,
        publication_package_id: str,
    ) -> DistributionJob | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT payload_json FROM content_distribution_jobs
                WHERE publication_package_id = ?
                """,
                (publication_package_id,),
            ).fetchone()
        return None if row is None else DistributionJob.model_validate_json(row["payload_json"])

    def list_distribution_job_events(self, job_id: str) -> tuple[dict[str, Any], ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT sequence, job_id, from_state, to_state, actor, reason, created_at
                FROM content_distribution_job_events
                WHERE job_id = ? ORDER BY sequence
                """,
                (job_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def transition_distribution_job(
        self,
        *,
        job_id: str,
        expected_state: DistributionJobState,
        target_state: DistributionJobState,
        actor: str,
        reason: str,
        remote_publication_id: str | None = None,
        remote_url: str | None = None,
        failure_code: str | None = None,
    ) -> DistributionJob:
        transitions = {
            DistributionJobState.APPROVED: {
                DistributionJobState.UPLOADING,
                DistributionJobState.CANCELLED,
            },
            DistributionJobState.SCHEDULED: {
                DistributionJobState.UPLOADING,
                DistributionJobState.CANCELLED,
            },
            DistributionJobState.UPLOADING: {
                DistributionJobState.PROCESSING,
                DistributionJobState.FAILED,
                DistributionJobState.UNCERTAIN,
            },
            DistributionJobState.PROCESSING: {
                DistributionJobState.PUBLISHED,
                DistributionJobState.FAILED,
                DistributionJobState.UNCERTAIN,
            },
            DistributionJobState.UNCERTAIN: {
                DistributionJobState.PROCESSING,
                DistributionJobState.PUBLISHED,
                DistributionJobState.FAILED,
            },
        }
        actor = actor.strip()
        reason = reason.strip()
        if not actor or not reason or len(reason) > 500:
            raise ValueError("Distribution transitions require a bounded actor and reason.")
        if target_state not in transitions.get(expected_state, set()):
            raise ValueError("Invalid distribution job state transition.")
        if remote_publication_id is not None and not remote_publication_id.strip():
            raise ValueError("Remote publication identity must not be blank.")
        if remote_url is not None and not remote_url.startswith("https://"):
            raise ValueError("Remote publication URLs must use HTTPS.")
        if failure_code is not None and not failure_code.strip():
            raise ValueError("Distribution failure code must not be blank.")

        with self._transaction() as connection:
            row = connection.execute(
                "SELECT payload_json FROM content_distribution_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Distribution job does not exist.")
            current = DistributionJob.model_validate_json(row["payload_json"])
            if current.state != expected_state:
                raise PublishingAttemptConflictError(
                    "Distribution job changed before the requested transition."
                )
            package_row = connection.execute(
                """
                SELECT payload_json FROM content_publication_packages
                WHERE publication_package_id = ?
                """,
                (current.publication_package_id,),
            ).fetchone()
            approval_row = connection.execute(
                """
                SELECT payload_json FROM content_publication_approvals
                WHERE approval_id = ?
                """,
                (current.approval_id,),
            ).fetchone()
            if package_row is None or approval_row is None:
                raise RuntimeError("Distribution job source package or approval is missing.")
            package = PublicationPackage.model_validate_json(package_row["payload_json"])
            approval = PublicationPackageApproval.model_validate_json(
                approval_row["payload_json"]
            )
            if (
                approval.state != PublicationApprovalState.APPROVED
                or approval.expires_at <= datetime.now(UTC)
                or approval.approval_fingerprint != package.approval_fingerprint
                or approval.approved_by == approval.requested_by
                or not self._publication_package_binding_is_current(
                    connection,
                    package,
                )
            ):
                raise PermissionError("Distribution job has no current exact human approval.")
            if target_state in {
                DistributionJobState.PROCESSING,
                DistributionJobState.PUBLISHED,
            } and not (remote_publication_id or current.remote_publication_id):
                raise ValueError("Remote processing states require a platform publication ID.")
            now = datetime.now(UTC)
            updated = current.model_copy(
                update={
                    "state": target_state,
                    "remote_publication_id": (
                        remote_publication_id or current.remote_publication_id
                    ),
                    "remote_url": remote_url or current.remote_url,
                    "failure_code": failure_code,
                    "updated_at": now,
                }
            )
            cursor = connection.execute(
                """
                UPDATE content_distribution_jobs
                SET state = ?, payload_json = ?, updated_at = ?
                WHERE job_id = ? AND state = ?
                """,
                (
                    target_state.value,
                    self._canonical(updated.model_dump(mode="json")),
                    now.isoformat(),
                    job_id,
                    expected_state.value,
                ),
            )
            if cursor.rowcount != 1:
                raise PublishingAttemptConflictError(
                    "Distribution job changed before the requested transition."
                )
            connection.execute(
                """
                INSERT INTO content_distribution_job_events (
                    job_id, from_state, to_state, actor, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    expected_state.value,
                    target_state.value,
                    actor,
                    reason[:500],
                    now.isoformat(),
                ),
            ).rowcount
        return updated

    def create_distribution_record(
        self,
        record: DistributionRecord,
    ) -> DistributionRecord:
        with self._transaction() as connection:
            job_row = connection.execute(
                """
                SELECT payload_json FROM content_distribution_jobs WHERE job_id = ?
                """,
                (record.job_id,),
            ).fetchone()
            package_row = connection.execute(
                """
                SELECT payload_json FROM content_publication_packages
                WHERE publication_package_id = ?
                """,
                (record.publication_package_id,),
            ).fetchone()
            if job_row is None or package_row is None:
                raise ValueError("Distribution record job or package does not exist.")
            job = DistributionJob.model_validate_json(job_row["payload_json"])
            package = PublicationPackage.model_validate_json(package_row["payload_json"])
            if (
                job.state not in {
                    DistributionJobState.PROCESSING,
                    DistributionJobState.PUBLISHED,
                }
                or job.publication_package_id != record.publication_package_id
                or job.remote_publication_id != record.remote_publication_id
                or package.platform != record.platform
                or package.account_id != record.account_id
                or package.media_artifact_id != record.media_artifact_id
                or package.media_sha256 != record.media_sha256
                or record.remote_status not in {"processing", "published"}
            ):
                raise ValueError("Distribution record does not match confirmed remote state.")
            if (
                record.remote_status == "published"
                and job.state != DistributionJobState.PUBLISHED
            ):
                raise ValueError("Published records require confirmed published job state.")
            if record.published_at is not None and (
                record.published_at.tzinfo is None
                or record.published_at.utcoffset() is None
            ):
                raise ValueError("Publication timestamp must include a timezone.")
            existing = connection.execute(
                """
                SELECT payload_json FROM content_distribution_records
                WHERE job_id = ? OR publication_id = ?
                """,
                (record.job_id, record.publication_id),
            ).fetchone()
            payload = self._canonical(record.model_dump(mode="json"))
            if existing is not None:
                saved = DistributionRecord.model_validate_json(existing["payload_json"])
                if saved != record:
                    raise IdempotencyConflictError(
                        "A different distribution record already exists for this job."
                    )
                return saved
            connection.execute(
                """
                INSERT INTO content_distribution_records (
                    publication_id, job_id, publication_package_id,
                    remote_publication_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.publication_id,
                    record.job_id,
                    record.publication_package_id,
                    record.remote_publication_id,
                    payload,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return record

    def get_distribution_record(self, publication_id: str) -> DistributionRecord | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT payload_json FROM content_distribution_records
                WHERE publication_id = ?
                """,
                (publication_id,),
            ).fetchone()
        return (
            None
            if row is None
            else DistributionRecord.model_validate_json(row["payload_json"])
        )

    def get_distribution_record_for_job(self, job_id: str) -> DistributionRecord | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT payload_json FROM content_distribution_records
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
        return (
            None
            if row is None
            else DistributionRecord.model_validate_json(row["payload_json"])
        )

    def save_youtube_upload_progress(self, job_id: str, progress_json: str) -> None:
        if not job_id.strip() or not progress_json:
            raise ValueError("YouTube upload progress requires a job and payload.")
        now = datetime.now(UTC).isoformat()
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO content_youtube_upload_sessions (
                    job_id, progress_json, updated_at
                ) VALUES (?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    progress_json = excluded.progress_json,
                    updated_at = excluded.updated_at
                """,
                (job_id, progress_json, now),
            )

    def get_youtube_upload_progress(self, job_id: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT progress_json FROM content_youtube_upload_sessions
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
        return None if row is None else row["progress_json"]

    def create_youtube_upload_session(
        self,
        *,
        job_id: str,
        reference_id: str,
        binding_json: str,
        generation: int,
        nonce: bytes,
        encrypted_session_uri: bytes,
        expires_at: str,
    ) -> None:
        if (
            not job_id.strip()
            or not reference_id.strip()
            or not binding_json
            or isinstance(generation, bool)
            or generation < 1
            or len(nonce) != 12
            or not encrypted_session_uri
            or not expires_at
        ):
            raise ValueError("Encrypted YouTube session metadata is invalid.")
        now = datetime.now(UTC).isoformat()
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT reference_id FROM content_youtube_upload_sessions
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
            if row is not None and row["reference_id"] is not None:
                raise IdempotencyConflictError(
                    "A resumable session is already bound to this job."
                )
            if row is None:
                connection.execute(
                    """
                    INSERT INTO content_youtube_upload_sessions (
                        job_id, reference_id, binding_json, generation, nonce,
                        encrypted_session_uri, expires_at, invalidated,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        job_id,
                        reference_id,
                        binding_json,
                        generation,
                        nonce,
                        encrypted_session_uri,
                        expires_at,
                        now,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE content_youtube_upload_sessions
                    SET reference_id = ?, binding_json = ?, generation = ?,
                        nonce = ?, encrypted_session_uri = ?, expires_at = ?,
                        invalidated = 0, updated_at = ?
                    WHERE job_id = ? AND reference_id IS NULL
                    """,
                    (
                        reference_id,
                        binding_json,
                        generation,
                        nonce,
                        encrypted_session_uri,
                        expires_at,
                        now,
                        job_id,
                    ),
                )

    def get_youtube_upload_session(self, reference_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT job_id, reference_id, binding_json, generation, nonce,
                       encrypted_session_uri, expires_at, invalidated
                FROM content_youtube_upload_sessions
                WHERE reference_id = ?
                """,
                (reference_id,),
            ).fetchone()
        return None if row is None else dict(row)

    def replace_youtube_upload_session(
        self,
        *,
        reference_id: str,
        binding_json: str,
        expected_generation: int,
        nonce: bytes,
        encrypted_session_uri: bytes,
        expires_at: str,
    ) -> int:
        if (
            len(nonce) != 12
            or not encrypted_session_uri
            or not expires_at
            or isinstance(expected_generation, bool)
            or expected_generation < 1
        ):
            raise ValueError("Encrypted YouTube session metadata is invalid.")
        next_generation = expected_generation + 1
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE content_youtube_upload_sessions
                SET generation = ?, nonce = ?, encrypted_session_uri = ?,
                    expires_at = ?, invalidated = 0, updated_at = ?
                WHERE reference_id = ? AND binding_json = ?
                  AND generation = ? AND invalidated = 0
                """,
                (
                    next_generation,
                    nonce,
                    encrypted_session_uri,
                    expires_at,
                    datetime.now(UTC).isoformat(),
                    reference_id,
                    binding_json,
                    expected_generation,
                ),
            )
            if cursor.rowcount != 1:
                raise IdempotencyConflictError(
                    "Resumable session binding or generation has changed."
                )
        return next_generation

    def invalidate_youtube_upload_session(
        self,
        *,
        reference_id: str,
        binding_json: str,
        generation: int,
    ) -> None:
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE content_youtube_upload_sessions
                SET invalidated = 1, updated_at = ?
                WHERE reference_id = ? AND binding_json = ?
                  AND generation = ?
                """,
                (
                    datetime.now(UTC).isoformat(),
                    reference_id,
                    binding_json,
                    generation,
                ),
            )
            if cursor.rowcount != 1:
                raise IdempotencyConflictError(
                    "Resumable session binding or generation has changed."
                )

    def delete_youtube_upload_session(
        self,
        *,
        reference_id: str,
        binding_json: str,
        generation: int,
    ) -> None:
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE content_youtube_upload_sessions
                SET reference_id = NULL, binding_json = NULL, generation = NULL,
                    nonce = NULL, encrypted_session_uri = NULL, expires_at = NULL,
                    invalidated = 1, updated_at = ?
                WHERE reference_id = ? AND binding_json = ? AND generation = ?
                """,
                (
                    datetime.now(UTC).isoformat(),
                    reference_id,
                    binding_json,
                    generation,
                ),
            )
            if cursor.rowcount != 1:
                raise IdempotencyConflictError(
                    "Resumable session binding or generation has changed."
                )

    def create_validation_report(
        self,
        *,
        report_id: str,
        workflow_id: str,
        artifact_type: str,
        artifact_id: str,
        artifact_version: int,
        artifact_sha256: str,
        rules: list[dict[str, Any]],
        results: list[dict[str, Any]],
        failure_reasons: list[str] | None = None,
    ) -> ContentValidationReport:
        now = self._now()
        canonical_rules = self._canonical(rules)
        canonical_results = self._canonical(results)
        canonical_failures = self._canonical(failure_reasons or [])
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM content_validation_reports WHERE report_id = ?",
                (report_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["workflow_id"] != workflow_id
                    or existing["artifact_type"] != artifact_type
                    or existing["artifact_id"] != artifact_id
                    or existing["artifact_version"] != artifact_version
                    or existing["artifact_sha256"] != artifact_sha256
                    or existing["rules_json"] != canonical_rules
                    or existing["results_json"] != canonical_results
                    or existing["failure_reasons_json"] != canonical_failures
                ):
                    raise ValueError("Validation report identity conflicts with persisted content.")
                return self._validation_report(existing)
            connection.execute(
                """
                INSERT INTO content_validation_reports (
                    report_id, workflow_id, artifact_type, artifact_id,
                    artifact_version, artifact_sha256, rules_json,
                    results_json, failure_reasons_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    workflow_id,
                    artifact_type,
                    artifact_id,
                    artifact_version,
                    artifact_sha256,
                    canonical_rules,
                    canonical_results,
                    canonical_failures,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM content_validation_reports WHERE report_id = ?",
                (report_id,),
            ).fetchone()
        return self._validation_report(row)

    def create_approval_decision(
        self,
        *,
        decision_id: str,
        workflow_id: str,
        artifact_type: str,
        artifact_id: str,
        artifact_version: int,
        artifact_sha256: str,
        decision: str,
        actor: str,
        comments: str | None = None,
    ) -> ContentApprovalDecision:
        if not actor.strip():
            raise ValueError("Approval actor must not be blank.")
        if decision not in {"approved", "rejected"}:
            raise ValueError("Approval decision must be approved or rejected.")
        now = self._now()
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE content_approval_decisions
                SET status = 'superseded'
                WHERE workflow_id = ? AND artifact_id = ? AND status = 'active'
                """,
                (workflow_id, artifact_id),
            )
            connection.execute(
                """
                INSERT INTO content_approval_decisions (
                    decision_id, workflow_id, artifact_type, artifact_id,
                    artifact_version, artifact_sha256, decision, actor,
                    comments, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
                """,
                (
                    decision_id,
                    workflow_id,
                    artifact_type,
                    artifact_id,
                    artifact_version,
                    artifact_sha256,
                    decision,
                    actor,
                    comments,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM content_approval_decisions WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
        return self._approval_decision(row)

    def get_current_approval(
        self,
        *,
        workflow_id: str,
        artifact_id: str,
        artifact_version: int,
        artifact_sha256: str,
    ) -> ContentApprovalDecision | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM content_approval_decisions
                WHERE workflow_id = ? AND artifact_id = ? AND artifact_version = ?
                  AND artifact_sha256 = ? AND status = 'active'
                ORDER BY created_at DESC LIMIT 1
                """,
                (workflow_id, artifact_id, artifact_version, artifact_sha256),
            ).fetchone()
        return None if row is None else self._approval_decision(row)

    def claim_content_workflow_task(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> ContentWorkflowTask | None:
        if lease_seconds <= 0:
            raise ValueError("Task lease must be positive.")
        token = str(uuid4())
        with self._transaction() as connection:
            current_time = now or datetime.now(UTC)
            current = current_time.isoformat()
            expiry = datetime.fromtimestamp(
                current_time.timestamp() + lease_seconds, UTC
            ).isoformat()
            row = connection.execute(
                """
                SELECT * FROM content_workflow_tasks
                WHERE (
                    state = 'pending'
                    AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                )
                OR (
                    state IN ('running', 'cancellation_requested')
                    AND lease_expires_at IS NOT NULL
                    AND lease_expires_at <= ?
                )
                ORDER BY created_at
                LIMIT 1
                """,
                (current, current),
            ).fetchone()
            if row is None:
                return None
            if row["state"] == "cancellation_requested":
                connection.execute(
                    """
                    UPDATE content_workflow_tasks
                    SET state = 'uncertain', failure_class = 'unknown_outcome',
                        error_code = 'CANCELLATION_WORKER_LOST',
                        completed_at = ?, lease_expires_at = NULL,
                        worker_id = NULL, lease_token = NULL, updated_at = ?
                    WHERE task_id = ? AND state = 'cancellation_requested'
                      AND lease_expires_at <= ?
                    """,
                    (current, current, row["task_id"], current),
                )
                self._append_workflow_task_event(
                    connection,
                    task_id=row["task_id"],
                    from_state="cancellation_requested",
                    to_state="uncertain",
                    event_type="cancellation_worker_lost",
                    attempt=row["attempt"],
                    failure_class="unknown_outcome",
                    detail="Cancellation owner lease expired before acknowledgement.",
                    timestamp=current,
                )
                return None
            if (
                row["state"] == "running"
                and row["external_side_effect"]
                and row["lease_expires_at"] <= current
            ):
                connection.execute(
                    """
                    UPDATE content_workflow_tasks
                    SET state = 'uncertain', failure_class = 'unknown_outcome',
                        error_code = 'LEASE_EXPIRED_EXTERNAL_OUTCOME',
                        completed_at = ?, lease_expires_at = NULL,
                        worker_id = NULL, lease_token = NULL, updated_at = ?
                    WHERE task_id = ? AND state = 'running'
                      AND lease_expires_at <= ?
                    """,
                    (current, current, row["task_id"], current),
                )
                self._append_workflow_task_event(
                    connection,
                    task_id=row["task_id"],
                    from_state="running",
                    to_state="uncertain",
                    event_type="external_lease_expired",
                    attempt=row["attempt"],
                    failure_class="unknown_outcome",
                    detail="External task lease expired; operator reconciliation is required.",
                    timestamp=current,
                )
                return None
            if row["attempt"] >= row["max_attempts"]:
                connection.execute(
                    """
                    UPDATE content_workflow_tasks
                    SET state = 'failed', failure_class = 'retry_exhausted',
                        error_code = 'RETRY_LIMIT', updated_at = ?
                    WHERE task_id = ?
                    """,
                    (current, row["task_id"]),
                )
                self._append_workflow_task_event(
                    connection,
                    task_id=row["task_id"],
                    from_state=row["state"],
                    to_state="failed",
                    event_type="retry_exhausted",
                    attempt=row["attempt"],
                    failure_class="retry_exhausted",
                    detail="Maximum task attempts reached.",
                    timestamp=current,
                )
                return None
            changed = connection.execute(
                """
                UPDATE content_workflow_tasks
                SET state = 'running', worker_id = ?, lease_token = ?,
                    lease_expires_at = ?, started_at = COALESCE(started_at, ?),
                    attempted_at = ?, attempt = attempt + 1, updated_at = ?
                WHERE task_id = ?
                  AND (
                    state = 'pending'
                    OR (state = 'running' AND lease_expires_at <= ?)
                  )
                """,
                (
                    worker_id,
                    token,
                    expiry,
                    current,
                    current,
                    current,
                    row["task_id"],
                    current,
                ),
            ).rowcount
            if changed != 1:
                raise RuntimeError("Task was claimed by another worker.")
            claimed = connection.execute(
                "SELECT * FROM content_workflow_tasks WHERE task_id = ?",
                (row["task_id"],),
            ).fetchone()
            self._append_workflow_task_event(
                connection,
                task_id=row["task_id"],
                from_state=row["state"],
                to_state="running",
                event_type="task_recovered" if row["state"] == "running" else "task_claimed",
                attempt=claimed["attempt"],
                worker_id=worker_id,
                timestamp=current,
            )
        return self._workflow_task(claimed)

    def renew_content_workflow_task(
        self,
        task_id: str,
        *,
        worker_id: str,
        lease_token: str,
        lease_seconds: int = 60,
    ) -> ContentWorkflowTask:
        if lease_seconds <= 0:
            raise ValueError("Task lease must be positive.")
        with self._transaction() as connection:
            now = datetime.now(UTC)
            expiry = datetime.fromtimestamp(now.timestamp() + lease_seconds, UTC).isoformat()
            updated = connection.execute(
                """
                UPDATE content_workflow_tasks
                SET lease_expires_at = ?, updated_at = ?
                WHERE task_id = ? AND state IN ('running', 'cancellation_requested')
                  AND worker_id = ? AND lease_token = ?
                  AND lease_expires_at IS NOT NULL AND lease_expires_at > ?
                """,
                (expiry, now.isoformat(), task_id, worker_id, lease_token, now.isoformat()),
            ).rowcount
            if updated != 1:
                raise RuntimeError("Task lease is stale or owned by another worker.")
            row = connection.execute(
                "SELECT * FROM content_workflow_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return self._workflow_task(row)

    def complete_content_workflow_task(
        self,
        task_id: str,
        *,
        worker_id: str,
        lease_token: str,
        result: dict[str, Any] | None = None,
    ) -> ContentWorkflowTask:
        return self._finalize_content_workflow_task(
            task_id,
            worker_id=worker_id,
            lease_token=lease_token,
            state="completed",
            result=result,
        )

    def fail_content_workflow_task(
        self,
        task_id: str,
        *,
        worker_id: str,
        lease_token: str,
        failure_class: str,
        error_code: str,
        detail: str,
        retryable: bool,
        backoff_seconds: int = 0,
        max_backoff_seconds: int = 3600,
    ) -> ContentWorkflowTask:
        if failure_class not in {
            "transient",
            "permanent",
            "timeout",
            "invalid_input",
            "resource_exhaustion",
            "external_provider",
            "unknown_outcome",
        }:
            raise ValueError("Unknown workflow task failure class.")
        if failure_class in {"permanent", "invalid_input", "unknown_outcome", "retry_exhausted"}:
            retryable = False
        if backoff_seconds < 0 or max_backoff_seconds < 0:
            raise ValueError("Retry backoff values must not be negative.")
        backoff_seconds = min(backoff_seconds, max_backoff_seconds)
        state = "pending" if retryable else "uncertain" if failure_class == "unknown_outcome" else "failed"
        with self._transaction() as connection:
            now = datetime.now(UTC)
            next_attempt = (
                datetime.fromtimestamp(
                    now.timestamp() + max(backoff_seconds, 0),
                    UTC,
                ).isoformat()
                if retryable
                else None
            )
            row = connection.execute(
                """
                SELECT * FROM content_workflow_tasks
                WHERE task_id = ? AND state IN ('running', 'cancellation_requested')
                  AND worker_id = ? AND lease_token = ?
                  AND lease_expires_at IS NOT NULL AND lease_expires_at > ?
                """,
                (task_id, worker_id, lease_token, now.isoformat()),
            ).fetchone()
            if row is None:
                raise RuntimeError("Task lease is stale, expired, or owned by another worker.")
            previous_state = row["state"]
            timed_out = error_code == "TASK_TIMEOUT"
            if previous_state == "cancellation_requested":
                state = "uncertain" if row["external_side_effect"] else "cancelled"
                failure_class = "unknown_outcome" if row["external_side_effect"] else "cancelled"
                retryable = False
                next_attempt = None
                error_code = (
                    "CANCELLATION_OUTCOME_UNCERTAIN"
                    if row["external_side_effect"]
                    else "CANCELLED"
                )
            if retryable and row["attempt"] >= row["max_attempts"]:
                state = "failed"
                failure_class = "retry_exhausted"
                next_attempt = None
            changed = connection.execute(
                """
                UPDATE content_workflow_tasks
                SET state = ?, failure_class = ?, error_code = ?, result_json = ?,
                    next_attempt_at = ?, completed_at = ?, lease_expires_at = NULL,
                    worker_id = NULL, lease_token = NULL, updated_at = ?
                WHERE task_id = ? AND state IN ('running', 'cancellation_requested')
                  AND worker_id = ? AND lease_token = ?
                  AND lease_expires_at IS NOT NULL AND lease_expires_at > ?
                """,
                (
                    state,
                    failure_class,
                    error_code,
                    json.dumps({"detail": detail}, sort_keys=True),
                    next_attempt,
                    None if state == "pending" else now.isoformat(),
                    now.isoformat(),
                    task_id,
                    worker_id,
                    lease_token,
                    now.isoformat(),
                ),
            ).rowcount
            if changed != 1:
                raise RuntimeError("Task lease is stale, expired, or owned by another worker.")
            event_type = (
                "cancellation_acknowledged"
                if previous_state == "cancellation_requested"
                else "retry_scheduled"
                if state == "pending"
                else "retry_exhausted"
                if failure_class == "retry_exhausted"
                else "task_failed"
            )
            if timed_out:
                self._append_workflow_task_event(
                    connection,
                    task_id=task_id,
                    from_state=previous_state,
                    to_state=previous_state,
                    event_type="task_timeout",
                    attempt=row["attempt"],
                    worker_id=worker_id,
                    failure_class=failure_class,
                    detail=detail,
                    timestamp=now.isoformat(),
                )
            self._append_workflow_task_event(
                connection,
                task_id=task_id,
                from_state=previous_state,
                to_state=state,
                event_type=event_type,
                attempt=row["attempt"],
                worker_id=worker_id,
                failure_class=failure_class,
                detail=detail,
                timestamp=now.isoformat(),
            )
            updated = connection.execute(
                "SELECT * FROM content_workflow_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return self._workflow_task(updated)

    def _finalize_content_workflow_task(
        self,
        task_id: str,
        *,
        worker_id: str,
        lease_token: str,
        state: str,
        result: dict[str, Any] | None,
    ) -> ContentWorkflowTask:
        with self._transaction() as connection:
            now = datetime.now(UTC).isoformat()
            current = connection.execute(
                """
                SELECT * FROM content_workflow_tasks
                WHERE task_id = ? AND state IN ('running', 'cancellation_requested')
                  AND worker_id = ? AND lease_token = ?
                  AND lease_expires_at IS NOT NULL AND lease_expires_at > ?
                """,
                (task_id, worker_id, lease_token, now),
            ).fetchone()
            if current is None:
                raise RuntimeError("Task lease is stale, expired, or owned by another worker.")
            previous_state = current["state"]
            final_state = state
            final_error = None
            event_type = "task_completed"
            if previous_state == "cancellation_requested":
                final_state = "uncertain" if current["external_side_effect"] else "cancelled"
                final_error = (
                    "CANCELLATION_OUTCOME_UNCERTAIN"
                    if current["external_side_effect"]
                    else "CANCELLED"
                )
                event_type = "cancellation_acknowledged"
            changed = connection.execute(
                """
                UPDATE content_workflow_tasks
                SET state = ?, result_json = ?, error_code = ?, completed_at = ?, updated_at = ?,
                    lease_expires_at = NULL, worker_id = NULL, lease_token = NULL
                WHERE task_id = ? AND state IN ('running', 'cancellation_requested')
                  AND worker_id = ? AND lease_token = ?
                  AND lease_expires_at IS NOT NULL AND lease_expires_at > ?
                """,
                (
                    final_state,
                    None if result is None else self._canonical(result),
                    final_error,
                    now,
                    now,
                    task_id,
                    worker_id,
                    lease_token,
                    now,
                ),
            ).rowcount
            if changed != 1:
                raise RuntimeError("Task lease is stale, expired, or owned by another worker.")
            row = connection.execute(
                "SELECT * FROM content_workflow_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            self._append_workflow_task_event(
                connection,
                task_id=task_id,
                from_state=previous_state,
                to_state=final_state,
                event_type=event_type,
                attempt=row["attempt"],
                worker_id=worker_id,
                timestamp=now,
            )
        return self._workflow_task(row)

    def list_unresolved_content_workflow_tasks(self) -> list[ContentWorkflowTask]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM content_workflow_tasks
                WHERE state IN ('uncertain', 'needs_reconciliation')
                ORDER BY updated_at
                """
            ).fetchall()
        return [self._workflow_task(row) for row in rows]

    def create_content_acquisition_request_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        request_id: str,
        workflow_id: str,
        opportunity_id: str,
        source_id: str,
        owner_id: str,
        intended_use: AcquisitionIntendedUse,
        authorization_reference: str,
        idempotency_key: str,
    ) -> ContentAcquisitionRequest:
        self._require_acquisition_request_schema(connection)
        if type(intended_use) is not AcquisitionIntendedUse:
            raise TypeError("Acquisition request use must be an explicit supported value.")
        fingerprint_payload = {
            "opportunity_id": opportunity_id,
            "source_id": source_id,
            "owner_id": owner_id,
            "intended_use": intended_use.value,
        }
        request_fingerprint = self._fingerprint(
            "content_acquisition_request", fingerprint_payload
        )
        existing = connection.execute(
            """
            SELECT * FROM content_acquisition_requests
            WHERE owner_id = ? AND idempotency_key = ?
            """,
            (owner_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            if existing["request_fingerprint"] != request_fingerprint:
                raise IdempotencyConflictError(
                    "Acquisition request idempotency key conflicts with the request."
                )
            return self._acquisition_request(existing)

        opportunity = connection.execute(
            "SELECT * FROM content_opportunities WHERE opportunity_id = ?",
            (opportunity_id,),
        ).fetchone()
        if opportunity is None:
            raise KeyError(f"Unknown content opportunity: {opportunity_id}")
        if opportunity["status"] == OpportunityStatus.SELECTED.value:
            raise ValueError(
                "Opportunity is already selected and cannot create another request."
            )
        if opportunity["status"] != OpportunityStatus.OPPORTUNITY_SCORED.value:
            raise ValueError("Only a persisted scored opportunity may be selected.")
        source = connection.execute(
            """
            SELECT s.*, i.source_identity, i.provider, i.provider_content_id,
                   i.original_url, i.resolved_url
            FROM content_sources s
            JOIN content_source_identities i ON i.source_id = s.source_id
            JOIN content_evidence e ON e.source_id = s.source_id
            WHERE e.opportunity_id = ? AND s.source_id = ?
            LIMIT 1
            """,
            (opportunity_id, source_id),
        ).fetchone()
        if source is None:
            raise ValueError(
                "The selected source is not persisted evidence for this opportunity."
            )
        if not source["source_identity"]:
            raise ValueError("The selected source has no persisted source identity.")

        now = self._now()
        request = ContentAcquisitionRequest(
            request_id=request_id,
            opportunity_id=opportunity_id,
            source_id=source_id,
            source_identity=source["source_identity"],
            provider=source["provider"],
            provider_content_id=source["provider_content_id"],
            original_url=source["original_url"] or source["canonical_url"],
            canonical_url=source["canonical_url"],
            resolved_url=source["resolved_url"],
            owner_id=owner_id,
            intended_use=intended_use,
            authorization_reference=authorization_reference,
            rights_status=AcquisitionRightsStatus.PENDING_ASSET_EVALUATION,
            rights_decision_id=None,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            state=AcquisitionRequestState.AWAITING_ADAPTER,
            workflow_id=workflow_id,
            task_id=None,
            version=3,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
        )
        workflow_key = f"acquisition-request:{request_id}"
        workflow_metadata = {
            "acquisition_request_id": request_id,
            "opportunity_id": opportunity_id,
            "source_id": source_id,
            "adapter_available": False,
            "rights_status": AcquisitionRightsStatus.PENDING_ASSET_EVALUATION.value,
        }
        connection.execute(
            """
            INSERT INTO content_workflows (
                workflow_id, owner_id, title, state, version,
                idempotency_key, request_fingerprint, execution_attempt,
                provider, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 1, ?, ?, 0, ?, ?, ?, ?)
            """,
            (
                workflow_id,
                owner_id,
                opportunity["topic"],
                ContentWorkflowState.DISCOVERED.value,
                workflow_key,
                request_fingerprint,
                source["provider"],
                self._canonical(workflow_metadata),
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO content_workflow_events
                (workflow_id, from_state, to_state, actor, reason, created_at)
            VALUES (?, NULL, ?, ?, 'explicit_acquisition_request', ?)
            """,
            (workflow_id, ContentWorkflowState.DISCOVERED.value, owner_id, now),
        )
        connection.execute(
            """
            INSERT INTO content_acquisition_requests (
                request_id, opportunity_id, source_id, source_identity,
                provider, provider_content_id, original_url, canonical_url,
                resolved_url, owner_id, intended_use, authorization_reference,
                rights_status, rights_decision_id, idempotency_key,
                request_fingerprint, state, last_error_code, workflow_id, task_id,
                version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL,
                ?, NULL, 3, ?, ?)
            """,
            (
                request.request_id,
                request.opportunity_id,
                request.source_id,
                request.source_identity,
                request.provider,
                request.provider_content_id,
                request.original_url,
                request.canonical_url,
                request.resolved_url,
                request.owner_id,
                request.intended_use.value,
                request.authorization_reference,
                request.rights_status.value,
                request.idempotency_key,
                request.request_fingerprint,
                request.state.value,
                request.workflow_id,
                now,
                now,
            ),
        )
        initial_states = (
            (None, AcquisitionRequestState.REQUESTED),
            (AcquisitionRequestState.REQUESTED, AcquisitionRequestState.VALIDATING),
            (AcquisitionRequestState.VALIDATING, AcquisitionRequestState.AWAITING_ADAPTER),
        )
        for from_state, to_state in initial_states:
            connection.execute(
                """
                INSERT INTO content_acquisition_request_events (
                    request_id, from_state, to_state, actor, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    None if from_state is None else from_state.value,
                    to_state.value,
                    owner_id,
                    "request_persisted" if from_state is None else "selection_validated",
                    now,
                ),
            )
        connection.execute(
            """
            UPDATE content_opportunities
            SET status = ?, workflow_id = ?, version = version + 1, updated_at = ?
            WHERE opportunity_id = ? AND version = ?
            """,
            (
                OpportunityStatus.SELECTED.value,
                workflow_id,
                now,
                opportunity_id,
                opportunity["version"],
            ),
        )
        return request

    def get_content_acquisition_request(
        self,
        request_id: str,
    ) -> ContentAcquisitionRequest | None:
        with self._lock:
            self._require_acquisition_request_schema(self._connection)
            row = self._connection.execute(
                "SELECT * FROM content_acquisition_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        return None if row is None else self._acquisition_request(row)

    def get_content_acquisition_request_for_asset(
        self,
        asset_id: str,
        owner_id: str,
    ) -> ContentAcquisitionRequest | None:
        with self._lock:
            self._require_acquisition_request_schema(self._connection)
            row = self._connection.execute(
                """
                SELECT * FROM content_acquisition_requests
                WHERE asset_id = ? AND owner_id = ?
                """,
                (asset_id, owner_id),
            ).fetchone()
        return None if row is None else self._acquisition_request(row)

    def require_cleared_acquisition_rights_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        asset_id: str,
        owner_id: str,
    ) -> None:
        if not self._table_exists(
            connection,
            "content_acquisition_request_schema_meta",
        ):
            return
        self._require_acquisition_request_schema(connection)
        row = connection.execute(
            """
            SELECT state, rights_status, rights_decision_id
            FROM content_acquisition_requests
            WHERE asset_id = ? AND owner_id = ?
            """,
            (asset_id, owner_id),
        ).fetchone()
        if row is None:
            return
        if (
            row["state"] != AcquisitionRequestState.COMPLETED.value
            or row["rights_status"] != AcquisitionRightsStatus.CLEARED.value
            or row["rights_decision_id"] is None
        ):
            raise PermissionError(
                "The associated acquisition request has not cleared its intended-use rights."
            )

    def list_pending_content_acquisition_requests(
        self,
        owner_id: str,
    ) -> tuple[ContentAcquisitionRequest, ...]:
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("An authenticated acquisition owner is required.")
        with self._lock:
            self._require_acquisition_request_schema(self._connection)
            rows = self._connection.execute(
                """
                SELECT * FROM content_acquisition_requests
                WHERE owner_id = ? AND state IN (?, ?, ?)
                ORDER BY created_at, request_id
                """,
                (
                    owner_id,
                    AcquisitionRequestState.AWAITING_ADAPTER.value,
                    AcquisitionRequestState.AWAITING_RIGHTS.value,
                    AcquisitionRequestState.COMPLETED.value,
                ),
            ).fetchall()
        return tuple(self._acquisition_request(row) for row in rows)

    def bind_content_acquisition_asset_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        request_id: str,
        owner_id: str,
        asset: SourceAsset,
        source_reference: SourceReferenceRecord,
        rights_evaluation: RightsEvaluation,
    ) -> ContentAcquisitionRequest:
        self._require_acquisition_request_schema(connection)
        row = connection.execute(
            "SELECT * FROM content_acquisition_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if row is None:
            raise KeyError("Unknown acquisition request.")
        if row["owner_id"] != owner_id or asset.owner_id != owner_id:
            raise PermissionError("Acquisition request and asset must share the authenticated owner.")
        if row["asset_id"] is not None:
            existing_asset = connection.execute(
                "SELECT owner_id, content_sha256 FROM content_assets WHERE asset_id = ?",
                (row["asset_id"],),
            ).fetchone()
            if (
                row["asset_id"] != asset.asset_id
                or existing_asset is None
                or existing_asset["owner_id"] != owner_id
                or existing_asset["content_sha256"] != asset.content_sha256
                or row["state"]
                not in {
                    AcquisitionRequestState.AWAITING_RIGHTS.value,
                    AcquisitionRequestState.COMPLETED.value,
                }
            ):
                raise ValueError("Acquisition request is already bound to another asset.")
            return self._acquisition_request(row)

        source = connection.execute(
            """
            SELECT o.status, o.workflow_id, i.source_identity, i.provider,
                   i.provider_content_id, i.original_url,
                   i.resolved_url, s.canonical_url AS source_canonical_url
            FROM content_opportunities o
            JOIN content_evidence e ON e.opportunity_id = o.opportunity_id
            JOIN content_sources s ON s.source_id = e.source_id
            JOIN content_source_identities i ON i.source_id = e.source_id
            WHERE o.opportunity_id = ? AND i.source_id = ?
            LIMIT 1
            """,
            (row["opportunity_id"], row["source_id"]),
        ).fetchone()
        if (
            source is None
            or source["status"] != OpportunityStatus.SELECTED.value
            or source["workflow_id"] != row["workflow_id"]
            or source["source_identity"] != row["source_identity"]
            or source["provider"] != row["provider"]
            or source["provider_content_id"] != row["provider_content_id"]
            or (source["original_url"] or source["source_canonical_url"])
            != row["original_url"]
            or source["source_canonical_url"] != row["canonical_url"]
            or source["resolved_url"] != row["resolved_url"]
        ):
            raise PermissionError(
                "Acquisition request no longer matches its persisted opportunity source."
            )
        intended_use = AcquisitionIntendedUse(row["intended_use"])
        commercial_intent = intended_use in {
            AcquisitionIntendedUse.COMMERCIAL_PUBLICATION,
            AcquisitionIntendedUse.MONETIZED_PUBLICATION,
        }
        monetization_intent = (
            intended_use is AcquisitionIntendedUse.MONETIZED_PUBLICATION
        )
        if (
            row["state"] != AcquisitionRequestState.AWAITING_ADAPTER.value
            and row["state"] != AcquisitionRequestState.VALIDATING.value
        ) or (
            row["rights_status"]
            != AcquisitionRightsStatus.PENDING_ASSET_EVALUATION.value
            or asset.asset_id != source_reference.asset_id
            or asset.content_sha256 != rights_evaluation.use.expected_asset_sha256
            or rights_evaluation.asset_id != asset.asset_id
            or rights_evaluation.use.asset_id != asset.asset_id
            or rights_evaluation.use.actions != intended_use.required_rights_actions
            or rights_evaluation.use.commercial_intent != commercial_intent
            or rights_evaluation.use.monetization_intent != monetization_intent
            or source_reference.owner_id != owner_id
            or source_reference.rights_use is None
            or source_reference.rights_use.platform
            is not rights_evaluation.use.platform
            or source_reference.rights_use.account_id
            != rights_evaluation.use.account_id
            or source_reference.rights_use.territory
            != rights_evaluation.use.territory
            or source_reference.source_reference not in {
                row["original_url"],
                row["canonical_url"],
                row["resolved_url"],
            }
            or source_reference.provenance.source_identity != row["source_identity"]
            or source_reference.provenance.provider != row["provider"]
            or source_reference.provenance.provider_content_id
            != row["provider_content_id"]
        ):
            raise ValueError("Uploaded asset does not match the selected acquisition request.")

        rights_cleared = (
            rights_evaluation.copyright_state is RightsState.CLEARED
            and rights_evaluation.platform_rights_state is RightsState.CLEARED
        )
        rights_blocked = any(
            state
            in {RightsState.BLOCKED, RightsState.EXPIRED, RightsState.REVOKED}
            for state in (
                rights_evaluation.copyright_state,
                rights_evaluation.platform_rights_state,
            )
        )
        rights_status = (
            AcquisitionRightsStatus.CLEARED
            if rights_cleared
            else AcquisitionRightsStatus.BLOCKED
            if rights_blocked
            else AcquisitionRightsStatus.PENDING_ASSET_EVALUATION
        )
        target_state = (
            AcquisitionRequestState.COMPLETED
            if rights_cleared
            else AcquisitionRequestState.REJECTED
            if rights_blocked
            else AcquisitionRequestState.AWAITING_RIGHTS
        )
        now = self._now()
        changed = connection.execute(
            """
            UPDATE content_acquisition_requests
            SET asset_id = ?, rights_status = ?, rights_decision_id = ?,
                state = ?, version = version + 1, updated_at = ?
            WHERE request_id = ? AND owner_id = ? AND state = ?
              AND asset_id IS NULL AND version = ?
            """,
            (
                asset.asset_id,
                rights_status.value,
                rights_evaluation.decision_id,
                target_state.value,
                now,
                request_id,
                owner_id,
                row["state"],
                row["version"],
            ),
        ).rowcount
        if changed != 1:
            raise RuntimeError("Acquisition request changed during asset registration.")
        connection.execute(
            """
            INSERT INTO content_acquisition_request_events (
                request_id, from_state, to_state, actor, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                row["state"],
                target_state.value,
                owner_id,
                (
                    "asset_registered_rights_cleared"
                    if rights_cleared
                    else "asset_registered_rights_blocked"
                    if rights_blocked
                    else "asset_registered_rights_pending"
                ),
                now,
            ),
        )
        return self._acquisition_request(
            connection.execute(
                "SELECT * FROM content_acquisition_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        )

    def record_content_acquisition_rights_decision_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        use: RightsUse,
        evaluation: RightsEvaluation,
        actor: str,
    ) -> None:
        if not self._table_exists(connection, "content_acquisition_request_schema_meta"):
            return
        meta = connection.execute(
            """
            SELECT schema_version FROM content_acquisition_request_schema_meta
            WHERE singleton = 1
            """
        ).fetchone()
        if (
            meta is None
            or meta["schema_version"] != self.ACQUISITION_REQUEST_SCHEMA_VERSION
        ):
            return
        self._require_acquisition_request_schema(connection)
        row = connection.execute(
            """
            SELECT * FROM content_acquisition_requests
            WHERE asset_id = ? AND owner_id = ?
            """,
            (use.asset_id, actor),
        ).fetchone()
        if row is None or use.actions != AcquisitionIntendedUse(
            row["intended_use"]
        ).required_rights_actions:
            return
        if evaluation.asset_id != use.asset_id or evaluation.use != use:
            raise ValueError("Acquisition rights result does not match its asset use.")
        rights_cleared = (
            evaluation.copyright_state is RightsState.CLEARED
            and evaluation.platform_rights_state is RightsState.CLEARED
        )
        rights_blocked = any(
            state
            in {RightsState.BLOCKED, RightsState.EXPIRED, RightsState.REVOKED}
            for state in (
                evaluation.copyright_state,
                evaluation.platform_rights_state,
            )
        )
        rights_status = (
            AcquisitionRightsStatus.CLEARED
            if rights_cleared
            else AcquisitionRightsStatus.BLOCKED
            if rights_blocked
            else AcquisitionRightsStatus.PENDING_ASSET_EVALUATION
        )
        target_state = (
            AcquisitionRequestState.COMPLETED
            if rights_cleared
            else AcquisitionRequestState.REJECTED
            if rights_blocked
            else AcquisitionRequestState.AWAITING_RIGHTS
        )
        current_state = AcquisitionRequestState(row["state"])
        if current_state not in {
            AcquisitionRequestState.AWAITING_RIGHTS,
            AcquisitionRequestState.COMPLETED,
        }:
            return
        now = self._now()
        if target_state is current_state:
            connection.execute(
                """
                UPDATE content_acquisition_requests
                SET rights_status = ?, rights_decision_id = ?,
                    version = version + 1, updated_at = ?
                WHERE request_id = ? AND version = ?
                """,
                (
                    rights_status.value,
                    evaluation.decision_id,
                    now,
                    row["request_id"],
                    row["version"],
                ),
            )
        else:
            connection.execute(
                """
                UPDATE content_acquisition_requests
                SET rights_status = ?, rights_decision_id = ?, state = ?,
                    version = version + 1, updated_at = ?
                WHERE request_id = ? AND version = ?
                """,
                (
                    rights_status.value,
                    evaluation.decision_id,
                    target_state.value,
                    now,
                    row["request_id"],
                    row["version"],
                ),
            )
        connection.execute(
            """
            INSERT INTO content_acquisition_request_events (
                request_id, from_state, to_state, actor, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                row["request_id"],
                current_state.value,
                target_state.value,
                actor,
                (
                    "asset_rights_cleared"
                    if rights_cleared
                    else "asset_rights_blocked"
                    if rights_blocked
                    else "asset_rights_pending"
                ),
                now,
            ),
        )

    def get_content_acquisition_request_for_opportunity(
        self,
        opportunity_id: str,
    ) -> ContentAcquisitionRequest | None:
        with self._lock:
            self._require_acquisition_request_schema(self._connection)
            row = self._connection.execute(
                """
                SELECT * FROM content_acquisition_requests
                WHERE opportunity_id = ?
                """,
                (opportunity_id,),
            ).fetchone()
        return None if row is None else self._acquisition_request(row)

    def list_content_acquisition_request_events(
        self,
        request_id: str,
    ) -> tuple[ContentAcquisitionRequestEvent, ...]:
        with self._lock:
            self._require_acquisition_request_schema(self._connection)
            rows = self._connection.execute(
                """
                SELECT * FROM content_acquisition_request_events
                WHERE request_id = ? ORDER BY sequence
                """,
                (request_id,),
            ).fetchall()
        return tuple(self._acquisition_request_event(row) for row in rows)

    def record_content_acquisition_evidence_assessment(
        self,
        *,
        assessment_id: str,
        request_id: str,
        provider: str,
        source_identity: str,
        work_identity: str,
        provider_identity: str,
        verification_decision: str,
        policy_decision: str,
        intended_use: AcquisitionIntendedUse,
        operation: str,
        policy_id: str,
        territory: str,
        duration_seconds: float,
        reasons: tuple[str, ...],
        evidence: tuple[dict[str, Any], ...],
        evaluated_at: datetime,
    ) -> dict[str, Any]:
        if (
            not isinstance(assessment_id, str)
            or not assessment_id.strip()
            or not isinstance(request_id, str)
            or not request_id.strip()
            or not isinstance(provider, str)
            or not provider.strip()
            or not isinstance(source_identity, str)
            or not source_identity.strip()
            or not isinstance(work_identity, str)
            or not work_identity.strip()
            or not isinstance(provider_identity, str)
            or not provider_identity.strip()
            or not isinstance(verification_decision, str)
            or not verification_decision.strip()
            or policy_decision
            not in {
                "VERIFIED_APPROVED",
                "POLICY_ACCEPTED_WITH_RESIDUAL_RISK",
                "CONDITIONAL",
                "REJECTED",
                "UNKNOWN",
            }
            or type(intended_use) is not AcquisitionIntendedUse
            or not isinstance(operation, str)
            or not operation.strip()
            or not isinstance(policy_id, str)
            or not policy_id.strip()
            or not isinstance(territory, str)
            or not territory.strip()
            or not isinstance(duration_seconds, (int, float))
            or isinstance(duration_seconds, bool)
            or not math.isfinite(float(duration_seconds))
            or duration_seconds <= 0
            or not isinstance(evaluated_at, datetime)
            or evaluated_at.tzinfo is None
            or not isinstance(reasons, tuple)
            or any(not isinstance(item, str) for item in reasons)
            or not isinstance(evidence, tuple)
            or any(not isinstance(item, dict) for item in evidence)
        ):
            raise ValueError("Provider rights assessment fields are invalid.")
        reasons_json = json.dumps(reasons, ensure_ascii=False, separators=(",", ":"))
        evidence_json = json.dumps(
            evidence,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(reasons_json) > 16_384 or len(evidence_json) > 1_000_000:
            raise ValueError("Provider rights assessment exceeds its persistence limit.")
        with self._transaction() as connection:
            self._require_acquisition_request_schema(connection)
            request = connection.execute(
                """
                SELECT provider, source_identity, intended_use
                FROM content_acquisition_requests
                WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()
            if (
                request is None
                or request["provider"] != provider
                or request["source_identity"] != source_identity
                or request["intended_use"] != intended_use.value
            ):
                raise PermissionError(
                    "Provider rights assessment does not match its acquisition request."
                )
            connection.execute(
                """
                INSERT INTO content_acquisition_evidence_assessments (
                    assessment_id, request_id, provider, source_identity,
                    work_identity, provider_identity, verification_decision,
                    policy_decision, intended_use, operation, policy_id,
                    territory, duration_seconds, reasons_json, evidence_json,
                    evaluated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assessment_id,
                    request_id,
                    provider,
                    source_identity,
                    work_identity,
                    provider_identity,
                    verification_decision,
                    policy_decision,
                    intended_use.value,
                    operation,
                    policy_id,
                    territory,
                    float(duration_seconds),
                    reasons_json,
                    evidence_json,
                    evaluated_at.astimezone(UTC).isoformat(),
                ),
            )
        return {
            "assessment_id": assessment_id,
            "request_id": request_id,
            "provider": provider,
            "source_identity": source_identity,
            "work_identity": work_identity,
            "provider_identity": provider_identity,
            "verification_decision": verification_decision,
            "policy_decision": policy_decision,
            "intended_use": intended_use.value,
            "operation": operation,
            "policy_id": policy_id,
            "territory": territory,
            "duration_seconds": float(duration_seconds),
            "reasons": reasons,
            "evidence": evidence,
            "evaluated_at": evaluated_at.astimezone(UTC),
        }

    def list_content_acquisition_evidence_assessments(
        self,
        request_id: str,
    ) -> tuple[dict[str, Any], ...]:
        with self._lock:
            self._require_acquisition_request_schema(self._connection)
            rows = self._connection.execute(
                """
                SELECT * FROM content_acquisition_evidence_assessments
                WHERE request_id = ?
                ORDER BY evaluated_at, assessment_id
                """,
                (request_id,),
            ).fetchall()
        return tuple(
            {
                "assessment_id": row["assessment_id"],
                "request_id": row["request_id"],
                "provider": row["provider"],
                "source_identity": row["source_identity"],
                "work_identity": row["work_identity"],
                "provider_identity": row["provider_identity"],
                "verification_decision": row["verification_decision"],
                "policy_decision": row["policy_decision"],
                "intended_use": row["intended_use"],
                "operation": row["operation"],
                "policy_id": row["policy_id"],
                "territory": row["territory"],
                "duration_seconds": row["duration_seconds"],
                "reasons": tuple(json.loads(row["reasons_json"])),
                "evidence": tuple(json.loads(row["evidence_json"])),
                "evaluated_at": datetime.fromisoformat(row["evaluated_at"]),
            }
            for row in rows
        )

    def cancel_content_acquisition_request_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        request_id: str,
        owner_id: str,
    ) -> ContentAcquisitionRequest:
        self._require_acquisition_request_schema(connection)
        row = connection.execute(
            "SELECT * FROM content_acquisition_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown acquisition request: {request_id}")
        if row["owner_id"] != owner_id:
            raise PermissionError("Acquisition request is not owned by this principal.")
        state = AcquisitionRequestState(row["state"])
        if state is AcquisitionRequestState.CANCELLED:
            return self._acquisition_request(row)
        if state in {
            AcquisitionRequestState.REJECTED,
            AcquisitionRequestState.COMPLETED,
            AcquisitionRequestState.FAILED,
        }:
            raise ValueError("A terminal acquisition request cannot be cancelled.")
        now = self._now()
        connection.execute(
            """
            UPDATE content_acquisition_requests
            SET state = ?, version = version + 1, updated_at = ?
            WHERE request_id = ? AND version = ?
            """,
            (
                AcquisitionRequestState.CANCELLED.value,
                now,
                request_id,
                row["version"],
            ),
        )
        connection.execute(
            """
            INSERT INTO content_acquisition_request_events (
                request_id, from_state, to_state, actor, reason, created_at
            ) VALUES (?, ?, ?, ?, 'owner_cancellation', ?)
            """,
            (
                request_id,
                state.value,
                AcquisitionRequestState.CANCELLED.value,
                owner_id,
                now,
            ),
        )
        updated = connection.execute(
            "SELECT * FROM content_acquisition_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        return self._acquisition_request(updated)

    def transition_content_acquisition_request(
        self,
        request_id: str,
        *,
        expected_version: int,
        target_state: AcquisitionRequestState,
        actor: str,
        reason: str,
        error_code: str | None = None,
    ) -> ContentAcquisitionRequest:
        transitions = {
            AcquisitionRequestState.REQUESTED: {
                AcquisitionRequestState.VALIDATING,
                AcquisitionRequestState.REJECTED,
                AcquisitionRequestState.CANCELLED,
                AcquisitionRequestState.FAILED,
            },
            AcquisitionRequestState.VALIDATING: {
                AcquisitionRequestState.AWAITING_ADAPTER,
                AcquisitionRequestState.REJECTED,
                AcquisitionRequestState.CANCELLED,
                AcquisitionRequestState.FAILED,
            },
            AcquisitionRequestState.AWAITING_ADAPTER: {
                AcquisitionRequestState.VALIDATING,
                AcquisitionRequestState.AWAITING_RIGHTS,
                AcquisitionRequestState.COMPLETED,
                AcquisitionRequestState.REJECTED,
                AcquisitionRequestState.CANCELLED,
                AcquisitionRequestState.FAILED,
            },
            AcquisitionRequestState.AWAITING_RIGHTS: {
                AcquisitionRequestState.COMPLETED,
                AcquisitionRequestState.REJECTED,
                AcquisitionRequestState.CANCELLED,
                AcquisitionRequestState.FAILED,
            },
            AcquisitionRequestState.COMPLETED: {
                AcquisitionRequestState.AWAITING_RIGHTS,
            },
            AcquisitionRequestState.REJECTED: set(),
            AcquisitionRequestState.CANCELLED: set(),
            AcquisitionRequestState.FAILED: {
                AcquisitionRequestState.VALIDATING,
                AcquisitionRequestState.CANCELLED,
            },
        }
        with self._transaction() as connection:
            self._require_acquisition_request_schema(connection)
            row = connection.execute(
                "SELECT * FROM content_acquisition_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown acquisition request: {request_id}")
            state = AcquisitionRequestState(row["state"])
            if row["version"] != expected_version:
                raise ValueError("Acquisition request version is stale.")
            if target_state not in transitions[state]:
                raise ValueError(
                    f"Invalid acquisition request transition: "
                    f"{state.value} -> {target_state.value}."
                )
            now = self._now()
            connection.execute(
                """
                UPDATE content_acquisition_requests
                SET state = ?, last_error_code = ?, version = version + 1,
                    updated_at = ?
                WHERE request_id = ? AND version = ?
                """,
                (
                    target_state.value,
                    error_code,
                    now,
                    request_id,
                    expected_version,
                ),
            )
            connection.execute(
                """
                INSERT INTO content_acquisition_request_events (
                    request_id, from_state, to_state, actor, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (request_id, state.value, target_state.value, actor, reason, now),
            )
            updated = connection.execute(
                "SELECT * FROM content_acquisition_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        return self._acquisition_request(updated)

    def create_content_opportunity(
        self,
        opportunity: ContentOpportunity,
    ) -> ContentOpportunity:
        with self._transaction() as connection:
            self._require_content_identity_schema(connection)
            existing = connection.execute(
                """
                SELECT * FROM content_opportunities
                WHERE normalized_topic = ? AND niche = ?
                  AND target_audience = ? AND target_language = ?
                  AND COALESCE(target_region, '') = ?
                  AND content_format = ?
                """,
                (
                    opportunity.normalized_topic,
                    opportunity.niche,
                    opportunity.target_audience,
                    opportunity.target_language,
                    opportunity.target_region or "",
                    opportunity.content_format,
                ),
            ).fetchone()
            if existing is not None:
                return self._opportunity(existing)
            connection.execute(
                """
                INSERT INTO content_opportunities (
                    opportunity_id, topic, normalized_topic, niche,
                    target_audience, target_language, target_region,
                    content_format, discovered_at, status,
                    relevance_assessment, evidence_count, research_status,
                    workflow_id, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    opportunity.opportunity_id,
                    opportunity.topic,
                    opportunity.normalized_topic,
                    opportunity.niche,
                    opportunity.target_audience,
                    opportunity.target_language,
                    opportunity.target_region,
                    opportunity.content_format,
                    opportunity.discovered_at.isoformat(),
                    opportunity.status.value,
                    opportunity.relevance_assessment,
                    opportunity.evidence_count,
                    opportunity.research_status.value,
                    opportunity.workflow_id,
                    opportunity.version,
                    opportunity.created_at.isoformat(),
                    opportunity.updated_at.isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM content_opportunities WHERE opportunity_id = ?",
                (opportunity.opportunity_id,),
            ).fetchone()
        return self._opportunity(row)

    def get_content_opportunity(self, opportunity_id: str) -> ContentOpportunity | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM content_opportunities WHERE opportunity_id = ?",
                (opportunity_id,),
            ).fetchone()
        return None if row is None else self._opportunity(row)

    def list_content_opportunities(self, *, status: OpportunityStatus | None = None) -> tuple[ContentOpportunity, ...]:
        query = "SELECT * FROM content_opportunities"
        parameters: tuple[str, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            parameters = (status.value,)
        query += " ORDER BY updated_at DESC"
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return tuple(self._opportunity(row) for row in rows)

    def get_content_opportunity_for_provider_source(
        self,
        provider: str,
        provider_content_id: str,
    ) -> ContentOpportunity | None:
        if (
            not isinstance(provider, str)
            or not provider.strip()
            or not isinstance(provider_content_id, str)
            or not provider_content_id.strip()
        ):
            raise ValueError("Provider source identity must be explicit.")
        with self._lock:
            self._require_content_identity_schema(self._connection)
            row = self._connection.execute(
                """
                SELECT DISTINCT o.*
                FROM content_opportunities o
                JOIN content_evidence e
                  ON e.opportunity_id = o.opportunity_id
                JOIN content_source_identities i
                  ON i.source_id = e.source_id
                WHERE i.provider = ? AND i.provider_content_id = ?
                ORDER BY o.created_at DESC, o.opportunity_id
                LIMIT 1
                """,
                (provider, provider_content_id),
            ).fetchone()
        return None if row is None else self._opportunity(row)

    def update_content_opportunity(
        self,
        opportunity_id: str,
        *,
        expected_version: int,
        status: OpportunityStatus | None = None,
        research_status: ResearchCompleteness | None = None,
        relevance_assessment: str | None = None,
        workflow_id: str | None = None,
    ) -> ContentOpportunity:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM content_opportunities WHERE opportunity_id = ?",
                (opportunity_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown content opportunity: {opportunity_id}")
            if row["version"] != expected_version:
                raise ValueError("Content opportunity version is stale.")
            now = self._now()
            connection.execute(
                """
                UPDATE content_opportunities
                SET status = COALESCE(?, status),
                    research_status = COALESCE(?, research_status),
                    relevance_assessment = COALESCE(?, relevance_assessment),
                    workflow_id = COALESCE(?, workflow_id),
                    version = version + 1, updated_at = ?
                WHERE opportunity_id = ? AND version = ?
                """,
                (
                    None if status is None else status.value,
                    None if research_status is None else research_status.value,
                    relevance_assessment,
                    workflow_id,
                    now,
                    opportunity_id,
                    expected_version,
                ),
            )
            row = connection.execute(
                "SELECT * FROM content_opportunities WHERE opportunity_id = ?",
                (opportunity_id,),
            ).fetchone()
        return self._opportunity(row)

    def create_content_source(self, source: ContentSourceRecord) -> ContentSourceRecord:
        source = ContentSourceRecord.model_validate(source.model_dump(mode="python"))
        if source.content_sha256 is not None:
            raise ValueError(
                "Source media hashes can only be recorded after byte verification."
            )
        identity_key = source_deduplication_key(
            canonical_url=source.canonical_url,
            provider=source.provider,
            provider_content_id=source.provider_content_id,
        )
        with self._transaction() as connection:
            self._require_content_identity_schema(connection)
            existing = connection.execute(
                """
                SELECT s.*, i.source_identity, i.provider, i.provider_content_id,
                    i.original_url, i.resolved_url, i.metadata_fingerprint
                FROM content_sources s
                JOIN content_source_identities i ON i.source_id = s.source_id
                WHERE i.deduplication_key = ?
                """,
                (identity_key,),
            ).fetchone()
            if existing is None and source.provider_content_id is not None:
                fallback_key = source_deduplication_key(
                    canonical_url=source.canonical_url,
                    provider=None,
                    provider_content_id=None,
                )
                existing = connection.execute(
                    """
                    SELECT s.*, i.source_identity, i.provider, i.provider_content_id,
                        i.original_url, i.resolved_url, i.metadata_fingerprint
                    FROM content_sources s
                    JOIN content_source_identities i ON i.source_id = s.source_id
                    WHERE i.deduplication_key = ?
                    """,
                    (fallback_key,),
                ).fetchone()
            elif existing is None:
                matching_urls = connection.execute(
                    """
                    SELECT s.*, i.source_identity, i.provider, i.provider_content_id,
                        i.original_url, i.resolved_url, i.metadata_fingerprint
                    FROM content_sources s
                    JOIN content_source_identities i ON i.source_id = s.source_id
                    WHERE s.canonical_url = ?
                    """,
                    (source.canonical_url,),
                ).fetchall()
                if len(matching_urls) == 1:
                    existing = matching_urls[0]
            if existing is not None:
                if source.metadata_fingerprint is not None:
                    if source.metadata_fingerprint != existing["metadata_fingerprint"]:
                        connection.execute(
                            """
                            UPDATE content_sources
                            SET source_name = ?, publisher = ?, published_at = ?
                            WHERE source_id = ?
                            """,
                            (
                                source.source_name,
                                source.publisher,
                                None
                                if source.published_at is None
                                else source.published_at.isoformat(),
                                existing["source_id"],
                            ),
                        )
                    connection.execute(
                        """
                        UPDATE content_source_identities
                        SET metadata_fingerprint = ?
                        WHERE source_id = ?
                        """,
                        (source.metadata_fingerprint, existing["source_id"]),
                    )
                connection.execute(
                    """
                    UPDATE content_source_identities
                    SET source_identity = COALESCE(source_identity, ?),
                        provider = COALESCE(provider, ?),
                        provider_content_id = COALESCE(provider_content_id, ?),
                        resolved_url = COALESCE(resolved_url, ?),
                        deduplication_key = ?
                    WHERE source_id = ?
                    """,
                    (
                        source.source_identity,
                        source.provider,
                        source.provider_content_id,
                        source.resolved_url,
                        identity_key,
                        existing["source_id"],
                    ),
                )
                existing = connection.execute(
                    """
                    SELECT s.*, i.source_identity, i.provider,
                        i.provider_content_id, i.original_url, i.resolved_url,
                        i.metadata_fingerprint
                    FROM content_sources s
                    JOIN content_source_identities i ON i.source_id = s.source_id
                    WHERE s.source_id = ?
                    """,
                    (existing["source_id"],),
                ).fetchone()
                return self._source(existing)
            connection.execute(
                """
                INSERT INTO content_sources (
                    source_id, canonical_url, source_name, publisher, source_type,
                    published_at, retrieved_at, reliability, content_sha256,
                    license_classification
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source.source_id,
                    source.canonical_url,
                    source.source_name,
                    source.publisher,
                    source.source_type,
                    None if source.published_at is None else source.published_at.isoformat(),
                    source.retrieved_at.isoformat(),
                    source.reliability.value,
                    source.content_sha256,
                    source.license_classification,
                ),
            )
            connection.execute(
                """
                INSERT INTO content_source_identities (
                    source_id, source_identity, provider, provider_content_id,
                    original_url, resolved_url, metadata_fingerprint,
                    legacy_metadata_digest, deduplication_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    source.source_id,
                    source.source_identity,
                    source.provider,
                    source.provider_content_id,
                    source.original_url or source.canonical_url,
                    source.resolved_url,
                    source.metadata_fingerprint,
                    identity_key,
                ),
            )
            row = connection.execute(
                """
                SELECT s.*, i.source_identity, i.provider, i.provider_content_id,
                    i.original_url, i.resolved_url, i.metadata_fingerprint
                FROM content_sources s
                JOIN content_source_identities i ON i.source_id = s.source_id
                WHERE s.source_id = ?
                """,
                (source.source_id,),
            ).fetchone()
        return self._source(row)

    @classmethod
    def _require_content_identity_schema(
        cls,
        connection: sqlite3.Connection,
    ) -> None:
        if not cls._table_exists(connection, "content_source_identity_schema_meta"):
            raise RuntimeError(
                "Content source identity migration must run before discovery writes."
            )
        row = connection.execute(
            """
            SELECT schema_version FROM content_source_identity_schema_meta
            WHERE singleton = 1
            """
        ).fetchone()
        if row is None or row["schema_version"] != 1:
            raise RuntimeError("Content source identity schema is unavailable.")

    def upsert_configured_feed(self, feed: ConfiguredFeed) -> ConfiguredFeed:
        try:
            feed = ConfiguredFeed.model_validate(feed.model_dump(mode="python"))
        except ValidationError:
            raise ValueError("Configured feed is invalid and was not persisted.") from None
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO content_configured_feeds (
                    feed_id, feed_name, canonical_url, allowed_hostname, allow_http,
                    enabled, source_category, niches_json, keywords_json,
                    retrieval_interval_seconds, last_successful_retrieval,
                    last_attempted_retrieval, etag, last_modified, health,
                    consecutive_failures, last_response_status, last_error,
                    next_refresh_at, configuration_version, last_ingestion_count,
                    last_duplicate_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(feed_id) DO UPDATE SET
                    feed_name=excluded.feed_name,
                    canonical_url=excluded.canonical_url,
                    allowed_hostname=excluded.allowed_hostname,
                    allow_http=excluded.allow_http,
                    enabled=excluded.enabled,
                    source_category=excluded.source_category,
                    niches_json=excluded.niches_json,
                    keywords_json=excluded.keywords_json,
                    retrieval_interval_seconds=excluded.retrieval_interval_seconds,
                    configuration_version=excluded.configuration_version
                """,
                (
                    feed.feed_id, feed.feed_name, feed.canonical_url,
                    feed.allowed_hostname, int(feed.allow_http), int(feed.enabled),
                    feed.source_category, self._canonical(feed.niches),
                    self._canonical(feed.keywords), feed.retrieval_interval_seconds,
                    None if feed.last_successful_retrieval is None else feed.last_successful_retrieval.isoformat(),
                    None if feed.last_attempted_retrieval is None else feed.last_attempted_retrieval.isoformat(),
                    feed.etag, feed.last_modified, feed.health.value,
                    feed.consecutive_failures, feed.last_response_status, feed.last_error,
                    None if feed.next_refresh_at is None else feed.next_refresh_at.isoformat(),
                    feed.configuration_version, feed.last_ingestion_count,
                    feed.last_duplicate_count,
                ),
            )
            row = connection.execute(
                "SELECT * FROM content_configured_feeds WHERE feed_id = ?",
                (feed.feed_id,),
            ).fetchone()
        return self._feed(row)

    def get_configured_feed(self, feed_id: str) -> ConfiguredFeed | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM content_configured_feeds WHERE feed_id = ?",
                (feed_id,),
            ).fetchone()
        return None if row is None else self._feed(row)

    def list_configured_feeds(self, *, enabled_only: bool = False) -> tuple[ConfiguredFeed, ...]:
        query = "SELECT * FROM content_configured_feeds"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY feed_id"
        with self._lock:
            rows = self._connection.execute(query).fetchall()
        return tuple(self._feed(row) for row in rows)

    def reserve_feed_host_request(
        self,
        hostname: str,
        *,
        now: datetime,
        minimum_interval_seconds: float,
        cleanup_before: datetime | None = None,
    ) -> bool:
        """Atomically reserve a host request across runtime instances."""
        if not hostname.strip() or minimum_interval_seconds < 0:
            raise ValueError("Invalid feed host rate-limit reservation.")
        current = now.astimezone(UTC)
        current_iso = current.isoformat()
        threshold = datetime.fromtimestamp(
            current.timestamp() - minimum_interval_seconds,
            UTC,
        ).isoformat()
        with self._transaction() as connection:
            if cleanup_before is not None:
                connection.execute(
                    "DELETE FROM content_feed_host_rate_limits WHERE updated_at < ?",
                    (cleanup_before.astimezone(UTC).isoformat(),),
                )
            updated = connection.execute(
                """
                UPDATE content_feed_host_rate_limits
                SET last_reserved_at = ?, updated_at = ?
                WHERE hostname = ? AND last_reserved_at <= ?
                """,
                (current_iso, current_iso, hostname.casefold(), threshold),
            ).rowcount
            if updated == 1:
                return True
            try:
                connection.execute(
                    """
                    INSERT INTO content_feed_host_rate_limits
                        (hostname, last_reserved_at, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (hostname.casefold(), current_iso, current_iso),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def record_feed_attempt(
        self,
        feed_id: str,
        *,
        attempted_at: str,
        successful: bool,
        status: int | None,
        etag: str | None = None,
        last_modified: str | None = None,
        error: str | None = None,
        not_modified: bool = False,
        ingestion_count: int = 0,
        duplicate_count: int = 0,
        retry_after_seconds: int | None = None,
    ) -> ConfiguredFeed:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM content_configured_feeds WHERE feed_id = ?",
                (feed_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown configured feed: {feed_id}")
            attempted = datetime.fromisoformat(attempted_at)
            failures = 0 if successful else row["consecutive_failures"] + 1
            if retry_after_seconds is not None:
                backoff = max(0, min(retry_after_seconds, 7 * 24 * 60 * 60))
            elif successful:
                backoff = row["retrieval_interval_seconds"]
            else:
                backoff = min(
                    row["retrieval_interval_seconds"] * (2 ** min(failures - 1, 5)),
                    7 * 24 * 60 * 60,
                )
            next_refresh = datetime.fromtimestamp(
                attempted.timestamp() + backoff,
                UTC,
            ).isoformat()
            health = (
                FeedHealth.NOT_MODIFIED.value
                if not_modified
                else FeedHealth.HEALTHY.value
                if successful
                else FeedHealth.UNAVAILABLE.value
            )
            connection.execute(
                """
                UPDATE content_configured_feeds
                SET last_attempted_retrieval = ?,
                    last_successful_retrieval = CASE WHEN ? THEN ? ELSE last_successful_retrieval END,
                    etag = COALESCE(?, etag),
                    last_modified = COALESCE(?, last_modified),
                    health = ?, consecutive_failures = ?,
                    last_response_status = ?, last_error = ?,
                    next_refresh_at = ?, last_ingestion_count = ?,
                    last_duplicate_count = ?
                WHERE feed_id = ?
                """,
                (
                    attempted_at, int(successful), attempted_at, etag, last_modified,
                    health, failures, status, error, next_refresh,
                    ingestion_count, duplicate_count, feed_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM content_configured_feeds WHERE feed_id = ?",
                (feed_id,),
            ).fetchone()
        return self._feed(row)

    def enqueue_feed_refresh_task(
        self,
        feed: ConfiguredFeed,
        *,
        scheduled_window: str,
    ) -> ContentWorkflowTask:
        """Atomically create the durable workflow/task for one feed window."""
        idempotency_key = (
            f"feed-refresh:{feed.feed_id}:{feed.configuration_version}:{scheduled_window}"
        )
        workflow_id = f"feed-refresh-workflow:{feed.feed_id}:{feed.configuration_version}:{scheduled_window}"
        task_id = hashlib.sha256(idempotency_key.encode()).hexdigest()
        now = self._now()
        input_data = {
            "configuration_version": feed.configuration_version,
            "feed_id": feed.feed_id,
        }
        with self._transaction() as connection:
            workflow = connection.execute(
                "SELECT * FROM content_workflows WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if workflow is None:
                connection.execute(
                    """
                    INSERT INTO content_workflows (
                        workflow_id, owner_id, title, state, version,
                        idempotency_key, request_fingerprint, execution_attempt,
                        provider, metadata_json, created_at, updated_at
                    ) VALUES (?, ?, ?, 'discovered', 1, ?, ?, 0, ?, ?, ?, ?)
                    """,
                    (
                        workflow_id,
                        "system:content_refresh",
                        f"Refresh configured feed {feed.feed_id}",
                        idempotency_key,
                        idempotency_key,
                        "rss_atom",
                        self._canonical(
                            {
                                "feed_id": feed.feed_id,
                                "configuration_version": feed.configuration_version,
                                "scheduled_window": scheduled_window,
                            }
                        ),
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO content_workflow_events
                        (workflow_id, from_state, to_state, actor, reason, created_at)
                    VALUES (?, NULL, 'discovered', ?, ?, ?)
                    """,
                    (
                        workflow_id,
                        "system:content_refresh",
                        "feed_refresh_scheduled",
                        now,
                    ),
                )
            existing = connection.execute(
                "SELECT * FROM content_workflow_tasks WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["workflow_id"] != workflow_id
                    or existing["task_type"] != "ingest_rss_feed"
                    or existing["provider"] != "rss_atom"
                    or existing["input_json"] != self._canonical(input_data)
                    or bool(existing["external_side_effect"])
                    or existing["max_attempts"] != 3
                ):
                    raise ContentTaskIdempotencyConflictError(
                        "Workflow task idempotency key conflicts with the feed request."
                    )
                return self._workflow_task(existing)
            connection.execute(
                """
                INSERT INTO content_workflow_tasks (
                    task_id, workflow_id, idempotency_key, task_type, state,
                    attempt, provider, result_json, error_code, created_at, updated_at,
                    external_side_effect, max_attempts, input_json
                ) VALUES (?, ?, ?, 'ingest_rss_feed', 'pending', 0, ?, NULL, NULL,
                          ?, ?, 0, 3, ?)
                """,
                (
                    task_id,
                    workflow_id,
                    idempotency_key,
                    "rss_atom",
                    now,
                    now,
                    self._canonical(input_data),
                ),
            )
            self._append_workflow_task_event(
                connection,
                task_id=task_id,
                from_state=None,
                to_state="pending",
                event_type="task_created",
                attempt=0,
                timestamp=now,
            )
            row = connection.execute(
                "SELECT * FROM content_workflow_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return self._workflow_task(row)

    def has_active_feed_refresh_task(
        self,
        feed_id: str,
        *,
        configuration_version: int | None = None,
    ) -> bool:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT state, input_json FROM content_workflow_tasks
                WHERE task_type = 'ingest_rss_feed'
                  AND state IN ('pending', 'running', 'uncertain')
                """
            ).fetchall()
        for row in rows:
            payload = json.loads(row["input_json"])
            if payload.get("feed_id") != feed_id:
                continue
            if (
                configuration_version is None
                or payload.get("configuration_version") == configuration_version
            ):
                return True
        return False

    def create_content_evidence(self, evidence: ContentEvidenceRecord) -> ContentEvidenceRecord:
        with self._transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM content_evidence
                WHERE opportunity_id = ? AND source_id = ? AND evidence_sha256 = ?
                """,
                (evidence.opportunity_id, evidence.source_id, evidence.evidence_sha256),
            ).fetchone()
            if existing is not None:
                return self._evidence(existing)
            connection.execute(
                """
                INSERT INTO content_evidence (
                    evidence_id, opportunity_id, source_id, claim, observation,
                    observed_at, published_at, extraction_method, confidence,
                    evidence_sha256, is_inference, contradictory
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence.evidence_id,
                    evidence.opportunity_id,
                    evidence.source_id,
                    evidence.claim,
                    evidence.observation,
                    evidence.observed_at.isoformat(),
                    None if evidence.published_at is None else evidence.published_at.isoformat(),
                    evidence.extraction_method,
                    evidence.confidence,
                    evidence.evidence_sha256,
                    int(evidence.is_inference),
                    int(evidence.contradictory),
                ),
            )
            connection.execute(
                """
                UPDATE content_opportunities
                SET evidence_count = evidence_count + 1, updated_at = ?
                WHERE opportunity_id = ?
                """,
                (self._now(), evidence.opportunity_id),
            )
            row = connection.execute(
                "SELECT * FROM content_evidence WHERE evidence_id = ?",
                (evidence.evidence_id,),
            ).fetchone()
        return self._evidence(row)

    def list_content_evidence(self, opportunity_id: str) -> tuple[ContentEvidenceRecord, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM content_evidence WHERE opportunity_id = ? ORDER BY observed_at",
                (opportunity_id,),
            ).fetchall()
        return tuple(self._evidence(row) for row in rows)

    def list_content_opportunity_sources(
        self,
        opportunity_id: str,
    ) -> tuple[ContentSourceRecord, ...]:
        with self._lock:
            self._require_content_identity_schema(self._connection)
            rows = self._connection.execute(
                """
                SELECT DISTINCT s.*, i.source_identity, i.provider,
                    i.provider_content_id, i.original_url, i.resolved_url,
                    i.metadata_fingerprint
                FROM content_sources s
                JOIN content_evidence e ON e.source_id = s.source_id
                JOIN content_source_identities i ON i.source_id = s.source_id
                WHERE e.opportunity_id = ?
                ORDER BY s.source_name, s.source_id
                """,
                (opportunity_id,),
            ).fetchall()
        return tuple(self._source(row) for row in rows)

    def create_content_research_report(self, report: ContentResearchReport) -> ContentResearchReport:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO content_research_reports (
                    report_id, opportunity_id, summary, report_json,
                    research_timestamp, completeness, report_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.report_id,
                    report.opportunity_id,
                    report.summary,
                    self._canonical(report.model_dump(mode="json")),
                    report.research_timestamp.isoformat(),
                    report.completeness.value,
                    report.report_sha256,
                ),
            )
            row = connection.execute(
                "SELECT * FROM content_research_reports WHERE report_id = ?",
                (report.report_id,),
            ).fetchone()
        return self._report(row)

    def get_latest_content_research_report(self, opportunity_id: str) -> ContentResearchReport | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM content_research_reports
                WHERE opportunity_id = ? ORDER BY research_timestamp DESC LIMIT 1
                """,
                (opportunity_id,),
            ).fetchone()
        return None if row is None else self._report(row)

    def create_content_opportunity_score(self, score: ContentOpportunityScore) -> ContentOpportunityScore:
        with self._transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM content_opportunity_scores
                WHERE opportunity_id = ? AND score_version = ?
                """,
                (score.opportunity_id, score.score_version),
            ).fetchone()
            if existing is not None:
                if (
                    existing["input_snapshot_sha256"] != score.input_snapshot_sha256
                    or existing["score"] != score.score
                    or existing["dimensions_json"] != self._canonical(score.dimensions)
                    or existing["evidence_coverage"] != score.evidence_coverage
                    or existing["confidence"] != score.confidence
                    or existing["explanation_json"] != self._canonical(score.explanation)
                ):
                    raise ValueError(
                        "Opportunity score identity conflicts with persisted inputs."
                    )
                persisted = self.get_content_opportunity_score(
                    score.opportunity_id,
                    score_version=score.score_version,
                )
                if persisted is None:
                    raise RuntimeError("Persisted opportunity score disappeared.")
                return persisted
            connection.execute(
                """
                INSERT INTO content_opportunity_scores (
                    opportunity_id, score_version, score, dimensions_json,
                    evidence_coverage, confidence, explanation_json,
                    input_snapshot_sha256, scored_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    score.opportunity_id,
                    score.score_version,
                    score.score,
                    self._canonical(score.dimensions),
                    score.evidence_coverage,
                    score.confidence,
                    self._canonical(score.explanation),
                    score.input_snapshot_sha256,
                    score.scored_at.isoformat(),
                ),
            )
        return score

    def get_content_opportunity_score(
        self,
        opportunity_id: str,
        *,
        score_version: str,
    ) -> ContentOpportunityScore | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM content_opportunity_scores
                WHERE opportunity_id = ? AND score_version = ?
                """,
                (opportunity_id, score_version),
            ).fetchone()
        if row is None:
            return None
        return ContentOpportunityScore(
            opportunity_id=row["opportunity_id"],
            score_version=row["score_version"],
            score=row["score"],
            dimensions=json.loads(row["dimensions_json"]),
            evidence_coverage=row["evidence_coverage"],
            confidence=row["confidence"],
            explanation=tuple(json.loads(row["explanation_json"])),
            input_snapshot_sha256=row["input_snapshot_sha256"],
            scored_at=datetime.fromisoformat(row["scored_at"]),
        )

    @staticmethod
    def _opportunity(row: sqlite3.Row) -> ContentOpportunity:
        return ContentOpportunity.model_validate(
            {
                **dict(row),
                "discovered_at": datetime.fromisoformat(row["discovered_at"]),
                "status": row["status"],
                "research_status": row["research_status"],
                "created_at": datetime.fromisoformat(row["created_at"]),
                "updated_at": datetime.fromisoformat(row["updated_at"]),
            }
        )

    @staticmethod
    def _source(row: sqlite3.Row) -> ContentSourceRecord:
        payload = dict(row)
        for key in ("published_at", "retrieved_at"):
            payload[key] = None if row[key] is None else datetime.fromisoformat(row[key])
        return ContentSourceRecord.model_validate(payload)

    @staticmethod
    def _acquisition_request(
        row: sqlite3.Row,
    ) -> ContentAcquisitionRequest:
        payload = dict(row)
        payload["intended_use"] = AcquisitionIntendedUse(payload["intended_use"])
        payload["rights_status"] = AcquisitionRightsStatus(payload["rights_status"])
        payload["state"] = AcquisitionRequestState(payload["state"])
        payload["created_at"] = datetime.fromisoformat(payload["created_at"])
        payload["updated_at"] = datetime.fromisoformat(payload["updated_at"])
        return ContentAcquisitionRequest.model_validate(payload)

    @staticmethod
    def _acquisition_request_event(
        row: sqlite3.Row,
    ) -> ContentAcquisitionRequestEvent:
        return ContentAcquisitionRequestEvent(
            sequence=row["sequence"],
            request_id=row["request_id"],
            from_state=(
                None
                if row["from_state"] is None
                else AcquisitionRequestState(row["from_state"])
            ),
            to_state=AcquisitionRequestState(row["to_state"]),
            actor=row["actor"],
            reason=row["reason"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _feed(row: sqlite3.Row) -> ConfiguredFeed:
        payload = dict(row)
        payload["allow_http"] = bool(row["allow_http"])
        payload["enabled"] = bool(row["enabled"])
        payload["niches"] = tuple(json.loads(row["niches_json"]))
        payload["keywords"] = tuple(json.loads(row["keywords_json"]))
        payload.pop("niches_json", None)
        payload.pop("keywords_json", None)
        for key in (
            "last_successful_retrieval",
            "last_attempted_retrieval",
            "next_refresh_at",
        ):
            payload[key] = None if row[key] is None else datetime.fromisoformat(row[key])
        payload["health"] = row["health"]
        payload["last_ingestion_count"] = row["last_ingestion_count"]
        payload["last_duplicate_count"] = row["last_duplicate_count"]
        try:
            return ConfiguredFeed.model_validate(payload)
        except ValidationError:
            raise ValueError("Stored configured feed is invalid.") from None

    @staticmethod
    def _evidence(row: sqlite3.Row) -> ContentEvidenceRecord:
        payload = dict(row)
        for key in ("observed_at", "published_at"):
            payload[key] = None if row[key] is None else datetime.fromisoformat(row[key])
        payload["is_inference"] = bool(row["is_inference"])
        payload["contradictory"] = bool(row["contradictory"])
        return ContentEvidenceRecord.model_validate(payload)

    @staticmethod
    def _report(row: sqlite3.Row) -> ContentResearchReport:
        payload = json.loads(row["report_json"])
        return ContentResearchReport.model_validate(payload)

    def get_content_worker_status(self) -> dict[str, int]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT state, COUNT(*) AS count FROM content_workflow_tasks GROUP BY state"
            ).fetchall()
            expired = self._connection.execute(
                """
                SELECT COUNT(*) AS count FROM content_workflow_tasks
                WHERE state IN ('running', 'cancellation_requested')
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                """,
                (self._now(),),
            ).fetchone()["count"]
        counts = {row["state"]: row["count"] for row in rows}
        with self._lock:
            retrying = self._connection.execute(
                "SELECT COUNT(*) AS count FROM content_workflow_tasks "
                "WHERE state = 'pending' AND next_attempt_at IS NOT NULL"
            ).fetchone()["count"]
        return {
            "pending": counts.get("pending", 0),
            "running": counts.get("running", 0) + counts.get("cancellation_requested", 0),
            "failed": counts.get("failed", 0),
            "uncertain": counts.get("uncertain", 0),
            "expired_leases": expired,
            "retrying": retrying,
        }

    def get_content_workflow_task_history(self, task_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT e.sequence, e.task_id, e.state, e.worker_id, e.failure_class,
                    e.detail, e.created_at, t.workflow_id
                FROM content_workflow_task_events e
                JOIN content_workflow_tasks t ON t.task_id = e.task_id
                WHERE e.task_id = ? ORDER BY e.sequence
                """,
                (task_id,),
            ).fetchall()
        history: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                payload = json.loads(item["detail"]) if item["detail"] else {}
            except json.JSONDecodeError:
                payload = {}
            if isinstance(payload, dict) and "event_type" in payload:
                item.update(
                    {
                        "event_id": item["sequence"],
                        "event_type": payload.get("event_type"),
                        "from_state": payload.get("from_state"),
                        "to_state": payload.get("to_state", item["state"]),
                        "attempt": payload.get("attempt"),
                        "detail": payload.get("detail"),
                    }
                )
            else:
                item.update(
                    {
                        "event_id": item["sequence"],
                        "event_type": "legacy_state_change",
                        "from_state": None,
                        "to_state": item["state"],
                        "attempt": None,
                    }
                )
            history.append(item)
        return history

    def record_content_authorization_audit(
        self,
        *,
        context: AuthorizationContext,
        action: str,
        subject_id: str,
        decision: str,
        fingerprint: str,
        details: dict[str, Any],
    ) -> None:
        """Record a content authorization decision without credential material."""
        if type(context) is not AuthorizationContext:
            raise TypeError("Authorization audit requires an authorization context.")
        safe_details = {
            key: value
            for key, value in details.items()
            if "token" not in key.lower() and "credential" not in key.lower()
            and "secret" not in key.lower()
        }
        with self._transaction() as connection:
            self._insert_audit(
                connection,
                context,
                action,
                subject_id,
                decision,
                fingerprint,
                safe_details,
                self._now(),
            )

    def record_feed_operation_audit(
        self,
        *,
        operator_id: str,
        feed_id: str,
        action: str,
        decision: str,
        details: dict[str, Any],
    ) -> None:
        if not operator_id.strip() or not feed_id.strip() or not action.strip():
            raise ValueError("Feed operation audit identity and action are required.")
        now = self._now()
        fingerprint = hashlib.sha256(self._canonical(details).encode()).hexdigest()
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO content_audit_events (
                    event_uuid, principal, session_id, action, subject_id, decision,
                    request_fingerprint, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    operator_id,
                    "content-feed-smoke-test",
                    action,
                    feed_id,
                    decision,
                    fingerprint,
                    self._canonical(details),
                    now,
                ),
            )

    def list_content_workflow_reconciliations(
        self,
        task_id: str,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT reconciliation_id, task_id, operator_id, outcome,
                    safe_to_retry, external_operation_id, reason, created_at
                FROM content_workflow_reconciliations
                WHERE task_id = ? ORDER BY created_at
                """,
                (task_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def reconcile_content_workflow_task(
        self,
        task_id: str,
        *,
        operator_id: str,
        outcome: str,
        safe_to_retry: bool,
        reason: str,
        external_operation_id: str | None = None,
    ) -> ContentWorkflowTask:
        if not operator_id.strip() or not reason.strip():
            raise ValueError("Operator identity and reconciliation reason are required.")
        if outcome not in {"succeeded", "failed", "retry"}:
            raise ValueError("Unsupported reconciliation outcome.")
        if outcome == "retry" and not safe_to_retry:
            raise ValueError("Retry reconciliation requires safe_to_retry.")
        now = self._now()
        state = "pending" if outcome == "retry" else "completed" if outcome == "succeeded" else "failed"
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM content_workflow_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown content workflow task: {task_id}")
            if row["state"] not in {"uncertain", "needs_reconciliation"}:
                raise ValueError("Only unresolved tasks may be reconciled.")
            reconciliation_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO content_workflow_reconciliations (
                    reconciliation_id, task_id, operator_id, outcome,
                    safe_to_retry, external_operation_id, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reconciliation_id,
                    task_id,
                    operator_id,
                    outcome,
                    int(safe_to_retry),
                    external_operation_id,
                    reason,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE content_workflow_tasks
                SET state = ?, failure_class = NULL, error_code = NULL,
                    next_attempt_at = NULL, completed_at = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (state, None if state == "pending" else now, now, task_id),
            )
            self._append_workflow_task_event(
                connection,
                task_id=task_id,
                from_state=row["state"],
                to_state=state,
                event_type=f"reconciled_{outcome}",
                attempt=row["attempt"],
                worker_id=operator_id,
                detail=f"reconciled:{outcome}:{reason}",
                timestamp=now,
            )
            updated = connection.execute(
                "SELECT * FROM content_workflow_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return self._workflow_task(updated)

    @staticmethod
    def _workflow_record(row: sqlite3.Row) -> ContentWorkflowRecord:
        return ContentWorkflowRecord(
            workflow_id=row["workflow_id"],
            owner_id=row["owner_id"],
            title=row["title"],
            state=ContentWorkflowState(row["state"]),
            version=row["version"],
            idempotency_key=row["idempotency_key"],
            request_fingerprint=row["request_fingerprint"],
            execution_attempt=row["execution_attempt"],
            provider=row["provider"],
            metadata_json=row["metadata_json"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _workflow_task(row: sqlite3.Row) -> ContentWorkflowTask:
        return ContentWorkflowTask(
            task_id=row["task_id"],
            workflow_id=row["workflow_id"],
            idempotency_key=row["idempotency_key"],
            task_type=row["task_type"],
            state=row["state"],
            attempt=row["attempt"],
            provider=row["provider"],
            result_json=row["result_json"],
            error_code=row["error_code"],
            worker_id=row["worker_id"],
            lease_token=row["lease_token"],
            lease_expires_at=(
                None
                if row["lease_expires_at"] is None
                else datetime.fromisoformat(row["lease_expires_at"])
            ),
            started_at=(
                None if row["started_at"] is None else datetime.fromisoformat(row["started_at"])
            ),
            completed_at=(
                None
                if row["completed_at"] is None
                else datetime.fromisoformat(row["completed_at"])
            ),
            failure_class=row["failure_class"],
            next_attempt_at=(
                None
                if row["next_attempt_at"] is None
                else datetime.fromisoformat(row["next_attempt_at"])
            ),
            max_attempts=row["max_attempts"],
            external_side_effect=bool(row["external_side_effect"]),
            input_json=row["input_json"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _script_artifact(row: sqlite3.Row) -> ContentScriptArtifact:
        return ContentScriptArtifact(
            artifact_id=row["artifact_id"],
            workflow_id=row["workflow_id"],
            version=row["version"],
            content=row["content"],
            content_sha256=row["content_sha256"],
            provenance_json=row["provenance_json"],
            generation_metadata_json=row["generation_metadata_json"],
            state=row["state"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _production_job(row: sqlite3.Row) -> ContentProductionJob:
        return ContentProductionJob(
            job_id=row["job_id"],
            workflow_id=row["workflow_id"],
            script_artifact_id=row["script_artifact_id"],
            script_version=row["script_version"],
            script_sha256=row["script_sha256"],
            configuration_json=row["configuration_json"],
            state=row["state"],
            output_asset_id=row["output_asset_id"],
            output_sha256=row["output_sha256"],
            failure_class=row["failure_class"],
            failure_detail=row["failure_detail"],
            started_at=(
                None if row["started_at"] is None else datetime.fromisoformat(row["started_at"])
            ),
            completed_at=(
                None
                if row["completed_at"] is None
                else datetime.fromisoformat(row["completed_at"])
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _validation_report(row: sqlite3.Row) -> ContentValidationReport:
        return ContentValidationReport(
            report_id=row["report_id"],
            workflow_id=row["workflow_id"],
            artifact_type=row["artifact_type"],
            artifact_id=row["artifact_id"],
            artifact_version=row["artifact_version"],
            artifact_sha256=row["artifact_sha256"],
            rules_json=row["rules_json"],
            results_json=row["results_json"],
            failure_reasons_json=row["failure_reasons_json"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _approval_decision(row: sqlite3.Row) -> ContentApprovalDecision:
        return ContentApprovalDecision(
            decision_id=row["decision_id"],
            workflow_id=row["workflow_id"],
            artifact_type=row["artifact_type"],
            artifact_id=row["artifact_id"],
            artifact_version=row["artifact_version"],
            artifact_sha256=row["artifact_sha256"],
            decision=row["decision"],
            actor=row["actor"],
            comments=row["comments"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def get_content_research_report(self, report_id: str) -> ContentResearchReport | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM content_research_reports WHERE report_id = ?",
                (report_id,),
            ).fetchone()
        return None if row is None else self._report(row)

    @staticmethod
    def _create_versioned_payload(
        connection: sqlite3.Connection,
        *,
        table: str,
        identity_column: str,
        identity: str,
        idempotency_key: str,
        payload: str,
        created_at: str,
        additional_columns: tuple[str, ...] = (),
        additional_values: tuple[Any, ...] = (),
    ) -> None:
        existing = connection.execute(
            f"SELECT {identity_column}, payload_json FROM {table} "
            "WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if existing is not None:
            if existing[identity_column] != identity or existing["payload_json"] != payload:
                raise IdempotencyConflictError(
                    "Idempotency key was reused with different content."
                )
            return
        columns = (identity_column, "idempotency_key", *additional_columns, "payload_json", "created_at")
        values = (identity, idempotency_key, *additional_values, payload, created_at)
        placeholders = ", ".join("?" for _ in columns)
        connection.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )

    def create_content_performance_measurement(
        self,
        measurement: ContentPerformanceMeasurement,
    ) -> ContentPerformanceMeasurement:
        payload = self._canonical(measurement.model_dump(mode="json"))
        with self._transaction() as connection:
            self._create_versioned_payload(
                connection,
                table="content_performance_measurements",
                identity_column="measurement_id",
                identity=measurement.measurement_id,
                idempotency_key=measurement.idempotency_key,
                payload=payload,
                created_at=measurement.created_at.isoformat(),
                additional_columns=(
                    "content_package_id",
                    "media_artifact_id",
                    "reporting_start",
                    "reporting_end",
                ),
                additional_values=(
                    measurement.content_package_id,
                    measurement.media_artifact_id,
                    measurement.reporting_window.starts_at.isoformat(),
                    measurement.reporting_window.ends_at.isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT payload_json FROM content_performance_measurements "
                "WHERE measurement_id = ?",
                (measurement.measurement_id,),
            ).fetchone()
            if row is None or row["payload_json"] != payload:
                raise IdempotencyConflictError(
                    "Measurement identifier conflicts with stored content."
                )
        return ContentPerformanceMeasurement.model_validate_json(payload)

    def get_content_performance_measurement(
        self,
        measurement_id: str,
    ) -> ContentPerformanceMeasurement | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM content_performance_measurements "
                "WHERE measurement_id = ?",
                (measurement_id,),
            ).fetchone()
        return (
            None
            if row is None
            else ContentPerformanceMeasurement.model_validate_json(row["payload_json"])
        )

    def list_content_performance_measurements(
        self,
        *,
        content_package_id: str | None = None,
        limit: int = 500,
    ) -> tuple[ContentPerformanceMeasurement, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5000:
            raise ValueError("Performance measurement limit must be between 1 and 5000.")
        query = "SELECT payload_json FROM content_performance_measurements"
        parameters: tuple[Any, ...] = ()
        if content_package_id is not None:
            query += " WHERE content_package_id = ?"
            parameters = (content_package_id,)
        query += " ORDER BY reporting_end, measurement_id LIMIT ?"
        parameters += (limit,)
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return tuple(
            ContentPerformanceMeasurement.model_validate_json(row["payload_json"])
            for row in rows
        )

    def create_production_cost_report(
        self,
        report: ProductionCostReport,
    ) -> ProductionCostReport:
        payload = self._canonical(report.model_dump(mode="json"))
        with self._transaction() as connection:
            self._create_versioned_payload(
                connection,
                table="content_production_cost_reports",
                identity_column="report_id",
                identity=report.report_id,
                idempotency_key=report.idempotency_key,
                payload=payload,
                created_at=report.created_at.isoformat(),
                additional_columns=(
                    "content_package_id",
                    "media_artifact_id",
                    "reporting_start",
                    "reporting_end",
                ),
                additional_values=(
                    report.content_package_id,
                    report.media_artifact_id,
                    report.reporting_window.starts_at.isoformat(),
                    report.reporting_window.ends_at.isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT payload_json FROM content_production_cost_reports "
                "WHERE report_id = ?",
                (report.report_id,),
            ).fetchone()
            if row is None or row["payload_json"] != payload:
                raise IdempotencyConflictError(
                    "Cost report identifier conflicts with stored content."
                )
        return ProductionCostReport.model_validate_json(payload)

    def get_production_cost_report(
        self,
        report_id: str,
    ) -> ProductionCostReport | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM content_production_cost_reports "
                "WHERE report_id = ?",
                (report_id,),
            ).fetchone()
        return None if row is None else ProductionCostReport.model_validate_json(row["payload_json"])

    def list_production_cost_reports(
        self,
        *,
        content_package_id: str | None = None,
        limit: int = 500,
    ) -> tuple[ProductionCostReport, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5000:
            raise ValueError("Production cost report limit must be between 1 and 5000.")
        query = "SELECT payload_json FROM content_production_cost_reports"
        parameters: tuple[Any, ...] = ()
        if content_package_id is not None:
            query += " WHERE content_package_id = ?"
            parameters = (content_package_id,)
        query += " ORDER BY reporting_end, report_id LIMIT ?"
        parameters += (limit,)
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return tuple(
            ProductionCostReport.model_validate_json(row["payload_json"])
            for row in rows
        )

    def create_performance_analysis(
        self,
        analysis: PerformanceAnalysis,
        recommendations: tuple[ContentPerformanceRecommendation, ...] = (),
    ) -> PerformanceAnalysis:
        payload = self._canonical(analysis.model_dump(mode="json"))
        with self._transaction() as connection:
            self._create_versioned_payload(
                connection,
                table="content_performance_analyses",
                identity_column="analysis_id",
                identity=analysis.analysis_id,
                idempotency_key=analysis.idempotency_key,
                payload=payload,
                created_at=analysis.created_at.isoformat(),
            )
            for recommendation in recommendations:
                if recommendation.analysis_id != analysis.analysis_id:
                    raise ValueError("Recommendation must belong to its persisted analysis.")
                recommendation_payload = self._canonical(
                    recommendation.model_dump(mode="json")
                )
                self._create_versioned_payload(
                    connection,
                    table="content_performance_recommendations",
                    identity_column="recommendation_id",
                    identity=recommendation.recommendation_id,
                    idempotency_key=recommendation.idempotency_key,
                    payload=recommendation_payload,
                    created_at=recommendation.created_at.isoformat(),
                    additional_columns=("analysis_id",),
                    additional_values=(analysis.analysis_id,),
                )
                stored_recommendation = connection.execute(
                    "SELECT payload_json FROM content_performance_recommendations "
                    "WHERE recommendation_id = ?",
                    (recommendation.recommendation_id,),
                ).fetchone()
                if (
                    stored_recommendation is None
                    or stored_recommendation["payload_json"] != recommendation_payload
                ):
                    raise IdempotencyConflictError(
                        "Recommendation identifier conflicts with stored content."
                    )
            row = connection.execute(
                "SELECT payload_json FROM content_performance_analyses "
                "WHERE analysis_id = ?",
                (analysis.analysis_id,),
            ).fetchone()
            if row is None or row["payload_json"] != payload:
                raise IdempotencyConflictError(
                    "Analysis identifier conflicts with stored content."
                )
        return PerformanceAnalysis.model_validate_json(payload)

    def get_performance_analysis(self, analysis_id: str) -> PerformanceAnalysis | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM content_performance_analyses "
                "WHERE analysis_id = ?",
                (analysis_id,),
            ).fetchone()
        return None if row is None else PerformanceAnalysis.model_validate_json(row["payload_json"])

    def list_performance_analyses(
        self,
        *,
        limit: int = 500,
    ) -> tuple[PerformanceAnalysis, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5000:
            raise ValueError("Performance analysis limit must be between 1 and 5000.")
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload_json FROM content_performance_analyses "
                "ORDER BY created_at DESC, analysis_id LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(
            PerformanceAnalysis.model_validate_json(row["payload_json"])
            for row in rows
        )

    def create_performance_recommendation(
        self,
        recommendation: ContentPerformanceRecommendation,
    ) -> ContentPerformanceRecommendation:
        payload = self._canonical(recommendation.model_dump(mode="json"))
        with self._transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM content_performance_analyses WHERE analysis_id = ?",
                (recommendation.analysis_id,),
            ).fetchone() is None:
                raise ValueError("Recommendation requires a persisted analysis.")
            self._create_versioned_payload(
                connection,
                table="content_performance_recommendations",
                identity_column="recommendation_id",
                identity=recommendation.recommendation_id,
                idempotency_key=recommendation.idempotency_key,
                payload=payload,
                created_at=recommendation.created_at.isoformat(),
                additional_columns=("analysis_id",),
                additional_values=(recommendation.analysis_id,),
            )
            row = connection.execute(
                "SELECT payload_json FROM content_performance_recommendations "
                "WHERE recommendation_id = ?",
                (recommendation.recommendation_id,),
            ).fetchone()
            if row is None or row["payload_json"] != payload:
                raise IdempotencyConflictError(
                    "Recommendation identifier conflicts with stored content."
                )
        return ContentPerformanceRecommendation.model_validate_json(payload)

    def get_performance_recommendation(
        self,
        recommendation_id: str,
    ) -> ContentPerformanceRecommendation | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM content_performance_recommendations "
                "WHERE recommendation_id = ?",
                (recommendation_id,),
            ).fetchone()
        return None if row is None else ContentPerformanceRecommendation.model_validate_json(row["payload_json"])

    def list_performance_recommendations(
        self,
        *,
        analysis_id: str | None = None,
        limit: int = 500,
    ) -> tuple[ContentPerformanceRecommendation, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5000:
            raise ValueError("Recommendation limit must be between 1 and 5000.")
        query = "SELECT payload_json FROM content_performance_recommendations"
        parameters: tuple[Any, ...] = ()
        if analysis_id is not None:
            query += " WHERE analysis_id = ?"
            parameters = (analysis_id,)
        query += " ORDER BY created_at, recommendation_id LIMIT ?"
        parameters += (limit,)
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return tuple(
            ContentPerformanceRecommendation.model_validate_json(row["payload_json"])
            for row in rows
        )

    def create_human_insight_decision(
        self,
        decision: HumanInsightDecision,
    ) -> HumanInsightDecision:
        payload = self._canonical(decision.model_dump(mode="json"))
        with self._transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM content_performance_recommendations "
                "WHERE recommendation_id = ?",
                (decision.recommendation_id,),
            ).fetchone() is None:
                raise ValueError("Human review requires a persisted recommendation.")
            existing = connection.execute(
                "SELECT payload_json FROM content_performance_decisions "
                "WHERE recommendation_id = ?",
                (decision.recommendation_id,),
            ).fetchone()
            if existing is not None:
                if existing["payload_json"] != payload:
                    raise IdempotencyConflictError(
                        "A recommendation already has a different human decision."
                    )
                return HumanInsightDecision.model_validate_json(existing["payload_json"])
            connection.execute(
                """
                INSERT INTO content_performance_decisions (
                    decision_id, recommendation_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    decision.decision_id,
                    decision.recommendation_id,
                    payload,
                    decision.created_at.isoformat(),
                ),
            )
        return decision

    def get_human_insight_decision(
        self,
        recommendation_id: str,
    ) -> HumanInsightDecision | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM content_performance_decisions "
                "WHERE recommendation_id = ?",
                (recommendation_id,),
            ).fetchone()
        return None if row is None else HumanInsightDecision.model_validate_json(row["payload_json"])

    def create_performance_strategy_revision(
        self,
        revision: PerformanceStrategyRevision,
    ) -> PerformanceStrategyRevision:
        payload = self._canonical(revision.model_dump(mode="json"))
        with self._transaction() as connection:
            decision_row = connection.execute(
                "SELECT payload_json FROM content_performance_decisions "
                "WHERE decision_id = ? AND recommendation_id = ?",
                (revision.decision_id, revision.recommendation_id),
            ).fetchone()
            if decision_row is None:
                raise ValueError("Strategy revision requires an explicit human decision.")
            decision = HumanInsightDecision.model_validate_json(decision_row["payload_json"])
            if decision.decision != HumanInsightDecisionKind.APPROVED:
                raise PermissionError("Rejected insights cannot create strategy revisions.")
            self._create_versioned_payload(
                connection,
                table="content_performance_strategy_revisions",
                identity_column="revision_id",
                identity=revision.revision_id,
                idempotency_key=revision.idempotency_key,
                payload=payload,
                created_at=revision.created_at.isoformat(),
                additional_columns=(
                    "recommendation_id",
                    "decision_id",
                    "strategy_id",
                ),
                additional_values=(
                    revision.recommendation_id,
                    revision.decision_id,
                    revision.strategy_id,
                ),
            )
            row = connection.execute(
                "SELECT payload_json FROM content_performance_strategy_revisions "
                "WHERE revision_id = ?",
                (revision.revision_id,),
            ).fetchone()
            if row is None or row["payload_json"] != payload:
                raise IdempotencyConflictError(
                    "Strategy revision identifier conflicts with stored content."
                )
        return revision

    def get_performance_strategy_revision(
        self,
        revision_id: str,
    ) -> PerformanceStrategyRevision | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM content_performance_strategy_revisions "
                "WHERE revision_id = ?",
                (revision_id,),
            ).fetchone()
        return None if row is None else PerformanceStrategyRevision.model_validate_json(row["payload_json"])

    def get_performance_strategy_revision_by_idempotency(
        self,
        idempotency_key: str,
    ) -> PerformanceStrategyRevision | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM content_performance_strategy_revisions "
                "WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return None if row is None else PerformanceStrategyRevision.model_validate_json(row["payload_json"])

    def list_performance_strategy_revisions(
        self,
        *,
        limit: int = 500,
    ) -> tuple[PerformanceStrategyRevision, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5000:
            raise ValueError("Performance strategy revision limit must be between 1 and 5000.")
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload_json FROM content_performance_strategy_revisions "
                "ORDER BY created_at DESC, revision_id LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(
            PerformanceStrategyRevision.model_validate_json(row["payload_json"])
            for row in rows
        )

    def create_content_strategy(self, brief: ContentStrategyBrief) -> ContentStrategyBrief:
        payload = self._canonical(brief.model_dump(mode="json"))
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM content_strategy_briefs "
                "WHERE opportunity_id = ? AND strategy_version = ?",
                (brief.opportunity_id, brief.strategy_version),
            ).fetchone()
            if existing is not None:
                return ContentStrategyBrief.model_validate_json(existing["payload_json"])
            connection.execute(
                """
                INSERT INTO content_strategy_briefs
                    (strategy_id, opportunity_id, research_report_id,
                     strategy_version, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    brief.strategy_id,
                    brief.opportunity_id,
                    brief.research_report_id,
                    brief.strategy_version,
                    payload,
                    brief.created_at.isoformat(),
                ),
            )
        return brief

    def get_content_strategy(self, strategy_id: str) -> ContentStrategyBrief | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM content_strategy_briefs WHERE strategy_id = ?",
                (strategy_id,),
            ).fetchone()
        return None if row is None else ContentStrategyBrief.model_validate_json(row["payload_json"])

    def get_content_strategy_for_opportunity(
        self, opportunity_id: str, research_report_id: str
    ) -> ContentStrategyBrief | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM content_strategy_briefs "
                "WHERE opportunity_id = ? AND research_report_id = ? "
                "ORDER BY strategy_version DESC, created_at DESC LIMIT 1",
                (opportunity_id, research_report_id),
            ).fetchone()
        return None if row is None else ContentStrategyBrief.model_validate_json(row["payload_json"])

    def create_content_angles(
        self,
        angles: tuple[ContentAngle, ...],
    ) -> tuple[ContentAngle, ...]:
        with self._transaction() as connection:
            for angle in angles:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO content_strategy_angles
                        (angle_id, strategy_id, payload_json)
                    VALUES (?, ?, ?)
                    """,
                    (
                        angle.angle_id,
                        angle.strategy_id,
                        self._canonical(angle.model_dump(mode="json")),
                    ),
                )
        return angles

    def list_content_angles(self, strategy_id: str) -> tuple[ContentAngle, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload_json FROM content_strategy_angles "
                "WHERE strategy_id = ? ORDER BY angle_id",
                (strategy_id,),
            ).fetchall()
        return tuple(ContentAngle.model_validate_json(row["payload_json"]) for row in rows)

    def create_editorial_plan(self, plan: EditorialPlan) -> EditorialPlan:
        payload = self._canonical(plan.model_dump(mode="json"))
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM content_editorial_plans WHERE idempotency_key = ?",
                (plan.idempotency_key,),
            ).fetchone()
            if existing is not None:
                prior = EditorialPlan.model_validate_json(existing["payload_json"])
                if prior.model_dump(mode="json") != plan.model_dump(mode="json"):
                    raise ValueError("Editorial plan idempotency key conflicts.")
                return prior
            connection.execute(
                """
                INSERT INTO content_editorial_plans
                    (plan_id, idempotency_key, strategy_id, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    plan.plan_id,
                    plan.idempotency_key,
                    plan.strategy_id,
                    payload,
                    plan.created_at.isoformat(),
                ),
            )
        return plan

    def list_editorial_plans(self) -> tuple[EditorialPlan, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload_json FROM content_editorial_plans ORDER BY created_at, plan_id"
            ).fetchall()
        return tuple(EditorialPlan.model_validate_json(row["payload_json"]) for row in rows)

    def get_editorial_plan(self, plan_id: str) -> EditorialPlan | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM content_editorial_plans WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
        return None if row is None else EditorialPlan.model_validate_json(row["payload_json"])

    def get_editorial_plan_by_idempotency_key(self, idempotency_key: str) -> EditorialPlan | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM content_editorial_plans WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return None if row is None else EditorialPlan.model_validate_json(row["payload_json"])

    def create_script_claims(
        self,
        *,
        artifact_id: str,
        artifact_version: int,
        artifact_sha256: str,
        claims: tuple[FactualClaim, ...],
    ) -> tuple[FactualClaim, ...]:
        artifact = self.get_script_artifact(artifact_id)
        if (
            artifact is None
            or artifact.version != artifact_version
            or artifact.content_sha256 != artifact_sha256
        ):
            raise ValueError("Claims must bind to the exact script artifact version and hash.")
        with self._transaction() as connection:
            for claim in claims:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO content_script_claims
                        (claim_id, artifact_id, artifact_version, artifact_sha256, payload_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        claim.claim_id,
                        artifact_id,
                        artifact_version,
                        artifact_sha256,
                        self._canonical(claim.model_dump(mode="json")),
                    ),
                )
        return claims

    def list_script_claims(
        self,
        artifact_id: str,
        *,
        artifact_version: int,
        artifact_sha256: str,
    ) -> tuple[FactualClaim, ...]:
        artifact = self.get_script_artifact(artifact_id)
        if (
            artifact is None
            or artifact.version != artifact_version
            or artifact.content_sha256 != artifact_sha256
        ):
            raise ValueError("Claim retrieval requires the exact script artifact version and hash.")
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload_json FROM content_script_claims "
                "WHERE artifact_id = ? AND artifact_version = ? AND artifact_sha256 = ? "
                "ORDER BY claim_id",
                (artifact_id, artifact_version, artifact_sha256),
            ).fetchall()
        return tuple(FactualClaim.model_validate_json(row["payload_json"]) for row in rows)

    def create_content_metadata(self, metadata: ContentMetadata) -> ContentMetadata:
        if metadata.script_artifact_id is not None:
            artifact = self.get_script_artifact(metadata.script_artifact_id)
            if (
                artifact is None
                or artifact.version != metadata.script_artifact_version
                or artifact.content_sha256 != metadata.script_artifact_sha256
            ):
                raise ValueError("Metadata must bind to the exact script artifact version.")
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO content_metadata_records
                    (metadata_id, script_artifact_id, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    metadata.metadata_id,
                    metadata.script_artifact_id,
                    self._canonical(metadata.model_dump(mode="json")),
                    metadata.created_at.isoformat() if metadata.created_at else self._now(),
                ),
            )
        return metadata

    def get_content_metadata(self, metadata_id: str) -> ContentMetadata | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM content_metadata_records WHERE metadata_id = ?",
                (metadata_id,),
            ).fetchone()
        return None if row is None else ContentMetadata.model_validate_json(row["payload_json"])

    def get_content_metadata_for_artifact(
        self, artifact_id: str, artifact_version: int, artifact_sha256: str
    ) -> ContentMetadata | None:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload_json FROM content_metadata_records "
                "WHERE script_artifact_id = ? ORDER BY created_at DESC",
                (artifact_id,),
            ).fetchall()
        for row in rows:
            metadata = ContentMetadata.model_validate_json(row["payload_json"])
            if (
                metadata.script_artifact_version == artifact_version
                and metadata.script_artifact_sha256 == artifact_sha256
            ):
                return metadata
        return None

    def create_thumbnail_brief(self, brief: ThumbnailBrief) -> ThumbnailBrief:
        if brief.script_artifact_id is not None:
            artifact = self.get_script_artifact(brief.script_artifact_id)
            if (
                artifact is None
                or artifact.version != brief.script_artifact_version
                or artifact.content_sha256 != brief.script_artifact_sha256
            ):
                raise ValueError("Thumbnail brief must bind to the exact script artifact version.")
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO content_thumbnail_briefs
                    (thumbnail_id, script_artifact_id, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    brief.thumbnail_id,
                    brief.script_artifact_id,
                    self._canonical(brief.model_dump(mode="json")),
                    brief.created_at.isoformat() if brief.created_at else self._now(),
                ),
            )
        return brief

    def get_thumbnail_brief(self, thumbnail_id: str) -> ThumbnailBrief | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM content_thumbnail_briefs WHERE thumbnail_id = ?",
                (thumbnail_id,),
            ).fetchone()
        return None if row is None else ThumbnailBrief.model_validate_json(row["payload_json"])

    def get_thumbnail_for_artifact(
        self, artifact_id: str, artifact_version: int, artifact_sha256: str
    ) -> ThumbnailBrief | None:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload_json FROM content_thumbnail_briefs "
                "WHERE script_artifact_id = ? ORDER BY created_at DESC",
                (artifact_id,),
            ).fetchall()
        for row in rows:
            thumbnail = ThumbnailBrief.model_validate_json(row["payload_json"])
            if (
                thumbnail.script_artifact_version == artifact_version
                and thumbnail.script_artifact_sha256 == artifact_sha256
            ):
                return thumbnail
        return None

    def create_quality_evaluation(self, evaluation: QualityEvaluation) -> QualityEvaluation:
        if evaluation.script_artifact_id is not None:
            artifact = self.get_script_artifact(evaluation.script_artifact_id)
            if (
                artifact is None
                or artifact.version != evaluation.script_artifact_version
                or artifact.content_sha256 != evaluation.script_artifact_sha256
            ):
                raise ValueError("Quality evaluation must bind to the exact script artifact.")
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO content_quality_evaluations
                    (evaluation_id, script_artifact_id, script_artifact_version,
                     script_artifact_sha256, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    evaluation.evaluation_id,
                    evaluation.script_artifact_id,
                    evaluation.script_artifact_version,
                    evaluation.script_artifact_sha256,
                    self._canonical(evaluation.model_dump(mode="json")),
                    evaluation.created_at.isoformat() if evaluation.created_at else self._now(),
                ),
            )
        return evaluation

    def save_content_package(self, package: ContentPackageCheckpoint) -> ContentPackageCheckpoint:
        payload = self._canonical(package.model_dump(mode="json"))
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO content_package_checkpoints
                    (package_id, workflow_id, opportunity_id, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(package_id) DO UPDATE SET
                    payload_json = excluded.payload_json, updated_at = excluded.updated_at
                """,
                (
                    package.package_id,
                    package.workflow_id,
                    package.opportunity_id,
                    payload,
                    package.created_at.isoformat(),
                    package.updated_at.isoformat(),
                ),
            )
        return package

    def save_content_package_stage(self, stage: ContentPackageStage) -> ContentPackageStage:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO content_package_stages
                    (stage_id, package_id, payload_json)
                VALUES (?, ?, ?)
                """,
                (stage.stage_id, stage.package_id, self._canonical(stage.model_dump(mode="json"))),
            )
        return stage

    def list_content_package_stages(self, package_id: str) -> tuple[ContentPackageStage, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload_json FROM content_package_stages "
                "WHERE package_id = ? ORDER BY rowid",
                (package_id,),
            ).fetchall()
        return tuple(ContentPackageStage.model_validate_json(row["payload_json"]) for row in rows)

    def save_angle_selection(self, decision: AngleSelectionDecision) -> AngleSelectionDecision:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO content_angle_selections
                    (selection_id, package_id, selection_version, payload_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(package_id, selection_version) DO NOTHING
                """,
                (
                    decision.selection_id,
                    decision.package_id,
                    decision.selection_version,
                    self._canonical(decision.model_dump(mode="json")),
                ),
            )
        return decision

    def list_angle_selections(self, package_id: str) -> tuple[AngleSelectionDecision, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload_json FROM content_angle_selections "
                "WHERE package_id = ? ORDER BY selection_version",
                (package_id,),
            ).fetchall()
        return tuple(AngleSelectionDecision.model_validate_json(row["payload_json"]) for row in rows)

    def save_readiness_decision(
        self, decision: ProductionReadinessDecision
    ) -> ProductionReadinessDecision:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO content_readiness_decisions
                    (decision_id, package_id, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    decision.decision_id,
                    decision.package_id,
                    self._canonical(decision.model_dump(mode="json")),
                    decision.decided_at.isoformat(),
                ),
            )
        return decision

    def get_latest_readiness_decision(
        self, package_id: str
    ) -> ProductionReadinessDecision | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM content_readiness_decisions "
                "WHERE package_id = ? ORDER BY created_at DESC LIMIT 1",
                (package_id,),
            ).fetchone()
        return None if row is None else ProductionReadinessDecision.model_validate_json(row["payload_json"])

    def get_content_package(self, package_id: str) -> ContentPackageCheckpoint | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM content_package_checkpoints WHERE package_id = ?",
                (package_id,),
            ).fetchone()
        return None if row is None else ContentPackageCheckpoint.model_validate_json(row["payload_json"])

    def list_content_packages(self, *, limit: int = 100) -> tuple[ContentPackageCheckpoint, ...]:
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("Package limit must be positive.")
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload_json FROM content_package_checkpoints "
                "ORDER BY updated_at DESC LIMIT ?",
                (min(limit, 1000),),
            ).fetchall()
        return tuple(ContentPackageCheckpoint.model_validate_json(row["payload_json"]) for row in rows)

    def get_quality_evaluation(self, evaluation_id: str) -> QualityEvaluation | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM content_quality_evaluations WHERE evaluation_id = ?",
                (evaluation_id,),
            ).fetchone()
        return None if row is None else QualityEvaluation.model_validate_json(row["payload_json"])

    def get_quality_evaluation_for_artifact(
        self, artifact_id: str, artifact_version: int, artifact_sha256: str
    ) -> QualityEvaluation | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM content_quality_evaluations "
                "WHERE script_artifact_id = ? AND script_artifact_version = ? "
                "AND script_artifact_sha256 = ? ORDER BY created_at DESC LIMIT 1",
                (artifact_id, artifact_version, artifact_sha256),
            ).fetchone()
        return None if row is None else QualityEvaluation.model_validate_json(row["payload_json"])

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
            if self._writer_lease is not None:
                self._writer_lease.__exit__(None, None, None)
                self._writer_lease = None
