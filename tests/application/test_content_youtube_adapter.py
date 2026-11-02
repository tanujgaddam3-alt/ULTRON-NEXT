from __future__ import annotations

import hashlib
import json
from collections import deque
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

import pytest
from pydantic import SecretStr

from tests.application.test_content_publishing import (
    MockAdapterOutcome,
    _approve,
    _create_job,
    _prepared,
)
from ultron.application.content.youtube_adapter import (
    OAuthCredentialMaterial,
    SecureSecretStoreError,
    SecureSecretStoreErrorCode,
    SecureUploadStorageUnavailable,
    UnavailableSecureUploadProgressStore,
    YouTubeAdapter,
    YouTubeAdapterError,
    YouTubeTransportError,
    YouTubeTransportResponse,
    YouTubeUploadProgress,
    YouTubeUploadState,
)
from ultron.domain.content import (
    OAuthCredentialBinding,
    OpaqueSecretReference,
    PublishingAttemptIdentity,
    PublishingJob,
    PublishingProvider,
    PublishingVisibility,
    SecretGeneration,
    UploadSecretBinding,
    UploadSecretPurpose,
    UploadSessionSecretHandle,
)


SESSION_URI = (
    "https://www.googleapis.com/upload/youtube/v3/videos"
    "?uploadType=resumable&upload_id=upload-session-secret"
)
ACCESS_TOKEN = "access-token-secret"


def _session_handle(job, *, attempt_number=1, attempt_id=None, **overrides):
    identity_data = {
        "attempt_id": attempt_id
        or str(uuid5(NAMESPACE_URL, f"cre-test-attempt:{job.job_id}:{attempt_number}")),
        "attempt_number": attempt_number,
        "owner_id": job.owner_id,
        "job_id": job.job_id,
        "account_id": job.account_id,
        "provider": PublishingProvider.YOUTUBE,
        "release_identity": job.request_fingerprint,
        "package_id": job.package_id,
        "package_version": job.package_version,
        "output_sha256": job.output_sha256,
    }
    identity_data.update(overrides)
    identity = PublishingAttemptIdentity(**identity_data)
    binding = UploadSecretBinding(
        attempt=identity,
        purpose=UploadSecretPurpose.RESUMABLE_UPLOAD_SESSION,
        schema_version=1,
    )
    reference = OpaqueSecretReference(
        reference_id=str(uuid5(NAMESPACE_URL, f"cre-test-ref:{identity.attempt_id}")),
        schema_version=1,
    )
    return UploadSessionSecretHandle(
        binding=binding,
        reference=reference,
        generation=SecretGeneration(value=1),
    )


class FakeCredentials:
    def access_token(self, account_id: str) -> SecretStr:
        assert account_id == "channel-1"
        return SecretStr(ACCESS_TOKEN)


class EmptyCredentials:
    def access_token(self, account_id: str) -> SecretStr:
        del account_id
        return SecretStr("")


class FakeProgressStore:
    """Test-only in-memory provider; not a production secret store."""

    def __init__(self):
        self.progress: dict[str, YouTubeUploadProgress] = {}
        self.sessions: dict[str, tuple[UploadSecretBinding, int, SecretStr, bool]] = {}

    def load(self, job_id: str):
        return self.progress.get(job_id)

    def save(self, job_id: str, progress: YouTubeUploadProgress):
        self.progress[job_id] = progress

    def retrieve_session_uri(self, handle: UploadSessionSecretHandle):
        item = self.sessions.get(handle.reference.reference_id)
        if item is None:
            return None
        binding, generation, secret, invalidated = item
        if binding != handle.binding:
            raise SecureSecretStoreError(SecureSecretStoreErrorCode.BINDING_MISMATCH)
        if generation != handle.generation.value:
            raise SecureSecretStoreError(SecureSecretStoreErrorCode.GENERATION_MISMATCH)
        return None if invalidated else secret

    def store_session_uri(self, handle: UploadSessionSecretHandle, session_uri: SecretStr):
        if handle.reference.reference_id in self.sessions:
            raise SecureSecretStoreError(SecureSecretStoreErrorCode.GENERATION_MISMATCH)
        self.sessions[handle.reference.reference_id] = (
            handle.binding,
            handle.generation.value,
            session_uri,
            False,
        )

    def replace_session_uri(
        self,
        handle: UploadSessionSecretHandle,
        session_uri: SecretStr,
    ) -> SecretGeneration:
        item = self.sessions.get(handle.reference.reference_id)
        if item is None:
            raise SecureSecretStoreError(SecureSecretStoreErrorCode.NOT_FOUND)
        binding, generation, _, _ = item
        if binding != handle.binding:
            raise SecureSecretStoreError(SecureSecretStoreErrorCode.BINDING_MISMATCH)
        if generation != handle.generation.value:
            raise SecureSecretStoreError(SecureSecretStoreErrorCode.GENERATION_MISMATCH)
        next_generation = SecretGeneration(value=generation + 1)
        self.sessions[handle.reference.reference_id] = (
            binding,
            next_generation.value,
            session_uri,
            False,
        )
        return next_generation

    def invalidate_session_uri(self, handle: UploadSessionSecretHandle):
        item = self.sessions.get(handle.reference.reference_id)
        if item is None:
            return
        binding, generation, secret, _ = item
        if binding != handle.binding:
            raise SecureSecretStoreError(SecureSecretStoreErrorCode.BINDING_MISMATCH)
        if generation != handle.generation.value:
            raise SecureSecretStoreError(SecureSecretStoreErrorCode.GENERATION_MISMATCH)
        self.sessions[handle.reference.reference_id] = (
            binding,
            generation,
            secret,
            True,
        )

    def delete_session_uri(self, handle: UploadSessionSecretHandle):
        item = self.sessions.get(handle.reference.reference_id)
        if item is None:
            return
        binding, generation, _, _ = item
        if binding != handle.binding:
            raise SecureSecretStoreError(SecureSecretStoreErrorCode.BINDING_MISMATCH)
        if generation != handle.generation.value:
            raise SecureSecretStoreError(SecureSecretStoreErrorCode.GENERATION_MISMATCH)
        self.sessions.pop(handle.reference.reference_id)


