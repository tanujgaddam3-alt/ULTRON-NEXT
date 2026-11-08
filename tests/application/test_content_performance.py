from __future__ import annotations

import hashlib
import json
import sqlite3
import shutil
import subprocess
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from ultron.application.content.distribution import ContentPublicationPreparationService
from ultron.application.content.discovery import ContentDiscoveryService
from ultron.application.content.performance import ContentPerformanceIntelligenceService
from ultron.application.content.processing import LocalTemporaryContentStore
from ultron.application.content.production import ContentMediaProductionService
from ultron.application.content.service import ContentRightsService
from ultron.application.content.status import create_content_runtime
from ultron.application.content.store import ContentStore, IdempotencyConflictError
from ultron.application.content.strategy import ContentPackageOrchestrator
from ultron.application.content.workflow import ContentWorkflowRuntime
from ultron.domain.content_performance import (
    ContentPerformanceMeasurement,
    ContentPerformanceRecommendation,
    CostCategory,
    CostEvidenceStatus,
    DataCollectionState,
    DataConnectionState,
    HumanInsightDecisionKind,
    MeasurementSourceKind,
    PerformanceAnalysis,
    PerformanceMetrics,
    ProductionCostLine,
    ProductionCostReport,
    RecommendationKind,
    ReportingWindow,
    RetentionInterval,
)
from ultron.domain.content_distribution import DistributionPlatform, PublicationMode
from ultron.security.identity.authenticator import WebSocketAuthenticator
from tests.application.test_content_distribution import _authorized_context
from tests.application.test_content_discovery import _document
from tests.application.test_content_package_e2e import _complete_with_provider
from tests.application.acquisition_test_utils import AcquisitionHarness


_REPORTING_WINDOW = ReportingWindow(
    starts_at=datetime(2026, 1, 1, tzinfo=UTC),
    ends_at=datetime(2026, 1, 8, tzinfo=UTC),
)


class _TestRenderer:
    timeout_seconds = 30

    def render(self, plan, staged_output):
        staged_output.write_bytes(b"performance-test-media")
        return {
            "name": "performance-test-renderer",
            "version": str(len(plan.scenes)),
        }

    def validate(self, plan, staged_output):
        passed = staged_output.is_file()
        return {
            "passed": passed,
            "results": ({"id": "test_media", "passed": passed},),
            "failure_reasons": () if passed else ("test_media_missing",),
            "duration_seconds": sum(scene.duration_seconds for scene in plan.scenes),
            "width": plan.rendering_configuration["width"],
            "height": plan.rendering_configuration["height"],
            "video_codec": "h264",
        }

    def renderer_information(self):
        return {"name": "performance-test-renderer", "version": "1"}


class _TestMeasurementProvider:
    provider_id = "test-provider"

    def __init__(self, state: DataConnectionState):
        self.state = state
        self.last_request: tuple[str, ReportingWindow] | None = None

    def connection_state(self) -> DataConnectionState:
        return self.state

    def collect(self, content_package_id, reporting_window):
        self.last_request = (content_package_id, reporting_window)
        return ()


def _create_package(store: ContentStore, topic: str):
    discovery = ContentDiscoveryService(store)
    opportunity = discovery.discover(
        topic=topic,
        niche="technology",
        target_audience="builders",
        target_language="en",
        content_format="short",
        source_documents=(_document(),),
    )
    discovery.research(opportunity, summary="Performance test evidence.")
    opportunity = store.get_content_opportunity(opportunity.opportunity_id)
    discovery.persist_score(opportunity)
    opportunity = store.get_content_opportunity(opportunity.opportunity_id)
    workflow = AcquisitionHarness(store=store).select(
        opportunity,
        owner="operator",
        idempotency_key=f"performance:{opportunity.opportunity_id}",
    )
    pending = ContentPackageOrchestrator(store).execute(
        opportunity_id=opportunity.opportunity_id,
        workflow_id=workflow.workflow_id,
        owner_id="operator",
    )
    angle = store.list_content_angles(pending.strategy_id)[0]
    return _complete_with_provider(
        store,
        opportunity,
        workflow,
        pending,
        angle,
        classification="fictional",
    )


def _produce_artifact(store: ContentStore, root, package):
    storage = LocalTemporaryContentStore(root)
    service = ContentMediaProductionService(store, storage, _TestRenderer())
    plan = service.create_plan(
        package.package_id,
        configuration={
            "width": 180,
            "height": 320,
            "frame_rate": 5,
            "aspect_ratio": "9:16",
            "scene_duration_seconds": 1,
        },
    )
    return service.execute(plan.job_id)


def _measurement(
    package,
    artifact,
    *,
    measurement_id: str,
    views: int | None = 100,
    revenue: Decimal | None = None,
    revenue_currency: str | None = None,
    reporting_window: ReportingWindow = _REPORTING_WINDOW,
    retention=(),
):
    return ContentPerformanceMeasurement(
        measurement_id=measurement_id,
        idempotency_key=f"measurement:{measurement_id}",
        content_package_id=package.package_id,
        media_artifact_id=artifact.artifact_id,
        script_artifact_id=artifact.script_artifact_id,
        script_version=artifact.script_version,
        script_sha256=artifact.script_sha256,
        platform="youtube",
        remote_content_id=f"fixture-video-{measurement_id}",
        source_kind=MeasurementSourceKind.IMPORTED,
        source_name="deterministic_fixture",
        source_version="1",
        source_reference=f"test-fixture:{measurement_id}",
        observed_at=reporting_window.ends_at + timedelta(hours=1),
        reporting_window=reporting_window,
        metrics=PerformanceMetrics(
            views=views,
            impressions=200 if views is not None else None,
            click_through_rate=0.25 if views is not None else None,
            watch_time_seconds=600 if views is not None else None,
            average_view_duration_seconds=30 if views is not None else None,
            engagement_rate=0.1 if views is not None else None,
            likes=10 if views is not None else None,
            comments=2 if views is not None else None,
            shares=1 if views is not None else None,
            subscribers_gained=3 if views is not None else None,
            audience_retention=retention,
            revenue_amount=revenue,
            revenue_currency=revenue_currency,
            revenue_source="platform" if revenue is not None else None,
        ),
        created_at=reporting_window.ends_at + timedelta(hours=2),
    )


