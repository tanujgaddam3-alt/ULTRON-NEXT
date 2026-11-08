from datetime import UTC, datetime, timedelta
from dataclasses import replace
import json
import multiprocessing
import secrets
import pytest

from ultron.application.content.discovery import ContentDiscoveryService
from ultron.application.content.store import ContentStore
from ultron.application.content.strategy import (
    ContentMetadataService,
    ContentPackageOrchestrator,
    ContentQualityEvaluator,
    ThumbnailBriefService,
)
from ultron.domain.content_strategy import QualityEvaluation
from ultron.security.identity.authorization import AuthorizationContext, resource_fingerprint
from ultron.security.identity.authenticator import WebSocketAuthenticator
from tests.application.test_content_discovery import _document
from tests.application.acquisition_test_utils import AcquisitionHarness


class _FailAfterStage(ContentPackageOrchestrator):
    def __init__(self, store, target_stage):
        super().__init__(store)
        self.target_stage = target_stage
        self.injected = False

    def _after_stage_persisted(self, stage):
        if stage.name == self.target_stage and not self.injected:
            self.injected = True
            raise RuntimeError(f"injected interruption after {stage.name}")


def _package_fixture(database):
    store = ContentStore(database)
    discovery = ContentDiscoveryService(store)
    opportunity = discovery.discover(
        topic="Recovery package",
        niche="technology",
        target_audience="builders",
        target_language="en",
        content_format="short",
        source_documents=(_document(),),
    )
    discovery.research(opportunity, summary="Evidence-backed report.")
    opportunity = store.get_content_opportunity(opportunity.opportunity_id)
    discovery.persist_score(opportunity)
    opportunity = store.get_content_opportunity(opportunity.opportunity_id)
    workflow = AcquisitionHarness(store=store).select(
        opportunity,
        owner="operator",
        idempotency_key=f"package:{opportunity.opportunity_id}",
    )
    return store, opportunity, workflow


def _selection_context(store, package, opportunity, angle):
    token = secrets.token_urlsafe(48)
    auth = WebSocketAuthenticator(token, principal="operator")
    websocket = object()
    auth.authenticate(websocket, token)
    parameters = {
        "package_id": package.package_id,
        "opportunity_id": opportunity.opportunity_id,
        "angle_id": angle.angle_id,
        "selection_version": 1,
    }
    fingerprint = resource_fingerprint("content.select_angle", parameters)
    grant = auth.authorize(
        websocket=websocket,
        operation={
            "operationId": "recovery-op",
            "correlationId": "recovery-corr",
            "requestFingerprint": fingerprint,
        },
        purpose="execute",
        request_correlation_id="recovery-request",
        action="content.select_angle",
        resource_fingerprint=fingerprint,
    )
    return AuthorizationContext.from_grant(grant), auth


class _NoClaimsProvider:
    provider_id = "deterministic-no-claims"
    model_version = "no-claims-1"

    def generate(self, prompt, context):
        return (
            '{"title":"No claims title","target_audience":"builders",'
            '"platform":"youtube","intended_duration_seconds":30,'
            '"content_objective":"Educate","opening_hook":"Hook",'
            '"introduction":"Introduction",'
            '"sections":[{"heading":"Context","narration":"Context",'
            '"visual_direction":"Show context"}],"closing":"Close"}'
        )


def _pending_package(database):
    store, opportunity, workflow = _package_fixture(database)
    package = ContentPackageOrchestrator(store).execute(
        opportunity_id=opportunity.opportunity_id,
        workflow_id=workflow.workflow_id,
        owner_id="operator",
    )
    angle = store.list_content_angles(package.strategy_id)[0]
    return store, opportunity, workflow, package, angle