class FakeOAuthCredentialStore:
    def __init__(self):
        self.credentials = {}

    def create(self, binding, credentials):
        if binding.credential_id in self.credentials:
            raise SecureSecretStoreError(SecureSecretStoreErrorCode.GENERATION_MISMATCH)
        self.credentials[binding.credential_id] = (binding, credentials)

    def retrieve(self, binding):
        item = self.credentials.get(binding.credential_id)
        if item is None:
            raise SecureSecretStoreError(SecureSecretStoreErrorCode.NOT_FOUND)
        stored_binding, credentials = item
        if stored_binding != binding:
            raise SecureSecretStoreError(SecureSecretStoreErrorCode.BINDING_MISMATCH)
        return credentials

    def replace(self, binding, *, expected_generation, credentials):
        current = self.retrieve(binding)
        if current.generation != expected_generation:
            raise SecureSecretStoreError(SecureSecretStoreErrorCode.GENERATION_MISMATCH)
        self.credentials[binding.credential_id] = (binding, credentials)
        return credentials.generation

    def delete(self, binding, *, expected_generation):
        current = self.retrieve(binding)
        if current.generation != expected_generation:
            raise SecureSecretStoreError(SecureSecretStoreErrorCode.GENERATION_MISMATCH)
        del self.credentials[binding.credential_id]


class FailingSessionStore(FakeProgressStore):
    def store_session_uri(self, handle: UploadSessionSecretHandle, session_uri: SecretStr):
        del handle, session_uri
        raise RuntimeError(f"secret persistence detail: {SESSION_URI}")


class FailUploadingProgressOnce(FakeProgressStore):
    def __init__(self):
        super().__init__()
        self.failed = False

    def save(self, job_id: str, progress: YouTubeUploadProgress):
        if progress.state == YouTubeUploadState.UPLOADING and not self.failed:
            self.failed = True
            raise RuntimeError("local persistence diagnostic")
        super().save(job_id, progress)


class FailUploadedProgressOnce(FakeProgressStore):
    def __init__(self):
        super().__init__()
        self.failed = False

    def save(self, job_id: str, progress: YouTubeUploadProgress):
        if progress.state == YouTubeUploadState.UPLOADED and not self.failed:
            self.failed = True
            raise RuntimeError("confirmed upload could not be persisted")
        super().save(job_id, progress)


class InaccessibleSessionStore(FakeProgressStore):
    def retrieve_session_uri(self, handle: UploadSessionSecretHandle):
        del handle
        raise PermissionError(f"credential service inaccessible: {SESSION_URI}")


class FakeTransport:
    def __init__(self, responses=()):
        self.responses = deque(responses)
        self.calls = []

    def request(self, method, url, *, headers, body, timeout_seconds):
        assert timeout_seconds == YouTubeAdapter.REQUEST_TIMEOUT_SECONDS
        authorization = headers["Authorization"]
        assert authorization == "Bearer " + ACCESS_TOKEN
        headers["Authorization"] = "******"
        assert len(headers["Authorization"]) == 6
        assert set(headers["Authorization"]) == {"*"}
        safe_headers = headers.copy()
        safe_headers["Authorization"] = "******"
        safe_url = (
            "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=REDACTED"
            if method == "PUT" and "/upload/youtube/v3/videos?" in url
            else url
        )
        self.calls.append((method, safe_url, safe_headers, body))
        if not self.responses:
            raise AssertionError("Unexpected transport call.")
        response = self.responses.popleft()
        if isinstance(response, BaseException):
            raise response
        return response


def _response(code, *, headers=None, body=b""):
    return YouTubeTransportResponse(code, headers or {}, body)


def _video(video_id="video-123"):
    return _response(200, body=json.dumps({"id": video_id}).encode())


def _remaining_chunk_responses(output, offset, chunk_size):
    responses = []
    while offset < len(output):
        offset = min(offset + chunk_size, len(output))
        if offset == len(output):
            responses.append(_video())
        else:
            responses.append(
                _response(308, headers={"Range": f"bytes=0-{offset - 1}"})
            )
    return responses


def _settings(status, *, video_id="video-123", upload_status="processed"):
    return _response(
        200,
        body=json.dumps(
            {
                "items": [
                    {
                        "id": video_id,
                        "status": {
                            "privacyStatus": status,
                            "uploadStatus": upload_status,
                        },
                    }
                ]
            }
        ).encode(),
    )


def _new_adapter(
    job: PublishingJob,
    output: bytes,
    metadata: dict,
    transport: FakeTransport,
    progress: FakeProgressStore | None = None,
    *,
    guard=None,
    backoff=None,
    credentials=None,
    session_handle_provider=None,
):
    checks = []

    def authorize(candidate):
        assert candidate.job_id == job.job_id
        checks.append(candidate.request_fingerprint)
        if guard is not None:
            guard(candidate)

    def provide_output(candidate):
        assert candidate.job_id == job.job_id
        return output

    def provide_metadata(candidate):
        assert candidate.job_id == job.job_id
        return metadata

    adapter = YouTubeAdapter(
        transport=transport,
        credentials=credentials if credentials is not None else FakeCredentials(),
        progress_store=progress if progress is not None else FakeProgressStore(),
        session_handle_provider=session_handle_provider or _session_handle,
        authorization_guard=authorize,
        output_provider=provide_output,
        metadata_provider=provide_metadata,
        backoff=backoff,
    )
    return adapter, checks


def _job_fixture(
    tmp_path,
    monkeypatch,
    *,
    visibility=PublishingVisibility.PRIVATE,
):
    (
        harness,
        _,
        _,
        _,
        rendering,
        render,
        version,
        publishing,
        _,
        reviewer_authority,
        reviewer_websocket,
    ) = _prepared(tmp_path, monkeypatch, outcome=MockAdapterOutcome.ACCEPTED)
    job = _create_job(harness, publishing, render, visibility=visibility)
    _approve(harness, publishing, job, reviewer_authority, reviewer_websocket)
    job = harness.store.get_publishing_job(job.job_id)
    assert job is not None
    output = rendering.get_clip_output_bytes(
        render.render_id,
        context=harness.context(
            "content_read_clip_render_output",
            {"render_id": render.render_id},
        ),
    )
    return harness, job, output, version.metadata


