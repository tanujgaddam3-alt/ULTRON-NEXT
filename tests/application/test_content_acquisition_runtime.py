from __future__ import annotations

import json
import hashlib
import os
import shutil
import sqlite3
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from tests.application.acquisition_test_utils import AcquisitionHarness
from ultron.application.content.acquisition import ContentAcquisitionService
from ultron.application.content.acquisition_config import AcquisitionOwnerScope
from ultron.application.content.evidence_trust_policy import (
    EvidenceTrustPolicy,
    EvidenceTrustPolicyStore,
    RiskOperation,
)
from ultron.application.content.nasa import (
    NASAImagesAcquisitionAdapter,
    NASAVideoCandidate,
    NASAVideoFile,
    register_nasa_trusted_issuer,
)
from ultron.application.content.processing import LocalTemporaryContentStore
from ultron.application.content.rights_verification import (
    FailClosedRightsVerifier,
    TrustedIssuerRegistry,
)
from ultron.application.content.service import ContentRightsService
from ultron.application.content.sources import SourceDocument
from ultron.application.content.status import create_content_runtime
from ultron.domain.content_acquisition import (
    AcquisitionIntendedUse,
    AcquisitionRequestState,
)
from ultron.domain.content_identity import url_source_identity
from ultron.domain.content import (
    ContentTargetContext,
    RightsAction,
    RightsEvaluation,
    RightsState,
    RightsUse,
)
from ultron.security.identity.authenticator import WebSocketAuthenticator


def _policy() -> EvidenceTrustPolicy:
    return EvidenceTrustPolicy(
        policy_id="test-editorial-policy",
        accepted_issuer_categories=frozenset({"government_media_usage_authority"}),
        accepted_license_types=frozenset({"NASA_OFFICIAL_USAGE_GUIDANCE"}),
        accepted_license_versions=frozenset({"official-guidance-current"}),
        permitted_intended_actions=frozenset(
            {RightsAction.EDIT, RightsAction.CREATE_DERIVATIVE}
        ),
        require_commercial_permission=False,
        require_derivative_permission=True,
        attribution_requirements=("NASA",),
        territories=("US",),
        max_asset_duration_seconds=1800,
        reject_third_party_content=True,
        evidence_max_age=timedelta(days=1),
        require_unrevoked_evidence=True,
        manual_intervention_conditions=(),
        accepted_assurance_thresholds=None,
        source_integrity_required=True,
        residual_risk_allowed=frozenset(),
    )


def _private_file(path: Path) -> None:
    os.chmod(path, 0o600)


def _write_configuration(directory: Path, principal: str = "alice") -> Path:
    policy_path = directory / "policy.json"
    EvidenceTrustPolicyStore(policy_path).configure(_policy())
    _private_file(policy_path)

    registry_path = directory / "issuers.json"
    registry = TrustedIssuerRegistry(persistence_path=registry_path)
    register_nasa_trusted_issuer(registry)
    _private_file(registry_path)

    config_path = directory / "acquisition.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "owner_scopes": [
                    {
                        "principal": principal,
                        "owner_id": principal,
                        "target_context": "youtube_shorts",
                        "account_id": "channel-test-001",
                        "territory": "US",
                    }
                ],
                "evidence_trust_policy_path": str(policy_path),
                "trusted_issuer_registry_path": str(registry_path),
            }
        ),
        encoding="utf-8",
    )
    _private_file(config_path)
    return config_path


def test_acquisition_configuration_requires_opt_in_and_explicit_private_files(
    tmp_path,
    monkeypatch,
):
    from ultron.application.content.acquisition_config import (
        ContentAcquisitionConfiguration,
    )

    authenticator = WebSocketAuthenticator("A" * 43, "alice")
    monkeypatch.setenv("ULTRON_CRE_ACQUISITION_ENABLED", "true")
    monkeypatch.delenv("ULTRON_CRE_ACQUISITION_CONFIG", raising=False)
    with pytest.raises(RuntimeError, match="requires ULTRON_CRE_ACQUISITION_CONFIG"):
        ContentAcquisitionConfiguration.from_environment(authenticator)

    config_path = _write_configuration(tmp_path)
    monkeypatch.setenv("ULTRON_CRE_ACQUISITION_CONFIG", str(config_path))
    monkeypatch.setenv("ULTRON_INTERFACE_AUTH_PRINCIPAL", "alice")
    configuration = ContentAcquisitionConfiguration.from_environment(authenticator)
    assert configuration is not None
    assert configuration.owner_scopes["alice"].owner_id == "alice"
    assert configuration.owner_scopes["alice"].account_id == "channel-test-001"
    assert configuration.evidence_trust_policy.policy_id == "test-editorial-policy"
    assert configuration.trusted_issuer_registry.get("nasa-official-media") is not None