def _complete_with_provider(
    store, opportunity, workflow, package, angle, *, classification
):
    context, authority = _selection_context(store, package, opportunity, angle)
    return ContentPackageOrchestrator(store).execute(
        opportunity_id=opportunity.opportunity_id,
        workflow_id=workflow.workflow_id,
        owner_id="operator",
        selected_angle_id=angle.angle_id,
        selected_angle_actor="operator",
        authorization_context=context,
        authorization_authority=authority,
        planned_publish_at=datetime(2030, 1, 1, tzinfo=UTC),
        provider=_NoClaimsProvider(),
        content_classification=classification,
    )


@pytest.mark.parametrize(
    ("classification", "expected_readiness"),
    (
        ("factual", "needs_factual_review"),
        ("financial", "needs_factual_review"),
        ("technical", "needs_factual_review"),
        ("mixed", "needs_factual_review"),
        ("opinion", "production_ready"),
        ("fictional", "production_ready"),
    ),
)
def test_content_readiness_classification_policy(tmp_path, classification, expected_readiness):
    store, opportunity, workflow, package, angle = _pending_package(
        tmp_path / f"{classification}.db"
    )
    result = _complete_with_provider(
        store, opportunity, workflow, package, angle,
        classification=classification,
    )
    assert result.readiness.value == expected_readiness
    decision = store.get_latest_readiness_decision(result.package_id)
    assert decision is not None
    assert decision.policy_version == f"content-readiness-v2:{classification}"
    if expected_readiness == "needs_factual_review":
        assert "missing_claims" in decision.failed_conditions
    else:
        assert decision.classification.value == "production_ready"
    store.close()


def test_content_stale_supporting_artifacts_are_rejected(tmp_path):
    store, opportunity, workflow, package, angle = _pending_package(
        tmp_path / "stale-artifacts.db"
    )
    result = _complete_with_provider(
        store, opportunity, workflow, package, angle, classification="factual"
    )
    artifact = store.get_script_artifact(result.script_artifact_id)
    assert artifact is not None
    original_metadata = store.get_content_metadata(result.metadata_id)
    original_thumbnail = store.get_thumbnail_brief(result.thumbnail_id)
    original_quality = store.get_quality_evaluation(result.quality_evaluation_id)
    assert original_metadata and original_thumbnail and original_quality

    for method_name, value in (
        ("get_content_metadata", original_metadata),
        ("get_thumbnail_brief", original_thumbnail),
        ("get_quality_evaluation", original_quality),
    ):
        original_method = getattr(store, method_name)
        setattr(
            store,
            method_name,
            lambda record_id, artifact_version=None, artifact_sha256=None,
                   value=value: value.model_copy(
                       update={"script_artifact_sha256": "stale-hash"}
                   ),
        )
        context, authority = _selection_context(store, result, opportunity, angle)
        with pytest.raises(ValueError, match="stale"):
            ContentPackageOrchestrator(store).execute(
                opportunity_id=opportunity.opportunity_id,
                workflow_id=workflow.workflow_id,
                owner_id="operator",
                selected_angle_id=angle.angle_id,
                selected_angle_actor="operator",
                authorization_context=context,
                authorization_authority=authority,
                planned_publish_at=datetime(2030, 1, 1, tzinfo=UTC),
                content_classification="factual",
            )
        setattr(store, method_name, original_method)
    store.close()


def test_content_readiness_persists_exact_binding_after_restart(tmp_path):
    database = tmp_path / "readiness-restart.db"
    store, opportunity, workflow, package, angle = _pending_package(database)
    result = _complete_with_provider(
        store, opportunity, workflow, package, angle, classification="opinion"
    )
    decision = store.get_latest_readiness_decision(result.package_id)
    store.close()
    reopened = ContentStore(database)
    restored = reopened.get_latest_readiness_decision(result.package_id)
    assert restored == decision
    assert restored.script_artifact_id == result.script_artifact_id
    assert restored.script_artifact_version == result.script_artifact_version
    assert restored.script_artifact_sha256 == result.script_artifact_sha256
    assert restored.policy_version == "content-readiness-v2:opinion"
    reopened.close()