def test_initial_request_binds_hash_metadata_and_explicit_visibility(
    tmp_path,
    monkeypatch,
):
    harness, job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    transport = FakeTransport(
        [
            _response(200, headers={"Location": SESSION_URI}),
            _video(),
        ]
    )
    progress = FakeProgressStore()
    adapter, checks = _new_adapter(job, output, metadata, transport, progress)
    adapter.dispatch(job)

    method, url, headers, body = transport.calls[0]
    request_metadata = json.loads(body)
    assert (method, url) == ("POST", YouTubeAdapter.INIT_URL)
    assert headers["X-Upload-Content-Length"] == str(len(output))
    assert len(headers["Authorization"]) == 6
    assert set(headers["Authorization"]) == {"*"}
    assert request_metadata["snippet"]["title"] == metadata["title"]
    assert request_metadata["status"] == {
        "privacyStatus": job.visibility.value,
        "containsSyntheticMedia": job.synthetic_media_disclosed,
    }
    assert job.output_sha256 == hashlib.sha256(output).hexdigest()
    assert checks
    assert progress.retrieve_session_uri(_session_handle(job)) is None
    assert SESSION_URI not in repr(harness.store.get_publishing_job(job.job_id))
    assert SESSION_URI not in repr(
        harness.store.list_publishing_job_events(job.job_id)
    )
    harness.store.close()


def test_missing_secure_store_and_authorization_fail_before_transport(
    tmp_path,
    monkeypatch,
):
    harness, job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    transport = FakeTransport()
    def allow(candidate):
        assert candidate.job_id == job.job_id

    def provide_output(candidate):
        assert candidate.job_id == job.job_id
        return output

    def provide_metadata(candidate):
        assert candidate.job_id == job.job_id
        return metadata

    with pytest.raises(YouTubeAdapterError, match="secure_session_store_required"):
        YouTubeAdapter(
            transport=transport,
            credentials=FakeCredentials(),
            progress_store=None,
            session_handle_provider=_session_handle,
            authorization_guard=allow,
            output_provider=provide_output,
            metadata_provider=provide_metadata,
        )

    unavailable_adapter, _ = _new_adapter(
        job,
        output,
        metadata,
        transport,
        progress=UnavailableSecureUploadProgressStore(),
    )
    with pytest.raises(YouTubeAdapterError, match="progress_persistence_unavailable"):
        unavailable_adapter.dispatch(job)

    def deny(candidate):
        assert candidate.job_id == job.job_id
        raise PermissionError("revoked")

    adapter, _ = _new_adapter(
        job,
        output,
        metadata,
        transport,
        guard=deny,
    )
    with pytest.raises(YouTubeAdapterError, match="authorization_denied"):
        adapter.dispatch(job)
    assert transport.calls == []
    harness.store.close()


def test_authorization_revoked_at_session_dispatch_boundary_is_blocked(
    tmp_path,
    monkeypatch,
):
    harness, job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    transport = FakeTransport()
    checks = 0

    def revoke_on_final_check(_job):
        del _job
        nonlocal checks
        checks += 1
        if checks == 4:
            raise PermissionError("rights revoked before session creation")

    progress = FakeProgressStore()
    adapter, _ = _new_adapter(
        job,
        output,
        metadata,
        transport,
        progress,
        guard=revoke_on_final_check,
    )

    with pytest.raises(YouTubeAdapterError, match="authorization_denied"):
        adapter.dispatch(job)

    assert checks == 4
    assert transport.calls == []
    assert progress.load(job.job_id).state == YouTubeUploadState.BLOCKED
    with pytest.raises(YouTubeAdapterError, match="operation_requires_reconciliation"):
        adapter.dispatch(job)
    assert transport.calls == []
    harness.store.close()


def test_session_creation_transport_error_is_safe_and_uncertain(
    tmp_path,
    monkeypatch,
):
    harness, job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    transport = FakeTransport(
        [YouTubeTransportError(f"session={SESSION_URI} token={ACCESS_TOKEN}")]
    )
    progress = FakeProgressStore()
    adapter, _ = _new_adapter(job, output, metadata, transport, progress)

    with pytest.raises(YouTubeAdapterError) as error:
        adapter.dispatch(job)

    assert error.value.code == "session_creation_uncertain"
    assert SESSION_URI not in str(error.value)
    assert ACCESS_TOKEN not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__
    assert progress.load(job.job_id).state == YouTubeUploadState.UNCERTAIN
    assert len(transport.calls) == 1
    harness.store.close()


def test_persisted_session_uri_is_not_in_publishing_records_or_audit(
    tmp_path,
    monkeypatch,
):
    harness, job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    transport = FakeTransport(
        [
            _response(200, headers={"Location": SESSION_URI}),
            TimeoutError("chunk response interrupted"),
            TimeoutError("status probe interrupted"),
        ]
    )
    progress = FakeProgressStore()
    adapter, _ = _new_adapter(job, output, metadata, transport, progress)

    with pytest.raises(YouTubeAdapterError, match="status_probe_uncertain"):
        adapter.dispatch(job)

    persisted_job = harness.store.get_publishing_job(job.job_id)
    persisted_events = harness.store.list_publishing_job_events(job.job_id)
    audit_events = harness.store.list_audit_events(job.job_id)
    assert progress.retrieve_session_uri(_session_handle(job)) == SecretStr(SESSION_URI)
    assert SESSION_URI not in repr(persisted_job)
    assert SESSION_URI not in repr(persisted_events)
    assert SESSION_URI not in repr(audit_events)
    assert progress.load(job.job_id).state == YouTubeUploadState.UNCERTAIN
    assert len(transport.calls) == 3
    harness.store.close()