def test_acquisition_configuration_rejects_unprotected_or_unmapped_identity(
    tmp_path,
    monkeypatch,
):
    from ultron.application.content.acquisition_config import (
        ContentAcquisitionConfiguration,
    )

    monkeypatch.setenv("ULTRON_CRE_ACQUISITION_ENABLED", "true")
    auth = WebSocketAuthenticator("A" * 43, "alice")
    config_path = _write_configuration(tmp_path, principal="bob")
    monkeypatch.setenv("ULTRON_CRE_ACQUISITION_CONFIG", str(config_path))
    monkeypatch.setenv("ULTRON_INTERFACE_AUTH_PRINCIPAL", "alice")
    with pytest.raises(RuntimeError, match="owner mapping is invalid"):
        ContentAcquisitionConfiguration.from_environment(auth)

    config_path = _write_configuration(tmp_path / "principal", principal="alice")
    monkeypatch.setenv("ULTRON_CRE_ACQUISITION_CONFIG", str(config_path))
    monkeypatch.delenv("ULTRON_INTERFACE_AUTH_PRINCIPAL", raising=False)
    with pytest.raises(RuntimeError, match="explicitly configured authenticated principal"):
        ContentAcquisitionConfiguration.from_environment(auth)

    config_path = _write_configuration(tmp_path / "private", principal="alice")
    os.chmod(config_path, 0o644)
    monkeypatch.setenv("ULTRON_CRE_ACQUISITION_CONFIG", str(config_path))
    monkeypatch.setenv("ULTRON_INTERFACE_AUTH_PRINCIPAL", "alice")
    with pytest.raises(RuntimeError, match="mode 0600"):
        ContentAcquisitionConfiguration.from_environment(auth)

    os.chmod(config_path, 0o400)
    with pytest.raises(RuntimeError, match="mode 0600"):
        ContentAcquisitionConfiguration.from_environment(auth)


def test_acquisition_rechecks_active_principal_before_provider_dispatch(tmp_path):
    harness = AcquisitionHarness(tmp_path / "revoked-session.sqlite")
    try:
        opportunity = harness.create_scored_opportunity()
        source_id = harness.store.list_content_evidence(
            opportunity.opportunity_id
        )[0].source_id
        intended_use = AcquisitionIntendedUse.EDITORIAL_DEVELOPMENT
        idempotency_key = "revoked-before-dispatch"
        request_payload = {
            "opportunity_id": opportunity.opportunity_id,
            "source_id": source_id,
            "intended_use": intended_use.value,
            "idempotency_key": idempotency_key,
        }
        context = harness.context(
            "alice",
            "content_request_acquisition",
            request_payload,
        )
        request = harness.acquisition.select_opportunity(
            opportunity_id=opportunity.opportunity_id,
            source_id=source_id,
            intended_use=intended_use,
            idempotency_key=idempotency_key,
            context=context,
        )
        harness.authority.revoke(harness.sockets["alice"])
        with pytest.raises(PermissionError, match="valid active-session"):
            harness.acquisition._validate_request_context(context, request)
    finally:
        harness.close()


def test_policy_configured_selection_persists_request_before_rights_investigation(
    tmp_path,
):
    harness = AcquisitionHarness(tmp_path / "rights-pending-request.sqlite")
    try:
        harness.acquisition.evidence_trust_policy = _policy()
        opportunity = harness.create_scored_opportunity()
        harness.select(opportunity)
        request = harness.store.get_content_acquisition_request_for_opportunity(
            opportunity.opportunity_id
        )
        assert request is not None
        assert request.state.value == "awaiting_adapter"
        assert request.asset_id is None
    finally:
        harness.close()


