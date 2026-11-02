from __future__ import annotations

from ultron.application.content.store import ContentStore
from ultron.domain.content_visual_analysis import (
    ContentVisualAnalysis,
    ContentVisualAnalysisEvent,
    VisualAnalysisRequest,
)
from ultron.domain.content_visual_intelligence import VisualVideoAnalysis
from ultron.security.identity.authorization import (
    AuthorizationContext,
    ValidatedAuthorization,
    _validated_authorization,
)
from ultron.security.identity.authenticator import WebSocketAuthenticator


_CREATE_ACTION = "content_visual_analysis_create"
_STATUS_ACTION = "content_visual_analysis_status"
_EVENTS_ACTION = "content_visual_analysis_events"


class VisualAnalysisLifecycleService:
    """Authenticated owner-scoped access to durable visual-analysis records."""

    def __init__(
        self,
        *,
        store: ContentStore,
        authority: WebSocketAuthenticator,
    ) -> None:
        if type(store) is not ContentStore:
            raise TypeError("Visual analysis requires the configured content store.")
        if type(authority) is not WebSocketAuthenticator:
            raise TypeError("A WebSocketAuthenticator trust authority is required.")
        self.store = store
        self.authority = authority
        self.store._register_authorization_authority(authority)

    def create(
        self,
        context: AuthorizationContext,
        request: VisualAnalysisRequest,
    ) -> tuple[ContentVisualAnalysis, bool]:
        payload = request.model_dump(mode="json")
        authorization = self._authorize(
            context,
            action=_CREATE_ACTION,
            payload=payload,
        )
        if authorization.context.principal != request.owner_id:
            raise PermissionError("Visual analysis source is unavailable.")
        return self.store.create_visual_analysis(request)

    def get(
        self,
        context: AuthorizationContext,
        analysis_id: str,
    ) -> ContentVisualAnalysis | None:
        payload = {"analysis_id": analysis_id}
        authorization = self._authorize(
            context,
            action=_STATUS_ACTION,
            payload=payload,
        )
        return self.store.get_visual_analysis(
            analysis_id,
            authorization.context.principal,
        )

    def events(
        self,
        context: AuthorizationContext,
        analysis_id: str,
    ) -> list[ContentVisualAnalysisEvent]:
        payload = {"analysis_id": analysis_id}
        authorization = self._authorize(
            context,
            action=_EVENTS_ACTION,
            payload=payload,
        )
        return self.store.list_visual_analysis_events(
            analysis_id,
            authorization.context.principal,
        )

    def _authorize(
        self,
        context: AuthorizationContext,
        *,
        action: str,
        payload: dict[str, object],
    ) -> ValidatedAuthorization:
        if type(context) is not AuthorizationContext or not self.authority.validate_context(
            context,
            action=action,
            parameters=payload,
            operation_id=context.operation_id,
            correlation_id=context.operation_correlation_id,
        ):
            raise PermissionError("A valid active-session authorization is required.")
        return _validated_authorization(
            context,
            self.authority,
            action=action,
            parameters=payload,
        )


class VisualAnalysisWorker:
    """Internal worker transitions; this interface must not be gateway-exposed."""

    def __init__(self, *, store: ContentStore) -> None:
        if type(store) is not ContentStore:
            raise TypeError("Visual analysis workers require the configured ContentStore.")
        self.store = store
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self.store.prepare_visual_analysis_worker(self)
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        self.store.stop_visual_analysis_worker(self)
        self._started = False

    def _require_started(self) -> None:
        if not self._started:
            raise RuntimeError(
                "Visual-analysis worker must complete startup recovery before use."
            )
        self.store._require_visual_analysis_worker(self)

    def claim(self, analysis_id: str, owner_id: str) -> bool:
        self._require_started()
        return self.store.claim_visual_analysis(
            analysis_id,
            owner_id,
            worker_token=self,
        )

    def complete(
        self,
        analysis_id: str,
        owner_id: str,
        result: VisualVideoAnalysis,
    ) -> None:
        self._require_started()
        self.store.complete_visual_analysis(
            analysis_id,
            owner_id,
            result,
            worker_token=self,
        )

    def fail(
        self,
        analysis_id: str,
        owner_id: str,
        *,
        failure_code: str,
        failure_detail: str,
    ) -> bool:
        self._require_started()
        return self.store.fail_visual_analysis(
            analysis_id,
            owner_id,
            failure_code=failure_code,
            failure_detail=failure_detail,
            worker_token=self,
        )