def test_progress_secret_store_contract_supports_replace_invalidate_and_delete():
    job = type(
        "TestJob",
        (),
        {
            "job_id": "job-1",
            "owner_id": "owner-1",
            "account_id": "channel-1",
            "request_fingerprint": "a" * 64,
            "package_id": "package-1",
            "package_version": 1,
            "output_sha256": "b" * 64,
        },
    )()
    handle = _session_handle(job)
    progress = FakeProgressStore()
    first = SecretStr(SESSION_URI)
    second = SecretStr(SESSION_URI + "&replacement=1")

    progress.store_session_uri(handle, first)
    assert progress.retrieve_session_uri(handle) == first
    next_generation = progress.replace_session_uri(
        handle,
        second,
    )
    updated_handle = handle.model_copy(update={"generation": next_generation})
    assert progress.retrieve_session_uri(updated_handle) == second
    progress.invalidate_session_uri(updated_handle)
    assert progress.retrieve_session_uri(updated_handle) is None
    progress.delete_session_uri(updated_handle)
    assert handle.reference.reference_id not in progress.sessions


def test_session_secret_binding_rejects_cross_attempt_account_and_stale_generation():
    job = type(
        "TestJob",
        (),
        {
            "job_id": "job-1",
            "owner_id": "owner-1",
            "account_id": "channel-1",
            "request_fingerprint": "a" * 64,
            "package_id": "package-1",
            "package_version": 1,
            "output_sha256": "b" * 64,
        },
    )()
    handle = _session_handle(job)
    other_attempt = _session_handle(job, attempt_number=2)
    assert handle.binding.attempt.attempt_id != other_attempt.binding.attempt.attempt_id
    assert handle.reference.reference_id != other_attempt.reference.reference_id

    progress = FakeProgressStore()
    progress.store_session_uri(handle, SecretStr(SESSION_URI))
    reused_reference = other_attempt.model_copy(
        update={"reference": handle.reference}
    )
    with pytest.raises(SecureSecretStoreError) as cross_attempt:
        progress.retrieve_session_uri(reused_reference)
    assert cross_attempt.value.code == SecureSecretStoreErrorCode.BINDING_MISMATCH

    wrong_account_binding = handle.binding.model_copy(
        update={
            "attempt": handle.binding.attempt.model_copy(
                update={"account_id": "different-channel"}
            )
        }
    )
    wrong_account = handle.model_copy(update={"binding": wrong_account_binding})
    with pytest.raises(SecureSecretStoreError) as cross_account:
        progress.retrieve_session_uri(wrong_account)
    assert cross_account.value.code == SecureSecretStoreErrorCode.BINDING_MISMATCH

    stale = handle.model_copy(update={"generation": SecretGeneration(value=2)})
    with pytest.raises(SecureSecretStoreError) as generation_error:
        progress.retrieve_session_uri(stale)
    assert generation_error.value.code == SecureSecretStoreErrorCode.GENERATION_MISMATCH


def test_adapter_rejects_mismatched_attempt_binding_before_transport(
    tmp_path,
    monkeypatch,
):
    harness, job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    transport = FakeTransport()
    wrong_account = lambda candidate: _session_handle(
        candidate,
        account_id="another-channel",
    )
    adapter, _ = _new_adapter(
        job,
        output,
        metadata,
        transport,
        session_handle_provider=wrong_account,
    )
    with pytest.raises(YouTubeAdapterError, match="session_binding_mismatch"):
        adapter.dispatch(job)
    assert transport.calls == []
    harness.store.close()


def test_attempt_binding_and_secret_reference_reject_invalid_identity_versions():
    job = type(
        "TestJob",
        (),
        {
            "job_id": "job-1",
            "owner_id": "owner-1",
            "account_id": "channel-1",
            "request_fingerprint": "a" * 64,
            "package_id": "package-1",
            "package_version": 1,
            "output_sha256": "b" * 64,
        },
    )()
    handle = _session_handle(job)
    with pytest.raises(ValueError, match="positive"):
        UploadSecretBinding(
            attempt=handle.binding.attempt,
            purpose=UploadSecretPurpose.RESUMABLE_UPLOAD_SESSION,
            schema_version=0,
        )
    with pytest.raises(ValueError, match="must match"):
        UploadSessionSecretHandle(
            binding=handle.binding,
            reference=handle.reference.model_copy(update={"schema_version": 2}),
            generation=handle.generation,
        )
    with pytest.raises(ValueError, match="UUID"):
        OpaqueSecretReference(reference_id="not-opaque", schema_version=1)
    with pytest.raises(ValueError, match="UUID"):
        OAuthCredentialBinding(
            credential_id="not-uuid",
            owner_id="owner-1",
            account_id="channel-1",
            provider=PublishingProvider.YOUTUBE,
            schema_version=1,
        )


def test_oauth_credential_contract_is_account_bound_versioned_and_redacted():
    binding = OAuthCredentialBinding(
        credential_id=str(uuid5(NAMESPACE_URL, "credential-1")),
        owner_id="owner-1",
        account_id="channel-1",
        provider=PublishingProvider.YOUTUBE,
        schema_version=1,
    )
    generation = SecretGeneration(value=1)
    credentials = OAuthCredentialMaterial(
        access_token=SecretStr(ACCESS_TOKEN),
        refresh_token=SecretStr("refresh-token-secret"),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scopes=frozenset({"youtube.upload"}),
        generation=generation,
    )
    store = FakeOAuthCredentialStore()
    store.create(binding, credentials)
    assert store.retrieve(binding) == credentials
    assert ACCESS_TOKEN not in repr(store.retrieve(binding))
    assert "refresh-token-secret" not in repr(store.retrieve(binding))

    rotated = OAuthCredentialMaterial(
        access_token=SecretStr("rotated-access-token"),
        refresh_token=SecretStr("rotated-refresh-token"),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scopes=credentials.scopes,
        generation=SecretGeneration(value=2),
    )
    assert store.replace(
        binding,
        expected_generation=generation,
        credentials=rotated,
    ) == rotated.generation
    with pytest.raises(SecureSecretStoreError) as stale:
        store.replace(
            binding,
            expected_generation=generation,
            credentials=credentials,
        )
    assert stale.value.code == SecureSecretStoreErrorCode.GENERATION_MISMATCH
    store.delete(binding, expected_generation=rotated.generation)
    with pytest.raises(SecureSecretStoreError) as missing:
        store.retrieve(binding)
    assert missing.value.code == SecureSecretStoreErrorCode.NOT_FOUND


