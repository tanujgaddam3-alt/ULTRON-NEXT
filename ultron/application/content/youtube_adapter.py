from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Callable, Protocol
from urllib.parse import parse_qs, urlencode, urlparse

from pydantic import SecretStr

from ultron.domain.content import (
    ContentPlatform,
    OAuthCredentialBinding,
    OpaqueSecretReference,
    PublishingAttemptIdentity,
    PublishingJob,
    PublishingProvider,
    SecretGeneration,
    UploadSecretBinding,
    UploadSecretPurpose,
    UploadSessionSecretHandle,
)

from .publishing import PublishingAdapter


class YouTubeUploadState(StrEnum):
    SESSION_CREATING = "session_creating"
    UPLOADING = "uploading"
    RECONCILING = "reconciling"
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    PUBLISHED = "published"
    AVAILABLE_PRIVATE = "available_private"
    FAILED = "failed"
    BLOCKED = "blocked"
    UNCERTAIN = "uncertain"
    SESSION_EXPIRED = "session_expired"


class YouTubeAdapterError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"YouTube upload operation failed ({code}).")


class YouTubeTransportError(RuntimeError):
    """A transport failure normalized without credential/session details."""


class SecureUploadStorageUnavailable(RuntimeError):
    """No secure persistence backend is configured."""


class SecureSecretStoreErrorCode(StrEnum):
    UNAVAILABLE = "secure_store_unavailable"
    LOCKED = "secure_store_locked"
    ACCESS_DENIED = "secure_store_access_denied"
    NOT_FOUND = "secure_secret_not_found"
    BINDING_MISMATCH = "secure_secret_binding_mismatch"
    SCHEMA_UNSUPPORTED = "secure_secret_schema_unsupported"
    GENERATION_MISMATCH = "secure_secret_generation_mismatch"
    OPERATION_FAILED = "secure_secret_operation_failed"


class SecureSecretStoreError(RuntimeError):
    def __init__(self, code: SecureSecretStoreErrorCode) -> None:
        if not isinstance(code, SecureSecretStoreErrorCode):
            raise TypeError("Secure-store errors require a safe error code.")
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class OAuthCredentialMaterial:
    access_token: SecretStr
    refresh_token: SecretStr
    expires_at: datetime
    scopes: frozenset[str]
    generation: SecretGeneration

    def __post_init__(self) -> None:
        if (
            not isinstance(self.access_token, SecretStr)
            or not self.access_token.get_secret_value()
            or not isinstance(self.refresh_token, SecretStr)
            or not self.refresh_token.get_secret_value()
        ):
            raise ValueError("OAuth credential material must contain protected tokens.")
        if (
            self.expires_at.tzinfo is None
            or self.expires_at.utcoffset() is None
            or not isinstance(self.scopes, frozenset)
            or not self.scopes
            or any(not isinstance(scope, str) or not scope.strip() for scope in self.scopes)
            or type(self.generation) is not SecretGeneration
        ):
            raise ValueError("OAuth credential metadata is invalid.")


