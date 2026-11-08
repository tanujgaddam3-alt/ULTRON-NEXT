from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from pydantic import SecretStr

from ultron.application.content.youtube_adapter import (
    OAuthCredentialMaterial,
    SecureSecretStoreError,
    SecureSecretStoreErrorCode,
    SecureUploadStorageUnavailable,
    YouTubeAdapterError,
    YouTubeTransportError,
)
from ultron.application.content.youtube_production import (
    GOOGLE_TOKEN_ENDPOINT,
    EncryptedYouTubeUploadProgressStore,
    YOUTUBE_OAUTH_SCOPES,
    GoogleYouTubeOAuth,
    RefreshingYouTubeCredentialProvider,
    RequestsYouTubeTransport,
    SecretServiceUnavailable,
    SecretServiceSessionEncryptionKeyProvider,
    SecretToolBackend,
    YouTubeOAuthCredentialStore,
)
from ultron.application.content.store import ContentStore
from ultron.application.content.processing import LocalTemporaryContentStore
from ultron.application.content.status import create_content_runtime
from ultron.application.content.distribution import (
    ContentPublicationPreparationService,
    DryRunPlatformAdapter,
    PlatformAdapterRegistry,
)
from ultron.application.content.production import ContentMediaProductionService
from ultron.application.content.service import ContentRightsService
from ultron.application.content.youtube_adapter import YouTubeTransportResponse
from ultron.application.content.youtube_adapter import (
    YouTubeUploadProgress,
    YouTubeUploadState,
)
from ultron.application.content.youtube_production import (
    YouTubeChannelVerifier,
    YouTubeDistributionPublishingService,
)
from ultron.domain.content import (
    OpaqueSecretReference,
    PublishingAttemptIdentity,
    PublishingProvider,
    SecretGeneration,
    UploadSecretBinding,
    UploadSecretPurpose,
    UploadSessionSecretHandle,
)
from ultron.domain.content_distribution import DistributionPlatform, PublicationMode
from ultron.security.identity.authorization import AuthorizationContext, resource_fingerprint
from ultron.security.identity.authenticator import WebSocketAuthenticator
from tests.application.test_content_media_production import _DeterministicRenderer
from tests.application.test_content_package_e2e import (
    _complete_with_provider,
    _pending_package,
)


class FakeSecretService:
    def __init__(self) -> None:
        self.items: dict[str, str] = {}
        self.fail = False

    def get(self, item_id: str) -> str | None:
        if self.fail:
            raise SecretServiceUnavailable()
        return self.items.get(item_id)

    def put(self, item_id: str, secret: str) -> None:
        if self.fail:
            raise SecretServiceUnavailable()
        self.items[item_id] = secret

    def delete(self, item_id: str) -> None:
        if self.fail:
            raise SecretServiceUnavailable()
        self.items.pop(item_id, None)


class FakeOAuthTransport:
    def __init__(self, responses: list[tuple[int, dict[str, object]]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, str] | None]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 20,
    ) -> tuple[int, dict[str, str], bytes]:
        del headers, timeout_seconds
        self.calls.append((method, url, data))
        status, body = self.responses.pop(0)
        return status, {}, json.dumps(body).encode()


def _material(
    *,
    access: str = "access-token-test",
    refresh: str = "refresh-token-test",
    generation: int = 1,
    expires_at: datetime | None = None,
) -> OAuthCredentialMaterial:
    return OAuthCredentialMaterial(
        access_token=SecretStr(access),
        refresh_token=SecretStr(refresh),
        expires_at=expires_at or datetime.now(UTC) + timedelta(hours=1),
        scopes=frozenset(YOUTUBE_OAUTH_SCOPES),
        generation=SecretGeneration(value=generation),
    )


def _reject_browser(url: str) -> bool:
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    return False


