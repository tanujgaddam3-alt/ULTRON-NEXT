from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from tests.application.acquisition_test_utils import AcquisitionHarness
from ultron.application.content.discovery import ContentDiscoveryService
from ultron.application.content.sources import SourceDocument
from ultron.application.content.store import (
    ContentStore,
    IdempotencyConflictError,
)
from ultron.domain.content import (
    ContentPlatform,
    RightsAction,
    RightsEvaluation,
    RightsState,
    RightsUse,
)
from ultron.domain.content_acquisition import (
    AcquisitionAssetHandoff,
    AcquisitionIntendedUse,
    AcquisitionRequestState,
    AcquisitionRightsStatus,
    ContentAcquisitionRequest,
)

from ultron.application.content.evidence_trust_policy import RightsDecisionModel


def test_commercial_acquisition_rejects_residual_risk_policy(tmp_path):
    harness = AcquisitionHarness(tmp_path / "residual-risk.db")
    try:
        class ResidualRiskPolicy:
            def evaluate_operation(self, result, **kwargs):
                return RightsDecisionModel.POLICY_ACCEPTED_WITH_RESIDUAL_RISK, (
                    "owner residual-risk threshold met",
                )

        harness.acquisition.evidence_trust_policy = ResidualRiskPolicy()
        with pytest.raises(
            PermissionError,
            match="Residual-risk acceptance cannot authorize commercial publication",
        ):
            harness.acquisition.evaluate_acquisition_policy(
                intended_use=AcquisitionIntendedUse.COMMERCIAL_PUBLICATION,
                rights_result=object(),
                duration_seconds=60.0,
                territory="US",
            )
    finally:
        harness.close()


def test_explicit_selection_persists_identity_rights_pending_and_workflow(tmp_path):
    harness = AcquisitionHarness(tmp_path / "acquisition.db")
    try:
        opportunity = harness.create_scored_opportunity()
        assert (
            harness.store.get_content_acquisition_request_for_opportunity(
                opportunity.opportunity_id
            )
            is None
        )

        workflow = harness.select(opportunity)
        request = harness.store.get_content_acquisition_request_for_opportunity(
            opportunity.opportunity_id
        )

        assert request is not None
        assert request.owner_id == "alice"
        assert request.opportunity_id == opportunity.opportunity_id
        assert request.source_id == harness.store.list_content_evidence(
            opportunity.opportunity_id
        )[0].source_id
        assert request.source_identity.startswith("rss_atom:url-sha256:")
        assert request.provider == "rss_atom"
        assert request.provider_content_id.startswith("entry-")
        assert request.original_url == request.canonical_url
        assert request.resolved_url is None
        assert request.intended_use is AcquisitionIntendedUse.EDITORIAL_DEVELOPMENT
        assert request.authorization_reference
        assert request.rights_status is AcquisitionRightsStatus.PENDING_ASSET_EVALUATION
        assert request.rights_decision_id is None
        assert request.state is AcquisitionRequestState.AWAITING_ADAPTER
        assert request.workflow_id == workflow.workflow_id
        assert request.task_id is None
        assert workflow.owner_id == "alice"
        assert workflow.state.value == "discovered"
        assert harness.store.get_content_opportunity(
            opportunity.opportunity_id
        ).status.value == "selected"
        assert harness.store._connection.execute(
            "SELECT count(*) FROM content_workflow_tasks WHERE workflow_id = ?",
            (workflow.workflow_id,),
        ).fetchone()[0] == 0
        events = harness.store.list_content_acquisition_request_events(
            request.request_id
        )
        assert [event.to_state for event in events] == [
            AcquisitionRequestState.REQUESTED,
            AcquisitionRequestState.VALIDATING,
            AcquisitionRequestState.AWAITING_ADAPTER,
        ]
    finally:
        harness.close()