def test_secure_store_error_text_never_accepts_secret_payloads():
    error = SecureSecretStoreError(SecureSecretStoreErrorCode.OPERATION_FAILED)
    assert str(error) == "secure_secret_operation_failed"
    assert SESSION_URI not in repr(error)
    assert ACCESS_TOKEN not in repr(error)


def test_unavailable_store_fails_closed_for_every_secret_operation():
    store = UnavailableSecureUploadProgressStore()
    secret = SecretStr(SESSION_URI)
    job = type(
        "TestJob",
        (),
        {
            "job_id": "job-1",
            "owner_id": "owner-1",
            "account_id": "channel-1",
            "request_fingerprint": "a" * 64,
            "package_id": "package-1",
            "package_version": 1,
            "output_sha256": "b" * 64,
        },
    )()
    handle = _session_handle(job)
    operations = (
        lambda: store.load("job-1"),
        lambda: store.save(
            "job-1",
            YouTubeUploadProgress(YouTubeUploadState.SESSION_CREATING, 0, 1),
        ),
        lambda: store.retrieve_session_uri(handle),
        lambda: store.store_session_uri(handle, secret),
        lambda: store.replace_session_uri(
            handle,
            secret,
        ),
        lambda: store.invalidate_session_uri(handle),
        lambda: store.delete_session_uri(handle),
    )

    for operation in operations:
        with pytest.raises(SecureUploadStorageUnavailable) as error:
            operation()
        assert SESSION_URI not in str(error.value)


def test_session_uri_persistence_failure_is_uncertain_and_never_reposted(
    tmp_path,
    monkeypatch,
):
    harness, job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    progress = FailingSessionStore()
    transport = FakeTransport([_response(200, headers={"Location": SESSION_URI})])
    adapter, _ = _new_adapter(job, output, metadata, transport, progress)

    with pytest.raises(YouTubeAdapterError) as error:
        adapter.dispatch(job)

    assert error.value.code == "secure_session_persistence_failed"
    assert SESSION_URI not in str(error.value)
    assert progress.load(job.job_id).state == YouTubeUploadState.UNCERTAIN
    assert progress.retrieve_session_uri(_session_handle(job)) is None
    with pytest.raises(YouTubeAdapterError, match="operation_requires_reconciliation"):
        adapter.dispatch(job)
    assert len(transport.calls) == 1
    harness.store.close()


def test_missing_or_corrupt_session_material_never_starts_a_new_session(
    tmp_path,
    monkeypatch,
):
    harness, job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    progress = FakeProgressStore()
    progress.progress[job.job_id] = YouTubeUploadProgress(
        YouTubeUploadState.UPLOADING,
        0,
        len(output),
    )
    transport = FakeTransport()
    adapter, _ = _new_adapter(job, output, metadata, transport, progress)

    with pytest.raises(YouTubeAdapterError, match="secure_session_unavailable"):
        adapter.dispatch(job)
    assert progress.load(job.job_id).state == YouTubeUploadState.UNCERTAIN
    with pytest.raises(YouTubeAdapterError, match="operation_requires_reconciliation"):
        adapter.dispatch(job)
    assert transport.calls == []
    harness.store.close()

    corrupt_root = tmp_path / "corrupt"
    corrupt_root.mkdir()
    harness, job, output, metadata = _job_fixture(corrupt_root, monkeypatch)
    progress = FakeProgressStore()
    progress.progress[job.job_id] = YouTubeUploadProgress(
        YouTubeUploadState.UPLOADING,
        0,
        len(output),
    )
    progress.store_session_uri(
        _session_handle(job),
        SecretStr("https://attacker.example/upload?token=secret"),
    )
    transport = FakeTransport()
    adapter, _ = _new_adapter(job, output, metadata, transport, progress)
    with pytest.raises(YouTubeAdapterError, match="invalid_upload_session"):
        adapter.dispatch(job)
    assert progress.load(job.job_id).state == YouTubeUploadState.UNCERTAIN
    assert transport.calls == []
    harness.store.close()


def test_inaccessible_session_material_is_uncertain_without_plaintext_fallback(
    tmp_path,
    monkeypatch,
):
    harness, job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    progress = InaccessibleSessionStore()
    progress.store_session_uri(_session_handle(job), SecretStr(SESSION_URI))
    progress.progress[job.job_id] = YouTubeUploadProgress(
        YouTubeUploadState.UPLOADING,
        0,
        len(output),
    )
    transport = FakeTransport()
    adapter, _ = _new_adapter(job, output, metadata, transport, progress)

    with pytest.raises(YouTubeAdapterError, match="secure_session_unavailable") as error:
        adapter.dispatch(job)

    assert SESSION_URI not in str(error.value)
    assert progress.load(job.job_id).state == YouTubeUploadState.UNCERTAIN
    assert transport.calls == []
    with pytest.raises(YouTubeAdapterError, match="operation_requires_reconciliation"):
        adapter.dispatch(job)
    assert transport.calls == []
    harness.store.close()


def test_progress_failure_after_session_storage_recovers_existing_session(
    tmp_path,
    monkeypatch,
):
    harness, job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(YouTubeAdapter, "CHUNK_BYTES", 16)
    progress = FailUploadingProgressOnce()
    transport = FakeTransport(
        [
            _response(200, headers={"Location": SESSION_URI}),
            _response(308),
            *_remaining_chunk_responses(output, 0, 16),
        ]
    )
    adapter, _ = _new_adapter(job, output, metadata, transport, progress)

    with pytest.raises(YouTubeAdapterError, match="progress_persistence_failed"):
        adapter.dispatch(job)
    assert progress.load(job.job_id).state == YouTubeUploadState.SESSION_CREATING
    assert progress.retrieve_session_uri(_session_handle(job)) is not None

    restarted_adapter, _ = _new_adapter(
        job,
        output,
        metadata,
        transport,
        progress,
    )
    restarted_adapter.dispatch(job)
    assert progress.load(job.job_id).state == YouTubeUploadState.UPLOADED
    assert not any(call[0] == "POST" for call in transport.calls[1:])
    harness.store.close()


