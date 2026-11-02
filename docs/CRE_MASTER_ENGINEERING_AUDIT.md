# CRE Master Engineering Audit

## Executive result

The Content Revenue Engine (CRE) has a working private-source-to-preview workflow: the dashboard uploads synthetic video, creates an edit project, generates a structured plan, renders through FFmpeg, stores a durable private artifact, and recovers that artifact after a lost response and page reload. A disposable Chrome acceptance run directly observed video metadata and decoded dimensions. This is meaningful integration evidence, not production certification.

**Overall assessment: partially passed / conditionally ready for controlled acceptance.** The run used only synthetic media, test identities, a disposable v10 SQLite database, temporary artifact storage, and local test-only gateway/helper listeners. The existing application database and services were not opened or contacted.

Two UX/diagnostic gaps remain:

1. After reload recovery, the same project's **Apply revision and re-render** control remains hidden. In `operator-console.js`, initialization restores the project and operation IDs, but only the project-creation handler unhides the revision control. The test completed a revision on a newly created project in the reloaded page rather than hiding this limitation.
2. Chrome reported eight network-source console errors during the run (two HTTP 404 and six `ERR_CONNECTION_REFUSED`). They did not prevent the workflow, but their request URLs were not captured, so their source is not established.

## Capability audit

| Area | Finding | Evidence |
|---|---|---|
| Source ingestion | The dashboard's video selection is wired to authenticated source registration; the browser acceptance used that UI path and verified the stored source digest against the synthetic file. | [operator-console.js](../interface/operator-console.js#L2780), [websocketProvider.js](../interface/services/providers/websocketProvider.js#L535), [interface_gateway.py](../ultron/application/runtime/interface_gateway.py#L2477) |
| Rights and project ownership | CRE uses the existing rights service and authenticated principal context for project creation, planning, and private rendering. The durable operation APIs are owner-scoped. The acceptance run confirmed that a second test principal received the same unavailable result for another owner's ID and a random unknown ID. | [rendering.py](../ultron/application/content/rendering.py#L762), [interface_gateway.py](../ultron/application/runtime/interface_gateway.py#L2127), [test_content_private_render_operations.py](../tests/application/test_content_private_render_operations.py#L647) |
| Inspection and planning | The renderer inspects media with ffprobe and uses FFmpeg scene-change analysis. Autoplan persists a v2 plan. This is deterministic media inspection and heuristic planning; it is not proof of semantic scene understanding. | [rendering.py](../ultron/application/content/rendering.py#L531), [clip_renderer.py](../ultron/application/content/clip_renderer.py#L937) |
| Audio and video rendering | Structured plans carry explicit audio choices and rendering validates output audio. The focused CRE test run included processing/rendering coverage; the browser run produced H.264 video and AAC audio. | [test_content_rendering.py](../tests/application/test_content_rendering.py#L1748), [clip_renderer.py](../ultron/application/content/clip_renderer.py#L937) |
| Durable render lifecycle | Operations have persisted states, pinned inputs, idempotency, and reconciliation behavior. The renderer executes outside the long-lived write transaction; uncertain interrupted work is reconciled rather than automatically rerun. | [store.py](../ultron/application/content/store.py#L460), [rendering.py](../ultron/application/content/rendering.py#L762), [test_content_private_render_operations.py](../tests/application/test_content_private_render_operations.py#L280) |
| Artifact delivery | Completed artifacts are owner-checked and revalidated for stored size and SHA-256. Transfer failure leaves the operation completed. The browser verified an artifact after an injected transfer timeout. | [rendering.py](../ultron/application/content/rendering.py#L1155), [test_content_private_render_operations.py](../tests/application/test_content_private_render_operations.py#L425) |
| Browser recovery | The operation ID is saved before dispatch and recovery queries the saved operation rather than creating a new one. The browser recovered the same completed operation after a dropped render response and reload. The revision affordance is not restored for that same project after reload. | [operator-console.js](../interface/operator-console.js#L2094), [operator-console.js](../interface/operator-console.js#L2873), [operator-console.js](../interface/operator-console.js#L2926) |
| Migration | Schema v10 is additive and explicitly invoked. Startup does not implicitly migrate v9; the gateway returns `RENDER_RECOVERY_NOT_ENABLED` for v9 without changing its version. Migration backup and rollback limits are documented. | [store.py](../ultron/application/content/store.py#L424), [CRE_PRIVATE_RENDER_OPERATIONS.md](./CRE_PRIVATE_RENDER_OPERATIONS.md) |
| Publication | This assessment covers private preview rendering and recovery. The synthetic workflow did not publish content to a platform or exercise production media, channel credentials, or monetization. | [CRE_PRIVATE_RENDER_OPERATIONS.md](./CRE_PRIVATE_RENDER_OPERATIONS.md) |

## Runtime integration

The active gateway dispatcher routes private render submission, status, and artifact requests to their handlers. The gateway receives the rendering service from the application container; that service composes the rights/content service, store, renderer, and artifact storage. The browser provider methods use the matching source/project/autoplan/render/status/artifact actions.

Static evidence is in [interface_gateway.py](../ultron/application/runtime/interface_gateway.py#L938), [interface_gateway.py](../ultron/application/runtime/interface_gateway.py#L241), and [websocketProvider.js](../interface/services/providers/websocketProvider.js#L535). The actual isolated run exercised those handlers and provider paths. No claim is made about the state or configuration of any existing deployment.

## Test evidence and limits

- Fresh focused run: `97 passed` across the private-render operation, rendering, and processing tests.
- Fresh interface run: `145 passed`, `0 failed`, `0 skipped`.
- Fresh isolated Chrome/FFmpeg workflow: successful. It generated a synthetic 12-second 192x108 H.264/AAC source, persisted projects and plans, completed three intentional renders (two initial renders and one revision), injected one lost render response and one artifact-transfer timeout, and decoded the recovered 1280x720 H.264/AAC preview in Chrome.
- The fake telemetry/system-intelligence responses in the harness were not CRE renderers. Rendering used local FFmpeg 8.0.1. This does not establish semantic creative quality or production encoding performance.
- The historical claim of 429 broad CRE/application/gateway tests is not recounted as a fresh run in this audit.

Detailed commands and observed output are recorded in [CRE_PHASE3_TEST_EVIDENCE.md](./CRE_PHASE3_TEST_EVIDENCE.md).

## Recommended closure

1. Restore the revision affordance when a saved project is recovered, while keeping operation recovery and revision submission distinct.
2. Capture browser network request URLs and classify the eight console errors in an isolated run.
3. Rerun the same synthetic workflow after those fixes, then perform a separately authorized controlled acceptance against an approved non-production deployment.
4. Treat migration, publication, production data, and production readiness as separate authorization gates.

No production source, configuration, database, or running service was modified by this assessment.