def test_selection_rejects_unknown_source_and_forged_owner(tmp_path):
    harness = AcquisitionHarness(tmp_path / "validation.db")
    try:
        opportunity = harness.create_scored_opportunity()
        source_id = harness.store.list_content_evidence(
            opportunity.opportunity_id
        )[0].source_id
        payload = {
            "opportunity_id": opportunity.opportunity_id,
            "source_id": source_id,
            "intended_use": AcquisitionIntendedUse.EDITORIAL_DEVELOPMENT.value,
            "idempotency_key": "request-1",
        }
        valid_context = harness.context(
            "alice", "content_request_acquisition", payload
        )
        forged_context = replace(valid_context, principal="bob")
        with pytest.raises(PermissionError):
            harness.select(opportunity, context=forged_context)
        with pytest.raises(ValueError, match="supported acquisition use"):
            harness.acquisition.select_opportunity(
                opportunity_id=opportunity.opportunity_id,
                source_id=source_id,
                intended_use="unsupported",
                idempotency_key="unsupported-use",
                context=valid_context,
            )
        with pytest.raises(ValueError, match="persisted evidence"):
            harness.select(opportunity, source_id="not-an-evidence-source")
        unknown_payload = {
            **payload,
            "opportunity_id": "missing-opportunity",
            "idempotency_key": "unknown-opportunity",
        }
        with pytest.raises(KeyError, match="Unknown content opportunity"):
            harness.acquisition.select_opportunity(
                opportunity_id=unknown_payload["opportunity_id"],
                source_id=source_id,
                intended_use=AcquisitionIntendedUse.EDITORIAL_DEVELOPMENT,
                idempotency_key=unknown_payload["idempotency_key"],
                context=harness.context(
                    "alice", "content_request_acquisition", unknown_payload
                ),
            )
        assert harness.store.get_content_acquisition_request_for_opportunity(
            opportunity.opportunity_id
        ) is None
    finally:
        harness.close()


def test_idempotent_retry_conflict_and_duplicate_workflow_association(tmp_path):
    harness = AcquisitionHarness(tmp_path / "idempotency.db")
    try:
        opportunity = harness.create_scored_opportunity()
        first_workflow = harness.select(opportunity, idempotency_key="same-key")
        first = harness.store.get_content_acquisition_request_for_opportunity(
            opportunity.opportunity_id
        )
        selected_opportunity = harness.store.get_content_opportunity(
            opportunity.opportunity_id
        )
        replay_workflow = harness.select(
            selected_opportunity, idempotency_key="same-key"
        )
        replay = harness.store.get_content_acquisition_request_for_opportunity(
            opportunity.opportunity_id
        )
        assert replay.request_id == first.request_id
        assert replay_workflow.workflow_id == first_workflow.workflow_id

        evidence = harness.store.list_content_evidence(opportunity.opportunity_id)[0]
        payload = {
            "opportunity_id": opportunity.opportunity_id,
            "source_id": evidence.source_id,
            "intended_use": AcquisitionIntendedUse.COMMERCIAL_PUBLICATION.value,
            "idempotency_key": "same-key",
        }
        context = harness.context(
            "alice", "content_request_acquisition", payload
        )
        with pytest.raises(IdempotencyConflictError):
            harness.acquisition.select_opportunity(
                opportunity_id=opportunity.opportunity_id,
                source_id=evidence.source_id,
                intended_use=AcquisitionIntendedUse.COMMERCIAL_PUBLICATION,
                idempotency_key="same-key",
                context=context,
            )
        with pytest.raises(ValueError, match="already selected"):
            harness.select(
                selected_opportunity, idempotency_key="different-key"
            )
        assert harness.store.get_content_acquisition_request_for_opportunity(
            opportunity.opportunity_id
        ).workflow_id == first_workflow.workflow_id
    finally:
        harness.close()


