from datetime import UTC, datetime, timedelta

import pytest

from ultron.application.content.discovery import ContentDiscoveryService
from ultron.application.content.store import ContentStore
from ultron.application.content.strategy import (
    ContentMetadataService,
    ContentQualityEvaluator,
    ContentStrategyService,
    EditorialCalendarService,
    ScriptGenerationService,
    ThumbnailBriefService,
)
from ultron.application.content.workflow import ContentWorkflowService
from ultron.application.content.workflow import (
    ContentTaskHandlerRegistry,
    default_content_task_handlers,
    register_content_strategy_handlers,
)
from ultron.domain.content_strategy import (
    AudienceKnowledgeLevel,
    EditorialPlan,
)
from tests.application.test_content_discovery import _document


def _researched(store):
    opportunity = ContentDiscoveryService(store).discover(
        topic="Evidence topic",
        niche="technology",
        target_audience="builders",
        target_language="en",
        content_format="short",
        source_documents=(_document(),),
    )
    _, report = ContentDiscoveryService(store).research(
        opportunity,
        summary="Evidence-backed summary.",
    )
    return opportunity, report


def test_strategy_angles_calendar_and_script_are_durable(tmp_path):
    store = ContentStore(tmp_path / "strategy.db")
    opportunity, report = _researched(store)
    strategy_service = ContentStrategyService(store)
    brief = strategy_service.create_brief(
        opportunity_id=opportunity.opportunity_id,
        research_report_id=report.report_id,
        target_audience="builders",
        audience_knowledge_level=AudienceKnowledgeLevel.INTERMEDIATE,
        intended_audience_outcome="Understand the evidence.",
        content_objective="Educate",
        content_format="short",
        platform="youtube",
        language="en",
        competitive_positioning="Evidence-first explanation.",
        differentiation_strategy="Separate measured observations from assumptions.",
        supporting_evidence_ids=tuple(
            item.evidence_id for item in store.list_content_evidence(opportunity.opportunity_id)
        ),
    )
    angles = strategy_service.generate_angles(brief)
    assert len(angles) == 3
    assert len({angle.unique_perspective for angle in angles}) == 3
    plan = EditorialPlan(
        plan_id="plan-1",
        idempotency_key="plan-key",
        opportunity_id=opportunity.opportunity_id,
        strategy_id=brief.strategy_id,
        platform="youtube",
        content_format="short",
        planned_publish_at=datetime.now(UTC) + timedelta(days=1),
        priority=10,
        production_complexity="low",
        assigned_owner="alice",
        created_at=datetime.now(UTC),
    )
    calendar = EditorialCalendarService(store)
    assert calendar.plan(plan) == calendar.plan(plan)
    workflow = ContentWorkflowService(store).discover(
        owner_id="alice",
        title=opportunity.topic,
        idempotency_key="workflow-key",
        request_fingerprint="workflow-fingerprint",
    )
    artifact, script = ScriptGenerationService(store).generate(
        workflow_id=workflow.workflow_id,
        strategy=brief,
        angle=angles[0],
        intended_duration_seconds=60,
    )
    assert artifact.version == 1
    assert script.sections[0].narration
    assert store.get_script_artifact(artifact.artifact_id).content_sha256 == artifact.content_sha256
    store.close()


def test_strategy_rejects_unlinked_audience_evidence(tmp_path):
    store = ContentStore(tmp_path / "strategy-validation.db")
    opportunity, report = _researched(store)
    with pytest.raises(ValueError, match="Audience observations"):
        ContentStrategyService(store).create_brief(
            opportunity_id=opportunity.opportunity_id,
            research_report_id=report.report_id,
            target_audience="builders",
            audience_knowledge_level=AudienceKnowledgeLevel.UNKNOWN,
            intended_audience_outcome="Outcome",
            content_objective="Educate",
            content_format="short",
            platform="youtube",
            language="en",
            competitive_positioning="Positioning",
            differentiation_strategy="Difference",
            audience_problems=("Unverified problem",),
        )
    store.close()


