from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

from ultron.application.content.acquisition import ContentAcquisitionService
from ultron.application.content.discovery import ContentDiscoveryService
from ultron.application.content.sources import SourceDocument
from ultron.application.content.store import ContentStore
from ultron.domain.content_acquisition import AcquisitionIntendedUse
from ultron.security.identity.authorization import (
    AuthorizationContext,
    resource_fingerprint,
)
from ultron.security.identity.authenticator import WebSocketAuthenticator


class AcquisitionHarness:
    def __init__(self, database=None, *, store: ContentStore | None = None):
        self._owns_store = store is None
        if store is None and database is None:
            raise ValueError("An acquisition test database or store is required.")
        self.store = ContentStore(database) if store is None else store
        self.store.migrate_acquisition_request_schema()
        self.authority = WebSocketAuthenticator(
            "A" * 43,
            "alice",
            additional_credentials={"bob": "B" * 43, "operator": "O" * 43},
        )
        self.sockets = {
            owner: object() for owner in ("alice", "bob", "operator")
        }
        for owner, token in (
            ("alice", "A" * 43),
            ("bob", "B" * 43),
            ("operator", "O" * 43),
        ):
            assert self.authority.authenticate(self.sockets[owner], token)
        self.acquisition = ContentAcquisitionService(
            store=self.store,
            authority=self.authority,
        )
        self.discovery = ContentDiscoveryService(self.store)

    def context(self, owner: str, action: str, payload: dict) -> AuthorizationContext:
        operation = {
            "operationId": str(uuid4()),
            "correlationId": str(uuid4()),
            "requestFingerprint": hashlib.sha256(str(uuid4()).encode()).hexdigest(),
        }
        grant = self.authority.authorize(
            websocket=self.sockets[owner],
            operation=operation,
            purpose="content_acquisition",
            request_correlation_id=str(uuid4()),
            action=action,
            resource_fingerprint=resource_fingerprint(action, payload),
        )
        assert grant is not None
        return AuthorizationContext.from_grant(grant)

    def create_scored_opportunity(self, topic: str = "Selected topic"):
        document = SourceDocument(
            canonical_url=f"https://example.com/{topic.replace(' ', '-').lower()}",
            title=topic,
            summary="Persisted source observation",
            published_at=datetime(2026, 11, 1, tzinfo=UTC),
            publisher="Example",
            source_identity="rss_atom:url-sha256:" + "a" * 64,
            provider="rss_atom",
            provider_content_id=f"entry-{uuid4()}",
            original_url=f"https://example.com/{topic.replace(' ', '-').lower()}",
        )
        opportunity = self.discovery.discover(
            topic=topic,
            niche="technology",
            target_audience="builders",
            target_language="en",
            content_format="short",
            source_documents=(document,),
        )
        researched, _ = self.discovery.research(
            opportunity,
            summary="Evidence-backed summary.",
        )
        self.discovery.persist_score(researched)
        return self.store.get_content_opportunity(opportunity.opportunity_id)

    def select(
        self,
        opportunity,
        *,
        owner: str = "alice",
        source_id: str | None = None,
        intended_use: AcquisitionIntendedUse = AcquisitionIntendedUse.EDITORIAL_DEVELOPMENT,
        idempotency_key: str = "request-1",
        context: AuthorizationContext | None = None,
    ):
        if source_id is None:
            source_id = self.store.list_content_evidence(
                opportunity.opportunity_id
            )[0].source_id
        payload = {
            "opportunity_id": opportunity.opportunity_id,
            "source_id": source_id,
            "intended_use": intended_use.value,
            "idempotency_key": idempotency_key,
        }
        context = context or self.context(
            owner,
            "content_request_acquisition",
            payload,
        )
        return self.discovery.select_for_workflow(
            opportunity,
            acquisition_service=self.acquisition,
            source_id=source_id,
            intended_use=intended_use,
            idempotency_key=idempotency_key,
            authorization_context=context,
        )

    def close(self):
        if self._owns_store:
            self.store.close()