def _cost_report(
    package,
    artifact,
    *,
    report_id: str,
    amount: Decimal,
    reporting_window: ReportingWindow = _REPORTING_WINDOW,
    coverage_complete: bool = True,
    status: CostEvidenceStatus = CostEvidenceStatus.MEASURED,
):
    line = ProductionCostLine(
        line_id=f"{report_id}-render",
        category=CostCategory.RENDERING,
        status=status,
        amount=amount,
        currency="USD",
        source_name="test-ledger",
        source_reference="test-fixture:cost",
        estimation_method="fixture estimate" if status == CostEvidenceStatus.ESTIMATED else None,
    )
    return ProductionCostReport(
        report_id=report_id,
        idempotency_key=f"cost:{report_id}",
        content_package_id=package.package_id,
        media_artifact_id=artifact.artifact_id,
        reporting_window=reporting_window,
        lines=(line,),
        expected_categories=(CostCategory.RENDERING,),
        coverage_complete=coverage_complete,
        coverage_reference="test-fixture:coverage" if coverage_complete else None,
        source_name="test-ledger",
        observed_at=reporting_window.ends_at + timedelta(hours=1),
        created_at=reporting_window.ends_at + timedelta(hours=2),
    )


def test_measurement_persistence_provenance_and_safe_validation(tmp_path):
    database = tmp_path / "performance.db"
    store = ContentStore(database)
    package = _create_package(store, "Persisted performance measurements")
    artifact = _produce_artifact(store, tmp_path / "media", package)
    intelligence = ContentPerformanceIntelligenceService(store)
    measurement = _measurement(package, artifact, measurement_id="measurement-one")

    assert intelligence.record_measurement(measurement) == measurement
    with pytest.raises(IdempotencyConflictError):
        intelligence.record_measurement(
            measurement.model_copy(
                update={
                    "metrics": measurement.metrics.model_copy(update={"views": 101}),
                }
            )
        )
    with pytest.raises(ValueError, match="exact package and media script"):
        intelligence.record_measurement(
            measurement.model_copy(update={"script_sha256": "0" * 64})
        )

    partial = _measurement(
        package,
        artifact,
        measurement_id="measurement-partial",
        views=12,
    ).model_copy(update={"metrics": PerformanceMetrics(views=12)})
    intelligence.record_measurement(partial)
    fixture_status = next(
        item
        for item in intelligence.source_status()
        if item.provider_id == "deterministic_fixture"
    )
    assert fixture_status.connection_state == DataConnectionState.DISCONNECTED
    assert fixture_status.collection_state == DataCollectionState.PARTIAL

    store.close()
    reopened = ContentStore(database)
    recovered = reopened.get_content_performance_measurement("measurement-one")
    assert recovered == measurement
    assert recovered.source_reference == "test-fixture:measurement-one"
    assert recovered.reporting_window == _REPORTING_WINDOW
    assert recovered.metrics.likes == 10
    assert reopened.list_content_performance_measurements() == (measurement, partial)
    reopened.close()

    with pytest.raises(ValueError):
        PerformanceMetrics(views=-1)
    with pytest.raises(ValueError):
        PerformanceMetrics()
    with pytest.raises(ValueError):
        RetentionInterval(
            start_seconds=3,
            end_seconds=2,
            retention_start=0.8,
            retention_end=0.7,
        )
    with pytest.raises(ValueError):
        ReportingWindow(
            starts_at=datetime(2026, 1, 8, tzinfo=UTC),
            ends_at=datetime(2026, 1, 8, tzinfo=UTC),
        )


def test_provider_measurements_require_an_explicit_connected_provider(tmp_path):
    store = ContentStore(tmp_path / "provider.db")
    package = _create_package(store, "Connected provider measurement")
    artifact = _produce_artifact(store, tmp_path / "provider-media", package)
    measurement = _measurement(
        package,
        artifact,
        measurement_id="provider-measurement",
    ).model_copy(
        update={
            "source_kind": MeasurementSourceKind.PROVIDER,
            "source_name": "test-provider",
        }
    )
    with pytest.raises(PermissionError, match="configured source"):
        ContentPerformanceIntelligenceService(store).record_measurement(measurement)
    disconnected = ContentPerformanceIntelligenceService(
        store,
        providers=(_TestMeasurementProvider(DataConnectionState.DISCONNECTED),),
    )
    with pytest.raises(RuntimeError, match="while disconnected"):
        disconnected.record_measurement(measurement)
    connected = ContentPerformanceIntelligenceService(
        store,
        providers=(_TestMeasurementProvider(DataConnectionState.CONNECTED),),
    )
    assert connected.record_measurement(measurement) == measurement
    status = next(
        item
        for item in connected.source_status()
        if item.provider_id == "test-provider"
    )
    assert status.connection_state == DataConnectionState.CONNECTED
    assert status.collection_state == DataCollectionState.COMPLETE
    assert connected.providers[0].last_request is None
    store.close()