def test_runtime_injects_one_shared_store_and_configured_acquisition_services(
    tmp_path,
    monkeypatch,
):
    config_path = _write_configuration(tmp_path / "config", principal="alice")
    monkeypatch.setenv("ULTRON_CRE_ACQUISITION_ENABLED", "true")
    monkeypatch.setenv("ULTRON_CRE_ACQUISITION_CONFIG", str(config_path))
    monkeypatch.setenv("ULTRON_INTERFACE_AUTH_PRINCIPAL", "alice")
    auth = WebSocketAuthenticator("A" * 43, "alice")
    runtime = create_content_runtime(
        auth,
        database_path=tmp_path / "disposable-cre.sqlite",
        content_storage_root=tmp_path / "storage",
    )
    try:
        assert runtime.discovery is not None
        assert runtime.discovery.store is runtime.store
        assert runtime.acquisition is not None
        assert runtime.acquisition.store is runtime.store
        assert runtime.acquisition.authority is auth
        assert runtime.acquisition.content_rights is runtime.rights
        assert runtime.acquisition.evidence_trust_policy is runtime.evidence_trust_policy
        assert runtime.acquisition.rights_verifier is runtime.rights_verifier
        assert runtime.acquisition.owner_scopes["alice"].account_id == "channel-test-001"
        assert set(runtime.acquisition.provider_adapters) == {
            "internet_archive",
            "wikimedia_commons",
            "nasa",
        }
        assert all(
            runtime.discovery.provider_adapters[name]
            is runtime.acquisition.provider_adapters[name]
            for name in runtime.acquisition.provider_adapters
        )
    finally:
        runtime.close()


