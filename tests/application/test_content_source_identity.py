import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from ultron.application.content.discovery import ContentDiscoveryService
from ultron.application.content.sources import SourceDocument
from ultron.application.content.store import ContentStore
from ultron.domain.content import (
    AcquisitionMethod,
    ContentTargetContext,
    SourceHashStatus,
    SourceProvenance,
    SourceReferenceRecord,
)
from ultron.domain.content_identity import metadata_fingerprint
from ultron.domain.content_discovery import ContentSourceRecord, SourceReliability


_PUBLISHED_AT = datetime(2026, 11, 1, tzinfo=UTC)


def _document(
    url: str,
    *,
    provider: str | None = None,
    provider_content_id: str | None = None,
    source_identity: str | None = None,
    resolved_url: str | None = None,
    title: str = "Topic update",
    summary: str = "Independent observation",
) -> SourceDocument:
    return SourceDocument(
        canonical_url=url,
        title=title,
        summary=summary,
        published_at=_PUBLISHED_AT,
        publisher="Example",
        metadata_fingerprint=metadata_fingerprint(
            title=title,
            description=summary,
            published_at=_PUBLISHED_AT,
        ),
        original_url=url,
        resolved_url=resolved_url,
        provider=provider,
        provider_content_id=provider_content_id,
        source_identity=source_identity,
    )


def _discover(service, document, *, audience="builders", content_format="short"):
    return service.discover(
        topic="Identity topic",
        niche="technology",
        target_audience=audience,
        target_language="en",
        content_format=content_format,
        source_documents=(document,),
    )


def test_source_provider_identity_precedes_url_aliases_and_is_namespaced(tmp_path):
    store = ContentStore(tmp_path / "source-identities.db")
    service = ContentDiscoveryService(store)
    first = _discover(
        service,
        _document(
            "https://www.youtube.com/watch?v=item-1",
            provider="YouTube",
            provider_content_id="item-1",
            source_identity="youtube:channel-7",
            resolved_url="https://youtube.com/watch?v=item-1",
        ),
    )
    alias = _discover(
        service,
        _document(
            "https://youtu.be/item-1",
            provider="youtube",
            provider_content_id="item-1",
            source_identity="youtube:channel-7",
        ),
    )
    updated_metadata = _discover(
        service,
        _document(
            "https://m.youtube.com/watch?v=item-1",
            provider="youtube",
            provider_content_id="item-1",
            title="Renamed title",
        ),
    )
    other_provider = _discover(
        service,
        _document(
            "https://video.example/watch/item-1",
            provider="other-video",
            provider_content_id="item-1",
        ),
    )
    other_item = _discover(
        service,
        _document(
            "https://www.youtube.com/watch?v=item-2",
            provider="youtube",
            provider_content_id="item-2",
        ),
    )

    identities = store._connection.execute(
        """
        SELECT source_id, provider, provider_content_id, original_url,
            resolved_url, source_identity, metadata_fingerprint
        FROM content_source_identities ORDER BY provider, provider_content_id
        """
    ).fetchall()
    assert len(identities) == 3
    youtube_item = next(
        row for row in identities
        if row["provider"] == "youtube" and row["provider_content_id"] == "item-1"
    )
    assert youtube_item["original_url"] == "https://www.youtube.com/watch?v=item-1"
    assert youtube_item["source_identity"] == "youtube:channel-7"
    assert youtube_item["resolved_url"] == "https://youtube.com/watch?v=item-1"
    assert youtube_item["metadata_fingerprint"] == metadata_fingerprint(
        title="Renamed title",
        description="Independent observation",
        published_at=_PUBLISHED_AT,
    )
    assert store._connection.execute(
        "SELECT source_name FROM content_sources WHERE source_id = ?",
        (youtube_item["source_id"],),
    ).fetchone()[0] == "Renamed title"
    assert first.opportunity_id == alias.opportunity_id
    assert first.opportunity_id == updated_metadata.opportunity_id
    assert other_provider.opportunity_id == first.opportunity_id
    assert other_item.opportunity_id == first.opportunity_id
    assert len(store.list_content_evidence(first.opportunity_id)) == 4
    store.close()


def test_unsupported_source_identity_falls_back_to_canonical_url(tmp_path):
    store = ContentStore(tmp_path / "source-url-fallback.db")
    service = ContentDiscoveryService(store)
    first = _discover(service, _document("https://example.com/item"))
    alias = _discover(service, _document("HTTPS://EXAMPLE.COM:443/item"))
    count = store._connection.execute(
        "SELECT COUNT(*) FROM content_sources"
    ).fetchone()[0]
    assert first.opportunity_id == alias.opportunity_id
    assert count == 1
    store.close()