def test_scene_retention_cohort_recommendations_and_analysis_replay(tmp_path):
    store = ContentStore(tmp_path / "retention.db")
    package_one = _create_package(store, "Retention cohort one")
    package_two = _create_package(store, "Retention cohort two")
    artifact_one = _produce_artifact(
        store, tmp_path / "media-one", package_one
    )
    artifact_two = _produce_artifact(
        store, tmp_path / "media-two", package_two
    )
    plan_one = store.get_media_production_plan(artifact_one.job_id)
    assert plan_one is not None
    cursor = 0.0
    points = []
    for scene in plan_one.scenes:
        end = cursor + scene.duration_seconds
        start_retention = 0.95 if scene.sequence == 1 else 0.8
        end_retention = 0.70 if scene.sequence == 1 else 0.75
        points.append(
            RetentionInterval(
                start_seconds=cursor,
                end_seconds=end,
                retention_start=start_retention,
                retention_end=end_retention,
                rewatch_fraction=0.08,
            )
        )
        cursor = end
    intelligence = ContentPerformanceIntelligenceService(store)
    first = intelligence.record_measurement(
        _measurement(
            package_one,
            artifact_one,
            measurement_id="retention-one",
            views=100,
            retention=tuple(points),
        )
    )
    second = intelligence.record_measurement(
        _measurement(
            package_two,
            artifact_two,
            measurement_id="retention-two",
            views=1000,
        )
    )
    cost_one = intelligence.record_cost_report(
        _cost_report(
            package_one,
            artifact_one,
            report_id="retention-cost-one",
            amount=Decimal("10"),
        )
    )
    cost_two = intelligence.record_cost_report(
        _cost_report(
            package_two,
            artifact_two,
            report_id="retention-cost-two",
            amount=Decimal("5"),
        )
    )

    analysis = intelligence.analyze(
        analysis_id="retention-analysis",
        idempotency_key="retention-analysis-once",
        measurement_ids=(first.measurement_id, second.measurement_id),
        cost_report_ids=(cost_one.report_id, cost_two.report_id),
    )
    first_scenes = tuple(
        item
        for item in analysis.scene_retention
        if item.measurement_id == first.measurement_id
    )
    assert len(first_scenes) == len(plan_one.scenes)
    first_scene = next(item for item in first_scenes if item.sequence == 1)
    assert first_scene.scene_id == plan_one.scenes[0].scene_id
    assert first_scene.data_status == DataCollectionState.COMPLETE
    assert first_scene.coverage_fraction == 1
    assert first_scene.drop_fraction == pytest.approx(0.25)
    recommendations = store.list_performance_recommendations(
        analysis_id=analysis.analysis_id
    )
    assert any(item.kind == RecommendationKind.RETENTION_DROP for item in recommendations)
    strong_view = next(
        item
        for item in recommendations
        if item.kind == RecommendationKind.STRONG_PERFORMANCE
    )
    assert set(strong_view.supporting_measurement_ids) == {
        first.measurement_id,
        second.measurement_id,
    }
    assert strong_view.observation_periods == (_REPORTING_WINDOW,)
    assert any(
        "correlation does not establish cause" in item.confidence_limitations
        for item in recommendations
    )
    cost_recommendation = next(
        item
        for item in recommendations
        if item.kind == RecommendationKind.COST_INEFFICIENCY
    )
    assert set(cost_recommendation.supporting_cost_report_ids) == {
        cost_one.report_id,
        cost_two.report_id,
    }
    assert intelligence.analyze(
        analysis_id="retention-analysis",
        idempotency_key="retention-analysis-once",
        measurement_ids=(first.measurement_id, second.measurement_id),
        cost_report_ids=(cost_one.report_id, cost_two.report_id),
    ) == analysis
    with pytest.raises(IdempotencyConflictError):
        intelligence.analyze(
            analysis_id="retention-analysis",
            idempotency_key="different-key",
            measurement_ids=(first.measurement_id,),
        )
    store.close()