def test_authorization_url_uses_loopback_pkce_and_minimal_scopes():
    service = FakeSecretService()
    store = YouTubeOAuthCredentialStore(service)
    oauth = GoogleYouTubeOAuth(
        client_id="desktop-client-id",
        credential_store=store,
        transport=FakeOAuthTransport([]),
        open_browser=_reject_browser,
    )
    redirect_uri = "http://127.0.0.1:49152/oauth2callback"
    verifier = "A" * 64

    url = oauth.authorization_url(
        redirect_uri,
        state="csrf-state",
        verifier=verifier,
    )

    query = parse_qs(urlparse(url).query)
    assert query["client_id"] == ["desktop-client-id"]
    assert query["redirect_uri"] == [redirect_uri]
    assert query["state"] == ["csrf-state"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [oauth._challenge(verifier)]
    assert set(query["scope"][0].split()) == set(YOUTUBE_OAUTH_SCOPES)
    assert query["access_type"] == ["offline"]


def test_channel_verification_sends_access_token_without_exposing_it():
    backend = FakeSecretService()
    credential_store = YouTubeOAuthCredentialStore(backend)
    credential_store.create(
        GoogleYouTubeOAuth.binding_for("owner", "channel-1"),
        _material(),
    )

    class ChannelTransport:
        def __init__(self) -> None:
            self.authorization_header = None

        def request(self, method, url, *, data=None, headers=None, timeout_seconds=20):
            del method, url, data, timeout_seconds
            self.authorization_header = headers["Authorization"]
            return 200, {}, json.dumps({"items": [{"id": "channel-1"}]}).encode()

    transport = ChannelTransport()
    credentials = RefreshingYouTubeCredentialProvider(
        owner_id="owner",
        credential_store=credential_store,
        client_id="desktop-client-id",
    )
    verifier = YouTubeChannelVerifier(
        owner_id="owner",
        credential_store=credential_store,
        credentials=credentials,
        transport=transport,
    )

    verifier.verify("channel-1")

    assert transport.authorization_header == "Bearer access-token-test"


def test_runtime_registers_production_handler_only_with_explicit_injection(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "runtime-youtube.db"
    setup_store, *_ = _pending_package(database)
    setup_store.migrate_publishing_attempt_schema()
    setup_store.migrate_private_render_operation_schema()
    setup_store.close()

    class TestCredentials:
        def access_token(self, _account_id: str) -> SecretStr:
            return SecretStr("test-access-token")

    class TestKeyProvider:
        def key(self) -> SecretStr:
            return SecretStr(
                base64.urlsafe_b64encode(b"k" * 32).decode("ascii")
            )

    class TestChannelVerifier:
        def verify(self, _account_id: str) -> None:
            return None

    class UnusedTransport:
        def request(self, *_args, **_kwargs):
            raise AssertionError("The runtime wiring test must not contact YouTube.")

    def service_factory(store, storage, owner_id):
        progress_store = EncryptedYouTubeUploadProgressStore(
            store=store,
            key_provider=TestKeyProvider(),
        )
        return YouTubeDistributionPublishingService(
            store=store,
            storage=storage,
            credentials=TestCredentials(),
            progress_store=progress_store,
            channel_verifier=TestChannelVerifier(),
            transport=UnusedTransport(),
            owner_id=owner_id,
        )

    monkeypatch.setenv("ULTRON_CRE_WORKER_ENABLED", "true")
    monkeypatch.setenv("ULTRON_CRE_WORKER_PERMISSIONS", "content.publish")
    monkeypatch.setenv("ULTRON_CRE_PUBLISHING_DRY_RUN_ENABLED", "true")
    authenticator = WebSocketAuthenticator("T" * 43, principal="operator")
    runtime = create_content_runtime(
        authenticator,
        database,
        content_storage_root=tmp_path / "managed-content",
        youtube_publishing_service_factory=service_factory,
    )
    try:
        worker = runtime.workflow_runtime
        assert worker is not None
        assert worker.enabled
        assert "publish_youtube" in worker.handlers.available()
        handler = worker.handlers.get("publish_youtube")
        assert handler.required_permission == "content.publish"
        assert handler.external_side_effect is True
        assert runtime.publication_preparation.adapters.get(
            DistributionPlatform.YOUTUBE
        ) is runtime.youtube_publishing
        assert type(
            runtime.publication_preparation.adapters.get(
                DistributionPlatform.YOUTUBE,
                dry_run=True,
            )
        ) is DryRunPlatformAdapter
        assert (
            runtime.content_storage.clip_staging_directory.parent
            == (tmp_path / "managed-content").resolve()
        )
        status = runtime.status.for_principal("operator")
        assert status["publishing"]["serviceConfigured"] is True
        assert status["publishing"]["publicDispatchEnabled"] is True
    finally:
        runtime.close()


def test_runtime_fails_closed_when_explicit_opt_in_lacks_secure_storage(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "runtime-without-secret-service.db"
    setup_store, *_ = _pending_package(database)
    setup_store.migrate_publishing_attempt_schema()
    setup_store.migrate_private_render_operation_schema()
    setup_store.close()

    class UnavailableSecretTool:
        available = False

    import ultron.application.content.youtube_production as youtube_production

    monkeypatch.setattr(
        youtube_production,
        "SecretToolBackend",
        UnavailableSecretTool,
    )
    monkeypatch.setenv("ULTRON_CRE_YOUTUBE_PUBLISHING_ENABLED", "true")
    monkeypatch.setenv(
        "ULTRON_CRE_GOOGLE_OAUTH_CLIENT_ID",
        "test-desktop-client-id",
    )
    authenticator = WebSocketAuthenticator("T" * 43, principal="operator")

    with pytest.raises(RuntimeError, match="Secret Service backend"):
        create_content_runtime(
            authenticator,
            database,
            content_storage_root=tmp_path / "managed-content",
        )


def test_authorized_worker_uploads_once_and_persists_distribution_result(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "authorized-runtime.db"
    store, opportunity, workflow, pending, angle = _pending_package(database)
    content_package = _complete_with_provider(
        store,
        opportunity,
        workflow,
        pending,
        angle,
        classification="fictional",
    )
    script = store.get_script_artifact(content_package.script_artifact_id)
    assert script is not None
    store.create_approval_decision(
        decision_id="youtube-source-approval",
        workflow_id=script.workflow_id,
        artifact_type="script",
        artifact_id=script.artifact_id,
        artifact_version=script.version,
        artifact_sha256=script.content_sha256,
        decision="approved",
        actor="script-reviewer",
    )
    managed_root = tmp_path / "managed-content"
    storage = LocalTemporaryContentStore(managed_root)
    media_service = ContentMediaProductionService(
        store,
        storage,
        _DeterministicRenderer(),
    )
    media_plan = media_service.create_plan(content_package.package_id)
    media_artifact = media_service.execute(media_plan.job_id)
    store.migrate_publishing_attempt_schema()
    store.migrate_private_render_operation_schema()
    store.close()

    class TestCredentials:
        def access_token(self, _account_id: str) -> SecretStr:
            return SecretStr("test-access-token")

    class TestKeyProvider:
        def key(self) -> SecretStr:
            return SecretStr(
                base64.urlsafe_b64encode(b"z" * 32).decode("ascii")
            )

    class TestChannelVerifier:
        def verify(self, account_id: str) -> None:
            assert account_id == "test-channel"

    class UploadTransport:
        def __init__(self) -> None:
            self.calls = []
            self.fail_first_upload = True
            self.fail_first_probe = True

        def request(
            self,
            method,
            url,
            *,
            headers,
            body,
            timeout_seconds,
        ):
            del body, timeout_seconds
            self.calls.append((method, url))
            if method == "POST":
                return YouTubeTransportResponse(
                    200,
                    {
                        "Location": (
                            "https://www.googleapis.com/upload/youtube/v3/videos"
                            "?upload_id=opaque-test-session"
                        )
                    },
                )
            if method == "PUT":
                content_range = headers["Content-Range"]
                if content_range.startswith("bytes */"):
                    if self.fail_first_probe:
                        self.fail_first_probe = False
                        raise ConnectionError("simulated lost upload response")
                    return YouTubeTransportResponse(308, {})
                if self.fail_first_upload:
                    self.fail_first_upload = False
                    raise TimeoutError("simulated upload timeout")
                return YouTubeTransportResponse(
                    200,
                    {},
                    json.dumps({"id": "test-remote-video"}).encode(),
                )
            if method == "GET":
                return YouTubeTransportResponse(
                    200,
                    {},
                    json.dumps(
                        {
                            "items": [
                                {
                                    "id": "test-remote-video",
                                    "status": {
                                        "privacyStatus": "private",
                                        "uploadStatus": "processed",
                                    },
                                }
                            ]
                        }
                    ).encode(),
                )
            raise AssertionError(f"Unexpected YouTube request method: {method}")

    transport = UploadTransport()

    def service_factory(runtime_store, runtime_storage, owner_id):
        return YouTubeDistributionPublishingService(
            store=runtime_store,
            storage=runtime_storage,
            credentials=TestCredentials(),
            progress_store=EncryptedYouTubeUploadProgressStore(
                store=runtime_store,
                key_provider=TestKeyProvider(),
            ),
            channel_verifier=TestChannelVerifier(),
            transport=transport,
            owner_id=owner_id,
        )

    monkeypatch.setenv("ULTRON_CRE_WORKER_ENABLED", "true")
    monkeypatch.setenv("ULTRON_CRE_WORKER_PERMISSIONS", "")
    monkeypatch.setenv("ULTRON_CRE_PUBLISHING_DRY_RUN_ENABLED", "true")
    token = "T" * 43
    authenticator = WebSocketAuthenticator(token, principal="operator")
    runtime = create_content_runtime(
        authenticator,
        database,
        content_storage_root=managed_root,
        youtube_publishing_service_factory=service_factory,
    )
    try:
        metadata = {
            "title": "Approved video",
            "description": "Mocked YouTube upload",
            "tags": ("offline",),
            "category": None,
            "thumbnail_reference": None,
            "subtitle_artifact_ids": tuple(
                item.artifact_id
                for item in runtime.store.list_media_subtitle_artifacts(
                    media_plan.job_id
                )
            ),
            "visibility": "private",
            "mode": PublicationMode.AUTHORIZED_PUBLISH.value,
            "scheduled_at": None,
        }
        binding = {
            "content_package_id": content_package.package_id,
            "script_artifact_id": script.artifact_id,
            "script_version": script.version,
            "script_sha256": script.content_sha256,
            "media_artifact_id": media_artifact.artifact_id,
            "media_sha256": media_artifact.output_sha256,
            "platform": DistributionPlatform.YOUTUBE.value,
            "account_id": "test-channel",
            "requested_by": "operator",
            "metadata": metadata,
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                binding,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        publication_package_id = f"publication-{fingerprint[:32]}"
        command_payload = {
            "publication_package_id": publication_package_id,
            "approval_fingerprint": fingerprint,
            "platform": DistributionPlatform.YOUTUBE.value,
            "account_id": "test-channel",
            "requested_by": "operator",
            "mode": PublicationMode.AUTHORIZED_PUBLISH.value,
        }

        def authorized_context(authority, principal_token, action, payload):
            authority.authenticate(principal_token, principal_token)
            operation_fingerprint = resource_fingerprint(action, payload)
            grant = authority.authorize(
                websocket=principal_token,
                operation={
                    "operationId": "youtube-test-operation",
                    "correlationId": "youtube-test-correlation",
                    "requestFingerprint": operation_fingerprint,
                },
                purpose="execute",
                request_correlation_id="youtube-test-request",
                action=action,
                resource_fingerprint=operation_fingerprint,
            )
            return AuthorizationContext.from_grant(grant)

        publication = runtime.publication_preparation.create_package(
            context=authorized_context(
                authenticator,
                token,
                ContentPublicationPreparationService.ACTION,
                command_payload,
            ),
            command_id="create-authorized-youtube-package",
            content_package_id=content_package.package_id,
            media_artifact_id=media_artifact.artifact_id,
            platform=DistributionPlatform.YOUTUBE,
            account_id="test-channel",
            title="Approved video",
            description="Mocked YouTube upload",
            tags=("offline",),
            mode=PublicationMode.AUTHORIZED_PUBLISH,
        )
        assert publication.publication_package_id == publication_package_id

        reviewer = WebSocketAuthenticator(token, principal="independent-reviewer")
        reviewer_rights = ContentRightsService(
            store=runtime.store,
            authority=reviewer,
            content_storage=runtime.content_storage,
        )
        reviewer_service = ContentPublicationPreparationService(
            content_rights=reviewer_rights,
            storage=runtime.content_storage,
            adapters=PlatformAdapterRegistry(),
        )
        approval_context = authorized_context(
            reviewer,
            token,
            ContentPublicationPreparationService.APPROVAL_ACTION,
            {
                "publication_package_id": publication.publication_package_id,
                "approval_fingerprint": publication.approval_fingerprint,
                "approved_by": "independent-reviewer",
            },
        )
        _approval, distribution_job = reviewer_service.approve_package(
            context=approval_context,
            command_id="approve-authorized-youtube-package",
            publication_package_id=publication.publication_package_id,
        )

        worker = runtime.workflow_runtime
        assert worker is not None
        unauthorized_task = runtime.store.create_content_workflow_task(
            task_id="youtube-unauthorized-task",
            workflow_id=workflow.workflow_id,
            task_type="publish_youtube",
            idempotency_key="youtube-unauthorized",
            input_data={"job_id": distribution_job.job_id},
            external_side_effect=True,
        )
        for _ in range(200):
            saved = runtime.store.get_content_workflow_task(unauthorized_task.task_id)
            if saved.state == "failed":
                break
            time.sleep(0.01)
        assert runtime.store.get_content_workflow_task(
            unauthorized_task.task_id
        ).error_code == "PERMISSION_DENIED"
        assert transport.calls == []

        worker.permissions = frozenset({"content.publish"})
        uncertain_task = runtime.store.create_content_workflow_task(
            task_id="youtube-authorized-task",
            workflow_id=workflow.workflow_id,
            task_type="publish_youtube",
            idempotency_key="youtube-authorized",
            input_data={"job_id": distribution_job.job_id},
            external_side_effect=True,
        )
        for _ in range(200):
            saved = runtime.store.get_content_workflow_task(uncertain_task.task_id)
            if saved.state == "failed":
                break
            time.sleep(0.01)
        assert runtime.store.get_content_workflow_task(
            uncertain_task.task_id
        ).state == "failed"
        assert runtime.store.get_distribution_job(
            distribution_job.job_id
        ).state.value == "uncertain"
        assert runtime.store.list_distribution_job_events(
            distribution_job.job_id
        )[-1]["to_state"] == "uncertain"
        assert len([call for call in transport.calls if call[0] == "POST"]) == 1

        resume_task = runtime.store.create_content_workflow_task(
            task_id="youtube-resume-task",
            workflow_id=workflow.workflow_id,
            task_type="publish_youtube",
            idempotency_key="youtube-resume",
            input_data={"job_id": distribution_job.job_id},
            external_side_effect=True,
        )
        for _ in range(200):
            saved = runtime.store.get_content_workflow_task(resume_task.task_id)
            if saved.state == "completed":
                break
            time.sleep(0.01)
        assert runtime.store.get_content_workflow_task(resume_task.task_id).state == (
            "completed"
        )
        record = runtime.store.get_distribution_record_for_job(distribution_job.job_id)
        assert record is not None
        assert record.remote_publication_id == "test-remote-video"
        assert record.media_sha256 == media_artifact.output_sha256
        assert runtime.store.get_distribution_job(
            distribution_job.job_id
        ).state.value == "published"
        call_count = len(transport.calls)
        runtime.youtube_publishing.clock = lambda: datetime.now(UTC) + timedelta(days=2)
        duplicate = runtime.youtube_publishing.publish_for_worker(distribution_job.job_id)
        assert duplicate.confirmed is True
        assert len(transport.calls) == call_count
        assert len([call for call in transport.calls if call[0] == "POST"]) == 1
    finally:
        runtime.close()


@pytest.mark.parametrize(
    ("request_path", "host_header", "expected_host", "expected_state"),
    [
        ("/oauth2callback?state=bad&code=ok", "127.0.0.1:8080", "127.0.0.1:8080", "good"),
        ("/oauth2callback?state=good&code=ok", "localhost:8080", "127.0.0.1:8080", "good"),
        ("/elsewhere?state=good&code=ok", "127.0.0.1:8080", "127.0.0.1:8080", "good"),
    ],
)
def test_callback_rejects_state_host_or_path_mismatch(
    request_path: str,
    host_header: str,
    expected_host: str,
    expected_state: str,
):
    with pytest.raises(YouTubeAdapterError, match="oauth_callback_rejected"):
        GoogleYouTubeOAuth.parse_callback(
            request_path=request_path,
            host_header=host_header,
            expected_host=expected_host,
            expected_state=expected_state,
        )


def test_callback_accepts_matching_state_and_exact_loopback_host():
    assert GoogleYouTubeOAuth.parse_callback(
        request_path="/oauth2callback?state=state-123&code=authorization-code",
        host_header="127.0.0.1:49152",
        expected_host="127.0.0.1:49152",
        expected_state="state-123",
    ) == "authorization-code"


def test_callback_rejects_malformed_query_without_leaking_callback_values():
    with pytest.raises(YouTubeAdapterError, match="oauth_callback_invalid") as raised:
        GoogleYouTubeOAuth.parse_callback(
            request_path="/oauth2callback?malformed",
            host_header="127.0.0.1:49152",
            expected_host="127.0.0.1:49152",
            expected_state="state-123",
        )
    assert "malformed" not in str(raised.value)


def test_authorization_url_rejects_redirect_uri_mismatch():
    oauth = GoogleYouTubeOAuth(
        client_id="desktop-client-id",
        credential_store=YouTubeOAuthCredentialStore(FakeSecretService()),
        transport=FakeOAuthTransport([]),
        open_browser=_reject_browser,
    )

    with pytest.raises(ValueError, match="exact loopback"):
        oauth.authorization_url(
            "http://localhost:49152/oauth2callback",
            state="state",
            verifier="B" * 64,
        )


def test_oauth_credentials_are_bound_replaced_and_revoked_in_secret_service():
    backend = FakeSecretService()
    store = YouTubeOAuthCredentialStore(backend)
    binding = GoogleYouTubeOAuth.binding_for("owner-1", "channel-1")
    original = _material()

    store.create(binding, original)
    restored = store.retrieve(binding)
    assert restored.access_token == original.access_token
    assert restored.refresh_token == original.refresh_token
    assert "access-token-test" in next(iter(backend.items.values()))

    replacement = _material(
        access="refreshed-access",
        refresh="refresh-token-test",
        generation=2,
    )
    assert store.replace(
        binding,
        expected_generation=SecretGeneration(value=1),
        credentials=replacement,
    ) == SecretGeneration(value=2)
    assert store.retrieve(binding).access_token.get_secret_value() == "refreshed-access"

    store.delete(
        binding,
        expected_generation=SecretGeneration(value=2),
    )
    assert not backend.items


def test_oauth_credential_store_fails_closed_when_secret_service_is_unavailable():
    backend = FakeSecretService()
    backend.fail = True
    store = YouTubeOAuthCredentialStore(backend)
    binding = GoogleYouTubeOAuth.binding_for("owner-1", "channel-1")

    with pytest.raises(SecureSecretStoreError) as raised:
        store.retrieve(binding)
    assert raised.value.code == SecureSecretStoreErrorCode.UNAVAILABLE


def test_secret_tool_backend_is_unavailable_without_a_supported_client():
    backend = SecretToolBackend(executable="/nonexistent/ultron-test-secret-tool")
    with pytest.raises(SecretServiceUnavailable, match="secure_credential_storage_unavailable"):
        backend.get("oauth-credential:test")


def test_refresh_rotates_access_token_without_losing_refresh_token():
    backend = FakeSecretService()
    store = YouTubeOAuthCredentialStore(backend)
    binding = GoogleYouTubeOAuth.binding_for("owner-1", "channel-1")
    store.create(
        binding,
        _material(
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        ),
    )
    transport = FakeOAuthTransport(
        [
            (
                200,
                {
                    "access_token": "new-access-token",
                    "expires_in": 3600,
                    "scope": " ".join(YOUTUBE_OAUTH_SCOPES),
                },
            )
        ]
    )
    provider = RefreshingYouTubeCredentialProvider(
        owner_id="owner-1",
        credential_store=store,
        transport=transport,
        client_id="desktop-client-id",
    )

    access_token = provider.access_token("channel-1")

    assert access_token == SecretStr("new-access-token")
    assert transport.calls[0][1] == GOOGLE_TOKEN_ENDPOINT
    assert transport.calls[0][2]["grant_type"] == "refresh_token"
    refreshed = store.retrieve(binding)
    assert refreshed.refresh_token == SecretStr("refresh-token-test")
    assert refreshed.generation == SecretGeneration(value=2)


def test_revocation_removes_local_credential_only_after_remote_success():
    backend = FakeSecretService()
    store = YouTubeOAuthCredentialStore(backend)
    binding = GoogleYouTubeOAuth.binding_for("owner-1", "channel-1")
    store.create(binding, _material())
    provider = RefreshingYouTubeCredentialProvider(
        owner_id="owner-1",
        credential_store=store,
        transport=FakeOAuthTransport([(200, {})]),
        client_id="desktop-client-id",
    )

    provider.revoke("channel-1")

    with pytest.raises(SecureSecretStoreError) as raised:
        store.retrieve(binding)
    assert raised.value.code == SecureSecretStoreErrorCode.NOT_FOUND


class FakeResponse:
    status_code = 308
    headers = {"Range": "bytes=0-5", "Authorization": "must-not-return"}

    def iter_content(self, chunk_size: int):
        assert chunk_size == 65536
        yield b""

    def close(self) -> None:
        self.closed = True


class FakeRequestsSession:
    def __init__(self) -> None:
        self.calls = []
        self.response = FakeResponse()

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response


def test_youtube_transport_disables_redirects_and_filters_response_headers():
    session = FakeRequestsSession()
    transport = RequestsYouTubeTransport(session=session)

    response = transport.request(
        "PUT",
        "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=test",
        headers={"Authorization": "Bearer do-not-log"},
        body=b"video-chunk",
        timeout_seconds=5,
    )

    assert response.status_code == 308
    assert response.header("Range") == "bytes=0-5"
    assert response.header("Authorization") is None
    assert session.calls[0][2]["allow_redirects"] is False
    assert session.calls[0][2]["stream"] is True
    assert session.response.closed is True


def test_youtube_transport_rejects_non_google_endpoints():
    session = FakeRequestsSession()
    transport = RequestsYouTubeTransport(session=session)

    with pytest.raises(YouTubeTransportError, match="youtube_endpoint_rejected"):
        transport.request(
            "PUT",
            "https://attacker.example/upload",
            headers={},
            body=b"",
            timeout_seconds=5,
        )
    assert not session.calls


def _upload_handle() -> UploadSessionSecretHandle:
    identity = PublishingAttemptIdentity(
        attempt_id=str(uuid4()),
        attempt_number=1,
        owner_id="owner-1",
        job_id="youtube-job-1",
        account_id="channel-1",
        provider=PublishingProvider.YOUTUBE,
        release_identity="a" * 64,
        package_id="package-1",
        package_version=1,
        output_sha256="b" * 64,
    )
    return UploadSessionSecretHandle(
        binding=UploadSecretBinding(
            attempt=identity,
            purpose=UploadSecretPurpose.RESUMABLE_UPLOAD_SESSION,
            schema_version=1,
        ),
        reference=OpaqueSecretReference(
            reference_id=str(uuid4()),
            schema_version=1,
        ),
        generation=SecretGeneration(value=1),
    )


def test_encrypted_upload_session_survives_content_store_reopen(tmp_path):
    database = tmp_path / "youtube-sessions.db"
    backend = FakeSecretService()
    backend.items[
        SecretServiceSessionEncryptionKeyProvider.ITEM_ID
    ] = base64.urlsafe_b64encode(b"K" * 32).decode()
    key_provider = SecretServiceSessionEncryptionKeyProvider(backend)
    handle = _upload_handle()
    store = ContentStore(database)
    progress_store = EncryptedYouTubeUploadProgressStore(
        store=store,
        key_provider=key_provider,
    )
    progress_store.save(
        "youtube-job-1",
        YouTubeUploadProgress(
            state=YouTubeUploadState.UPLOADING,
            byte_offset=0,
            total_bytes=1024,
        ),
    )
    session_uri = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=secret-value"
    progress_store.store_session_uri(handle, SecretStr(session_uri))
    progress_store.replace_session_uri(
        handle,
        SecretStr(
            "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=replaced-secret"
        ),
    )
    store.close()

    raw_connection = sqlite3.connect(database)
    try:
        row = raw_connection.execute(
            "SELECT encrypted_session_uri FROM content_youtube_upload_sessions"
        ).fetchone()
        assert row is not None
        assert b"secret-value" not in row[0]
        assert b"replaced-secret" not in row[0]
    finally:
        raw_connection.close()

    recovered_store = ContentStore(database)
    recovered = EncryptedYouTubeUploadProgressStore(
        store=recovered_store,
        key_provider=key_provider,
    )
    recovered_handle = UploadSessionSecretHandle(
        binding=handle.binding,
        reference=handle.reference,
        generation=SecretGeneration(value=2),
    )
    assert recovered.load("youtube-job-1") == YouTubeUploadProgress(
        state=YouTubeUploadState.UPLOADING,
        byte_offset=0,
        total_bytes=1024,
    )
    restored_uri = recovered.retrieve_session_uri(recovered_handle)
    assert restored_uri == SecretStr(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=replaced-secret"
    )
    recovered.invalidate_session_uri(recovered_handle)
    assert recovered.retrieve_session_uri(recovered_handle) is None
    recovered_store.close()


def test_upload_session_storage_fails_closed_without_key(tmp_path):
    store = ContentStore(tmp_path / "missing-key.db")
    progress_store = EncryptedYouTubeUploadProgressStore(
        store=store,
        key_provider=SecretServiceSessionEncryptionKeyProvider(FakeSecretService()),
    )

    with pytest.raises(
        SecureUploadStorageUnavailable,
        match="secure_session_key_not_provisioned",
    ):
        progress_store.store_session_uri(
            _upload_handle(),
            SecretStr(
                "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=secret"
            ),
        )
    assert store.get_youtube_upload_progress("youtube-job-1") is None
    store.close()


def test_upload_session_expiry_invalidates_existing_session(tmp_path):
    database = tmp_path / "expired-session.db"
    backend = FakeSecretService()
    backend.items[
        SecretServiceSessionEncryptionKeyProvider.ITEM_ID
    ] = base64.urlsafe_b64encode(b"E" * 32).decode()
    current_time = [datetime(2026, 1, 1, tzinfo=UTC)]
    store = ContentStore(database)
    progress_store = EncryptedYouTubeUploadProgressStore(
        store=store,
        key_provider=SecretServiceSessionEncryptionKeyProvider(backend),
        clock=lambda: current_time[0],
    )
    handle = _upload_handle()
    progress_store.store_session_uri(
        handle,
        SecretStr(
            "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=expires"
        ),
    )

    current_time[0] += timedelta(days=8)
    assert progress_store.retrieve_session_uri(handle) is None
    row = store.get_youtube_upload_session(handle.reference.reference_id)
    assert row is not None and row["invalidated"] == 1
    store.close()