def test_provider_identity_enriches_existing_url_fallback(tmp_path):
    store = ContentStore(tmp_path / "source-identity-enrichment.db")
    service = ContentDiscoveryService(store)
    url = "https://example.com/item"
    first = _discover(service, _document(url))
    enriched = _discover(
        service,
        _document(url, provider="provider", provider_content_id="item-1"),
    )
    identity = store._connection.execute(
        "SELECT provider, provider_content_id FROM content_source_identities"
    ).fetchone()
    assert first.opportunity_id == enriched.opportunity_id
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_sources"
    ).fetchone()[0] == 1
    assert tuple(identity) == ("provider", "item-1")
    store.close()


def test_distinct_provider_ids_can_share_a_canonical_url(tmp_path):
    store = ContentStore(tmp_path / "provider-items.db")
    service = ContentDiscoveryService(store)
    document_url = "https://provider.example/watch"
    _discover(
        service,
        _document(
            document_url, provider="provider", provider_content_id="item-a"
        ),
    )
    _discover(
        service,
        _document(
            document_url, provider="provider", provider_content_id="item-b"
        ),
    )
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_sources"
    ).fetchone()[0] == 2
    _discover(service, _document(document_url))
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_sources"
    ).fetchone()[0] == 3
    store.close()


def test_url_fallback_matches_one_known_provider_identity(tmp_path):
    store = ContentStore(tmp_path / "source-fallback-known-provider.db")
    service = ContentDiscoveryService(store)
    url = "https://provider.example/item"
    first = _discover(
        service,
        _document(url, provider="provider", provider_content_id="item-1"),
    )
    fallback = _discover(service, _document(url))
    assert first.opportunity_id == fallback.opportunity_id
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_sources"
    ).fetchone()[0] == 1
    assert store._connection.execute(
        "SELECT provider_content_id FROM content_source_identities"
    ).fetchone()[0] == "item-1"
    store.close()


def test_opportunity_identity_includes_audience_and_format(tmp_path):
    store = ContentStore(tmp_path / "opportunity-identity.db")
    service = ContentDiscoveryService(store)
    first = _discover(service, _document("https://example.com/a"))
    different_audience = _discover(
        service,
        _document("https://example.com/b"),
        audience="students",
    )
    different_format = _discover(
        service,
        _document("https://example.com/c"),
        content_format="long-form",
    )
    assert len(
        {
            first.opportunity_id,
            different_audience.opportunity_id,
            different_format.opportunity_id,
        }
    ) == 3
    store.close()


def test_url_and_metadata_fingerprints_are_distinct_and_deterministic():
    original = "HTTPS://Example.com:443/item?category=tech"
    source = ContentSourceRecord(
        source_id="source-1",
        canonical_url=original,
        source_name="Update",
        source_type="rss",
        retrieved_at=_PUBLISHED_AT,
        reliability=SourceReliability.UNKNOWN,
        original_url=original,
    )
    assert source.canonical_url == "https://example.com/item?category=tech"
    assert source.original_url == original
    assert source.content_sha256 is None

    fingerprint = metadata_fingerprint(
        title="Update", description="Details", published_at=_PUBLISHED_AT
    )
    assert fingerprint == metadata_fingerprint(
        title="Update",
        description="Details",
        published_at=_PUBLISHED_AT,
    )
    assert fingerprint != metadata_fingerprint(
        title="Changed", description="Details", published_at=_PUBLISHED_AT
    )
    assert fingerprint != metadata_fingerprint(
        title="Update", description="Changed", published_at=_PUBLISHED_AT
    )
    assert fingerprint != metadata_fingerprint(
        title="Update",
        description="Details",
        published_at=_PUBLISHED_AT + timedelta(seconds=1),
    )
    assert metadata_fingerprint(
        title=None, description=None, published_at=None
    ) == metadata_fingerprint(title=None, description=None, published_at=None)

    media_digest = hashlib.sha256(b"actual media bytes").hexdigest()
    assert media_digest == hashlib.sha256(b"actual media bytes").hexdigest()
    assert media_digest != hashlib.sha256(b"different media bytes").hexdigest()
    assert fingerprint != media_digest