def test_content_failed_quality_evaluation_requires_quality_review(tmp_path, monkeypatch):
    store, opportunity, workflow, package, angle = _pending_package(
        tmp_path / "failed-quality.db"
    )
    original = ContentQualityEvaluator.evaluate

    def failed_evaluation(self, script, metadata=None, **kwargs):
        evaluation = original(self, script, metadata, **kwargs)
        return evaluation.model_copy(
            update={"missing_data": ("quality_failure",), "clarity": 0.0}
        )

    monkeypatch.setattr(ContentQualityEvaluator, "evaluate", failed_evaluation)
    result = _complete_with_provider(
        store, opportunity, workflow, package, angle, classification="opinion"
    )
    assert result.readiness.value == "needs_quality_review"
    decision = store.get_latest_readiness_decision(result.package_id)
    assert decision is not None
    assert "quality_evaluation_failed" in decision.failed_conditions
    store.close()


@pytest.mark.parametrize("service", (ContentMetadataService, ThumbnailBriefService))
def test_content_missing_required_supporting_record_fails_explicitly(
    tmp_path, monkeypatch, service
):
    store, opportunity, workflow, package, angle = _pending_package(
        tmp_path / f"missing-{service.__name__}.db"
    )
    monkeypatch.setattr(service, "create", lambda *args, **kwargs: None)
    context, authority = _selection_context(store, package, opportunity, angle)
    with pytest.raises(ValueError, match="could not be persisted"):
        ContentPackageOrchestrator(store).execute(
            opportunity_id=opportunity.opportunity_id,
            workflow_id=workflow.workflow_id,
            owner_id="operator",
            selected_angle_id=angle.angle_id,
            selected_angle_actor="operator",
            authorization_context=context,
            authorization_authority=authority,
            planned_publish_at=datetime(2030, 1, 1, tzinfo=UTC),
        )
    store.close()


@pytest.mark.parametrize(
    "case",
    (
        "missing_context",
        "invalid_context",
        "principal_mismatch",
        "incorrect_fingerprint",
        "incorrect_package",
        "incorrect_opportunity",
        "incorrect_angle",
        "stale_selection_version",
    ),
)
def test_content_angle_selection_rejection_matrix(tmp_path, case):
    store, opportunity, workflow, package, angle = _pending_package(
        tmp_path / f"auth-{case}.db"
    )
    context, authority = _selection_context(store, package, opportunity, angle)
    if case == "missing_context":
        context = None
    elif case == "invalid_context":
        context = replace(context, signature="invalid")
    elif case == "principal_mismatch":
        context = replace(context, principal="untrusted-input")
    elif case == "incorrect_fingerprint":
        context = replace(context, resource_fingerprint="wrong")
    elif case == "incorrect_package":
        context = replace(context, resource_fingerprint=resource_fingerprint(
                "content.select_angle",
                {"package_id": "package:other", "opportunity_id": opportunity.opportunity_id,
                 "angle_id": angle.angle_id, "selection_version": 1},
            ))
    elif case == "incorrect_opportunity":
        context = replace(context, resource_fingerprint=resource_fingerprint(
                "content.select_angle",
                {"package_id": package.package_id, "opportunity_id": "other",
                 "angle_id": angle.angle_id, "selection_version": 1},
            ))
    else:
        context = replace(context, resource_fingerprint=resource_fingerprint(
                "content.select_angle",
                {"package_id": package.package_id, "opportunity_id": opportunity.opportunity_id,
                 "angle_id": (
                     "other" if case == "incorrect_angle" else angle.angle_id
                 ),
                 "selection_version": 2 if case == "stale_selection_version" else 1},
            ))
    with pytest.raises(PermissionError):
        ContentPackageOrchestrator(store).execute(
            opportunity_id=opportunity.opportunity_id,
            workflow_id=workflow.workflow_id,
            owner_id="operator",
            selected_angle_id=angle.angle_id,
            selected_angle_actor="operator",
            authorization_context=context,
            authorization_authority=authority,
            planned_publish_at=datetime(2030, 1, 1, tzinfo=UTC),
        )
    assert store.list_angle_selections(package.package_id) == ()
    if context is not None:
        events = store.list_audit_events(package.package_id)
        assert any(event["decision"] == "rejected" for event in events)
        assert all("token" not in json.dumps(event["details"]).lower() for event in events)
    store.close()