@dataclass(frozen=True)
class YouTubeTransportResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes = b""

    def header(self, name: str) -> str | None:
        target = name.casefold()
        return next(
            (value for key, value in self.headers.items() if key.casefold() == target),
            None,
        )

    def json_object(self) -> dict[str, object]:
        if len(self.body) > YouTubeAdapter.MAX_RESPONSE_BYTES:
            raise YouTubeAdapterError("response_too_large")
        try:
            value = json.loads(self.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise YouTubeAdapterError("malformed_response") from exc
        if not isinstance(value, dict):
            raise YouTubeAdapterError("malformed_response")
        return value


@dataclass(frozen=True)
class YouTubeUploadProgress:
    state: YouTubeUploadState
    byte_offset: int
    total_bytes: int
    video_id: str | None = None


class YouTubeTransport(Protocol):
    """Transport implementations must normalize client errors to safe errors."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> YouTubeTransportResponse: ...


class YouTubeCredentialProvider(Protocol):
    def access_token(self, account_id: str) -> SecretStr: ...


class OAuthCredentialStore(Protocol):
    """Stores provider credentials in a protected secret backend only.

    The caller must derive the binding from trusted CRE records. A binding or
    credential ID is not, by itself, an authorization grant.
    """

    def create(
        self,
        binding: OAuthCredentialBinding,
        credentials: OAuthCredentialMaterial,
    ) -> None: ...

    def retrieve(
        self,
        binding: OAuthCredentialBinding,
    ) -> OAuthCredentialMaterial: ...

    def replace(
        self,
        binding: OAuthCredentialBinding,
        *,
        expected_generation: SecretGeneration,
        credentials: OAuthCredentialMaterial,
    ) -> SecretGeneration: ...

    def delete(
        self,
        binding: OAuthCredentialBinding,
        *,
        expected_generation: SecretGeneration,
    ) -> None: ...


class SecureUploadProgressStore(Protocol):
    """Implementations protect session URIs; a reference is not authorization.

    The caller supplies a binding derived from trusted publishing-attempt
    records, and the backend must compare it with the stored item attributes.
    """

    def load(self, job_id: str) -> YouTubeUploadProgress | None: ...

    def save(self, job_id: str, progress: YouTubeUploadProgress) -> None: ...

    def retrieve_session_uri(
        self,
        handle: UploadSessionSecretHandle,
    ) -> SecretStr | None: ...

    def store_session_uri(
        self,
        handle: UploadSessionSecretHandle,
        session_uri: SecretStr,
    ) -> None: ...

    def replace_session_uri(
        self,
        handle: UploadSessionSecretHandle,
        session_uri: SecretStr,
    ) -> SecretGeneration: ...

    def invalidate_session_uri(self, handle: UploadSessionSecretHandle) -> None: ...

    def delete_session_uri(self, handle: UploadSessionSecretHandle) -> None: ...


class UnavailableSecureUploadProgressStore:
    """Fail-closed backend until protected durable secret storage is configured."""

    @staticmethod
    def _unavailable() -> None:
        raise SecureUploadStorageUnavailable("secure_upload_storage_unavailable")

    def load(self, job_id: str) -> YouTubeUploadProgress | None:
        del job_id
        self._unavailable()

    def save(self, job_id: str, progress: YouTubeUploadProgress) -> None:
        del job_id, progress
        self._unavailable()

    def retrieve_session_uri(self, handle: UploadSessionSecretHandle) -> SecretStr | None:
        del handle
        self._unavailable()

    def store_session_uri(
        self,
        handle: UploadSessionSecretHandle,
        session_uri: SecretStr,
    ) -> None:
        del handle, session_uri
        self._unavailable()

    def replace_session_uri(
        self,
        handle: UploadSessionSecretHandle,
        session_uri: SecretStr,
    ) -> SecretGeneration:
        del handle, session_uri
        self._unavailable()

    def invalidate_session_uri(self, handle: UploadSessionSecretHandle) -> None:
        del handle
        self._unavailable()

    def delete_session_uri(self, handle: UploadSessionSecretHandle) -> None:
        del handle
        self._unavailable()


class YouTubeAdapter(PublishingAdapter):
    """Offline-testable adapter boundary; no network transport is provided."""

    INIT_URL = (
        "https://www.googleapis.com/upload/youtube/v3/videos"
        "?uploadType=resumable&part=snippet,status"
    )
    VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
    CHUNK_BYTES = 256 * 1024
    MAX_RETRIES = 3
    MAX_TOTAL_RETRIES = 12
    MAX_BACKOFF_SECONDS = 300
    RETRY_DEADLINE_SECONDS = 900
    REQUEST_TIMEOUT_SECONDS = 60
    MAX_RESPONSE_BYTES = 1024 * 1024
    MAX_UPLOAD_BYTES = 16 * 1024 * 1024
    RETRYABLE_STATUS = frozenset({500, 502, 503, 504})

    def __init__(
        self,
        *,
        transport: YouTubeTransport,
        credentials: YouTubeCredentialProvider,
        progress_store: SecureUploadProgressStore | None,
        session_handle_provider: Callable[[PublishingJob], UploadSessionSecretHandle],
        authorization_guard: Callable[[PublishingJob], None],
        output_provider: Callable[[PublishingJob], bytes],
        metadata_provider: Callable[[PublishingJob], dict[str, object]],
        backoff: Callable[[int, str | None], None] | None = None,
    ) -> None:
        if progress_store is None:
            raise YouTubeAdapterError("secure_session_store_required")
        self.transport = transport
        self.credentials = credentials
        self.progress_store = progress_store
        self.session_handle_provider = session_handle_provider
        self.authorization_guard = authorization_guard
        self.output_provider = output_provider
        self.metadata_provider = metadata_provider
        self.backoff = backoff or self._sleep_backoff

    @staticmethod
    def _sleep_backoff(_attempt: int, delay: str) -> None:
        del _attempt
        time.sleep(min(max(float(delay), 0.0), YouTubeAdapter.MAX_BACKOFF_SECONDS))

    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    @staticmethod
    def _safe_response_error(response: YouTubeTransportResponse, code: str) -> None:
        del response
        raise YouTubeAdapterError(code)

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes,
    ) -> YouTubeTransportResponse:
        try:
            response = self.transport.request(
                method,
                url,
                headers=headers,
                body=body,
                timeout_seconds=self.REQUEST_TIMEOUT_SECONDS,
            )
            if (
                not isinstance(response, YouTubeTransportResponse)
                or isinstance(response.status_code, bool)
                or not isinstance(response.status_code, int)
                or response.status_code < 100
                or response.status_code > 599
                or not isinstance(response.headers, dict)
                or any(
                    not isinstance(key, str) or not isinstance(value, str)
                    for key, value in response.headers.items()
                )
                or not isinstance(response.body, bytes)
                or len(response.body) > self.MAX_RESPONSE_BYTES
            ):
                raise YouTubeTransportError("transport_response_invalid")
            return response
        except YouTubeTransportError:
            raise
        except Exception:
            raise YouTubeTransportError("transport_request_failed") from None

    @staticmethod
    def _validate_session_uri(uri: str) -> None:
        try:
            parsed = urlparse(uri)
            query = parse_qs(parsed.query, keep_blank_values=True)
            port = parsed.port
        except ValueError:
            raise YouTubeAdapterError("invalid_upload_session") from None
        if (
            parsed.scheme != "https"
            or parsed.hostname != "www.googleapis.com"
            or port not in (None, 443)
            or parsed.path != "/upload/youtube/v3/videos"
            or not query.get("upload_id")
            or not query["upload_id"][0]
            or len(uri) > 8192
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise YouTubeAdapterError("invalid_upload_session")

    @staticmethod
    def _metadata(job: PublishingJob, metadata: dict[str, object]) -> dict[str, object]:
        title = metadata.get("title")
        description = metadata.get("description", "")
        tags = metadata.get("tags", [])
        category_id = metadata.get("categoryId")
        if (
            not isinstance(title, str)
            or not title.strip()
            or len(title) > 100
            or not isinstance(description, str)
            or len(description) > 5000
            or not isinstance(tags, list)
            or len(tags) > 500
            or any(not isinstance(tag, str) or len(tag) > 500 for tag in tags)
            or (
                category_id is not None
                and (
                    not isinstance(category_id, str)
                    or not re.fullmatch(r"[0-9]{1,3}", category_id)
                )
            )
        ):
            raise YouTubeAdapterError("invalid_video_metadata")

        snippet: dict[str, object] = {
            "title": title,
            "description": description,
            "tags": tags,
        }
        if category_id is not None:
            snippet["categoryId"] = category_id
        return {
            "snippet": snippet,
            "status": {
                "privacyStatus": job.visibility.value,
                "containsSyntheticMedia": job.synthetic_media_disclosed,
            },
        }

    def _headers(self, job: PublishingJob, extra: dict[str, str] | None = None) -> dict[str, str]:
        try:
            token = self.credentials.access_token(job.account_id)
        except Exception:
            raise YouTubeAdapterError("credential_unavailable") from None
        if not isinstance(token, SecretStr) or not token.get_secret_value():
            raise YouTubeAdapterError("credential_unavailable")
        headers = {
            "Authorization": f"Bearer {token.get_secret_value()}",
            "Accept": "application/json",
        }
        if extra:
            headers.update(extra)
        headers["Authorization"] = "Bearer " + token.get_secret_value()
        return headers

    def _progress(
        self,
        job: PublishingJob,
        state: YouTubeUploadState,
        offset: int,
        total: int,
        video_id: str | None = None,
    ) -> YouTubeUploadProgress:
        if (
            total <= 0
            or offset < 0
            or offset > total
            or (
                video_id is not None
                and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", video_id) is None
            )
            or (
                state
                in {
                    YouTubeUploadState.UPLOADED,
                    YouTubeUploadState.PROCESSING,
                    YouTubeUploadState.PUBLISHED,
                    YouTubeUploadState.AVAILABLE_PRIVATE,
                }
                and video_id is None
            )
        ):
            raise YouTubeAdapterError("invalid_upload_progress")
        progress = YouTubeUploadProgress(state, offset, total, video_id)
        try:
            self.progress_store.save(job.job_id, progress)
        except Exception:
            raise YouTubeAdapterError("progress_persistence_failed") from None
        return progress

    def _load_progress(self, job: PublishingJob) -> YouTubeUploadProgress | None:
        try:
            return self.progress_store.load(job.job_id)
        except Exception:
            raise YouTubeAdapterError("progress_persistence_unavailable") from None

    def _session_handle(self, job: PublishingJob) -> UploadSessionSecretHandle:
        try:
            handle = self.session_handle_provider(job)
        except Exception:
            raise YouTubeAdapterError("session_binding_unavailable") from None
        if (
            type(handle) is not UploadSessionSecretHandle
            or type(handle.binding.attempt) is not PublishingAttemptIdentity
        ):
            raise YouTubeAdapterError("session_binding_invalid")
        identity = handle.binding.attempt
        if (
            identity.owner_id != job.owner_id
            or identity.job_id != job.job_id
            or identity.account_id != job.account_id
            or identity.provider != PublishingProvider.YOUTUBE
            or identity.release_identity != job.request_fingerprint
            or identity.package_id != job.package_id
            or identity.package_version != job.package_version
            or identity.output_sha256 != job.output_sha256
            or handle.binding.purpose != UploadSecretPurpose.RESUMABLE_UPLOAD_SESSION
            or handle.binding.schema_version != handle.reference.schema_version
        ):
            raise YouTubeAdapterError("session_binding_mismatch")
        return handle

    def _retrieve_session_uri(
        self,
        handle: UploadSessionSecretHandle,
    ) -> SecretStr | None:
        try:
            return self.progress_store.retrieve_session_uri(handle)
        except Exception:
            raise YouTubeAdapterError("secure_session_unavailable") from None

    def _store_session_uri(
        self,
        handle: UploadSessionSecretHandle,
        session_uri: SecretStr,
    ) -> None:
        try:
            self.progress_store.store_session_uri(handle, session_uri)
        except Exception:
            raise YouTubeAdapterError("secure_session_persistence_failed") from None

    def _replace_session_uri(
        self,
        handle: UploadSessionSecretHandle,
        session_uri: SecretStr,
    ) -> None:
        try:
            self.progress_store.replace_session_uri(
                handle,
                session_uri,
            )
        except Exception:
            raise YouTubeAdapterError("secure_session_persistence_failed") from None

    def _invalidate_session_uri(self, handle: UploadSessionSecretHandle) -> None:
        try:
            self.progress_store.invalidate_session_uri(handle)
        except Exception:
            raise YouTubeAdapterError("secure_session_invalidation_failed") from None

    def _delete_session_uri(self, handle: UploadSessionSecretHandle) -> None:
        try:
            self.progress_store.delete_session_uri(handle)
        except Exception:
            raise YouTubeAdapterError("secure_session_cleanup_failed") from None

    def _mark_session_expired(
        self,
        job: PublishingJob,
        handle: UploadSessionSecretHandle,
        offset: int,
        total: int,
    ) -> None:
        self._progress(job, YouTubeUploadState.SESSION_EXPIRED, offset, total)
        self._invalidate_session_uri(handle)
        self._delete_session_uri(handle)

    @staticmethod
    def _validate_progress_record(
        progress: YouTubeUploadProgress,
        total: int,
    ) -> None:
        if (
            not isinstance(progress, YouTubeUploadProgress)
            or not isinstance(progress.state, YouTubeUploadState)
            or isinstance(total, bool)
            or not isinstance(total, int)
            or total <= 0
            or isinstance(progress.total_bytes, bool)
            or not isinstance(progress.total_bytes, int)
            or progress.total_bytes <= 0
            or progress.total_bytes != total
            or isinstance(progress.byte_offset, bool)
            or not isinstance(progress.byte_offset, int)
            or progress.byte_offset < 0
            or progress.byte_offset > total
            or (
                progress.state
                in {
                    YouTubeUploadState.UPLOADED,
                    YouTubeUploadState.PROCESSING,
                    YouTubeUploadState.PUBLISHED,
                    YouTubeUploadState.AVAILABLE_PRIVATE,
                }
                and (
                    progress.byte_offset != total
                    or progress.video_id is None
                )
            )
            or (
                progress.video_id is not None
                and (
                    not isinstance(progress.video_id, str)
                    or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", progress.video_id)
                    is None
                )
            )
        ):
            raise YouTubeAdapterError("corrupt_upload_progress")

    def _authorize(self, job: PublishingJob) -> None:
        try:
            self.authorization_guard(job)
        except Exception:
            raise YouTubeAdapterError("authorization_denied") from None

    def _new_session(
        self,
        job: PublishingJob,
        handle: UploadSessionSecretHandle,
        metadata: dict[str, object],
        total_bytes: int,
    ) -> str:
        self._progress(job, YouTubeUploadState.SESSION_CREATING, 0, total_bytes)
        try:
            self._authorize(job)
        except YouTubeAdapterError:
            self._progress(
                job,
                YouTubeUploadState.BLOCKED,
                0,
                total_bytes,
            )
            raise
        try:
            headers = self._headers(
                job,
                {
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Length": str(total_bytes),
                    "X-Upload-Content-Type": "video/*",
                },
            )
        except YouTubeAdapterError:
            self._progress(
                job,
                YouTubeUploadState.FAILED,
                0,
                total_bytes,
            )
            raise
        try:
            self._authorize(job)
        except YouTubeAdapterError:
            self._progress(
                job,
                YouTubeUploadState.BLOCKED,
                0,
                total_bytes,
            )
            raise
        try:
            response = self._request(
                "POST",
                self.INIT_URL,
                headers=headers,
                body=self._canonical(metadata),
            )
        except YouTubeAdapterError as exc:
            if exc.code == "credential_unavailable":
                self._progress(job, YouTubeUploadState.FAILED, 0, total_bytes)
            else:
                self._progress(job, YouTubeUploadState.UNCERTAIN, 0, total_bytes)
            raise
        except (YouTubeTransportError, TimeoutError, ConnectionError):
            self._progress(job, YouTubeUploadState.UNCERTAIN, 0, total_bytes)
            raise YouTubeAdapterError("session_creation_uncertain") from None
        if response.status_code != 200:
            if response.status_code in self.RETRYABLE_STATUS:
                self._progress(
                    job,
                    YouTubeUploadState.UNCERTAIN,
                    0,
                    total_bytes,
                )
                raise YouTubeAdapterError("session_creation_uncertain")
            self._progress(job, YouTubeUploadState.FAILED, 0, total_bytes)
            self._safe_response_error(response, "session_creation_rejected")
        session_uri = response.header("Location")
        if not session_uri:
            self._progress(job, YouTubeUploadState.UNCERTAIN, 0, total_bytes)
            raise YouTubeAdapterError("session_creation_uncertain")
        try:
            self._validate_session_uri(session_uri)
        except YouTubeAdapterError:
            self._progress(job, YouTubeUploadState.UNCERTAIN, 0, total_bytes)
            raise
        self._progress(job, YouTubeUploadState.SESSION_CREATING, 0, total_bytes)
        try:
            self._store_session_uri(handle, SecretStr(session_uri))
        except YouTubeAdapterError:
            try:
                self._progress(job, YouTubeUploadState.UNCERTAIN, 0, total_bytes)
            except YouTubeAdapterError:
                pass
            raise
        self._progress(job, YouTubeUploadState.UPLOADING, 0, total_bytes)
        return session_uri

    @staticmethod
    def _offset_from_range(value: str | None, total: int) -> int:
        if value is None:
            return 0
        match = re.fullmatch(r"bytes=0-([0-9]+)", value.strip())
        if match is None:
            raise YouTubeAdapterError("malformed_upload_range")
        offset = int(match.group(1)) + 1
        if offset <= 0 or offset > total:
            raise YouTubeAdapterError("malformed_upload_range")
        return offset

    @classmethod
    def _retry_delay(cls, value: str | None, attempt: int) -> str:
        fallback = min(2 ** (attempt - 1), cls.MAX_BACKOFF_SECONDS)
        if value is None:
            return str(fallback)
        stripped = value.strip()
        if stripped.isdigit():
            try:
                return str(min(int(stripped), cls.MAX_BACKOFF_SECONDS))
            except ValueError:
                return str(fallback)
        try:
            retry_at = parsedate_to_datetime(stripped)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            remaining = max(
                0,
                int((retry_at - datetime.now(UTC)).total_seconds()),
            )
            return str(min(remaining, cls.MAX_BACKOFF_SECONDS))
        except (TypeError, ValueError, OverflowError):
            return str(fallback)

    def _completed(
        self,
        job: PublishingJob,
        handle: UploadSessionSecretHandle,
        response: YouTubeTransportResponse,
        total: int,
    ) -> None:
        if response.status_code not in {200, 201}:
            raise YouTubeAdapterError("upload_not_complete")
        try:
            payload = response.json_object()
        except YouTubeAdapterError:
            self._progress(job, YouTubeUploadState.UNCERTAIN, total, total)
            raise YouTubeAdapterError("completion_response_invalid") from None
        video_id = payload.get("id")
        if (
            not isinstance(video_id, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", video_id) is None
        ):
            self._progress(job, YouTubeUploadState.UNCERTAIN, total, total)
            raise YouTubeAdapterError("completion_response_invalid")
        self._progress(
            job,
            YouTubeUploadState.UPLOADED,
            total,
            total,
            video_id,
        )
        self._invalidate_session_uri(handle)
        self._delete_session_uri(handle)

    def _probe(
        self,
        job: PublishingJob,
        handle: UploadSessionSecretHandle,
        session_uri: str,
        total: int,
        offset: int,
    ):
        self._authorize(job)
        self._progress(job, YouTubeUploadState.RECONCILING, offset, total)
        try:
            return self._request(
                "PUT",
                session_uri,
                headers=self._headers(
                    job,
                    {
                        "Content-Length": "0",
                        "Content-Range": f"bytes */{total}",
                    },
                ),
                body=b"",
            )
        except (YouTubeTransportError, TimeoutError, ConnectionError):
            self._progress(job, YouTubeUploadState.UNCERTAIN, offset, total)
            raise YouTubeAdapterError("status_probe_uncertain") from None

    def dispatch(self, job: PublishingJob) -> None:
        if job.platform != ContentPlatform.YOUTUBE:
            raise YouTubeAdapterError("unsupported_platform")
        if job.state.value != "queued":
            raise YouTubeAdapterError("job_not_queued")
        handle = self._session_handle(job)
        self._authorize(job)
        try:
            metadata = self.metadata_provider(job)
        except Exception:
            raise YouTubeAdapterError("metadata_unavailable") from None
        if not isinstance(metadata, dict):
            raise YouTubeAdapterError("invalid_video_metadata")
        if hashlib.sha256(self._canonical(metadata)).hexdigest() != job.metadata_sha256:
            raise YouTubeAdapterError("metadata_binding_mismatch")
        request_metadata = self._metadata(job, metadata)
        try:
            content = self.output_provider(job)
        except Exception:
            raise YouTubeAdapterError("output_unavailable") from None
        if (
            not isinstance(content, bytes)
            or not content
            or len(content) > self.MAX_UPLOAD_BYTES
            or hashlib.sha256(content).hexdigest() != job.output_sha256
        ):
            raise YouTubeAdapterError("output_binding_mismatch")

        self._authorize(job)
        progress = self._load_progress(job)
        if progress is not None:
            self._validate_progress_record(progress, len(content))
        if progress is None:
            session_uri = self._new_session(
                job,
                handle,
                request_metadata,
                len(content),
            )
            offset = 0
        elif progress.state in {
            YouTubeUploadState.UNCERTAIN,
            YouTubeUploadState.SESSION_EXPIRED,
            YouTubeUploadState.FAILED,
            YouTubeUploadState.BLOCKED,
            YouTubeUploadState.UPLOADED,
            YouTubeUploadState.PROCESSING,
            YouTubeUploadState.PUBLISHED,
            YouTubeUploadState.AVAILABLE_PRIVATE,
        }:
            raise YouTubeAdapterError("operation_requires_reconciliation")
        else:
            self._validate_progress_record(progress, len(content))
            try:
                session = self._retrieve_session_uri(handle)
            except YouTubeAdapterError:
                self._progress(
                    job,
                    YouTubeUploadState.UNCERTAIN,
                    progress.byte_offset,
                    len(content),
                )
                raise
            if session is None:
                self._progress(job, YouTubeUploadState.UNCERTAIN, progress.byte_offset, len(content))
                raise YouTubeAdapterError("secure_session_unavailable")
            if not isinstance(session, SecretStr) or not session.get_secret_value():
                self._progress(
                    job,
                    YouTubeUploadState.UNCERTAIN,
                    progress.byte_offset,
                    len(content),
                )
                raise YouTubeAdapterError("secure_session_corrupt")
            session_uri = session.get_secret_value()
            try:
                self._validate_session_uri(session_uri)
            except YouTubeAdapterError:
                self._progress(
                    job,
                    YouTubeUploadState.UNCERTAIN,
                    progress.byte_offset,
                    len(content),
                )
                raise
            probe = self._probe(
                job,
                handle,
                session_uri,
                len(content),
                progress.byte_offset,
            )
            if probe.status_code in {200, 201}:
                self._completed(job, handle, probe, len(content))
                return
            if probe.status_code == 404:
                self._mark_session_expired(
                    job,
                    handle,
                    progress.byte_offset,
                    len(content),
                )
                raise YouTubeAdapterError("session_expired_requires_approval")
            if probe.status_code != 308:
                self._progress(job, YouTubeUploadState.UNCERTAIN, progress.byte_offset, len(content))
                raise YouTubeAdapterError("reconciliation_uncertain")
            offset = self._offset_from_range(probe.header("Range"), len(content))
            self._progress(job, YouTubeUploadState.UPLOADING, offset, len(content))

        retries = 0
        total_retries = 0
        retry_started = time.monotonic()
        while offset < len(content):
            self._authorize(job)
            end = min(offset + self.CHUNK_BYTES, len(content)) - 1
            response: YouTubeTransportResponse | None = None
            try:
                response = self._request(
                    "PUT",
                    session_uri,
                    headers=self._headers(
                        job,
                        {
                            "Content-Type": "video/*",
                            "Content-Length": str(end - offset + 1),
                            "Content-Range": f"bytes {offset}-{end}/{len(content)}",
                        },
                    ),
                    body=content[offset : end + 1],
                )
            except YouTubeAdapterError as exc:
                if exc.code == "credential_unavailable":
                    self._progress(
                        job,
                        YouTubeUploadState.FAILED,
                        offset,
                        len(content),
                    )
                raise
            except (YouTubeTransportError, TimeoutError, ConnectionError):
                self._progress(job, YouTubeUploadState.RECONCILING, offset, len(content))
                try:
                    response = self._probe(
                        job,
                        handle,
                        session_uri,
                        len(content),
                        offset,
                    )
                except (YouTubeTransportError, TimeoutError, ConnectionError):
                    self._progress(job, YouTubeUploadState.UNCERTAIN, offset, len(content))
                    raise YouTubeAdapterError("reconciliation_uncertain") from None
                if response.status_code in {200, 201}:
                    if end + 1 != len(content):
                        self._progress(
                            job,
                            YouTubeUploadState.UNCERTAIN,
                            offset,
                            len(content),
                        )
                        raise YouTubeAdapterError("premature_upload_completion")
                    self._completed(job, handle, response, len(content))
                    return
                if response.status_code == 404:
                    self._mark_session_expired(job, handle, offset, len(content))
                    raise YouTubeAdapterError("session_expired_requires_approval") from None
                if response.status_code != 308:
                    self._progress(job, YouTubeUploadState.UNCERTAIN, offset, len(content))
                    raise YouTubeAdapterError("reconciliation_uncertain") from None
            assert response is not None
            if response.status_code in {200, 201}:
                self._completed(job, handle, response, len(content))
                return
            if response.status_code == 404:
                self._mark_session_expired(job, handle, offset, len(content))
                raise YouTubeAdapterError("session_expired_requires_approval")
            if response.status_code == 308:
                next_offset = self._offset_from_range(response.header("Range"), len(content))
                if next_offset <= offset and offset < len(content):
                    self._progress(job, YouTubeUploadState.UNCERTAIN, offset, len(content))
                    raise YouTubeAdapterError("upload_made_no_progress")
                offset = next_offset
                retries = 0
                self._progress(job, YouTubeUploadState.UPLOADING, offset, len(content))
                continue
            if response.status_code in self.RETRYABLE_STATUS:
                retries += 1
                total_retries += 1
                if (
                    retries > self.MAX_RETRIES
                    or total_retries > self.MAX_TOTAL_RETRIES
                    or time.monotonic() - retry_started
                    > self.RETRY_DEADLINE_SECONDS
                ):
                    self._progress(job, YouTubeUploadState.UNCERTAIN, offset, len(content))
                    raise YouTubeAdapterError("retry_limit_reached")
                retry_after = self._retry_delay(
                    response.header("Retry-After"),
                    retries,
                )
                try:
                    self.backoff(retries, retry_after)
                except Exception:
                    self._progress(
                        job,
                        YouTubeUploadState.UNCERTAIN,
                        offset,
                        len(content),
                    )
                    raise YouTubeAdapterError("retry_backoff_failed") from None
                if (
                    total_retries > self.MAX_TOTAL_RETRIES
                    or time.monotonic() - retry_started
                    > self.RETRY_DEADLINE_SECONDS
                ):
                    self._progress(job, YouTubeUploadState.UNCERTAIN, offset, len(content))
                    raise YouTubeAdapterError("retry_deadline_reached")
                probe = self._probe(
                    job,
                    handle,
                    session_uri,
                    len(content),
                    offset,
                )
                if probe.status_code in {200, 201}:
                    self._completed(job, handle, probe, len(content))
                    return
                if probe.status_code != 308:
                    self._progress(job, YouTubeUploadState.UNCERTAIN, offset, len(content))
                    raise YouTubeAdapterError("reconciliation_uncertain")
                next_offset = self._offset_from_range(probe.header("Range"), len(content))
                if next_offset < offset:
                    self._progress(job, YouTubeUploadState.UNCERTAIN, offset, len(content))
                    raise YouTubeAdapterError("server_resume_offset_regressed")
                offset = next_offset
                self._progress(job, YouTubeUploadState.UPLOADING, offset, len(content))
                continue
            self._progress(job, YouTubeUploadState.FAILED, offset, len(content))
            self._safe_response_error(response, "upload_rejected")

    def reconcile_upload_session(self, job: PublishingJob) -> YouTubeUploadState:
        """Explicitly inspect an uncertain existing session without uploading."""
        handle = self._session_handle(job)
        progress = self._load_progress(job)
        if (
            progress is None
            or progress.state
            not in {YouTubeUploadState.UNCERTAIN, YouTubeUploadState.RECONCILING}
        ):
            raise YouTubeAdapterError("upload_reconciliation_not_allowed")
        self._authorize(job)
        try:
            content = self.output_provider(job)
        except Exception:
            raise YouTubeAdapterError("output_unavailable") from None
        if (
            not isinstance(content, bytes)
            or not content
            or len(content) > self.MAX_UPLOAD_BYTES
            or hashlib.sha256(content).hexdigest() != job.output_sha256
            or progress.total_bytes != len(content)
        ):
            raise YouTubeAdapterError("output_binding_mismatch")
        self._validate_progress_record(progress, len(content))
        try:
            session = self._retrieve_session_uri(handle)
        except YouTubeAdapterError:
            raise
        if session is None:
            raise YouTubeAdapterError("secure_session_unavailable")
        if not isinstance(session, SecretStr) or not session.get_secret_value():
            raise YouTubeAdapterError("secure_session_corrupt")
        session_uri = session.get_secret_value()
        self._validate_session_uri(session_uri)
        response = self._probe(
            job,
            handle,
            session_uri,
            len(content),
            progress.byte_offset,
        )
        if response.status_code in {200, 201}:
            self._completed(job, handle, response, len(content))
            return YouTubeUploadState.UPLOADED
        if response.status_code == 404:
            self._mark_session_expired(
                job,
                handle,
                progress.byte_offset,
                len(content),
            )
            raise YouTubeAdapterError("session_expired_requires_approval")
        if response.status_code != 308:
            self._progress(
                job,
                YouTubeUploadState.UNCERTAIN,
                progress.byte_offset,
                len(content),
            )
            raise YouTubeAdapterError("reconciliation_uncertain")
        offset = self._offset_from_range(response.header("Range"), len(content))
        self._progress(job, YouTubeUploadState.UPLOADING, offset, len(content))
        return YouTubeUploadState.UPLOADING

    def reconcile_video(self, job: PublishingJob) -> YouTubeUploadState:
        progress = self._load_progress(job)
        if progress is None or progress.video_id is None:
            raise YouTubeAdapterError("video_identity_unavailable")
        self._validate_progress_record(progress, progress.total_bytes)
        if progress.state not in {
            YouTubeUploadState.UPLOADED,
            YouTubeUploadState.PROCESSING,
            YouTubeUploadState.AVAILABLE_PRIVATE,
        }:
            raise YouTubeAdapterError("video_reconciliation_not_allowed")
        self._authorize(job)
        token_headers = self._headers(
            job,
            {"Accept": "application/json"},
        )
        url = f"{self.VIDEOS_URL}?{urlencode({'part': 'status', 'id': progress.video_id})}"
        try:
            response = self._request(
                "GET",
                url,
                headers=token_headers,
                body=b"",
            )
        except (YouTubeTransportError, TimeoutError, ConnectionError):
            raise YouTubeAdapterError("video_status_unavailable") from None
        if response.status_code != 200:
            raise YouTubeAdapterError("video_status_unavailable")
        payload = response.json_object()
        items = payload.get("items")
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise YouTubeAdapterError("video_status_invalid")
        item = items[0]
        status = item.get("status")
        if (
            item.get("id") != progress.video_id
            or not isinstance(status, dict)
            or status.get("privacyStatus") != job.visibility.value
        ):
            raise YouTubeAdapterError("video_status_mismatch")
        upload_status = status.get("uploadStatus")
        target = (
            (
                YouTubeUploadState.AVAILABLE_PRIVATE
                if job.visibility.value == "private"
                else YouTubeUploadState.PUBLISHED
            )
            if upload_status == "processed"
            else YouTubeUploadState.PROCESSING
            if upload_status == "uploaded"
            else YouTubeUploadState.FAILED
            if upload_status in {"failed", "rejected", "deleted"}
            else None
        )
        if target is None:
            raise YouTubeAdapterError("video_status_unknown")
        self._progress(job, target, progress.total_bytes, progress.total_bytes, progress.video_id)
        return target