def test_remote_completion_with_local_persistence_failure_reconciles_same_session(
    tmp_path,
    monkeypatch,
):
    harness, job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    progress = FailUploadedProgressOnce()
    transport = FakeTransport(
        [
            _response(200, headers={"Location": SESSION_URI}),
            _video("accepted-before-local-failure"),
            _video("accepted-before-local-failure"),
        ]
    )
    adapter, _ = _new_adapter(job, output, metadata, transport, progress)

    with pytest.raises(YouTubeAdapterError, match="progress_persistence_failed"):
        adapter.dispatch(job)

    assert progress.load(job.job_id).state == YouTubeUploadState.UPLOADING
    assert progress.retrieve_session_uri(_session_handle(job)) is not None
    first_attempt_count = len(transport.calls)
    adapter.dispatch(job)

    assert progress.load(job.job_id).state == YouTubeUploadState.UPLOADED
    assert progress.load(job.job_id).video_id == "accepted-before-local-failure"
    assert progress.retrieve_session_uri(_session_handle(job)) is None
    assert [call[0] for call in transport.calls] == ["POST", "PUT", "PUT"]
    assert len(transport.calls) == first_attempt_count + 1
    harness.store.close()


def test_missing_credentials_fail_before_transport(tmp_path, monkeypatch):
    harness, job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    progress = FakeProgressStore()
    transport = FakeTransport()
    adapter, _ = _new_adapter(
        job,
        output,
        metadata,
        transport,
        progress,
        credentials=EmptyCredentials(),
    )
    with pytest.raises(YouTubeAdapterError, match="credential_unavailable"):
        adapter.dispatch(job)
    assert transport.calls == []
    assert progress.load(job.job_id).state == YouTubeUploadState.FAILED
    harness.store.close()


@pytest.mark.parametrize("denial", ["rights revoked", "kill switch enabled"])
def test_rights_and_kill_switch_guard_denials_dispatch_nothing(
    tmp_path,
    monkeypatch,
    denial,
):
    harness, job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    transport = FakeTransport()
    def deny(candidate):
        assert candidate.job_id == job.job_id
        raise PermissionError(denial)

    adapter, _ = _new_adapter(
        job,
        output,
        metadata,
        transport,
        guard=deny,
    )
    with pytest.raises(YouTubeAdapterError, match="authorization_denied"):
        adapter.dispatch(job)
    assert transport.calls == []
    assert adapter.progress_store.load(job.job_id) is None
    harness.store.close()


def test_session_uri_and_token_are_absent_from_progress_and_errors(
    tmp_path,
    monkeypatch,
):
    harness, job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    progress = FakeProgressStore()
    transport = FakeTransport(
        [
            _response(
                200,
                headers={
                    "Location": "https://attacker.example/upload?secret=bad",
                },
            )
        ]
    )
    adapter, _ = _new_adapter(job, output, metadata, transport, progress)
    with pytest.raises(YouTubeAdapterError) as error:
        adapter.dispatch(job)
    assert "attacker.example" not in str(error.value)
    assert ACCESS_TOKEN not in str(error.value)
    assert SESSION_URI not in repr(progress.progress)
    assert SESSION_URI not in repr(progress.sessions)
    assert SESSION_URI not in repr(transport.calls)
    assert progress.load(job.job_id).state == YouTubeUploadState.UNCERTAIN
    harness.store.close()


def test_oversized_transport_response_fails_uncertain_without_retry(
    tmp_path,
    monkeypatch,
):
    harness, job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    transport = FakeTransport(
        [
            YouTubeTransportResponse(
                200,
                {"Location": SESSION_URI},
                b"x" * (YouTubeAdapter.MAX_RESPONSE_BYTES + 1),
            )
        ]
    )
    progress = FakeProgressStore()
    adapter, _ = _new_adapter(job, output, metadata, transport, progress)

    with pytest.raises(YouTubeAdapterError, match="session_creation_uncertain"):
        adapter.dispatch(job)

    assert progress.load(job.job_id).state == YouTubeUploadState.UNCERTAIN
    assert len(transport.calls) == 1
    harness.store.close()


def test_partial_upload_uses_correct_ranges_and_finishes_only_as_uploaded(
    tmp_path,
    monkeypatch,
):
    harness, job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(YouTubeAdapter, "CHUNK_BYTES", 16)
    responses = [_response(200, headers={"Location": SESSION_URI})]
    expected_final_offset = 0
    while expected_final_offset < len(output):
        expected_final_offset = min(expected_final_offset + 16, len(output))
        responses.append(
            _response(
                200 if expected_final_offset == len(output) else 308,
                headers=(
                    {}
                    if expected_final_offset == len(output)
                    else {"Range": f"bytes=0-{expected_final_offset - 1}"}
                ),
                body=b'{"id":"video-123"}'
                if expected_final_offset == len(output)
                else b"",
            )
        )
    transport = FakeTransport(responses)
    progress = FakeProgressStore()
    adapter, _ = _new_adapter(job, output, metadata, transport, progress)
    adapter.dispatch(job)

    chunk_calls = [call for call in transport.calls if call[0] == "PUT"]
    assert len(chunk_calls) == (len(output) + 15) // 16
    offset = 0
    for method, _, headers, body in chunk_calls:
        assert method == "PUT"
        range_part, total = headers["Content-Range"].removeprefix("bytes ").split("/")
        start_byte, end_byte = map(int, range_part.split("-"))
        assert (start_byte, end_byte, int(total)) == (
            offset,
            offset + len(body) - 1,
            len(output),
        )
        offset = end_byte + 1
    assert progress.load(job.job_id).state == YouTubeUploadState.UPLOADED
    assert progress.load(job.job_id).video_id == "video-123"
    assert progress.load(job.job_id).state != YouTubeUploadState.PUBLISHED
    harness.store.close()