def test_content_repeated_selection_is_idempotent_and_audited(tmp_path):
    store, opportunity, workflow, package, angle = _pending_package(
        tmp_path / "auth-repeat.db"
    )
    context, authority = _selection_context(store, package, opportunity, angle)
    orchestrator = ContentPackageOrchestrator(store)
    first = orchestrator.execute(
        opportunity_id=opportunity.opportunity_id, workflow_id=workflow.workflow_id,
        owner_id="operator", selected_angle_id=angle.angle_id,
        selected_angle_actor="operator", authorization_context=context,
        authorization_authority=authority, planned_publish_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    second = orchestrator.execute(
        opportunity_id=opportunity.opportunity_id, workflow_id=workflow.workflow_id,
        owner_id="operator", selected_angle_id=angle.angle_id,
        selected_angle_actor="operator", authorization_context=context,
        authorization_authority=authority, planned_publish_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    assert second.script_artifact_id == first.script_artifact_id
    assert len(store.list_angle_selections(package.package_id)) == 1
    store.close()


def _resume_package_process(
    database, opportunity_id, workflow_id, angle_id, planned_publish_at, barrier, results
):
    store = ContentStore(database)
    token = secrets.token_urlsafe(48)
    auth = WebSocketAuthenticator(token, principal="operator")
    websocket = object()
    auth.authenticate(websocket, token)
    package_id = f"package:{workflow_id}"
    parameters = {
        "package_id": package_id,
        "opportunity_id": opportunity_id,
        "angle_id": angle_id,
        "selection_version": 1,
    }
    fingerprint = resource_fingerprint("content.select_angle", parameters)
    grant = auth.authorize(
        websocket=websocket,
        operation={
            "operationId": f"concurrent-{multiprocessing.current_process().pid}",
            "correlationId": "concurrent-correlation",
            "requestFingerprint": fingerprint,
        },
        purpose="execute",
        request_correlation_id="concurrent-request",
        action="content.select_angle",
        resource_fingerprint=fingerprint,
    )
    barrier.wait(timeout=15)
    try:
        package = ContentPackageOrchestrator(store).execute(
            opportunity_id=opportunity_id,
            workflow_id=workflow_id,
            owner_id="operator",
            selected_angle_id=angle_id,
            selected_angle_actor="operator",
            authorization_context=AuthorizationContext.from_grant(grant),
            authorization_authority=auth,
            planned_publish_at=planned_publish_at,
        )
        results.put(("ok", package.script_artifact_id, package.readiness.value))
    except Exception as error:
        results.put(("error", type(error).__name__, str(error)))
    finally:
        store.close()


def test_content_package_concurrent_resume_is_idempotent(tmp_path):
    database = tmp_path / "concurrent-package.db"
    store, opportunity, workflow = _package_fixture(database)
    pending = ContentPackageOrchestrator(store).execute(
        opportunity_id=opportunity.opportunity_id,
        workflow_id=workflow.workflow_id,
        owner_id="operator",
    )
    angle = store.list_content_angles(pending.strategy_id)[0]
    store.close()
    planned_publish_at = datetime(2030, 1, 1, tzinfo=UTC)

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(
            target=_resume_package_process,
            args=(database, opportunity.opportunity_id, workflow.workflow_id,
                  angle.angle_id, planned_publish_at, barrier, results),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(30)
        assert process.exitcode == 0
    outcomes = [results.get(timeout=5) for _ in processes]
    assert all(outcome[0] == "ok" for outcome in outcomes), outcomes
    assert len({outcome[1] for outcome in outcomes}) == 1

    reopened = ContentStore(database)
    package_id = f"package:{workflow.workflow_id}"
    package = reopened.get_content_package(package_id)
    assert package is not None
    assert len(reopened.list_content_packages()) == 1
    assert len(reopened.list_angle_selections(package_id)) == 1
    assert len(reopened.list_editorial_plans()) == 1
    assert len(reopened.list_content_package_stages(package_id)) == 10
    assert package.script_artifact_id == outcomes[0][1]
    assert reopened.get_latest_readiness_decision(package_id).script_artifact_id == package.script_artifact_id
    reopened.close()


def test_content_package_selection_fails_closed_when_audit_persistence_fails(
    tmp_path, monkeypatch
):
    store, opportunity, workflow = _package_fixture(tmp_path / "audit-failure.db")
    pending = ContentPackageOrchestrator(store).execute(
        opportunity_id=opportunity.opportunity_id,
        workflow_id=workflow.workflow_id,
        owner_id="operator",
    )
    angle = store.list_content_angles(pending.strategy_id)[0]
    context, authority = _selection_context(store, pending, opportunity, angle)

    def fail_audit(**kwargs):
        raise OSError("audit store unavailable")

    monkeypatch.setattr(store, "record_content_authorization_audit", fail_audit)
    with pytest.raises(OSError, match="audit store unavailable"):
        ContentPackageOrchestrator(store).execute(
            opportunity_id=opportunity.opportunity_id,
            workflow_id=workflow.workflow_id,
            owner_id="operator",
            selected_angle_id=angle.angle_id,
            selected_angle_actor="operator",
            authorization_context=context,
            authorization_authority=authority,
            planned_publish_at=datetime.now(UTC) + timedelta(days=1),
        )
    assert store.list_angle_selections(pending.package_id) == ()
    store.close()


@pytest.mark.parametrize(
    "stage_name",
    ("research", "strategy", "angles", "angle_selection", "editorial_plan",
     "script", "claims", "metadata", "thumbnail", "quality"),
)
def test_content_package_recovers_after_each_persisted_stage(tmp_path, stage_name):
    store, opportunity, workflow = _package_fixture(tmp_path / f"{stage_name}.db")
    orchestrator = _FailAfterStage(store, stage_name)
    pending = None
    if stage_name not in {"research", "strategy", "angles"}:
        pending = ContentPackageOrchestrator(store).execute(
            opportunity_id=opportunity.opportunity_id,
            workflow_id=workflow.workflow_id,
            owner_id="operator",
        )
        angle = store.list_content_angles(pending.strategy_id)[0]
        context, authority = _selection_context(store, pending, opportunity, angle)
        selected = {
            "selected_angle_id": angle.angle_id,
            "selected_angle_actor": "operator",
            "authorization_context": context,
            "authorization_authority": authority,
            "planned_publish_at": datetime.now(UTC) + timedelta(days=1),
        }
    else:
        selected = {}

    with pytest.raises(RuntimeError, match="injected interruption"):
        orchestrator.execute(
            opportunity_id=opportunity.opportunity_id,
            workflow_id=workflow.workflow_id,
            owner_id="operator",
            **selected,
        )
    package_id = f"package:{workflow.workflow_id}"
    failed = store.get_content_package(package_id)
    assert failed is not None
    assert failed.last_failed_stage == stage_name
    assert failed.recovery_status == "recovery_required"
    store.close()

    reopened = ContentStore(tmp_path / f"{stage_name}.db")
    if not selected:
        pending = ContentPackageOrchestrator(reopened).execute(
            opportunity_id=opportunity.opportunity_id,
            workflow_id=workflow.workflow_id,
            owner_id="operator",
        )
        angle = reopened.list_content_angles(pending.strategy_id)[0]
        context, authority = _selection_context(reopened, pending, opportunity, angle)
        selected = {
            "selected_angle_id": angle.angle_id,
            "selected_angle_actor": "operator",
            "authorization_context": context,
            "authorization_authority": authority,
            "planned_publish_at": datetime.now(UTC) + timedelta(days=1),
        }
    recovered = ContentPackageOrchestrator(reopened).execute(
        opportunity_id=opportunity.opportunity_id,
        workflow_id=workflow.workflow_id,
        owner_id="operator",
        **selected,
    )
    assert recovered.script_artifact_id is not None
    assert recovered.readiness.value in {"production_ready", "needs_factual_review"}
    stages = reopened.list_content_package_stages(package_id)
    assert len({stage.stage_id for stage in stages}) == len(stages)
    assert {stage.name for stage in stages} >= {
        "research", "strategy", "angles", "angle_selection",
        "editorial_plan", "script", "claims", "metadata", "thumbnail", "quality",
    }
    assert recovered.recovery_status == "not_required"
    reopened.close()


def test_content_package_survives_restart_without_duplicate_artifacts(tmp_path):
    database = tmp_path / "package.db"
    store = ContentStore(database)
    discovery = ContentDiscoveryService(store)
    opportunity = discovery.discover(
        topic="Evidence package",
        niche="technology",
        target_audience="builders",
        target_language="en",
        content_format="short",
        source_documents=(_document(),),
    )
    _, report = discovery.research(opportunity, summary="Evidence-backed report.")
    opportunity = store.get_content_opportunity(opportunity.opportunity_id)
    discovery.persist_score(opportunity)
    opportunity = store.get_content_opportunity(opportunity.opportunity_id)
    workflow = AcquisitionHarness(store=store).select(
        opportunity,
        owner="operator",
        idempotency_key=f"package:{opportunity.opportunity_id}",
    )
    orchestrator = ContentPackageOrchestrator(store)
    first = orchestrator.execute(
        opportunity_id=opportunity.opportunity_id,
        workflow_id=workflow.workflow_id,
        owner_id="operator",
        planned_publish_at=datetime.now(UTC) + timedelta(days=1),
    )
    assert first.stage == "awaiting_angle_selection"
    angle = store.list_content_angles(first.strategy_id)[0]
    token = secrets.token_urlsafe(48)
    auth = WebSocketAuthenticator(token, principal="operator")
    websocket = object()
    auth.authenticate(websocket, token)
    parameters = {
        "package_id": first.package_id,
        "opportunity_id": opportunity.opportunity_id,
        "angle_id": angle.angle_id,
        "selection_version": 1,
    }
    operation = {"operationId": "op-1", "correlationId": "corr-1",
                 "requestFingerprint": resource_fingerprint("content.select_angle", parameters)}
    grant = auth.authorize(
        websocket=websocket,
        operation=operation,
        purpose="execute",
        request_correlation_id="request-1",
        action="content.select_angle",
        resource_fingerprint=resource_fingerprint("content.select_angle", parameters),
    )
    context = AuthorizationContext.from_grant(grant)
    with pytest.raises(PermissionError):
        orchestrator.execute(
            opportunity_id=opportunity.opportunity_id,
            workflow_id=workflow.workflow_id,
            owner_id="operator",
            selected_angle_id="stale-angle",
            selected_angle_actor="operator",
            authorization_context=context,
            authorization_authority=auth,
            planned_publish_at=datetime.now(UTC) + timedelta(days=1),
        )
    rejected = store.list_audit_events(first.package_id)
    assert any(event["decision"] == "rejected" for event in rejected)
    complete = orchestrator.execute(
        opportunity_id=opportunity.opportunity_id,
        workflow_id=workflow.workflow_id,
        owner_id="operator",
        selected_angle_id=angle.angle_id,
        selected_angle_actor="operator",
        authorization_context=context,
        authorization_authority=auth,
        planned_publish_at=datetime.now(UTC) + timedelta(days=1),
    )
    assert complete.script_artifact_version == 1
    assert complete.readiness.value == "production_ready"
    artifact_id = complete.script_artifact_id
    store.close()

    reopened = ContentStore(database)
    resumed = ContentPackageOrchestrator(reopened).execute(
        opportunity_id=opportunity.opportunity_id,
        workflow_id=workflow.workflow_id,
        owner_id="operator",
        selected_angle_id=angle.angle_id,
        selected_angle_actor="operator",
        authorization_context=context,
        authorization_authority=auth,
        planned_publish_at=datetime.now(UTC) + timedelta(days=1),
    )
    assert resumed.script_artifact_id == artifact_id
    assert resumed.script_artifact_version == 1
    assert len(reopened.list_content_packages()) == 1
    assert len(reopened.list_angle_selections(resumed.package_id)) == 1
    assert len(reopened.list_content_package_stages(resumed.package_id)) >= 8
    assert reopened.get_latest_readiness_decision(resumed.package_id).script_artifact_id == artifact_id
    audit = reopened.list_audit_events(resumed.package_id)
    assert any(event["decision"] == "allowed" for event in audit)
    assert all("token" not in json.dumps(event["details"]).lower() for event in audit)
    reopened.close()


def test_content_package_mock_provider_is_persisted_and_labeled(tmp_path):
    class MockProvider:
        provider_id = "deterministic-mock"
        model_version = "mock-1"

        def generate(self, prompt, context):
            return (
                '{"title":"Mock title","target_audience":"builders",'
                '"platform":"youtube","intended_duration_seconds":30,'
                '"content_objective":"Educate","opening_hook":"Hook",'
                '"introduction":"Introduction",'
                '"sections":[{"heading":"Evidence","narration":"Evidence",'
                '"visual_direction":"Show evidence"}],"closing":"Close"}'
            )

    database = tmp_path / "mock-package.db"
    store = ContentStore(database)
    discovery = ContentDiscoveryService(store)
    opportunity = discovery.discover(
        topic="Mock provider topic",
        niche="technology",
        target_audience="builders",
        target_language="en",
        content_format="short",
        source_documents=(_document(),),
    )
    _, report = discovery.research(opportunity, summary="Evidence-backed report.")
    opportunity = store.get_content_opportunity(opportunity.opportunity_id)
    discovery.persist_score(opportunity)
    opportunity = store.get_content_opportunity(opportunity.opportunity_id)
    workflow = AcquisitionHarness(store=store).select(
        opportunity,
        owner="operator",
        idempotency_key=f"package:{opportunity.opportunity_id}",
    )
    orchestrator = ContentPackageOrchestrator(store)
    pending = orchestrator.execute(
        opportunity_id=opportunity.opportunity_id,
        workflow_id=workflow.workflow_id,
        owner_id="operator",
    )
    angle = store.list_content_angles(pending.strategy_id)[0]
    token = secrets.token_urlsafe(48)
    auth = WebSocketAuthenticator(token, principal="operator")
    websocket = object()
    auth.authenticate(websocket, token)
    parameters = {
        "package_id": pending.package_id,
        "opportunity_id": opportunity.opportunity_id,
        "angle_id": angle.angle_id,
        "selection_version": 1,
    }
    fingerprint = resource_fingerprint("content.select_angle", parameters)
    grant = auth.authorize(
        websocket=websocket,
        operation={
            "operationId": "mock-op",
            "correlationId": "mock-corr",
            "requestFingerprint": fingerprint,
        },
        purpose="execute",
        request_correlation_id="mock-request",
        action="content.select_angle",
        resource_fingerprint=fingerprint,
    )
    complete = orchestrator.execute(
        opportunity_id=opportunity.opportunity_id,
        workflow_id=workflow.workflow_id,
        owner_id="operator",
        selected_angle_id=angle.angle_id,
        selected_angle_actor="operator",
        authorization_context=AuthorizationContext.from_grant(grant),
        authorization_authority=auth,
        provider=MockProvider(),
        planned_publish_at=datetime.now(UTC) + timedelta(days=1),
    )
    artifact = store.get_script_artifact(complete.script_artifact_id)
    generation_metadata = json.loads(artifact.generation_metadata_json)
    assert generation_metadata["provider_id"] == "deterministic-mock"
    assert generation_metadata["model_version"] == "mock-1"
    assert "token" not in generation_metadata
    assert complete.readiness.value == "needs_factual_review"
    store.close()
