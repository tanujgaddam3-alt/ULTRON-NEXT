from datetime import UTC, datetime
from dataclasses import replace

import pytest

from ultron.application.content.discovery import ContentDiscoveryService
from ultron.application.content.sources import RSSAtomAdapter, SourceDocument, canonicalize_public_url
from ultron.application.content.sources import FeedIngestionService, FetchResponse, PublicFeedFetcher
from ultron.application.content.store import ContentStore
from ultron.application.content.nasa import (
    NASAImagesAcquisitionAdapter,
    NASAVideoCandidate,
)
from ultron.application.content.provider_network import ProviderSearchResult
from tests.application.acquisition_test_utils import AcquisitionHarness
from ultron.domain.content_acquisition import AcquisitionIntendedUse
from ultron.domain.content_identity import metadata_fingerprint
from ultron.domain.content_discovery import (
    ConfiguredFeed,
    FeedHealth,
    OpportunityStatus,
    ResearchCompleteness,
)


def _document(title: str = "Topic update") -> SourceDocument:
    return SourceDocument(
        canonical_url="https://example.com/topic",
        title=title,
        summary="Independent observation",
        published_at=datetime(2026, 11, 1, tzinfo=UTC),
        publisher="Example",
        metadata_fingerprint=metadata_fingerprint(
            title=title,
            description="Independent observation",
            published_at=datetime(2026, 11, 1, tzinfo=UTC),
        ),
        source_identity="rss_atom:url-sha256:" + "a" * 64,
        provider="rss_atom",
        provider_content_id="entry-test-document",
        original_url="https://example.com/topic",
    )


def test_source_url_validation_rejects_private_hosts():
    with pytest.raises(ValueError, match="Private or local"):
        canonicalize_public_url("http://127.0.0.1/private")
    with pytest.raises(ValueError, match="Credential"):
        canonicalize_public_url("https://user:pass@example.com/item")


def test_rss_adapter_parses_timestamp_and_metadata_fingerprint():
    payload = b"""<rss><channel><item><title>Update</title>
        <description>Details</description><pubDate>2026-11-01T10:00:00Z</pubDate>
        <link>https://example.com/item</link><guid>entry-17</guid>
        </item></channel></rss>"""
    documents = RSSAtomAdapter().parse("https://example.com/feed", payload)
    assert len(documents) == 1
    assert documents[0].published_at == datetime(2026, 11, 1, 10, tzinfo=UTC)
    assert len(documents[0].metadata_fingerprint) == 64
    assert documents[0].provider == "rss_atom"
    assert documents[0].provider_content_id == "entry-17"
    assert documents[0].source_identity.startswith("rss_atom:url-sha256:")
    assert documents[0].original_url == "https://example.com/item"


def test_atom_link_and_xml_entities_are_safe():
    payload = b"""<feed xmlns='http://www.w3.org/2005/Atom'><entry>
      <title>Atom update</title><link href='https://example.com/atom'/>
      <summary>Summary</summary><updated>2026-11-01T10:00:00Z</updated>
    </entry></feed>"""
    documents = RSSAtomAdapter().parse("https://example.com/feed", payload)
    assert documents[0].canonical_url == "https://example.com/atom"
    with pytest.raises(ValueError, match="DTD"):
        RSSAtomAdapter().parse("https://example.com/feed", b"<!DOCTYPE feed>")


def test_discovery_persists_idempotent_opportunity_and_evidence(tmp_path):
    store = ContentStore(tmp_path / "content.db")
    service = ContentDiscoveryService(store)
    first = service.discover(
        topic="New Topic",
        niche="technology",
        target_audience="builders",
        target_language="en",
        content_format="short",
        source_documents=(_document(),),
    )
    second = service.discover(
        topic="new topic",
        niche="technology",
        target_audience="builders",
        target_language="en",
        content_format="short",
        source_documents=(_document(),),
    )
    assert second.opportunity_id == first.opportunity_id
    assert second.evidence_count == 1
    assert len(store.list_content_evidence(first.opportunity_id)) == 1
    store.close()