def test_metadata_thumbnail_and_quality_are_deterministic(tmp_path):
    store = ContentStore(tmp_path / "records.db")
    opportunity, report = _researched(store)
    brief = ContentStrategyService(store).create_brief(
        opportunity_id=opportunity.opportunity_id,
        research_report_id=report.report_id,
        target_audience="builders",
        audience_knowledge_level=AudienceKnowledgeLevel.BEGINNER,
        intended_audience_outcome="Understand",
        content_objective="Educate",
        content_format="short",
        platform="youtube",
        language="en",
        competitive_positioning="Evidence first",
        differentiation_strategy="Clear limitations",
    )
    workflow = ContentWorkflowService(store).discover(
        owner_id="alice",
        title="Workflow",
        idempotency_key="records-workflow",
        request_fingerprint="records-fingerprint",
    )
    angle = ContentStrategyService(store).generate_angles(brief)[0]
    artifact, script = ScriptGenerationService(store).generate(
        workflow_id=workflow.workflow_id,
        strategy=brief,
        angle=angle,
        intended_duration_seconds=60,
    )
    metadata = ContentMetadataService(store).create(
        title_candidates=("Evidence topic explained",),
        description="A bounded evidence-first explanation.",
        keywords=("evidence",),
        thumbnail_text="Evidence first",
        script_artifact_id=artifact.artifact_id,
        script_artifact_version=artifact.version,
        script_artifact_sha256=artifact.content_sha256,
    )
    thumbnail = ThumbnailBriefService(store).create(
        main_subject="Evidence topic",
        visual_composition="Subject left, evidence card right",
        focal_point="Evidence card",
        suggested_background="Neutral studio",
        text_overlay="Evidence first",
        visual_hierarchy=("subject", "evidence", "text"),
        aspect_ratio="16:9",
        platform="youtube",
        brand_guidance="Clear, non-deceptive",
        script_artifact_id=artifact.artifact_id,
        script_artifact_version=artifact.version,
        script_artifact_sha256=artifact.content_sha256,
    )
    evaluation = ContentQualityEvaluator().evaluate(
        script,
        metadata,
        artifact_id=artifact.artifact_id,
        artifact_version=artifact.version,
        artifact_sha256=artifact.content_sha256,
        store=store,
    )
    assert store.get_content_metadata(metadata.metadata_id) == metadata
    assert store.get_thumbnail_brief(thumbnail.thumbnail_id) == thumbnail
    assert store.get_quality_evaluation(evaluation.evaluation_id) == evaluation
    store.close()
    reopened = ContentStore(tmp_path / "records.db")
    assert reopened.get_content_metadata(metadata.metadata_id) == metadata
    assert reopened.get_thumbnail_brief(thumbnail.thumbnail_id) == thumbnail
    assert reopened.get_quality_evaluation(evaluation.evaluation_id) == evaluation
    reopened.close()
    assert thumbnail.platform == "youtube"
    assert metadata.description


def test_provider_manager_and_explicit_strategy_handler_registration(tmp_path):
    class Provider:
        provider_id = "mock"
        model_version = "mock-1"

        def generate(self, prompt, context):
            return '{"title":"T","target_audience":"A","platform":"youtube","intended_duration_seconds":30,"content_objective":"O","opening_hook":"H","introduction":"I","sections":[{"heading":"S","narration":"N","visual_direction":"V"}],"closing":"C"}'

    store = ContentStore(tmp_path / "provider.db")
    opportunity, report = _researched(store)
    brief = ContentStrategyService(store).create_brief(
        opportunity_id=opportunity.opportunity_id,
        research_report_id=report.report_id,
        target_audience="builders",
        audience_knowledge_level=AudienceKnowledgeLevel.UNKNOWN,
        intended_audience_outcome="Understand",
        content_objective="Educate",
        content_format="short",
        platform="youtube",
        language="en",
        competitive_positioning="Evidence first",
        differentiation_strategy="Clear limitations",
    )
    angle = ContentStrategyService(store).generate_angles(brief)[0]
    workflow = ContentWorkflowService(store).discover(
        owner_id="alice",
        title="Provider workflow",
        idempotency_key="provider-workflow",
        request_fingerprint="provider-fingerprint",
    )
    from ultron.ai.llm.provider import ProviderManager
    artifact, _ = ScriptGenerationService(
        store,
        provider_manager=ProviderManager(Provider()),
    ).generate(
        workflow_id=workflow.workflow_id,
        strategy=brief,
        angle=angle,
        intended_duration_seconds=30,
    )
    assert '"provider_id":"mock"' in artifact.generation_metadata_json
    registry = default_content_task_handlers()
    assert "generate_script" not in registry.available()
    register_content_strategy_handlers(registry)
    assert "generate_script" in registry.available()
    with pytest.raises(ValueError):
        register_content_strategy_handlers(registry)
    store.close()
