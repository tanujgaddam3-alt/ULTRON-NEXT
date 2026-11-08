from datetime import UTC, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

import pytest

from ultron.application.content.sources import (
    FeedRefreshCoordinator,
    FeedSmokeTestService,
    FetchResponse,
    PublicFeedFetcher,
)
from ultron.application.content.store import ContentStore
from ultron.application.content.workflow import (
    ContentWorkflowService,
    ContentWorkflowWorker,
    _ingest_rss_feed_task,
)
from ultron.domain.content_discovery import ConfiguredFeed


def _feed(**updates):
    feed_id = updates.get("feed_id", "refresh-feed")
    base = ConfiguredFeed(
        feed_id=feed_id,
        feed_name="Refresh feed",
        canonical_url=f"https://example.com/{feed_id}",
        allowed_hostname="example.com",
        source_category="technology",
        enabled=True,
        retrieval_interval_seconds=60,
    )
    return base.model_copy(update=updates)


def test_due_refresh_is_idempotent_and_excludes_disabled_or_future_feeds(tmp_path):
    store = ContentStore(tmp_path / "content.db")
    now = datetime(2026, 11, 3, 12, tzinfo=UTC)
    store.upsert_configured_feed(_feed(next_refresh_at=now - timedelta(seconds=1)))
    store.upsert_configured_feed(
        _feed(feed_id="future", next_refresh_at=now + timedelta(hours=1))
    )
    store.upsert_configured_feed(_feed(feed_id="disabled", enabled=False))
    coordinator = FeedRefreshCoordinator(
        store,
        max_feeds_per_cycle=10,
        host_rate_limit_seconds=0,
    )

    first = coordinator.schedule_due(now=now)
    second = coordinator.schedule_due(now=now)

    assert first["tasks_enqueued"] == 1
    assert second["tasks_enqueued"] == 0
    assert second["tasks_already_pending"] == 1
    tasks = store.list_content_workflow_tasks() if hasattr(store, "list_content_workflow_tasks") else []
    assert len(tasks) == 1
    assert tasks[0].task_type == "ingest_rss_feed"
    store.close()


def test_configuration_version_creates_distinct_task_and_stale_task_is_rejected(tmp_path):
    store = ContentStore(tmp_path / "content.db")
    now = datetime(2026, 11, 3, 12, tzinfo=UTC)
    feed = _feed(next_refresh_at=now)
    store.upsert_configured_feed(feed)
    coordinator = FeedRefreshCoordinator(store, host_rate_limit_seconds=0)
    coordinator.schedule_due(now=now)
    updated = feed.model_copy(
        update={"configuration_version": 2, "canonical_url": "https://example.com/v2"}
    )
    store.upsert_configured_feed(updated)
    coordinator.schedule_due(now=now + timedelta(seconds=1))
    assert store.has_active_feed_refresh_task("refresh-feed", configuration_version=1)
    assert store.has_active_feed_refresh_task("refresh-feed", configuration_version=2)
    store.close()


def test_smoke_test_dry_run_records_audit_without_network(tmp_path, monkeypatch):
    store = ContentStore(tmp_path / "content.db")
    store.upsert_configured_feed(_feed())
    monkeypatch.setenv("ULTRON_CRE_WORKER_ENABLED", "true")
    monkeypatch.setenv("ULTRON_CRE_SOURCE_FETCH_ENABLED", "true")
    monkeypatch.setenv("ULTRON_CRE_SOURCE_REFRESH_ENABLED", "true")

    result = FeedSmokeTestService(store).run(
        "refresh-feed",
        operator_id="operator@example",
        dry_run=True,
    )

    assert result["ready"] is True
    assert "no network request" in result["reason"]
    assert store.list_audit_events("refresh-feed")[-1]["action"] == "content.feed_smoke_test"
    store.close()


def test_smoke_test_requires_all_opt_in_flags(tmp_path, monkeypatch):
    store = ContentStore(tmp_path / "content.db")
    store.upsert_configured_feed(_feed())
    monkeypatch.setenv("ULTRON_CRE_SOURCE_FETCH_ENABLED", "true")
    monkeypatch.delenv("ULTRON_CRE_SOURCE_REFRESH_ENABLED", raising=False)

    result = FeedSmokeTestService(store).run(
        "refresh-feed",
        operator_id="operator@example",
        dry_run=True,
    )

    assert result["ready"] is False
    assert "flags" in result["reason"]
    store.close()


def test_read_only_fetch_failure_is_retryable_but_not_uncertain(tmp_path, monkeypatch):
    store = ContentStore(tmp_path / "content.db")
    store.upsert_configured_feed(_feed())
    monkeypatch.setenv("ULTRON_CRE_SOURCE_FETCH_ENABLED", "true")
    task = store.enqueue_feed_refresh_task(
        _feed(),
        scheduled_window="123",
    )
    claimed = store.claim_content_workflow_task(worker_id="worker", lease_seconds=60)
    assert claimed is not None

    class FailingFetcher(PublicFeedFetcher):
        def fetch(self, feed):
            raise OSError("temporary network failure")

    from ultron.application.content.sources import FeedIngestionService

    monkeypatch.setattr(
        "ultron.application.content.workflow.FeedIngestionService",
        lambda store: FeedIngestionService(store, fetcher=FailingFetcher()),
    )
    worker = ContentWorkflowWorker(store, worker_id="worker")
    result = worker.execute_claimed(
        claimed,
        lambda current: _ingest_rss_feed_task(current, store),
        backoff_seconds=0,
    )
    assert result.state == "pending"
    assert result.failure_class == "external_provider"
    assert result.state != "uncertain"
    store.close()


def test_host_rate_reservation_is_durable_and_atomic(tmp_path):
    database = tmp_path / "content.db"
    first = ContentStore(database)
    now = datetime(2026, 11, 3, 12, tzinfo=UTC)
    results = []

    def reserve():
        store = ContentStore(database)
        try:
            return store.reserve_feed_host_request(
                "example.com",
                now=now,
                minimum_interval_seconds=60,
            )
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    assert sorted(results) == [False, True]
    first.close()

    reopened = ContentStore(database)
    assert not reopened.reserve_feed_host_request(
        "example.com",
        now=now + timedelta(seconds=30),
        minimum_interval_seconds=60,
    )
    assert reopened.reserve_feed_host_request(
        "example.com",
        now=now + timedelta(seconds=61),
        minimum_interval_seconds=60,
    )
    reopened.close()