def test_source_urls_reject_credentials_fragments_and_untrusted_provider_ids():
    common = {
        "source_id": "source-1",
        "source_name": "Update",
        "source_type": "feed",
        "retrieved_at": _PUBLISHED_AT,
    }
    for url in (
        "https://user:password@example.com/item",
        "https://example.com/item#access_token=secret",
    ):
        with pytest.raises(ValidationError):
            ContentSourceRecord(canonical_url=url, **common)
    with pytest.raises(ValidationError):
        ContentSourceRecord(
            canonical_url="https://example.com/item",
            provider="provider",
            provider_content_id="auth_token=secret",
            **common,
        )
    with pytest.raises(ValidationError):
        ContentSourceRecord(
            canonical_url="https://example.com/item",
            source_identity="https://example.com/feed?token=secret",
            **common,
        )


def test_unverified_source_hash_status_cannot_be_promoted_by_round_trip():
    source_reference = SourceReferenceRecord(
        intake_id="intake-1",
        asset_id="asset-1",
        owner_id="owner-1",
        source_reference="https://provider.example/item",
        acquisition_method=AcquisitionMethod.AUTHORIZED_PROVIDER,
        provenance=SourceProvenance(
            acquisition_method=AcquisitionMethod.AUTHORIZED_PROVIDER,
            acquired_at=_PUBLISHED_AT,
            source_reference="https://provider.example/item",
            provider="provider",
            provider_content_id="item-1",
            original_url="https://provider.example/item?alias=original",
            canonical_url="https://provider.example/item",
            opportunity_id="opportunity-1",
        ),
        target_context=ContentTargetContext.SNAPCHAT_SPOTLIGHT,
    )
    copied = source_reference.model_copy(
        update={"source_hash_status": "verified"}
    )
    with pytest.warns(UserWarning, match="source_hash_status"):
        copied_payload = copied.model_dump()
    with pytest.raises(ValidationError):
        SourceReferenceRecord.model_validate(copied_payload)
    assert source_reference.source_hash_status == SourceHashStatus.UNVERIFIED
    assert source_reference.provenance.provider_content_id == "item-1"