def test_interrupted_chunk_reconciles_server_offset_before_continuing(
    tmp_path,
    monkeypatch,
):
    harness, job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(YouTubeAdapter, "CHUNK_BYTES", 16)
    transport = FakeTransport(
        [
            _response(200, headers={"Location": SESSION_URI}),
            TimeoutError("network diagnostic containing secret"),
            _response(308, headers={"Range": "bytes=0-15"}),
            *_remaining_chunk_responses(output, 16, 16),
        ]
    )
    progress = FakeProgressStore()
    adapter, _ = _new_adapter(job, output, metadata, transport, progress)
    adapter.dispatch(job)

    assert [call[0] for call in transport.calls[:3]] == ["POST", "PUT", "PUT"]
    assert all(call[0] == "PUT" for call in transport.calls[1:])
    probe = transport.calls[2]
    assert probe[2]["Content-Range"] == f"bytes */{len(output)}"
    resumed = transport.calls[3]
    assert resumed[2]["Content-Range"].startswith("bytes 16-")
    assert progress.load(job.job_id).state == YouTubeUploadState.UPLOADED
    harness.store.close()


def test_completed_upload_is_discovered_by_status_probe(tmp_path, monkeypatch):
    harness, job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    transport = FakeTransport(
        [
            _response(200, headers={"Location": SESSION_URI}),
            TimeoutError("completion response lost"),
            _video("completed-before-timeout"),
        ]
    )
    progress = FakeProgressStore()
    adapter, _ = _new_adapter(job, output, metadata, transport, progress)
    adapter.dispatch(job)
    assert progress.load(job.job_id).state == YouTubeUploadState.UPLOADED
    assert progress.load(job.job_id).video_id == "completed-before-timeout"
    assert len([call for call in transport.calls if call[0] == "POST"]) == 1
    harness.store.close()


def test_retryable_response_uses_bounded_backoff_and_reconciliation(
    tmp_path,
    monkeypatch,
):
    harness, job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(YouTubeAdapter, "CHUNK_BYTES", 16)
    backoffs = []
    first_end = min(15, len(output) - 1)
    transport = FakeTransport(
        [
            _response(200, headers={"Location": SESSION_URI}),
            _response(503, headers={"Retry-After": "2"}),
            _response(308),
            *_remaining_chunk_responses(output, 0, 16),
        ]
    )
    adapter, _ = _new_adapter(
        job,
        output,
        metadata,
        transport,
        backoff=lambda attempt, delay: backoffs.append((attempt, delay)),
    )
    adapter.dispatch(job)
    assert backoffs == [(1, "2")]
    assert transport.calls[2][2]["Content-Range"] == f"bytes */{len(output)}"
    assert transport.calls[3][2]["Content-Range"].startswith("bytes 0-")
    assert first_end >= 0
    harness.store.close()


@pytest.mark.parametrize(
    ("retry_after", "attempt", "expected"),
    [
        ("2", 1, "2"),
        ("999999", 2, "300"),
        ("invalid", 2, "2"),
        (None, 3, "4"),
    ],
)
def test_retry_after_is_bounded(retry_after, attempt, expected):
    assert YouTubeAdapter._retry_delay(retry_after, attempt) == expected


def test_permanent_rejection_and_session_expiry_are_terminal(tmp_path, monkeypatch):
    harness, job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    progress = FakeProgressStore()
    transport = FakeTransport(
        [
            _response(200, headers={"Location": SESSION_URI}),
            _response(403, body=b"secret error body"),
        ]
    )
    adapter, _ = _new_adapter(job, output, metadata, transport, progress)
    with pytest.raises(YouTubeAdapterError) as error:
        adapter.dispatch(job)
    assert error.value.code == "upload_rejected"
    assert "secret error body" not in str(error.value)
    assert progress.load(job.job_id).state == YouTubeUploadState.FAILED
    harness.store.close()


def test_expired_session_never_automatically_creates_replacement(
    tmp_path,
    monkeypatch,
):
    harness, job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(YouTubeAdapter, "CHUNK_BYTES", 16)
    progress = FakeProgressStore()
    progress.store_session_uri(_session_handle(job), SecretStr(SESSION_URI))
    progress.progress[job.job_id] = YouTubeUploadProgress(
        YouTubeUploadState.UPLOADING,
        0,
        len(output),
    )
    transport = FakeTransport([_response(404)])
    adapter, _ = _new_adapter(job, output, metadata, transport, progress)
    with pytest.raises(YouTubeAdapterError, match="session_expired_requires_approval"):
        adapter.dispatch(job)
    with pytest.raises(YouTubeAdapterError, match="operation_requires_reconciliation"):
        adapter.dispatch(job)
    assert len([call for call in transport.calls if call[0] == "POST"]) == 0
    assert progress.load(job.job_id).state == YouTubeUploadState.SESSION_EXPIRED
    assert progress.retrieve_session_uri(_session_handle(job)) is None
    harness.store.close()


def test_restart_resumes_existing_session_without_new_session_creation(
    tmp_path,
    monkeypatch,
):
    harness, job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(YouTubeAdapter, "CHUNK_BYTES", 16)
    progress = FakeProgressStore()
    progress.store_session_uri(_session_handle(job), SecretStr(SESSION_URI))
    progress.progress[job.job_id] = YouTubeUploadProgress(
        YouTubeUploadState.UPLOADING,
        0,
        len(output),
    )
    transport = FakeTransport(
        [
            _response(308, headers={"Range": "bytes=0-15"}),
            *_remaining_chunk_responses(output, 16, 16),
        ]
    )
    adapter, _ = _new_adapter(job, output, metadata, transport, progress)
    adapter.dispatch(job)
    assert transport.calls[0][2]["Content-Range"] == f"bytes */{len(output)}"
    assert not any(call[0] == "POST" for call in transport.calls)
    assert progress.load(job.job_id).state == YouTubeUploadState.UPLOADED
    harness.store.close()