def test_concurrent_same_owner_selection_is_idempotent(tmp_path):
    harness = AcquisitionHarness(tmp_path / "concurrent-same-owner.db")
    try:
        opportunity = harness.create_scored_opportunity()
        evidence = harness.store.list_content_evidence(opportunity.opportunity_id)[0]
        payload = {
            "opportunity_id": opportunity.opportunity_id,
            "source_id": evidence.source_id,
            "intended_use": AcquisitionIntendedUse.EDITORIAL_DEVELOPMENT.value,
            "idempotency_key": "concurrent-key",
        }
        contexts = [
            harness.context("alice", "content_request_acquisition", payload)
            for _ in range(2)
        ]

        def select(context):
            return harness.acquisition.select_opportunity(
                opportunity_id=opportunity.opportunity_id,
                source_id=evidence.source_id,
                intended_use=AcquisitionIntendedUse.EDITORIAL_DEVELOPMENT,
                idempotency_key="concurrent-key",
                context=context,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            requests = list(pool.map(select, contexts))
        assert requests[0].request_id == requests[1].request_id
        assert requests[0].workflow_id == requests[1].workflow_id
        assert len(
            harness.store.list_content_acquisition_request_events(
                requests[0].request_id
            )
        ) == 3
    finally:
        harness.close()


def test_two_owners_cannot_associate_duplicate_requests_to_one_opportunity(tmp_path):
    harness = AcquisitionHarness(tmp_path / "concurrent-owners.db")
    try:
        opportunity = harness.create_scored_opportunity()
        evidence = harness.store.list_content_evidence(opportunity.opportunity_id)[0]
        requests = []
        failures = []

        def select(owner):
            payload = {
                "opportunity_id": opportunity.opportunity_id,
                "source_id": evidence.source_id,
                "intended_use": AcquisitionIntendedUse.EDITORIAL_DEVELOPMENT.value,
                "idempotency_key": f"{owner}-key",
            }
            return harness.acquisition.select_opportunity(
                opportunity_id=opportunity.opportunity_id,
                source_id=evidence.source_id,
                intended_use=AcquisitionIntendedUse.EDITORIAL_DEVELOPMENT,
                idempotency_key=f"{owner}-key",
                context=harness.context(
                    owner, "content_request_acquisition", payload
                ),
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(select, owner) for owner in ("alice", "bob")]
            for future in futures:
                try:
                    requests.append(future.result())
                except ValueError as error:
                    failures.append(error)
        assert len(requests) == 1
        assert len(failures) == 1
        assert "already selected" in str(failures[0])
        assert harness.store.get_content_acquisition_request_for_opportunity(
            opportunity.opportunity_id
        ).owner_id == requests[0].owner_id
    finally:
        harness.close()


def test_request_survives_store_restart_and_schema_migration_is_repeatable(tmp_path):
    database = tmp_path / "restart.db"
    harness = AcquisitionHarness(database)
    opportunity = harness.create_scored_opportunity()
    workflow = harness.select(opportunity)
    request = harness.store.get_content_acquisition_request_for_opportunity(
        opportunity.opportunity_id
    )
    assert harness.store.migrate_acquisition_request_schema() == 4
    harness.close()

    from ultron.application.content.store import ContentStore

    reopened = ContentStore(database)
    try:
        persisted = reopened.get_content_acquisition_request(request.request_id)
        assert persisted == request
        assert reopened.get_content_workflow(workflow.workflow_id).owner_id == "alice"
        assert len(reopened.list_content_acquisition_request_events(request.request_id)) == 3
    finally:
        reopened.close()


def test_request_schema_migration_preserves_existing_discovery_records(tmp_path):
    store = ContentStore(tmp_path / "migration-preservation.db")
    try:
        discovery = ContentDiscoveryService(store)
        opportunity = discovery.discover(
            topic="Pre-migration opportunity",
            niche="technology",
            target_audience="builders",
            target_language="en",
            content_format="short",
            source_documents=(
                SourceDocument(
                    canonical_url="https://example.com/pre-migration",
                    title="Pre-migration source",
                    summary="Existing source evidence",
                    published_at=None,
                    publisher="Example",
                    source_identity="rss_atom:url-sha256:" + "c" * 64,
                    provider="rss_atom",
                    provider_content_id="pre-migration-entry",
                    original_url="https://example.com/pre-migration",
                ),
            ),
        )
        before_opportunity = store.get_content_opportunity(opportunity.opportunity_id)
        before_evidence = store.list_content_evidence(opportunity.opportunity_id)
        source_id = before_evidence[0].source_id
        before_source = dict(
            store._connection.execute(
                """
                SELECT s.*, i.source_identity, i.provider, i.provider_content_id,
                       i.original_url, i.resolved_url
                FROM content_sources s
                JOIN content_source_identities i ON i.source_id = s.source_id
                WHERE s.source_id = ?
                """,
                (source_id,),
            ).fetchone()
        )

        assert store.migrate_acquisition_request_schema() == 4
        assert store.migrate_acquisition_request_schema() == 4
        assert store.get_content_opportunity(opportunity.opportunity_id) == before_opportunity
        after_source = dict(
            store._connection.execute(
                """
                SELECT s.*, i.source_identity, i.provider, i.provider_content_id,
                       i.original_url, i.resolved_url
                FROM content_sources s
                JOIN content_source_identities i ON i.source_id = s.source_id
                WHERE s.source_id = ?
                """,
                (source_id,),
            ).fetchone()
        )
        assert after_source == before_source
        assert store.list_content_evidence(opportunity.opportunity_id) == before_evidence
        store.verify_complete_runtime_schema(store._connection)
    finally:
        store.close()


def test_request_schema_v2_migration_preserves_requests_and_extends_revocation_state(
    tmp_path,
):
    harness = AcquisitionHarness(tmp_path / "request-schema-v2.db")
    try:
        opportunity = harness.create_scored_opportunity()
        harness.select(opportunity)
        request = harness.store.get_content_acquisition_request_for_opportunity(
            opportunity.opportunity_id
        )
        events = harness.store.list_content_acquisition_request_events(
            request.request_id
        )
        current_trigger = harness.store._acquisition_request_triggers()[
            "content_acquisition_request_state_transition"
        ]
        previous_trigger = current_trigger.replace(
            "'awaiting_rights', 'rejected'",
            "'awaiting_rights'",
        )
        harness.store._connection.execute(
            "DROP TRIGGER content_acquisition_request_state_transition"
        )
        harness.store._connection.execute(previous_trigger)
        harness.store._connection.execute(
            """
            UPDATE content_acquisition_request_schema_meta
            SET schema_version = 2 WHERE singleton = 1
            """
        )

        assert harness.store.migrate_acquisition_request_schema() == 4
        assert harness.store.get_content_acquisition_request(
            request.request_id
        ) == request
        assert harness.store.list_content_acquisition_request_events(
            request.request_id
        ) == events
        trigger = harness.store._connection.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'trigger'
              AND name = 'content_acquisition_request_state_transition'
            """
        ).fetchone()["sql"]
        assert "'awaiting_rights', 'rejected'" in trigger
    finally:
        harness.close()


def test_request_schema_v3_migration_adds_immutable_evidence_assessments(tmp_path):
    harness = AcquisitionHarness(tmp_path / "request-schema-v3.db")
    try:
        opportunity = harness.create_scored_opportunity()
        harness.select(opportunity)
        harness.store._connection.execute(
            "DROP TABLE content_acquisition_evidence_assessments"
        )
        harness.store._connection.execute(
            """
            UPDATE content_acquisition_request_schema_meta
            SET schema_version = 3 WHERE singleton = 1
            """
        )

        assert harness.store.migrate_acquisition_request_schema() == 4
        columns = {
            row["name"]
            for row in harness.store._connection.execute(
                "PRAGMA table_info(content_acquisition_evidence_assessments)"
            )
        }
        assert "policy_decision" in columns
        assert "evidence_json" in columns
        harness.store.verify_complete_runtime_schema(harness.store._connection)
    finally:
        harness.close()


def test_cancellation_during_validation_is_durable_and_terminal(tmp_path):
    harness = AcquisitionHarness(tmp_path / "cancellation.db")
    try:
        opportunity = harness.create_scored_opportunity()
        harness.select(opportunity)
        request = harness.store.get_content_acquisition_request_for_opportunity(
            opportunity.opportunity_id
        )
        validating = harness.store.transition_content_acquisition_request(
            request.request_id,
            expected_version=request.version,
            target_state=AcquisitionRequestState.VALIDATING,
            actor="adapter:validation",
            reason="adapter_validation_started",
        )
        cancelled = harness.acquisition.cancel(
            request.request_id,
            context=harness.context(
                "alice",
                "content_cancel_acquisition",
                {"request_id": request.request_id},
            ),
            command_id="cancel-1",
        )
        repeated = harness.acquisition.cancel(
            request.request_id,
            context=harness.context(
                "alice",
                "content_cancel_acquisition",
                {"request_id": request.request_id},
            ),
            command_id="cancel-2",
        )
        assert validating.state is AcquisitionRequestState.VALIDATING
        assert cancelled.state is AcquisitionRequestState.CANCELLED
        assert repeated.state is AcquisitionRequestState.CANCELLED
        events = harness.store.list_content_acquisition_request_events(
            request.request_id
        )
        assert events[-1].from_state is AcquisitionRequestState.VALIDATING
        assert events[-1].to_state is AcquisitionRequestState.CANCELLED
        with pytest.raises(ValueError, match="Invalid acquisition request transition"):
            harness.store.transition_content_acquisition_request(
                request.request_id,
                expected_version=cancelled.version,
                target_state=AcquisitionRequestState.VALIDATING,
                actor="adapter:validation",
                reason="must_not_reopen",
            )
        with pytest.raises(PermissionError, match="not owned"):
            harness.acquisition.cancel(
                request.request_id,
                context=harness.context(
                    "bob",
                    "content_cancel_acquisition",
                    {"request_id": request.request_id},
                ),
                command_id="bob-cancel",
            )
    finally:
        harness.close()


def test_terminal_request_cannot_be_cancelled_or_reopened(tmp_path):
    harness = AcquisitionHarness(tmp_path / "terminal.db")
    try:
        opportunity = harness.create_scored_opportunity()
        harness.select(opportunity)
        request = harness.store.get_content_acquisition_request_for_opportunity(
            opportunity.opportunity_id
        )
        failed = harness.store.transition_content_acquisition_request(
            request.request_id,
            expected_version=request.version,
            target_state=AcquisitionRequestState.FAILED,
            actor="system:test",
            reason="test_failure",
            error_code="test_failure",
        )
        with pytest.raises(ValueError, match="terminal"):
            harness.acquisition.cancel(
                request.request_id,
                context=harness.context(
                    "alice",
                    "content_cancel_acquisition",
                    {"request_id": request.request_id},
                ),
                command_id="cancel-failed",
            )
        with pytest.raises(ValueError, match="Invalid acquisition request transition"):
            harness.store.transition_content_acquisition_request(
                request.request_id,
                expected_version=failed.version,
                target_state=AcquisitionRequestState.AWAITING_ADAPTER,
                actor="system:test",
                reason="must_not_reopen",
            )
    finally:
        harness.close()


def test_asset_handoff_contract_requires_matching_cleared_asset_rights(tmp_path):
    harness = AcquisitionHarness(tmp_path / "asset-handoff.db")
    try:
        opportunity = harness.create_scored_opportunity()
        harness.select(
            opportunity,
            intended_use=AcquisitionIntendedUse.MONETIZED_PUBLICATION,
        )
        request = harness.store.get_content_acquisition_request_for_opportunity(
            opportunity.opportunity_id
        )
        cleared_request = ContentAcquisitionRequest.model_validate(
            {
                **request.model_dump(),
                "asset_id": "asset-1",
                "rights_status": AcquisitionRightsStatus.CLEARED,
                "rights_decision_id": "rights-decision-1",
                "state": AcquisitionRequestState.COMPLETED,
            }
        )
        use = RightsUse(
            asset_id="asset-1",
            expected_asset_sha256="b" * 64,
            platform=ContentPlatform.YOUTUBE,
            account_id="channel-1",
            territory="US",
            actions=frozenset(
                {
                    RightsAction.PUBLISH,
                    RightsAction.COMMERCIAL_USE,
                    RightsAction.MONETIZE,
                }
            ),
            commercial_intent=True,
            monetization_intent=True,
            duration_seconds=30,
        )
        evaluation = RightsEvaluation(
            decision_id="rights-decision-1",
            asset_id="asset-1",
            use=use,
            copyright_state=RightsState.CLEARED,
            platform_rights_state=RightsState.CLEARED,
            monetization_permission=True,
            policy_version="test-policy",
        )
        handoff = AcquisitionAssetHandoff(
            request=cleared_request,
            asset_owner_id="alice",
            source_reference=cleared_request.original_url,
            asset_id="asset-1",
            asset_sha256="b" * 64,
            rights_decision_id=evaluation.decision_id,
            rights_evaluation=evaluation,
            intended_use=cleared_request.intended_use,
            accepted_at=datetime.now(timezone.utc),
        )
        assert handoff.request.source_identity == request.source_identity
        with pytest.raises(ValidationError, match="active request"):
            AcquisitionAssetHandoff(
                request=request,
                asset_owner_id="alice",
                source_reference=request.original_url,
                asset_id="asset-1",
                asset_sha256="b" * 64,
                rights_decision_id=evaluation.decision_id,
                rights_evaluation=evaluation,
                intended_use=request.intended_use,
                accepted_at=datetime.now(timezone.utc),
            )
        uncleared_evaluation = evaluation.model_copy(
            update={"copyright_state": RightsState.BLOCKED}
        )
        with pytest.raises(ValidationError, match="cleared asset-level rights"):
            AcquisitionAssetHandoff(
                request=cleared_request,
                asset_owner_id="alice",
                source_reference=cleared_request.original_url,
                asset_id="asset-1",
                asset_sha256="b" * 64,
                rights_decision_id=uncleared_evaluation.decision_id,
                rights_evaluation=uncleared_evaluation,
                intended_use=cleared_request.intended_use,
                accepted_at=datetime.now(timezone.utc),
            )
        mismatched_use = use.model_copy(update={"asset_id": "other-asset"})
        mismatched_evaluation = evaluation.model_copy(
            update={"use": mismatched_use}
        )
        with pytest.raises(ValidationError, match="does not match"):
            AcquisitionAssetHandoff(
                request=cleared_request,
                asset_owner_id="alice",
                source_reference=cleared_request.original_url,
                asset_id="asset-1",
                asset_sha256="b" * 64,
                rights_decision_id=evaluation.decision_id,
                rights_evaluation=mismatched_evaluation,
                intended_use=cleared_request.intended_use,
                accepted_at=datetime.now(timezone.utc),
            )
        revoked_evaluation = evaluation.model_copy(
            update={"platform_rights_state": RightsState.REVOKED}
        )
        with pytest.raises(ValidationError, match="cleared asset-level rights"):
            AcquisitionAssetHandoff(
                request=cleared_request,
                asset_owner_id="alice",
                source_reference=cleared_request.original_url,
                asset_id="asset-1",
                asset_sha256="b" * 64,
                rights_decision_id=revoked_evaluation.decision_id,
                rights_evaluation=revoked_evaluation,
                intended_use=cleared_request.intended_use,
                accepted_at=datetime.now(timezone.utc),
            )
    finally:
        harness.close()