def test_research_and_score_are_explicit_about_missing_dimensions(tmp_path):
    store = ContentStore(tmp_path / "content.db")
    service = ContentDiscoveryService(store)
    opportunity = service.discover(
        topic="Insufficient topic",
        niche="technology",
        target_audience="builders",
        target_language="en",
        content_format="short",
        source_documents=(_document(),),
    )
    updated, report = service.research(
        opportunity,
        summary="One source-backed observation.",
        uncertainties=("No search-interest history is available.",),
    )
    assert report.completeness == ResearchCompleteness.PARTIAL
    score = service.score(updated)
    assert score.confidence < 1
    assert any("missing_dimensions=" in item for item in score.explanation)
    persisted = service.persist_score(updated)
    assert persisted.score == score.score
    assert store.get_content_opportunity_score(
        updated.opportunity_id,
        score_version=score.score_version,
    ) == persisted
    assert store.get_content_opportunity(updated.opportunity_id).status == OpportunityStatus.OPPORTUNITY_SCORED
    store.close()


def test_selection_hands_off_only_scored_opportunities(tmp_path):
    harness = AcquisitionHarness(tmp_path / "content.db")
    try:
        opportunity = harness.discovery.discover(
            topic="Selected topic",
            niche="technology",
            target_audience="builders",
            target_language="en",
            content_format="short",
            source_documents=(_document("Selected topic"),),
        )
        with pytest.raises(ValueError, match="scored"):
            harness.select(opportunity)
        researched, _ = harness.discovery.research(
            opportunity, summary="Evidence-backed summary."
        )
        harness.discovery.persist_score(researched)
        selected = harness.store.get_content_opportunity(opportunity.opportunity_id)
        workflow = harness.select(
            selected,
            intended_use=AcquisitionIntendedUse.EDITORIAL_DEVELOPMENT,
        )
        request = harness.store.get_content_acquisition_request_for_opportunity(
            selected.opportunity_id
        )
        assert workflow.title == "Selected topic"
        assert request.workflow_id == workflow.workflow_id
        assert harness.store.get_content_opportunity(
            selected.opportunity_id
        ).status == OpportunityStatus.SELECTED
    finally:
        harness.close()


def test_provider_discovery_persists_and_deduplicates_by_provider_media_identity(
    tmp_path,
    monkeypatch,
):
    store = ContentStore(tmp_path / "provider-discovery.db")
    adapter = NASAImagesAcquisitionAdapter()
    candidate = NASAVideoCandidate(
        nasa_id="NASA_TEST_MEDIA_001",
        title="Solar observation",
        center="NASA",
        description="First metadata snapshot.",
        metadata_url="https://images-api.nasa.gov/search?nasa_id=NASA_TEST_MEDIA_001",
        canonical_url="https://images-api.nasa.gov/search?nasa_id=NASA_TEST_MEDIA_001",
        asset_url="https://images-assets.nasa.gov/video/NASA_TEST_MEDIA_001/source.mp4",
        copyright_notice=None,
        photographer=None,
    )
    result = ProviderSearchResult(status=200, candidates=(candidate,))
    monkeypatch.setattr(adapter, "search_with_status", lambda *_args, **_kwargs: result)
    service = ContentDiscoveryService(
        store,
        provider_adapters={"nasa": adapter},
    )
    try:
        first = service.discover_provider(
            "nasa",
            "solar observation",
            niche="science",
            target_audience="learners",
            target_language="en",
            content_format="short",
            page_size=5,
        )
        stored_source = store.list_content_opportunity_sources(
            first.items[0].opportunity_id,
        )[0]
        changed_candidate = replace(
            candidate,
            title="Solar observation, revised",
            description="Updated metadata only.",
        )
        monkeypatch.setattr(
            adapter,
            "search_with_status",
            lambda *_args, **_kwargs: ProviderSearchResult(
                status=200,
                candidates=(changed_candidate,),
            ),
        )
        second = service.discover_provider(
            "nasa",
            "solar observation",
            niche="science",
            target_audience="learners",
            target_language="en",
            content_format="short",
            page_size=5,
        )
        updated_source = store.list_content_opportunity_sources(
            second.items[0].opportunity_id,
        )[0]
        assert first.http_status == second.http_status == 200
        assert first.result_count == second.result_count == 1
        assert first.items[0].opportunity_id == second.items[0].opportunity_id
        assert first.items[0].source_id == second.items[0].source_id
        assert first.items[0].provider_content_id == "NASA_TEST_MEDIA_001"
        assert stored_source.source_identity == updated_source.source_identity
        assert stored_source.metadata_fingerprint != updated_source.metadata_fingerprint
        assert updated_source.content_sha256 is None
        assert len(store.list_content_opportunities()) == 1
        assert store._connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert store._connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        store.close()