def _downgrade_identity_tables_to_v10(store: ContentStore) -> None:
    connection = store._connection
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("BEGIN IMMEDIATE")
    try:
        source = connection.execute(
            "SELECT * FROM content_sources ORDER BY source_id LIMIT 1"
        ).fetchone()
        connection.execute("DROP TABLE content_source_identities")
        connection.execute("DROP TABLE content_source_identity_schema_meta")
        connection.execute(
            """
            CREATE TABLE content_sources_v10 (
                source_id TEXT PRIMARY KEY,
                canonical_url TEXT NOT NULL UNIQUE,
                source_name TEXT NOT NULL,
                publisher TEXT,
                source_type TEXT NOT NULL,
                published_at TEXT,
                retrieved_at TEXT NOT NULL,
                reliability TEXT NOT NULL,
                content_sha256 TEXT,
                license_classification TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO content_sources_v10 (
                source_id, canonical_url, source_name, publisher, source_type,
                published_at, retrieved_at, reliability, content_sha256,
                license_classification
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source["source_id"],
                source["canonical_url"],
                source["source_name"],
                source["publisher"],
                source["source_type"],
                source["published_at"],
                source["retrieved_at"],
                source["reliability"],
                "f" * 64,
                source["license_classification"],
            ),
        )
        connection.execute("DROP TABLE content_sources")
        connection.execute(
            "ALTER TABLE content_sources_v10 RENAME TO content_sources"
        )

        connection.execute(
            """
            CREATE TABLE content_opportunities_v10 (
                opportunity_id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                normalized_topic TEXT NOT NULL,
                niche TEXT NOT NULL,
                target_audience TEXT NOT NULL,
                target_language TEXT NOT NULL,
                target_region TEXT,
                content_format TEXT NOT NULL,
                discovered_at TEXT NOT NULL,
                status TEXT NOT NULL,
                relevance_assessment TEXT,
                evidence_count INTEGER NOT NULL DEFAULT 0,
                research_status TEXT NOT NULL,
                workflow_id TEXT REFERENCES content_workflows(workflow_id),
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(normalized_topic, niche, target_language, target_region)
            )
            """
        )
        columns = (
            "opportunity_id, topic, normalized_topic, niche, target_audience, "
            "target_language, target_region, content_format, discovered_at, status, "
            "relevance_assessment, evidence_count, research_status, workflow_id, "
            "version, created_at, updated_at"
        )
        connection.execute(
            f"INSERT INTO content_opportunities_v10 ({columns}) "
            f"SELECT {columns} FROM content_opportunities"
        )
        connection.execute("DROP TABLE content_opportunities")
        connection.execute(
            "ALTER TABLE content_opportunities_v10 RENAME TO content_opportunities"
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


def test_explicit_v10_reconciliation_is_idempotent_and_preserves_assets(tmp_path):
    database = tmp_path / "legacy-v10.db"
    store = ContentStore(database, recover_interrupted_jobs=False)
    store._connection.execute(
        """
        INSERT INTO content_assets (
            asset_id, owner_id, category, content_sha256, provenance_json,
            storage_key, asset_state, created_at
        ) VALUES ('asset-preserved', 'local-operator', 'video', ?, '{}',
                  'storage-preserved', 'active', ?)
        """,
        ("a" * 64, _PUBLISHED_AT.isoformat()),
    )
    store._connection.commit()
    service = ContentDiscoveryService(store)
    opportunity = _discover(service, _document("https://example.com/reconciliation"))
    _downgrade_identity_tables_to_v10(store)
    store.close()

    with pytest.raises(RuntimeError, match="runtime schema"):
        ContentStore(database, recover_interrupted_jobs=False)

    reconciled = ContentStore(
        database,
        recover_interrupted_jobs=False,
        allow_legacy_v10_reconciliation=True,
    )
    assert reconciled.migrate_v10_schema_reconciliation() == 10
    assert reconciled.migrate_v10_schema_reconciliation() == 10
    assert reconciled._connection.execute(
        "SELECT asset_id FROM content_assets WHERE asset_id = 'asset-preserved'"
    ).fetchone()["asset_id"] == "asset-preserved"
    assert reconciled.get_content_opportunity(opportunity.opportunity_id) is not None
    ContentStore.verify_complete_runtime_schema(reconciled._connection)
    assert reconciled._connection.execute(
        "PRAGMA foreign_key_check"
    ).fetchall() == []
    assert reconciled._connection.execute(
        "PRAGMA quick_check"
    ).fetchone()[0] == "ok"
    reconciled.close()


def test_explicit_v10_reconciliation_rejects_modified_legacy_shape(tmp_path):
    database = tmp_path / "modified-legacy-v10.db"
    store = ContentStore(database, recover_interrupted_jobs=False)
    service = ContentDiscoveryService(store)
    _discover(service, _document("https://example.com/reconciliation-negative"))
    _downgrade_identity_tables_to_v10(store)
    store._connection.execute(
        "ALTER TABLE content_sources ADD COLUMN unexpected_drift TEXT"
    )
    store._connection.commit()
    store.close()

    with pytest.raises(RuntimeError, match="unexpected content_sources shape"):
        ContentStore(
            database,
            recover_interrupted_jobs=False,
            allow_legacy_v10_reconciliation=True,
        )

    before = sqlite3.connect(database)
    assert before.execute("PRAGMA foreign_key_check").fetchall() == []
    assert before.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert before.execute(
        "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
    ).fetchone()[0] == 10
    before.close()


def test_source_identity_migration_preserves_rows_assets_rights_and_indexes(tmp_path):
    store = ContentStore(tmp_path / "identity-migration.db")
    service = ContentDiscoveryService(store)
    opportunity = _discover(service, _document("https://example.com/legacy"))
    source_id = store._connection.execute(
        "SELECT source_id FROM content_sources"
    ).fetchone()[0]
    with store._transaction() as connection:
        connection.execute(
            """
            INSERT INTO content_assets (
                asset_id, owner_id, category, content_sha256, provenance_json,
                storage_key, asset_state, created_at
            ) VALUES ('asset-preserved', 'owner-1', 'footage', ?, '{}',
                'asset:preserved', 'active', ?)
            """,
            ("a" * 64, _PUBLISHED_AT.isoformat()),
        )
        connection.execute(
            """
            INSERT INTO ownership_claims (
                claim_id, asset_id, claimant_id, rights_holder,
                authority_description, created_at
            ) VALUES ('claim-preserved', 'asset-preserved', 'owner-1',
                'Rights holder', 'Authorized for test fixture', ?)
            """,
            (_PUBLISHED_AT.isoformat(),),
        )
        connection.execute(
            """
            INSERT INTO rights_evidence (
                evidence_id, asset_id, claim_id, submitted_by, evidence_kind,
                evidence_blob, evidence_sha256, grantor_identity,
                grantor_authority, claimed_terms_json, created_at
            ) VALUES ('rights-evidence-preserved', 'asset-preserved',
                'claim-preserved', 'owner-1', 'document', X'01', ?, 'holder',
                'authorized', '{}', ?)
            """,
            ("b" * 64, _PUBLISHED_AT.isoformat()),
        )
    _downgrade_identity_tables_to_v10(store)

    assert store.migrate_source_identity_schema() == 1
    assert store.migrate_source_identity_schema() == 1
    source = store.create_content_source(
        ContentSourceRecord(
            source_id="new-source-id",
            canonical_url="https://example.com/legacy",
            source_name="Legacy",
            source_type="feed",
            retrieved_at=_PUBLISHED_AT,
        )
    )
    assert source.source_id == source_id
    assert source.content_sha256 is None
    assert store.get_content_opportunity(opportunity.opportunity_id) is not None
    assert len(store.list_content_evidence(opportunity.opportunity_id)) == 1
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_assets WHERE asset_id = 'asset-preserved'"
    ).fetchone()[0] == 1
    assert store._connection.execute(
        "SELECT COUNT(*) FROM ownership_claims WHERE claim_id = 'claim-preserved'"
    ).fetchone()[0] == 1
    assert store._connection.execute(
        """
        SELECT COUNT(*) FROM rights_evidence
        WHERE evidence_id = 'rights-evidence-preserved'
        """
    ).fetchone()[0] == 1
    migrated_identity = store._connection.execute(
        """
        SELECT original_url, legacy_metadata_digest, metadata_fingerprint
        FROM content_source_identities WHERE source_id = ?
        """,
        (source_id,),
    ).fetchone()
    assert migrated_identity["original_url"] is None
    assert migrated_identity["legacy_metadata_digest"] == "f" * 64
    assert migrated_identity["metadata_fingerprint"] is None
    assert store._connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert store._connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    source_unique_indexes = [
        tuple(
            item["name"]
            for item in store._connection.execute(
                f"PRAGMA index_info({index['name']})"
            )
        )
        for index in store._connection.execute("PRAGMA index_list(content_sources)")
        if index["unique"]
    ]
    assert ("canonical_url",) not in source_unique_indexes
    identity_unique_indexes = [
        index
        for index in store._connection.execute(
            "PRAGMA index_list(content_source_identities)"
        )
        if index["unique"]
    ]
    assert len(identity_unique_indexes) >= 2
    opportunity_unique_indexes = [
        tuple(
            index_row["name"]
            for index_row in store._connection.execute(
                f"PRAGMA index_info({index['name']})"
            )
        )
        for index in store._connection.execute(
            "PRAGMA index_list(content_opportunities)"
        )
        if index["unique"]
    ]
    assert any(
        set(index) == {
            "normalized_topic",
            "niche",
            "target_audience",
            "target_language",
            "target_region",
            "content_format",
        }
        for index in opportunity_unique_indexes
    )
    store.close()


def test_source_identity_migration_failure_rolls_back_schema_and_data(
    tmp_path,
    monkeypatch,
):
    store = ContentStore(tmp_path / "identity-migration-rollback.db")
    service = ContentDiscoveryService(store)
    opportunity = _discover(service, _document("https://example.com/rollback"))
    source_id = store._connection.execute(
        "SELECT source_id FROM content_sources"
    ).fetchone()[0]
    _downgrade_identity_tables_to_v10(store)

    original_create = ContentStore._create_content_identity_schema

    def fail_after_schema_creation(connection):
        original_create(connection)
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(
        ContentStore,
        "_create_content_identity_schema",
        staticmethod(fail_after_schema_creation),
    )
    with pytest.raises(RuntimeError, match="injected migration failure"):
        store.migrate_source_identity_schema()
    monkeypatch.setattr(
        ContentStore,
        "_create_content_identity_schema",
        staticmethod(original_create),
    )

    assert not store._table_exists(
        store._connection, "content_source_identity_schema_meta"
    )
    assert store._connection.execute(
        "SELECT source_id FROM content_sources"
    ).fetchone()[0] == source_id
    assert store.get_content_opportunity(opportunity.opportunity_id) is not None
    assert len(store.list_content_evidence(opportunity.opportunity_id)) == 1
    assert store._connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert store.migrate_source_identity_schema() == 1
    store.close()