def test_authorized_provider_download_registers_asset_and_persists_provenance(
    tmp_path,
    monkeypatch,
):
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("FFmpeg and ffprobe are required for media validation.")
    video_path = tmp_path / "source.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x120:rate=5:duration=1",
            "-c:v",
            "mpeg4",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(video_path),
        ],
        check=True,
        timeout=30,
    )
    video_bytes = video_path.read_bytes()
    candidate = NASAVideoCandidate(
        nasa_id="GSFC_20261108_TEST",
        title="NASA scientific visualization",
        center="GSFC",
        description="A NASA scientific visualization.",
        metadata_url="https://images-api.nasa.gov/search?nasa_id=GSFC_20261108_TEST",
        canonical_url="https://images-api.nasa.gov/search?nasa_id=GSFC_20261108_TEST",
        asset_url="https://images-assets.nasa.gov/video/GSFC_20261108_TEST/source~large.mp4",
        copyright_notice=None,
        photographer="NASA",
    )
    metadata = {
        "AVAIL:NASAID": candidate.nasa_id,
        "AVAIL:Owner": "NASA",
        "AVAIL:Center": "GSFC",
        "AVAIL:Description": candidate.description,
        "QuickTime:Copyright": (
            "For copyright and restrictions refer to "
            "http://www.nasa.gov/multimedia/guidelines/index.html"
        ),
    }

    harness = AcquisitionHarness(tmp_path / "pipeline.sqlite")
    try:
        registry_path = tmp_path / "runtime-issuers.json"
        registry = TrustedIssuerRegistry(persistence_path=registry_path)
        register_nasa_trusted_issuer(registry)
        trusted = FailClosedRightsVerifier(issuer_registry=registry)
        rights = ContentRightsService(
            store=harness.store,
            authority=harness.authority,
            content_storage=LocalTemporaryContentStore(tmp_path / "pipeline-storage"),
        )
        base_adapter = NASAImagesAcquisitionAdapter(issuer_registry=registry)

        class FixtureNASAAdapter:
            max_duration_seconds = 1800.0

            def search(self, query, *, page_size):
                return (candidate,)

            def fetch_metadata(self, found):
                assert found.nasa_id == candidate.nasa_id
                digest = hashlib.sha256(b"metadata").hexdigest()
                return metadata, {
                    "manifest": {"collection": {"items": []}},
                    "metadata_sha256": digest,
                }

            def verify_candidate(self, found, found_metadata, metadata_record):
                return base_adapter.verify_candidate(
                    found,
                    found_metadata,
                    metadata_record,
                )

            def select_video_file(self, found, metadata_record):
                return NASAVideoFile(
                    name="source.mp4",
                    size=len(video_bytes),
                    download_url=found.asset_url,
                )

            def download(self, video, *, destination):
                destination.write_bytes(video_bytes)
                probe = subprocess.run(
                    [
                        ffprobe,
                        "-v",
                        "error",
                        "-show_entries",
                        "format=duration:stream=codec_type",
                        "-of",
                        "json",
                        str(destination),
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=30,
                )
                probe_data = json.loads(probe.stdout)
                return {
                    "sha256": hashlib.sha256(video_bytes).hexdigest(),
                    "bytes": len(video_bytes),
                    "duration_seconds": float(probe_data["format"]["duration"]),
                    "ffprobe": probe_data,
                }

        url = candidate.canonical_url
        opportunity = harness.discovery.discover(
            topic="NASA scientific visualization",
            niche="science",
            target_audience="learners",
            target_language="en",
            content_format="short",
            source_documents=(
                SourceDocument(
                    canonical_url=url,
                    title=candidate.title,
                    summary=candidate.description,
                    published_at=None,
                    publisher="NASA",
                    source_identity=url_source_identity("nasa", url),
                    provider="nasa",
                    provider_content_id=candidate.nasa_id,
                    original_url=url,
                    resolved_url=url,
                ),
            ),
        )
        researched, _ = harness.discovery.research(
            opportunity,
            summary="Persisted provider source evidence.",
        )
        harness.discovery.persist_score(researched)
        source_id = harness.store.list_content_evidence(
            opportunity.opportunity_id
        )[0].source_id
        selection_payload = {
            "opportunity_id": opportunity.opportunity_id,
            "source_id": source_id,
            "intended_use": AcquisitionIntendedUse.EDITORIAL_DEVELOPMENT.value,
            "idempotency_key": "nasa-pipeline-request",
        }
        selection_context = harness.context(
            "alice",
            "content_request_acquisition",
            selection_payload,
        )
        request = harness.acquisition.select_opportunity(
            opportunity_id=opportunity.opportunity_id,
            source_id=source_id,
            intended_use=AcquisitionIntendedUse.EDITORIAL_DEVELOPMENT,
            idempotency_key="nasa-pipeline-request",
            context=selection_context,
        )
        acquisition = ContentAcquisitionService(
            store=harness.store,
            authority=harness.authority,
            evidence_trust_policy=_policy(),
            provider_adapters={"nasa": FixtureNASAAdapter()},
            rights_verifier=trusted,
            content_rights=rights,
            content_storage=rights.content_storage,
            owner_scopes={
                "alice": AcquisitionOwnerScope(
                    principal="alice",
                    owner_id="alice",
                    target_context=ContentTargetContext.YOUTUBE_SHORTS,
                    account_id="channel-test-001",
                    territory="US",
                )
            },
        )

        def registration_context(action, parameters):
            return harness.context("alice", action, parameters)

        original_bind = harness.store.bind_content_acquisition_asset_in_transaction

        def fail_registration(*_args, **_kwargs):
            raise RuntimeError("disposable registration failure")

        monkeypatch.setattr(
            harness.store,
            "bind_content_acquisition_asset_in_transaction",
            fail_registration,
        )
        with pytest.raises(RuntimeError, match="disposable registration failure"):
            acquisition.acquire_selected_request(
                request.request_id,
                context=selection_context,
                registration_context_factory=registration_context,
            )
        failed_request = harness.store.get_content_acquisition_request(
            request.request_id
        )
        assert failed_request.state.value == "failed"
        assert failed_request.asset_id is None
        assert harness.store._connection.execute(
            "SELECT COUNT(*) FROM content_assets"
        ).fetchone()[0] == 0
        assert tuple(
            (tmp_path / "pipeline-storage" / "inputs").iterdir()
        ) == ()
        assert not list(
            (tmp_path / "pipeline-storage" / ".staging").glob("acquisition-*")
        )
        monkeypatch.setattr(
            harness.store,
            "bind_content_acquisition_asset_in_transaction",
            original_bind,
        )
        stored_request, stored_asset = acquisition.acquire_selected_request(
            failed_request.request_id,
            context=selection_context,
            registration_context_factory=registration_context,
        )
        assert stored_request.asset_id == stored_asset.asset_id
        assert stored_request.state.value == "awaiting_rights"
        assert stored_request.rights_status.value == "pending_asset_evaluation"
        assert stored_request.rights_decision_id is not None
        assert stored_asset.content_sha256 == hashlib.sha256(video_bytes).hexdigest()
        assert stored_asset.provenance.source_identity == url_source_identity("nasa", url)
        assert stored_asset.provenance.provider_content_id == candidate.nasa_id
        assert rights.store.get_asset(stored_asset.asset_id) == stored_asset
        persisted_asset = harness.store._connection.execute(
            """
            SELECT content_sha256, provenance_json
            FROM content_assets WHERE asset_id = ?
            """,
            (stored_asset.asset_id,),
        ).fetchone()
        persisted_provenance = json.loads(persisted_asset["provenance_json"])
        assert persisted_asset["content_sha256"] == stored_asset.content_sha256
        assert persisted_provenance["source_identity"] == url_source_identity("nasa", url)
        assert persisted_provenance["provider_content_id"] == candidate.nasa_id
        assert persisted_provenance["opportunity_id"] == opportunity.opportunity_id
        assert harness.store._connection.execute(
            """
            SELECT asset_id FROM content_acquisition_requests
            WHERE request_id = ?
            """,
            (request.request_id,),
        ).fetchone()["asset_id"] == stored_asset.asset_id
        rights_row = harness.store._connection.execute(
            "SELECT result_json FROM rights_decisions WHERE decision_id = ?",
            (stored_request.rights_decision_id,),
        ).fetchone()
        platform_decision = json.loads(rights_row["result_json"])
        assert platform_decision["copyright_state"] != RightsState.CLEARED.value
        assert platform_decision["platform_rights_state"] != RightsState.CLEARED.value
        evidence_assessments = (
            harness.store.list_content_acquisition_evidence_assessments(
                request.request_id
            )
        )
        assert len(evidence_assessments) == 4
        assert all(
            item["provider"] == "nasa"
            and item["source_identity"] == url_source_identity("nasa", url)
            and item["work_identity"] == f"nasa:{candidate.nasa_id}"
            and item["policy_id"] == "test-editorial-policy"
            and item["intended_use"] == "editorial_development"
            for item in evidence_assessments
        )
        assert all(item["evidence"] for item in evidence_assessments)
        assert harness.store._connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert harness.store._connection.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError, match="completed acquisition requires"):
            harness.store.transition_content_acquisition_request(
                stored_request.request_id,
                expected_version=stored_request.version,
                target_state=AcquisitionRequestState.COMPLETED,
                actor="test:invalid_transition",
                reason="must_not_complete_before_rights",
            )
        assert harness.store.get_content_acquisition_request(
            stored_request.request_id
        ).state.value == "awaiting_rights"

        rights_row = harness.store._connection.execute(
            "SELECT use_json FROM rights_decisions WHERE decision_id = ?",
            (stored_request.rights_decision_id,),
        ).fetchone()
        acquisition_use = RightsUse.model_validate_json(rights_row["use_json"])

        def clear_disposable_rights(**values):
            return RightsEvaluation(
                decision_id="disposable-cleared-rights-decision",
                asset_id=values["asset"].asset_id,
                use=values["use"],
                copyright_state=RightsState.CLEARED,
                platform_rights_state=RightsState.CLEARED,
                policy_version="disposable-test-policy",
            )

        monkeypatch.setattr(rights.policy, "evaluate", clear_disposable_rights)
        rights_context = harness.context(
            "alice",
            "content_evaluate_rights",
            acquisition_use.model_dump(mode="json"),
        )
        cleared = rights.evaluate_rights(
            acquisition_use,
            context=rights_context,
            command_id="disposable-rights-clear",
        )
        completed_request = harness.store.get_content_acquisition_request(
            stored_request.request_id
        )
        assert cleared.copyright_state is RightsState.CLEARED
        assert completed_request.state is AcquisitionRequestState.COMPLETED
        assert completed_request.rights_status.value == "cleared"
        assert completed_request.rights_decision_id == cleared.decision_id
        with pytest.raises(
            sqlite3.IntegrityError,
            match="reopened acquisition requires a new pending rights decision",
        ):
            harness.store.transition_content_acquisition_request(
                completed_request.request_id,
                expected_version=completed_request.version,
                target_state=AcquisitionRequestState.AWAITING_RIGHTS,
                actor="test:invalid_reopen",
                reason="must_not_reopen_without_new_decision",
            )

        def pending_disposable_rights(**values):
            return RightsEvaluation(
                decision_id="disposable-pending-rights-decision",
                asset_id=values["asset"].asset_id,
                use=values["use"],
                copyright_state=RightsState.UNKNOWN,
                platform_rights_state=RightsState.UNKNOWN,
                policy_version="disposable-test-policy",
            )

        monkeypatch.setattr(rights.policy, "evaluate", pending_disposable_rights)
        reevaluation_context = harness.context(
            "alice",
            "content_evaluate_rights",
            acquisition_use.model_dump(mode="json"),
        )
        rights.evaluate_rights(
            acquisition_use,
            context=reevaluation_context,
            command_id="disposable-rights-pending",
        )
        reopened_request = harness.store.get_content_acquisition_request(
            stored_request.request_id
        )
        assert reopened_request.state is AcquisitionRequestState.AWAITING_RIGHTS
        assert reopened_request.rights_status.value == "pending_asset_evaluation"
        events = harness.store.list_content_acquisition_request_events(
            request.request_id
        )
        assert [event.to_state.value for event in events][-8:] == [
            "validating",
            "awaiting_adapter",
            "failed",
            "validating",
            "awaiting_adapter",
            "awaiting_rights",
            "completed",
            "awaiting_rights",
        ]
        assert not list((tmp_path / "pipeline-storage" / ".staging").glob("acquisition-*"))
    finally:
        harness.close()