def test_economics_require_complete_matching_evidence(tmp_path):
    store = ContentStore(tmp_path / "economics.db")
    package = _create_package(store, "Measured production economics")
    artifact = _produce_artifact(store, tmp_path / "economics-media", package)
    intelligence = ContentPerformanceIntelligenceService(store)
    measured = intelligence.record_measurement(
        _measurement(
            package,
            artifact,
            measurement_id="economics-measured",
            revenue=Decimal("20.00"),
            revenue_currency="USD",
        )
    )
    cost = intelligence.record_cost_report(
        _cost_report(
            package,
            artifact,
            report_id="cost-measured",
            amount=Decimal("5.00"),
        )
    )
    complete = intelligence.analyze(
        analysis_id="economics-analysis",
        idempotency_key="economics-analysis",
        measurement_ids=(measured.measurement_id,),
        cost_report_ids=(cost.report_id,),
    )
    economics = complete.economics[0]
    assert economics.total_production_cost == Decimal("5.00")
    assert economics.revenue == Decimal("20.00")
    assert economics.net_revenue == Decimal("15.00")
    assert economics.roi_percent == Decimal("300.00")
    assert economics.currency == "USD"

    no_revenue_window = ReportingWindow(
        starts_at=datetime(2026, 2, 1, tzinfo=UTC),
        ends_at=datetime(2026, 2, 8, tzinfo=UTC),
    )
    no_revenue = intelligence.record_measurement(
        _measurement(
            package,
            artifact,
            measurement_id="economics-no-revenue",
            revenue=None,
            reporting_window=no_revenue_window,
        )
    )
    zero_cost = intelligence.record_cost_report(
        _cost_report(
            package,
            artifact,
            report_id="cost-zero",
            amount=Decimal("0"),
            reporting_window=no_revenue_window,
        )
    )
    no_revenue_analysis = intelligence.analyze(
        analysis_id="no-revenue-analysis",
        idempotency_key="no-revenue-analysis",
        measurement_ids=(no_revenue.measurement_id,),
        cost_report_ids=(zero_cost.report_id,),
    )
    assert no_revenue_analysis.economics[0].total_production_cost == Decimal("0")
    assert no_revenue_analysis.economics[0].net_revenue is None
    assert no_revenue_analysis.economics[0].roi_percent is None
    assert "revenue_unavailable" in no_revenue_analysis.economics[0].unavailable_reasons

    another_window = ReportingWindow(
        starts_at=datetime(2026, 3, 1, tzinfo=UTC),
        ends_at=datetime(2026, 3, 8, tzinfo=UTC),
    )
    zero_cost_revenue = intelligence.record_measurement(
        _measurement(
            package,
            artifact,
            measurement_id="economics-zero-cost-revenue-march",
            revenue=Decimal("10"),
            revenue_currency="USD",
            reporting_window=another_window,
        )
    )
    zero_cost_march = intelligence.record_cost_report(
        _cost_report(
            package,
            artifact,
            report_id="cost-zero-march",
            amount=Decimal("0"),
            reporting_window=another_window,
        )
    )
    zero_analysis = intelligence.analyze(
        analysis_id="zero-cost-analysis",
        idempotency_key="zero-cost-analysis",
        measurement_ids=(zero_cost_revenue.measurement_id,),
        cost_report_ids=(zero_cost_march.report_id,),
    )
    assert zero_analysis.economics[0].net_revenue == Decimal("10")
    assert zero_analysis.economics[0].roi_percent is None
    assert "roi_unavailable_zero_production_cost" in zero_analysis.economics[0].unavailable_reasons

    mismatch_window = ReportingWindow(
        starts_at=datetime(2026, 4, 1, tzinfo=UTC),
        ends_at=datetime(2026, 4, 8, tzinfo=UTC),
    )
    mismatch = intelligence.record_measurement(
        _measurement(
            package,
            artifact,
            measurement_id="economics-currency-mismatch",
            revenue=Decimal("10"),
            revenue_currency="GBP",
            reporting_window=mismatch_window,
        )
    )
    mismatch_cost = intelligence.record_cost_report(
        _cost_report(
            package,
            artifact,
            report_id="cost-mismatch",
            amount=Decimal("4"),
            reporting_window=mismatch_window,
        )
    )
    mismatch_analysis = intelligence.analyze(
        analysis_id="mismatch-analysis",
        idempotency_key="mismatch-analysis",
        measurement_ids=(mismatch.measurement_id,),
        cost_report_ids=(mismatch_cost.report_id,),
    )
    assert mismatch_analysis.economics[0].net_revenue is None
    assert mismatch_analysis.economics[0].roi_percent is None
    assert "revenue_cost_currency_mismatch" in mismatch_analysis.economics[0].unavailable_reasons

    incomplete_window = ReportingWindow(
        starts_at=datetime(2026, 5, 1, tzinfo=UTC),
        ends_at=datetime(2026, 5, 8, tzinfo=UTC),
    )
    incomplete_measurement = intelligence.record_measurement(
        _measurement(
            package,
            artifact,
            measurement_id="economics-incomplete-cost",
            revenue=Decimal("10"),
            revenue_currency="USD",
            reporting_window=incomplete_window,
        )
    )
    incomplete_cost = intelligence.record_cost_report(
        _cost_report(
            package,
            artifact,
            report_id="cost-incomplete",
            amount=Decimal("4"),
            reporting_window=incomplete_window,
            coverage_complete=False,
        )
    )
    incomplete_analysis = intelligence.analyze(
        analysis_id="incomplete-analysis",
        idempotency_key="incomplete-analysis",
        measurement_ids=(incomplete_measurement.measurement_id,),
        cost_report_ids=(incomplete_cost.report_id,),
    )
    assert incomplete_analysis.economics[0].total_production_cost is None
    assert incomplete_analysis.economics[0].net_revenue is None
    assert "cost_coverage_incomplete" in incomplete_analysis.economics[0].unavailable_reasons

    measured_window = ReportingWindow(
        starts_at=datetime(2026, 6, 1, tzinfo=UTC),
        ends_at=datetime(2026, 6, 8, tzinfo=UTC),
    )
    different_cost_window = ReportingWindow(
        starts_at=datetime(2026, 7, 1, tzinfo=UTC),
        ends_at=datetime(2026, 7, 8, tzinfo=UTC),
    )
    period_mismatch = intelligence.record_measurement(
        _measurement(
            package,
            artifact,
            measurement_id="economics-period-mismatch",
            revenue=Decimal("10"),
            revenue_currency="USD",
            reporting_window=measured_window,
        )
    )
    period_cost = intelligence.record_cost_report(
        _cost_report(
            package,
            artifact,
            report_id="cost-period-mismatch",
            amount=Decimal("4"),
            reporting_window=different_cost_window,
        )
    )
    period_analysis = intelligence.analyze(
        analysis_id="period-mismatch-analysis",
        idempotency_key="period-mismatch-analysis",
        measurement_ids=(period_mismatch.measurement_id,),
        cost_report_ids=(period_cost.report_id,),
    )
    assert period_analysis.economics[0].total_production_cost is None
    assert period_analysis.economics[0].net_revenue is None
    assert "cost_reporting_period_mismatch" in period_analysis.economics[0].unavailable_reasons

    missing_cost_window = ReportingWindow(
        starts_at=datetime(2026, 8, 1, tzinfo=UTC),
        ends_at=datetime(2026, 8, 8, tzinfo=UTC),
    )
    missing_cost = intelligence.record_measurement(
        _measurement(
            package,
            artifact,
            measurement_id="economics-missing-cost",
            revenue=Decimal("10"),
            revenue_currency="USD",
            reporting_window=missing_cost_window,
        )
    )
    missing_cost_analysis = intelligence.analyze(
        analysis_id="missing-cost-analysis",
        idempotency_key="missing-cost-analysis",
        measurement_ids=(missing_cost.measurement_id,),
    )
    assert missing_cost_analysis.economics[0].total_production_cost is None
    assert missing_cost_analysis.economics[0].net_revenue is None
    assert "matching_cost_report_missing" in missing_cost_analysis.economics[0].unavailable_reasons
    store.close()