def test_uncertain_session_requires_explicit_reconciliation_before_resume(
    tmp_path,
    monkeypatch,
):
    harness, job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(YouTubeAdapter, "CHUNK_BYTES", 16)
    progress = FakeProgressStore()
    progress.store_session_uri(_session_handle(job), SecretStr(SESSION_URI))
    progress.progress[job.job_id] = YouTubeUploadProgress(
        YouTubeUploadState.UNCERTAIN,
        16,
        len(output),
    )
    transport = FakeTransport(
        [
            _response(308, headers={"Range": "bytes=0-15"}),
            _response(308, headers={"Range": "bytes=0-15"}),
            *_remaining_chunk_responses(output, 16, 16),
        ]
    )
    adapter, _ = _new_adapter(job, output, metadata, transport, progress)

    with pytest.raises(YouTubeAdapterError, match="operation_requires_reconciliation"):
        adapter.dispatch(job)
    assert adapter.reconcile_upload_session(job) == YouTubeUploadState.UPLOADING
    assert transport.calls[0][2]["Content-Range"] == f"bytes */{len(output)}"
    assert all(call[0] != "POST" for call in transport.calls)
    adapter.dispatch(job)

    assert progress.load(job.job_id).state == YouTubeUploadState.UPLOADED
    assert not any(call[0] == "POST" for call in transport.calls)
    harness.store.close()


def test_reconciliation_separates_uploaded_processing_and_published(
    tmp_path,
    monkeypatch,
):
    harness, job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    progress = FakeProgressStore()
    transport = FakeTransport([_settings(job.visibility.value)])
    progress.progress[job.job_id] = YouTubeUploadProgress(
        YouTubeUploadState.UPLOADED,
        len(output),
        len(output),
        "video-123",
    )
    adapter, _ = _new_adapter(job, output, metadata, transport, progress)
    assert adapter.reconcile_video(job) == YouTubeUploadState.AVAILABLE_PRIVATE
    assert progress.load(job.job_id).state == YouTubeUploadState.AVAILABLE_PRIVATE
    harness.store.close()


def test_processing_and_publication_are_distinct_states(tmp_path, monkeypatch):
    harness, private_job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    progress = FakeProgressStore()
    progress.progress[private_job.job_id] = YouTubeUploadProgress(
        YouTubeUploadState.UPLOADED,
        len(output),
        len(output),
        "video-123",
    )
    transport = FakeTransport(
        [_settings(private_job.visibility.value, upload_status="uploaded")]
    )
    adapter, _ = _new_adapter(private_job, output, metadata, transport, progress)
    assert adapter.reconcile_video(private_job) == YouTubeUploadState.PROCESSING
    harness.store.close()

    public_root = tmp_path / "public"
    public_root.mkdir()
    harness, public_job, output, metadata = _job_fixture(
        public_root,
        monkeypatch,
        visibility=PublishingVisibility.PUBLIC,
    )
    progress = FakeProgressStore()
    progress.progress[public_job.job_id] = YouTubeUploadProgress(
        YouTubeUploadState.UPLOADED,
        len(output),
        len(output),
        "video-456",
    )
    transport = FakeTransport([_settings("public", video_id="video-456")])
    adapter, _ = _new_adapter(public_job, output, metadata, transport, progress)
    assert adapter.reconcile_video(public_job) == YouTubeUploadState.PUBLISHED
    assert progress.load(public_job.job_id).state == YouTubeUploadState.PUBLISHED
    harness.store.close()


def test_repeated_video_reconciliation_preserves_confirmed_remote_state(
    tmp_path,
    monkeypatch,
):
    harness, job, output, metadata = _job_fixture(
        tmp_path,
        monkeypatch,
        visibility=PublishingVisibility.PUBLIC,
    )
    progress = FakeProgressStore()
    progress.progress[job.job_id] = YouTubeUploadProgress(
        YouTubeUploadState.UPLOADED,
        len(output),
        len(output),
        "video-123",
    )
    transport = FakeTransport(
        [
            _settings("public", upload_status="uploaded"),
            _settings("public", upload_status="processed"),
        ]
    )
    adapter, _ = _new_adapter(job, output, metadata, transport, progress)

    assert adapter.reconcile_video(job) == YouTubeUploadState.PROCESSING
    assert adapter.reconcile_video(job) == YouTubeUploadState.PUBLISHED
    assert progress.load(job.job_id).state == YouTubeUploadState.PUBLISHED
    assert len(transport.calls) == 2
    harness.store.close()


@pytest.mark.parametrize(
    "response",
    [
        _response(200, headers={"Location": "http://www.googleapis.com/upload"}),
        _response(308, headers={"Range": "bytes=1-10"}),
        _response(200, body=b'{"id":""}'),
    ],
)
def test_malformed_or_inconsistent_responses_fail_closed(
    tmp_path,
    monkeypatch,
    response,
):
    harness, job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    transport = FakeTransport(
        [
            response
            if response.header("Location") is not None
            else _response(200, headers={"Location": SESSION_URI}),
            response
            if response.header("Range") is not None
            else response,
        ]
    )
    adapter, _ = _new_adapter(job, output, metadata, transport)
    with pytest.raises(YouTubeAdapterError):
        adapter.dispatch(job)
    assert all(url.startswith("https://www.googleapis.com/") for _, url, _, _ in transport.calls)
    harness.store.close()


def test_no_default_live_transport_and_metadata_or_hash_mismatch_fail_closed(
    tmp_path,
    monkeypatch,
):
    harness, job, output, metadata = _job_fixture(tmp_path, monkeypatch)
    transport = FakeTransport()
    adapter, _ = _new_adapter(job, output, {**metadata, "title": "altered"}, transport)
    with pytest.raises(YouTubeAdapterError, match="metadata_binding_mismatch"):
        adapter.dispatch(job)
    adapter, _ = _new_adapter(job, b"different bytes", metadata, transport)
    with pytest.raises(YouTubeAdapterError, match="output_binding_mismatch"):
        adapter.dispatch(job)
    assert transport.calls == []
    harness.store.close()
