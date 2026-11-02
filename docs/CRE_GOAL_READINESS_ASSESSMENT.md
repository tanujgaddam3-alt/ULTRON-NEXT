# CRE Goal Readiness Assessment

## Verdict

**PARTIALLY PASSED — ready for a bounded, non-production controlled acceptance workflow; not ready to claim clean end-to-end UX or production readiness.**

The isolated acceptance achieved source upload, project creation, media inspection/autoplan, real FFmpeg rendering, durable completion, response-loss recovery after reload, artifact-transfer retry, browser integrity verification, decoded video readiness, owner-isolation checks, and a plan revision. The test database, artifacts, helper, and gateway were disposable and local. No production service or database was opened.

## Readiness by goal

| Goal | Status | Evidence / qualification |
|---|---|---|
| Synthetic source ingestion through the dashboard | PASS | Source registration action ran twice; stored source SHA-256 matched the generated MP4. |
| Real inspection and scene-cut planning | PASS | ffprobe/FFmpeg ran; the v2 plan had six segments and detected boundaries at 6 and 9 seconds. This is media analysis and heuristic planning, not semantic scene comprehension. |
| Real render and persisted artifact | PASS | Three FFmpeg invocations completed, including an explicit plan revision. The initial completed artifact was 143,356 bytes with SHA-256 recorded and independently rechecked. |
| Lost-response/reload recovery | PASS | A render response was intentionally dropped. The browser reloaded and recovered the original ID, queried status, retried a transfer timeout, and played the same completed artifact. The first operation rendered once. |
| Browser media readiness | PASS | Chrome directly observed `readyState=4`, `videoWidth=1280`, and `videoHeight=720` for both the recovered preview and revised preview. |
| Owner isolation | PASS | Cross-owner and unknown-ID status responses were indistinguishable; artifact requests also returned the same unavailable result. |
| Revision editing | PARTIAL | The UI created a new plan revision and rendered it, with changed plan digest. After reload recovery, however, the same recovered project's revision control stayed hidden. A new project was used to exercise the revision path. |
| Browser console/reconnect cleanliness | PARTIAL | Eight network console errors were captured (two 404 and six `ERR_CONNECTION_REFUSED`). They did not stop the workflow; request URLs were not recorded, so attribution and clean reconnect behavior remain unverified. |
| Migration and rollout | PASS WITH GATE | The synthetic fixture was explicitly migrated in isolation; a v9 request remained at v9 and reported recovery unavailable. No existing database was migrated. Production migration requires separately approved backup and maintenance steps. |
| Production publication, rights in live accounts, commercial output quality | NOT TESTED | No platform connection, production credential, private production media, or publication was used. |

## Strengths confirmed

- The frontend persisted the operation ID before dispatch.
- Recovery used the same ID and did not auto-submit another operation.
- The store recorded the first operation through `queued → rendering → completed`.
- Transfer failure did not change completed state.
- The artifact's byte count and SHA-256 were verified before browser playback.
- The video element reached metadata/data readiness with nonzero dimensions.
- Migration is explicit and repeatable in tests; unsupported future versions are rejected.
- Existing focused audio, renderer, and durable-operation regressions passed in the current test run.

## Risks and required closure

### R1 — Revision control not restored for recovered projects

**Impact:** Users can recover an existing render after reload but cannot directly revise that same project from the reloaded dashboard because the revision button remains hidden. The control is revealed by project creation but not by saved-project restoration. Source: [operator-console.js](../interface/operator-console.js#L2094), [operator-console.js](../interface/operator-console.js#L3063).

**Required before broad acceptance:** Restore the revision affordance from validated saved project state, and test that a recovered project can append revision 2 without creating a different project.

### R2 — Unattributed browser network errors

**Impact:** The workflow succeeds, but eight network console errors make it impossible to assert that the reconnect and browser resource path is clean. The harness captured messages but not failing request URLs.

**Required before claiming clean browser behavior:** Add URL-aware CDP network-event capture to a disposable run, classify the two 404s and six refused connections, and rerun without losing operation recovery evidence.

### R3 — Crash outcomes remain reconciliation cases

**Impact:** A process crash after a render claim cannot provide exactly-once execution. The system correctly avoids automatic rerun but may require operator reconciliation and cleanup of an orphan artifact. See [CRE_PRIVATE_RENDER_OPERATIONS.md](./CRE_PRIVATE_RENDER_OPERATIONS.md).

### R4 — Non-production results are not deployment evidence

**Impact:** This test establishes one isolated configuration, not the live application's database version, secrets, process topology, or production performance. Do not infer deployment readiness.

## Authorization gates before a future live workflow

1. Close R1 and R2 in a separate implementation and retest.
2. Obtain explicit authorization for the specific non-production database, storage, service endpoints, and operator identity.
3. Verify that the approved target is not the production database or production content store; keep a transaction-consistent backup when migration is authorized.
4. Confirm a tested restore/rollback procedure and migration compatibility with the deployed application version.
5. Use only synthetic or explicitly approved source material and a test principal; verify publication remains disabled.
6. Obtain separate authorization before any production migration, publication, service restart, or access to private production media.

## Rollback

This assessment changed no application code, data, configuration, or running service. The five files in this report set are documentation only. Removing those documentation files would revert the repository change. Temporary acceptance directories were removed after their evidence was transcribed. No database rollback was needed.
