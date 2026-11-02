# CRE End-to-End Workflow Map

## Scope

This map describes the private edit-project path inspected and exercised with synthetic data. It does not describe or authorize production publication.

## Request and data flow

```mermaid
sequenceDiagram
    actor Operator
    participant UI as Dashboard
    participant Provider as WebSocket provider
    participant Helper as Isolated auth helper
    participant Gateway as Interface gateway
    participant Rights as Content rights/service
    participant Store as SQLite content store
    participant Media as ffprobe / FFmpeg
    participant Artifacts as Managed artifact storage

    Operator->>UI: Select synthetic video
    UI->>Provider: CRE_SOURCE_VIDEO_REQUEST
    Provider->>Helper: Authenticated WebSocket
    Helper->>Gateway: Forward authenticated request
    Gateway->>Media: Inspect and validate uploaded source
    Gateway->>Rights: Register source with principal/rights context
    Rights->>Store: Persist source identity and digest
    Gateway-->>UI: SOURCE_REGISTERED

    Operator->>UI: Start edit
    UI->>Provider: CRE_PROJECT_CREATE_REQUEST
    Provider->>Gateway: Authenticated project request
    Gateway->>Rights: Create owner-scoped project
    Rights->>Store: Persist project and provenance
    Gateway-->>UI: PROJECT_CREATED

    UI->>Provider: CRE_PROJECT_AUTOPLAN_REQUEST
    Provider->>Gateway: Plan request
    Gateway->>Media: Inspect source and detect scene changes
    Gateway->>Rights: Build validated structured plan
    Rights->>Store: Persist plan revision
    Gateway-->>UI: AUTOPLAN_READY (v2)

    UI->>UI: Generate UUID and persist project + operation ID
    UI->>Provider: CRE_PRIVATE_PROJECT_RENDER_REQUEST
    Provider->>Gateway: Operation ID + project + render options
    Gateway->>Store: Claim queued operation (short transaction)
    Gateway->>Media: Render pinned plan outside write transaction
    Media->>Artifacts: Write bounded staged output
    Artifacts-->>Gateway: Artifact key, byte count, SHA-256
    Gateway->>Store: Persist completed state and artifact metadata
    Gateway-->>UI: PRIVATE_RENDER_OPERATION_AVAILABLE

    Note over UI,Gateway: If response is lost, the saved ID is retained; no new render is submitted.
    UI->>Provider: CRE_PRIVATE_RENDER_STATUS_REQUEST (same ID)
    Provider->>Gateway: Owner-scoped status lookup
    Gateway->>Store: Read operation for authenticated owner
    Gateway-->>UI: Persisted state and operation result
    UI->>Provider: CRE_PRIVATE_RENDER_ARTIFACT_REQUEST (only if completed)
    Provider->>Gateway: Owner-scoped artifact request
    Gateway->>Artifacts: Recheck size and SHA-256
    Gateway-->>UI: Bounded artifact response
    UI->>UI: Verify bytes/hash; wait for video readiness and dimensions

    Operator->>UI: Request plan revision
    UI->>Provider: CRE_PROJECT_AUTOPLAN_REQUEST
    Provider->>Gateway: Same project, revision instruction
    Gateway->>Store: Append next plan revision
    UI->>UI: Save a fresh render operation ID before dispatch
    UI->>Gateway: Render revised plan as a distinct operation
```

## Implemented trust and persistence boundaries

1. **Browser request boundary.** The dashboard delegates actions through provider methods in [websocketProvider.js](../interface/services/providers/websocketProvider.js#L535). Operation recovery data contains a project ID and operation ID, not credentials or media bytes.
2. **Gateway boundary.** The active dispatcher registers project, autoplan, private-render, status, and artifact handlers in [interface_gateway.py](../ultron/application/runtime/interface_gateway.py#L938). Each private operation is evaluated under the authenticated principal.
3. **Content and rights boundary.** The gateway obtains the content rendering service from the runtime container and routes project and render work through its rights service in [interface_gateway.py](../ultron/application/runtime/interface_gateway.py#L241).
4. **Planning boundary.** Autoplan inspects registered media and persists a structured plan; scene-change data is an input to a heuristic edit, not a semantic understanding guarantee. See [rendering.py](../ultron/application/content/rendering.py#L531).
5. **Durable operation boundary.** Schema v10 stores operation identity, owner, project, idempotency data, pinned inputs, state transitions, and completion metadata. A renderer claim and state changes use controlled transactions; FFmpeg runs outside the long-lived SQLite write transaction. See [store.py](../ultron/application/content/store.py#L460) and [rendering.py](../ultron/application/content/rendering.py#L762).
6. **Artifact boundary.** Only a completed owner-scoped operation can return an artifact. Storage size and digest are rechecked on retrieval in [rendering.py](../ultron/application/content/rendering.py#L1155); the dashboard also validates the response before playback in [operator-console.js](../interface/operator-console.js#L2690).
7. **Recovery boundary.** Reload recovery looks up the saved ID and requests an artifact only for a completed state. It does not generate a replacement operation ID in [operator-console.js](../interface/operator-console.js#L2873).

## Lifecycle and failure behavior

The persisted states are `queued`, `rendering`, `completed`, `failed`, and `reconciliation_required`. A normal successful path is `queued → rendering → completed`. A renderer failure may become `failed`; an interrupted or uncertain outcome becomes `reconciliation_required`. Uncertain rendering is not automatically repeated. Artifact-transfer failure is an artifact-delivery issue and does not change `completed`.

The integration acceptance deliberately dropped the first render response and the first artifact transfer. After reload it queried the same operation ID, recovered the completed state, retried artifact retrieval, verified the persisted digest, and decoded the video. The first operation's renderer invocation count remained one.

## Migration boundary

Schema v10 is an explicit operator action, not startup behavior. The runtime request path against an isolated v9 fixture returned `RENDER_RECOVERY_NOT_ENABLED` and left that fixture at v9. The documented invocation, backup rules, compatibility, and rollback limitations are in [CRE_PRIVATE_RENDER_OPERATIONS.md](./CRE_PRIVATE_RENDER_OPERATIONS.md).

## Observed limitation

After reloading and recovering a project, the render recovery button is restored but the revision button is not. The latter is unhidden by the project-creation handler, not by saved-project recovery. This was observed directly in Chrome. A second project on the reloaded page was used to complete and verify the revision path; that is evidence of revision rendering, not evidence that a recovered project can immediately be revised.
