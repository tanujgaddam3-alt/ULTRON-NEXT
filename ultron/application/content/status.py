from __future__ import annotations

import sqlite3
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ultron.security.identity.authenticator import WebSocketAuthenticator

from .service import ContentRightsService
from .intelligence import FasterWhisperLocalEngine
from .intelligence_service import ContentIntelligenceService
from .script_preview import OriginalScriptPreviewService
from .processing import LocalTemporaryContentStore
from .clip_renderer import FFmpegLocalClipRenderer
from .rendering import ContentClipRenderingService
from .limits import MAX_SOURCE_BYTES
from .store import ContentStore


MAX_STATUS_LIMIT = 100


@dataclass(frozen=True, slots=True)
class ContentOwnerMapping:
    principal: str
    owner_id: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.principal, str)
            or not self.principal.strip()
            or not isinstance(self.owner_id, str)
            or not self.owner_id.strip()
            or self.principal.strip() != self.owner_id.strip()
        ):
            raise ValueError(
                "CRE owner identity must match the gateway principal required by "
                "the existing content authorization contract."
            )

    def owner_for(self, principal: str) -> str | None:
        if not isinstance(principal, str) or principal != self.principal:
            return None
        return self.owner_id


class ContentStatusService:
    """Read-only, owner-scoped status projection over the configured CRE store."""

    def __init__(
        self,
        *,
        content_rights: ContentRightsService,
        owner_mapping: ContentOwnerMapping,
        script_preview_configured: bool = False,
        transcription_configured: bool = False,
        source_rendering_configured: bool = False,
    ) -> None:
        if type(content_rights) is not ContentRightsService:
            raise TypeError("Content status requires ContentRightsService.")
        if type(owner_mapping) is not ContentOwnerMapping:
            raise TypeError("Content status requires an explicit owner mapping.")
        if not isinstance(script_preview_configured, bool):
            raise TypeError("Script preview configuration status must be boolean.")
        if not isinstance(transcription_configured, bool) or not isinstance(
            source_rendering_configured,
            bool,
        ):
            raise TypeError("CRE source workflow configuration flags must be boolean.")
        self.content_rights = content_rights
        self.owner_mapping = owner_mapping
        self.script_preview_configured = script_preview_configured
        self.transcription_configured = transcription_configured
        self.source_rendering_configured = source_rendering_configured

    def for_principal(self, principal: str, *, limit: int = 50) -> dict:
        owner_id = self.owner_mapping.owner_for(principal)
        if owner_id is None:
            raise PermissionError("The gateway principal has no CRE read permission.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_STATUS_LIMIT:
            raise ValueError("CRE status limit must be between 1 and 100.")

        snapshot = self.content_rights.store.get_operator_status(
            owner_id,
            limit=limit,
        )
        return {
            "status": "AVAILABLE",
            "generatedAt": datetime.now(UTC).isoformat(),
            "counts": snapshot["counts"],
            "jobs": snapshot["jobs"],
            "lifecycle": snapshot["lifecycle"],
            "events": snapshot["events"],
            "services": {
                "store": "AVAILABLE",
                "rights": "AVAILABLE",
                "processing": (
                    "SYNTHETIC_PROCESSOR_CONFIGURED"
                    if self.content_rights.processor is not None
                    else "NOT_CONFIGURED"
                ),
                "contentStorage": (
                    "OWNER_ONLY_LOCAL_STORAGE"
                    if self.content_rights.content_storage is not None
                    else "NOT_CONFIGURED"
                ),
                "rendering": (
                    "LOCAL_SOURCE_FOOTAGE_AND_SCRIPT_PREVIEW"
                    if self.source_rendering_configured
                    else "LOCAL_SCRIPT_PREVIEW_ONLY"
                    if self.script_preview_configured
                    else "NOT_CONFIGURED"
                ),
                "transcription": (
                    "LOCAL_MODEL_CONFIGURED"
                    if self.transcription_configured
                    else "NOT_CONFIGURED"
                ),
                "maxSourceVideoBytes": MAX_SOURCE_BYTES,
                "publishing": "MOCK_ONLY_NOT_CONFIGURED",
                "scriptPreview": (
                    "WIRED_LOCAL_NARRATED_PREVIEW"
                    if self.script_preview_configured
                    else "NOT_CONFIGURED"
                ),
            },
            "publishing": {
                "mode": "MOCK_ONLY",
                "serviceConfigured": False,
                "killSwitchEnabled": (
                    self.content_rights.store.is_publishing_kill_switch_enabled()
                ),
            },
            "analytics": {
                "available": False,
                "reason": "NOT_INSTRUMENTED",
            },
        }


@dataclass(slots=True)
class ContentRuntime:
    store: ContentStore
    rights: ContentRightsService
    status: ContentStatusService
    script_previews: OriginalScriptPreviewService
    content_storage: LocalTemporaryContentStore
    intelligence: ContentIntelligenceService | None
    clip_rendering: ContentClipRenderingService

    def close(self) -> None:
        self.store.close()


def default_content_database_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "ultron_cre.db"


def resolve_content_database_path(
    configured_path: str | Path | None = None,
) -> Path:
    if configured_path is None:
        configured_path = os.environ.get("ULTRON_CRE_DATABASE")
    if configured_path is None:
        candidate = default_content_database_path()
    else:
        if not isinstance(configured_path, (str, Path)) or not str(configured_path):
            raise RuntimeError(
                "Configured CRE database path must be a non-empty absolute path."
            )
        candidate = Path(configured_path)
        if not candidate.is_absolute():
            raise RuntimeError("Configured CRE database path must be absolute.")

    try:
        is_symlink = candidate.is_symlink()
    except (OSError, ValueError):
        raise RuntimeError(
            "Configured CRE database path does not exist or is invalid."
        ) from None
    if is_symlink:
        raise RuntimeError("Configured CRE database path must not be a symbolic link.")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise RuntimeError(
            "Configured CRE database path does not exist or is invalid."
        ) from None
    if not resolved.is_file():
        raise RuntimeError("Configured CRE database path is not a regular file.")
    return resolved


def verify_cre_runtime_database_read_only(path: str | Path) -> None:
    """Validate an existing CRE runtime database without constructing a store."""
    if not isinstance(path, (str, Path)):
        raise RuntimeError("CRE database path must be an absolute path.")
    candidate = Path(path)
    if not candidate.is_absolute():
        raise RuntimeError("CRE database path must be absolute.")
    if candidate.is_symlink():
        raise RuntimeError("CRE database path must not be a symbolic link.")
    if not candidate.exists():
        raise RuntimeError(
            "CRE database path does not exist; it must be explicitly initialized and migrated."
        )
    if not candidate.is_file():
        raise RuntimeError("CRE database path is not a regular file.")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise RuntimeError("CRE database path does not exist or is invalid.") from None

    try:
        connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only = ON")
            ContentStore.verify_runtime_schema_metadata(connection)
            ContentStore._verify_publishing_attempt_schema(connection)
            ContentStore._verify_private_render_operation_schema(connection)
            user_version = connection.execute("PRAGMA user_version").fetchone()[0]
            if type(user_version) is not int or user_version != 0:
                raise RuntimeError(
                    "CRE SQLite user_version must be 0; "
                    "CRE schema_version is stored in content_schema_meta."
                )
            integrity = connection.execute("PRAGMA integrity_check").fetchall()
            if len(integrity) != 1 or integrity[0][0] != "ok":
                raise RuntimeError("CRE database failed SQLite integrity_check.")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise RuntimeError("CRE database contains foreign-key violations.")
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise RuntimeError(
            "Existing CRE database is not a recognized content database."
        ) from error


def _verify_existing_database(path: Path) -> None:
    verify_cre_runtime_database_read_only(path)


def create_content_runtime(
    authenticator: WebSocketAuthenticator,
    database_path: Path | None = None,
) -> ContentRuntime:
    if type(authenticator) is not WebSocketAuthenticator:
        raise TypeError("CRE runtime requires the configured gateway authenticator.")

    path = (
        resolve_content_database_path()
        if database_path is None
        else database_path
    )
    if not isinstance(path, Path) or not path.is_absolute():
        raise RuntimeError("Configured CRE database path must be absolute.")
    _verify_existing_database(path)

    store = ContentStore(path)
    try:
        staging_directory = path.parent / "ultron_cre_script_staging"
        staging_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if staging_directory.is_symlink() or not staging_directory.is_dir():
            raise RuntimeError(
                "CRE script preview staging path must be a real directory."
            )
        os.chmod(staging_directory, 0o700)
        rights = ContentRightsService(
            store=store,
            authority=authenticator,
            reviewer_principals=frozenset(authenticator.additional_principals),
            content_storage=LocalTemporaryContentStore(
                path.parent / "ultron_cre_content"
            ),
        )
        mapping = ContentOwnerMapping(
            principal=authenticator.principal,
            owner_id=authenticator.principal,
        )
        script_previews = OriginalScriptPreviewService(
            store=store,
            staging_directory=staging_directory.resolve(strict=True),
        )
        content_storage = rights.content_storage
        if type(content_storage) is not LocalTemporaryContentStore:
            raise RuntimeError("CRE managed content storage was not initialized.")
        media_renderer = FFmpegLocalClipRenderer(
            managed_staging_directory=content_storage.clip_staging_directory,
        )
        model_directory = os.environ.get("ULTRON_CRE_FASTER_WHISPER_MODEL")
        intelligence = None
        if model_directory:
            intelligence = ContentIntelligenceService(
                content_rights=rights,
                transcription_engine=FasterWhisperLocalEngine(model_directory),
                media_renderer=media_renderer,
            )
        clip_rendering = ContentClipRenderingService(
            content_rights=rights,
            renderer=media_renderer,
        )
        status = ContentStatusService(
            content_rights=rights,
            owner_mapping=mapping,
            script_preview_configured=True,
            transcription_configured=intelligence is not None,
            source_rendering_configured=True,
        )
    except Exception:
        store.close()
        raise

    return ContentRuntime(
        store=store,
        rights=rights,
        status=status,
        script_previews=script_previews,
        content_storage=content_storage,
        intelligence=intelligence,
        clip_rendering=clip_rendering,
    )