def test_human_approval_traces_recommendation_into_idempotent_strategy_revision(tmp_path):
    store = ContentStore(tmp_path / "learning.db")
    package = _create_package(store, "Approved learning trace")
    artifact = _produce_artifact(store, tmp_path / "learning-media", package)
    intelligence = ContentPerformanceIntelligenceService(store)
    measurement = intelligence.record_measurement(
        _measurement(package, artifact, measurement_id="learning-measurement")
    )
    analysis = PerformanceAnalysis(
        analysis_id="learning-analysis",
        idempotency_key="learning-analysis",
        measurement_ids=(measurement.measurement_id,),
        data_state=DataCollectionState.COMPLETE,
        created_at=datetime(2026, 1, 9, tzinfo=UTC),
    )
    recommendation = ContentPerformanceRecommendation(
        recommendation_id="learning-recommendation",
        analysis_id=analysis.analysis_id,
        idempotency_key="learning-recommendation",
        kind=RecommendationKind.TOPIC_FORMAT_PATTERN,
        summary="The measured cohort showed a descriptive format pattern.",
        content_package_ids=(package.package_id,),
        supporting_measurement_ids=(measurement.measurement_id,),
        observation_periods=(measurement.reporting_window,),
        confidence=0.5,
        confidence_limitations=("observational association; no causal claim",),
        suggested_strategy_updates={"content_format": "evidence-led short"},
        created_at=datetime(2026, 1, 9, tzinfo=UTC),
    )
    store.create_performance_analysis(analysis, (recommendation,))
    updates = {"content_format": "evidence-led short"}
    decision = intelligence.review_recommendation(
        decision_id="learning-decision",
        recommendation_id=recommendation.recommendation_id,
        actor="operator",
        decision=HumanInsightDecisionKind.APPROVED,
        reason="Reviewed the supporting measurement and limitations.",
        approved_strategy_updates=updates,
    )
    revision = intelligence.adopt_recommendation(
        revision_id="learning-revision",
        idempotency_key="learning-revision-once",
        recommendation_id=recommendation.recommendation_id,
        content_package_id=package.package_id,
        strategy_updates=updates,
    )
    duplicate = intelligence.adopt_recommendation(
        revision_id="learning-revision",
        idempotency_key="learning-revision-once",
        recommendation_id=recommendation.recommendation_id,
        content_package_id=package.package_id,
        strategy_updates=updates,
    )
    assert duplicate == revision
    assert revision.decision_id == decision.decision_id
    assert revision.recommendation_id == recommendation.recommendation_id
    assert revision.content_package_id == package.package_id
    assert revision.strategy_version == 2
    assert store.get_content_strategy(revision.strategy_id).content_format == updates["content_format"]
    snapshot = intelligence.dashboard_snapshot(
        content_package_ids=frozenset({package.package_id})
    )
    dashboard_recommendation = snapshot["recommendations"][0]
    assert dashboard_recommendation["recommendationId"] == recommendation.recommendation_id
    assert dashboard_recommendation["reviewStatus"] == "APPROVED"
    assert dashboard_recommendation["humanDecision"]["decision"] == "approved"
    assert dashboard_recommendation["supportingMeasurementIds"] == (
        measurement.measurement_id,
    )
    assert snapshot["strategyRevisions"][0]["decisionId"] == decision.decision_id
    with pytest.raises(PermissionError):
        intelligence.adopt_recommendation(
            revision_id="learning-revision",
            idempotency_key="learning-revision-once",
            recommendation_id=recommendation.recommendation_id,
            content_package_id=package.package_id,
            strategy_updates={"content_format": "different"},
        )
    store.close()