def test_provider_discovery_timeout_does_not_create_opportunities(tmp_path, monkeypatch):
    store = ContentStore(tmp_path / "provider-discovery-timeout.db")
    adapter = NASAImagesAcquisitionAdapter()

    def timeout(*_args, **_kwargs):
        raise TimeoutError("provider timed out")

    monkeypatch.setattr(adapter, "search_with_status", timeout)
    service = ContentDiscoveryService(store, provider_adapters={"nasa": adapter})
    try:
        with pytest.raises(TimeoutError, match="timed out"):
            service.discover_provider(
                "nasa",
                "query",
                niche="science",
                target_audience="learners",
                target_language="en",
                content_format="short",
                page_size=5,
            )
        assert store.list_content_opportunities() == ()
        with pytest.raises(ValueError, match="result limit"):
            service.discover_provider(
                "nasa",
                "query",
                niche="science",
                target_audience="learners",
                target_language="en",
                content_format="short",
                page_size=51,
            )
    finally:
        store.close()


def test_configured_feed_is_disabled_and_ingestion_requires_feature_flag(tmp_path, monkeypatch):
    store = ContentStore(tmp_path / "content.db")
    feed = ConfiguredFeed(
        feed_id="feed-1",
        feed_name="Example",
        canonical_url="https://example.com/feed",
        allowed_hostname="example.com",
        source_category="technology",
    )
    store.upsert_configured_feed(feed)
    with pytest.raises(PermissionError, match="disabled"):
        FeedIngestionService(store).ingest("feed-1")
    enabled = feed.model_copy(update={"enabled": True})
    store.upsert_configured_feed(enabled)
    with pytest.raises(PermissionError, match="source fetching"):
        FeedIngestionService(store).ingest("feed-1")
    monkeypatch.setenv("ULTRON_CRE_SOURCE_FETCH_ENABLED", "true")
    monkeypatch.setattr(
        PublicFeedFetcher,
        "fetch",
        lambda self, configured: (_ for _ in ()).throw(RuntimeError("offline test")),
    )
    with pytest.raises(RuntimeError, match="offline test"):
        FeedIngestionService(store).ingest("feed-1")
    assert store.get_configured_feed("feed-1").health == FeedHealth.UNAVAILABLE
    store.close()


def test_feed_ingestion_uses_registry_id_and_records_304(tmp_path, monkeypatch):
    class NotModifiedFetcher(PublicFeedFetcher):
        def fetch(self, feed):
            return FetchResponse(304, {"etag": '"etag-1"'}, b"", feed.canonical_url)

    store = ContentStore(tmp_path / "content.db")
    feed = ConfiguredFeed(
        feed_id="feed-2",
        feed_name="Example",
        canonical_url="https://example.com/feed",
        allowed_hostname="example.com",
        source_category="technology",
        enabled=True,
    )
    store.upsert_configured_feed(feed)
    monkeypatch.setenv("ULTRON_CRE_SOURCE_FETCH_ENABLED", "true")
    result = FeedIngestionService(store, fetcher=NotModifiedFetcher()).ingest("feed-2")
    assert result["status"] == 304
    assert store.get_configured_feed("feed-2").health == FeedHealth.NOT_MODIFIED
    with pytest.raises(KeyError):
        FeedIngestionService(store, fetcher=NotModifiedFetcher()).ingest("https://evil.example/feed")
    store.close()