def test_dashboard_snapshot_excludes_cross_package_analysis_evidence(tmp_path):
    store = ContentStore(tmp_path / "dashboard-scope.db")
    first_package = _create_package(store, "First dashboard package")
    second_package = _create_package(store, "Second dashboard package")
    first_artifact = _produce_artifact(
        store,
        tmp_path / "first-media",
        first_package,
    )
    second_artifact = _produce_artifact(
        store,
        tmp_path / "second-media",
        second_package,
    )
    first_measurement = _measurement(
        first_package,
        first_artifact,
        measurement_id="dashboard-first-measurement",
    )
    second_measurement = _measurement(
        second_package,
        second_artifact,
        measurement_id="dashboard-second-measurement",
    )
    store.create_content_performance_measurement(first_measurement)
    store.create_content_performance_measurement(second_measurement)
    analysis = PerformanceAnalysis(
        analysis_id="dashboard-cross-package-analysis",
        idempotency_key="dashboard-cross-package-analysis",
        measurement_ids=(
            first_measurement.measurement_id,
            second_measurement.measurement_id,
        ),
        data_state=DataCollectionState.COMPLETE,
        created_at=datetime(2026, 1, 9, tzinfo=UTC),
    )
    recommendation = ContentPerformanceRecommendation(
        recommendation_id="dashboard-cross-package-recommendation",
        analysis_id=analysis.analysis_id,
        idempotency_key="dashboard-cross-package-recommendation",
        kind=RecommendationKind.TOPIC_FORMAT_PATTERN,
        summary="A cross-package cohort pattern.",
        content_package_ids=(first_package.package_id,),
        supporting_measurement_ids=(first_measurement.measurement_id,),
        observation_periods=(first_measurement.reporting_window,),
        confidence=0.5,
        confidence_limitations=("Observational association only.",),
        created_at=datetime(2026, 1, 9, tzinfo=UTC),
    )
    store.create_performance_analysis(analysis, (recommendation,))

    snapshot = ContentPerformanceIntelligenceService(store).dashboard_snapshot(
        content_package_ids=frozenset({first_package.package_id})
    )

    assert [item["measurementId"] for item in snapshot["measurements"]] == [
        first_measurement.measurement_id,
    ]
    assert snapshot["analyses"] == ()
    assert snapshot["recommendations"] == ()
    store.close()


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="Local FFmpeg and ffprobe are required for runtime media integration.",
)
def test_runtime_wires_provider_free_performance_service_to_configured_store(tmp_path):
    database = tmp_path / "runtime.db"
    store = ContentStore(database)
    package = _create_package(store, "Runtime performance integration")
    store.migrate_publishing_attempt_schema()
    store.migrate_private_render_operation_schema()
    store.close()
    authenticator = WebSocketAuthenticator("T" * 43, principal="operator")
    storage_root = tmp_path / "managed-content"

    runtime = create_content_runtime(
        authenticator,
        database,
        content_storage_root=storage_root,
    )
    try:
        assert runtime.performance_intelligence.store is runtime.store
        assert runtime.status.performance_intelligence is runtime.performance_intelligence
        plan = runtime.media_production.create_plan(
            package.package_id,
            configuration={
                "width": 180,
                "height": 320,
                "frame_rate": 5,
                "aspect_ratio": "9:16",
                "scene_duration_seconds": 0.5,
            },
        )
        artifact = runtime.media_production.execute(plan.job_id)
        media_path = Path(artifact.storage_reference).resolve()
        assert storage_root.resolve() in media_path.parents
        assert media_path.is_file()
        assert hashlib.sha256(media_path.read_bytes()).hexdigest() == artifact.output_sha256
        decode = subprocess.run(
            [
                shutil.which("ffmpeg") or "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-v",
                "error",
                "-xerror",
                "-i",
                str(media_path),
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert decode.returncode == 0, decode.stderr
        measurement = runtime.performance_intelligence.record_measurement(
            _measurement(
                package,
                artifact,
                measurement_id="runtime-measurement",
                views=321,
            )
        )
        cost_report = runtime.performance_intelligence.record_cost_report(
            _cost_report(
                package,
                artifact,
                report_id="runtime-cost-report",
                amount=Decimal("3.50"),
            )
        )
        analysis = runtime.performance_intelligence.analyze(
            analysis_id="runtime-analysis",
            idempotency_key="runtime-analysis",
            measurement_ids=(measurement.measurement_id,),
            cost_report_ids=(cost_report.report_id,),
        )
        assert analysis.data_state == DataCollectionState.COMPLETE
        status = runtime.status.for_principal("operator")
        performance = status["services"]["performanceIntelligence"]
        assert performance["livePollingEnabled"] is False
        source_states = {
            item["providerId"]: item
            for item in performance["dataSources"]
        }
        assert source_states == {
            "deterministic_fixture": {
                "providerId": "deterministic_fixture",
                "connectionState": "disconnected",
                "collectionState": "complete",
                "lastObservedAt": measurement.observed_at.isoformat().replace(
                    "+00:00",
                    "Z",
                ),
                "detail": "No provider is connected; no live analytics polling is configured.",
            },
            "youtube_analytics": {
                "providerId": "youtube_analytics",
                "connectionState": "disconnected",
                "collectionState": "no_measurements",
                "lastObservedAt": None,
                "detail": "No provider is connected; no live analytics polling is configured.",
            },
        }
        dashboard_measurement = performance["measurements"][0]
        assert dashboard_measurement["measurementId"] == measurement.measurement_id
        assert dashboard_measurement["metrics"]["views"] == 321
        assert dashboard_measurement["sourceName"] == "deterministic_fixture"
        assert dashboard_measurement["reportingWindow"]["startsAt"] == (
            _REPORTING_WINDOW.starts_at.isoformat().replace("+00:00", "Z")
        )
        assert performance["analyses"][0]["analysisId"] == analysis.analysis_id
        assert performance["costReports"][0]["reportId"] == cost_report.report_id
        assert performance["analyses"][0]["economics"][0]["totalProductionCost"] == "3.50"
        assert status["services"]["contentPackages"][0]["packageId"] == package.package_id
        production_job = status["services"]["contentPackages"][0]["productionJobs"][0]
        assert production_job["mediaArtifact"]["artifactId"] == artifact.artifact_id
        assert production_job["mediaArtifact"]["outputSha256"] == artifact.output_sha256
        assert production_job["outputArtifactReference"] is None
        assert (
            runtime.content_storage.clip_staging_directory.parent
            == storage_root.resolve()
        )
    finally:
        runtime.close()

    reopened = create_content_runtime(
        authenticator,
        database,
        content_storage_root=storage_root,
    )
    try:
        recovered_measurement = reopened.performance_intelligence.store.get_content_performance_measurement(
            "runtime-measurement"
        )
        recovered_analysis = reopened.performance_intelligence.store.get_performance_analysis(
            "runtime-analysis"
        )
        recovered_artifact = reopened.store.get_media_artifact(artifact.artifact_id)
        assert recovered_measurement == measurement
        assert recovered_analysis == analysis
        assert reopened.store.get_production_cost_report(
            cost_report.report_id
        ) == cost_report
        assert recovered_artifact == artifact
        assert Path(recovered_artifact.storage_reference).is_file()
        assert reopened.performance_intelligence.record_measurement(
            recovered_measurement
        ) == measurement
        assert reopened.performance_intelligence.analyze(
            analysis_id="runtime-analysis",
            idempotency_key="runtime-analysis",
            measurement_ids=(measurement.measurement_id,),
            cost_report_ids=(cost_report.report_id,),
        ) == analysis
        assert len(
            reopened.performance_intelligence.list_measurements(
                content_package_id=package.package_id
            )
        ) == 1
    finally:
        reopened.close()


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="Local FFmpeg and ffprobe are required for integrated CRE acceptance.",
)
def test_runtime_end_to_end_cre_acceptance_recovers_dry_run_and_dashboard(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "integrated-cre.db"
    storage_root = tmp_path / "managed-content"
    seed_store = ContentStore(database)
    seed_store.migrate_publishing_attempt_schema()
    seed_store.migrate_private_render_operation_schema()
    seed_store.close()

    monkeypatch.setenv("ULTRON_CRE_PUBLISHING_DRY_RUN_ENABLED", "true")
    operator_token = "T" * 43
    authenticator = WebSocketAuthenticator(
        operator_token,
        principal="operator",
    )
    runtime = create_content_runtime(
        authenticator,
        database,
        content_storage_root=storage_root,
    )
    worker = None
    try:
        package = _create_package(runtime.store, "Integrated CRE acceptance")
        script = runtime.store.get_script_artifact(package.script_artifact_id)
        assert script is not None
        runtime.store.create_approval_decision(
            decision_id="integrated-script-approval",
            workflow_id=script.workflow_id,
            artifact_type="script",
            artifact_id=script.artifact_id,
            artifact_version=script.version,
            artifact_sha256=script.content_sha256,
            decision="approved",
            actor="independent-reviewer",
        )

        plan = runtime.media_production.create_plan(
            package.package_id,
            configuration={
                "width": 180,
                "height": 320,
                "frame_rate": 5,
                "aspect_ratio": "9:16",
                "scene_duration_seconds": 0.5,
            },
        )
        artifact = runtime.media_production.execute(plan.job_id)
        media_path = Path(artifact.storage_reference).resolve()
        assert storage_root.resolve() in media_path.parents
        assert hashlib.sha256(media_path.read_bytes()).hexdigest() == artifact.output_sha256
        decode = subprocess.run(
            [
                shutil.which("ffmpeg") or "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-v",
                "error",
                "-xerror",
                "-i",
                str(media_path),
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert decode.returncode == 0, decode.stderr
        subtitles = runtime.store.list_media_subtitle_artifacts(plan.job_id)
        assert subtitles
        assert all(
            item.script_artifact_id == script.artifact_id
            and item.script_version == script.version
            and item.script_sha256 == script.content_sha256
            for item in subtitles
        )

        retention_points = []
        cursor = 0.0
        for scene in plan.scenes:
            end = cursor + scene.duration_seconds
            retention_points.append(
                RetentionInterval(
                    start_seconds=cursor,
                    end_seconds=end,
                    retention_start=0.95 if scene.sequence == 1 else 0.75,
                    retention_end=0.50 if scene.sequence == 1 else 0.70,
                    rewatch_fraction=0.08,
                )
            )
            cursor = end
        measurement = runtime.performance_intelligence.record_measurement(
            _measurement(
                package,
                artifact,
                measurement_id="integrated-measurement",
                views=321,
                revenue=Decimal("10.00"),
                revenue_currency="USD",
                retention=tuple(retention_points),
            )
        )
        cost_report = runtime.performance_intelligence.record_cost_report(
            _cost_report(
                package,
                artifact,
                report_id="integrated-cost-report",
                amount=Decimal("3.50"),
            )
        )
        analysis = runtime.performance_intelligence.analyze(
            analysis_id="integrated-analysis",
            idempotency_key="integrated-analysis-once",
            measurement_ids=(measurement.measurement_id,),
            cost_report_ids=(cost_report.report_id,),
        )
        assert analysis.data_state == DataCollectionState.COMPLETE
        assert analysis.economics[0].net_revenue == Decimal("6.50")
        assert analysis.economics[0].roi_percent is not None
        retention_recommendation = next(
            item
            for item in runtime.store.list_performance_recommendations(
                analysis_id=analysis.analysis_id
            )
            if item.kind == RecommendationKind.RETENTION_DROP
        )
        first_scene_result = next(
            item
            for item in analysis.scene_retention
            if item.sequence == 1
        )
        assert first_scene_result.scene_id == plan.scenes[0].scene_id
        assert first_scene_result.coverage_fraction == 1
        assert first_scene_result.drop_fraction == pytest.approx(0.45)
        decision = runtime.performance_intelligence.review_recommendation(
            decision_id="integrated-human-decision",
            recommendation_id=retention_recommendation.recommendation_id,
            actor="operator",
            decision=HumanInsightDecisionKind.APPROVED,
            reason="Reviewed the measured scene retention and stated limitations.",
        )
        assert decision.recommendation_id == retention_recommendation.recommendation_id

        publication_service = runtime.publication_preparation
        assert type(
            publication_service.adapters.get(DistributionPlatform.YOUTUBE)
        ).__name__ == "DryRunPlatformAdapter"
        subtitle_ids = tuple(item.artifact_id for item in subtitles)
        metadata = {
            "title": "Integrated acceptance",
            "description": "Offline dry-run acceptance fixture.",
            "tags": ("cre", "offline"),
            "category": None,
            "thumbnail_reference": None,
            "subtitle_artifact_ids": subtitle_ids,
            "visibility": "private",
            "mode": PublicationMode.DRY_RUN.value,
            "scheduled_at": None,
        }
        binding = {
            "content_package_id": package.package_id,
            "script_artifact_id": script.artifact_id,
            "script_version": script.version,
            "script_sha256": script.content_sha256,
            "media_artifact_id": artifact.artifact_id,
            "media_sha256": artifact.output_sha256,
            "platform": DistributionPlatform.YOUTUBE.value,
            "account_id": "offline-test-channel",
            "requested_by": "operator",
            "metadata": metadata,
        }
        approval_fingerprint = publication_service._fingerprint(binding)
        package_id = f"publication-{approval_fingerprint[:32]}"
        request_payload = {
            "publication_package_id": package_id,
            "approval_fingerprint": approval_fingerprint,
            "platform": DistributionPlatform.YOUTUBE.value,
            "account_id": "offline-test-channel",
            "requested_by": "operator",
            "mode": PublicationMode.DRY_RUN.value,
        }
        request_context = _authorized_context(
            authenticator,
            operator_token,
            publication_service.ACTION,
            request_payload,
        )
        runtime.store._connection.execute(
            """
            CREATE TEMP TRIGGER fail_publication_package_insert
            BEFORE INSERT ON content_publication_packages
            BEGIN
                SELECT RAISE(ABORT, 'injected publication persistence interruption');
            END;
            """
        )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="injected publication persistence interruption",
        ):
            publication_service.create_package(
                context=request_context,
                command_id="integrated-publication-package",
                content_package_id=package.package_id,
                media_artifact_id=artifact.artifact_id,
                platform=DistributionPlatform.YOUTUBE,
                account_id="offline-test-channel",
                title="Integrated acceptance",
                description="Offline dry-run acceptance fixture.",
                tags=("cre", "offline"),
                mode=PublicationMode.DRY_RUN,
            )
        runtime.store._connection.execute(
            "DROP TRIGGER fail_publication_package_insert"
        )
        assert runtime.store.get_publication_package(package_id) is None
        retry_context = _authorized_context(
            authenticator,
            operator_token,
            publication_service.ACTION,
            request_payload,
        )
        publication_package = publication_service.create_package(
            context=retry_context,
            command_id="integrated-publication-package",
            content_package_id=package.package_id,
            media_artifact_id=artifact.artifact_id,
            platform=DistributionPlatform.YOUTUBE,
            account_id="offline-test-channel",
            title="Integrated acceptance",
            description="Offline dry-run acceptance fixture.",
            tags=("cre", "offline"),
            mode=PublicationMode.DRY_RUN,
        )
        assert publication_package.approval_fingerprint == approval_fingerprint
        assert publication_package.script_artifact_id == script.artifact_id
        assert publication_package.script_version == script.version
        assert publication_package.script_sha256 == script.content_sha256
        assert publication_package.media_artifact_id == artifact.artifact_id
        assert publication_package.media_sha256 == artifact.output_sha256

        reviewer_token = "R" * 43
        reviewer_authority = WebSocketAuthenticator(
            reviewer_token,
            principal="reviewer",
        )
        approval_payload = {
            "publication_package_id": package_id,
            "approval_fingerprint": approval_fingerprint,
            "approved_by": "reviewer",
        }
        approval_context = _authorized_context(
            reviewer_authority,
            reviewer_token,
            publication_service.APPROVAL_ACTION,
            approval_payload,
        )
        reviewer_service = ContentPublicationPreparationService(
            content_rights=ContentRightsService(
                store=runtime.store,
                authority=reviewer_authority,
                content_storage=runtime.content_storage,
            ),
            storage=runtime.content_storage,
            adapters=publication_service.adapters,
        )
        approval, distribution_job = reviewer_service.approve_package(
            context=approval_context,
            command_id="integrated-publication-approval",
            publication_package_id=package_id,
        )
        repeated_approval_context = _authorized_context(
            reviewer_authority,
            reviewer_token,
            publication_service.APPROVAL_ACTION,
            approval_payload,
        )
        repeated_approval, repeated_job = reviewer_service.approve_package(
            context=repeated_approval_context,
            command_id="integrated-publication-approval",
            publication_package_id=package_id,
        )
        assert repeated_approval == approval
        assert repeated_job == distribution_job
        assert approval.requested_by == "operator"
        assert approval.approved_by == "reviewer"

        dry_run_task = runtime.store.create_content_workflow_task(
            task_id="integrated-dry-run-task",
            workflow_id=package.workflow_id,
            task_type="dry_run_publication",
            idempotency_key="integrated-dry-run-once",
            input_data={"job_id": distribution_job.job_id},
        )
        assert runtime.store.create_content_workflow_task(
            task_id="integrated-dry-run-task",
            workflow_id=package.workflow_id,
            task_type="dry_run_publication",
            idempotency_key="integrated-dry-run-once",
            input_data={"job_id": distribution_job.job_id},
        ) == dry_run_task
        worker = ContentWorkflowRuntime(
            runtime.store,
            worker_id="integrated-dry-run-worker",
            permissions=frozenset({"content.publish"}),
            poll_interval_seconds=0.01,
            publication_preparation_service=publication_service,
        )
        worker.start()
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                dry_run_task = runtime.store.get_content_workflow_task(
                    dry_run_task.task_id
                )
                if dry_run_task is not None and dry_run_task.state == "completed":
                    break
                time.sleep(0.01)
        finally:
            worker.stop()
        assert dry_run_task is not None and dry_run_task.state == "completed"
        dry_run_result = json.loads(dry_run_task.result_json)
        assert dry_run_result == {
            "accepted": True,
            "confirmed": False,
            "job_id": distribution_job.job_id,
            "remote_publication_id": None,
            "remote_status": "dry_run_validated",
        }
        assert runtime.youtube_publishing is None
        assert "publish_youtube" not in (
            runtime.workflow_runtime.handlers.available()
            if runtime.workflow_runtime
            else ()
        )
        assert runtime.workflow_runtime is None

        status = runtime.status.for_principal("operator")
        projected = next(
            item
            for item in status["services"]["contentPackages"]
            if item["packageId"] == package.package_id
        )
        assert projected["productionJobs"][0]["mediaArtifact"]["artifactId"] == artifact.artifact_id
        assert projected["productionJobs"][0]["outputArtifactReference"] is None
        assert projected["publicationPackages"][0]["approvalState"] == "APPROVED"
        performance = status["services"]["performanceIntelligence"]
        assert performance["livePollingEnabled"] is False
        assert performance["measurements"][0]["sourceName"] == "deterministic_fixture"
        assert performance["measurements"][0]["metrics"]["views"] == 321
        assert performance["analyses"][0]["analysisId"] == analysis.analysis_id
        assert performance["analyses"][0]["economics"][0]["netRevenue"] == "6.50"
        projected_recommendation = next(
            item
            for item in performance["recommendations"]
            if item["recommendationId"] == retention_recommendation.recommendation_id
        )
        assert projected_recommendation["reviewStatus"] == "APPROVED"
        assert (
            projected_recommendation["humanDecision"]["decisionId"]
            == decision.decision_id
        )
        assert str(storage_root.resolve()) not in json.dumps(status)
        with pytest.raises(PermissionError):
            runtime.status.for_principal("unmapped-owner")
    finally:
        if worker is not None:
            worker.stop()
        runtime.close()

    recovered = create_content_runtime(
        authenticator,
        database,
        content_storage_root=storage_root,
    )
    try:
        assert recovered.store.get_media_artifact(artifact.artifact_id) == artifact
        assert recovered.store.get_publication_package(package_id) == publication_package
        assert recovered.store.get_publication_approval(package_id) == approval
        assert recovered.store.get_distribution_job(distribution_job.job_id) == distribution_job
        assert recovered.store.get_content_workflow_task(dry_run_task.task_id) == dry_run_task
        assert (
            recovered.performance_intelligence.store.get_content_performance_measurement(
                measurement.measurement_id
            )
            == measurement
        )
        assert recovered.store.get_production_cost_report(cost_report.report_id) == cost_report
        assert recovered.store.get_performance_analysis(analysis.analysis_id) == analysis
        assert recovered.store.get_human_insight_decision(
            retention_recommendation.recommendation_id
        ) == decision
        reopened_status = recovered.status.for_principal("operator")
        reopened_package = next(
            item
            for item in reopened_status["services"]["contentPackages"]
            if item["packageId"] == package.package_id
        )
        assert reopened_package["publicationPackages"][0]["jobState"] == "APPROVED"
        assert (
            reopened_status["services"]["performanceIntelligence"]["recommendations"][0][
                "reviewStatus"
            ]
            == "APPROVED"
        )
    finally:
        recovered.close()
