# CRE Phase 3 Test Evidence

## Evidence classification

| Evidence class | Result |
|---|---|
| Source inspection | Gateway/provider/UI request paths, durable store behavior, migration utility, and tests inspected. Source evidence does not establish live deployment state. |
| Fresh automated tests | 97 focused CRE Python tests and 145 interface Node tests executed during this phase. |
| Fresh browser workflow | Chrome exercised the dashboard with an isolated local auth helper, gateway, disposable SQLite database, and actual FFmpeg/ffprobe. |
| Historical report | A previous implementation report stated 429 CRE/application/gateway/helper passes and 145 interface passes. The 429 result was not rerun here. |
| Production/browser deployment | Not tested. No existing service, production credential, private media, or existing database was opened. |

## Environment isolation

The successful run used:

- Test root: `/tmp/cre-phase3-e2e-PFEF0l-run5`.
- Isolated v10 database: `/tmp/cre-phase3-e2e-PFEF0l-run5/app/content.db`.
- Isolated artifact storage: `/tmp/cre-phase3-e2e-PFEF0l-run5/app/managed-content`.
- Isolated v9 fixture: `/tmp/cre-phase3-e2e-PFEF0l-run5/app/unmigrated-v9.db`.
- One application test process as database owner; Chrome used the local test gateway/helper as a client.
- Synthetic test-only credentials and generated synthetic audio/video.
- Google Chrome `155.0.8059.39`; FFmpeg `8.0.1-3ubuntu2+esm5`.

The known application database environment variables were unset. The existing `data/ultron_cre.db` was checked for path distinction only; it was not opened, queried, migrated, copied, or backed up. No existing application service was contacted.

## Exact commands and outcomes

### Focused CRE regression tests

```sh
cd /home/ultron/Projects/ULTRON_INTERFACE_FINAL
.venv/bin/python -m pytest -q \
  tests/application/test_content_private_render_operations.py \
  tests/application/test_content_rendering.py \
  tests/application/test_content_processing.py
```

- Exit code: **0**
- Result: **97 passed in 23.46s**
- Failures/skips: **0 / 0**

This run included tests for concurrent duplicate claims, stale-operation reconciliation, owner authorization, renderer/artifact failures, transfer timeout preserving completion, migration repeatability/data preservation, backup validation, media rendering, and plan/audio behavior. Relevant tests include [test_content_private_render_operations.py](../tests/application/test_content_private_render_operations.py#L280), [test_content_private_render_operations.py](../tests/application/test_content_private_render_operations.py#L331), [test_content_private_render_operations.py](../tests/application/test_content_private_render_operations.py#L425), and [test_content_private_render_operations.py](../tests/application/test_content_private_render_operations.py#L572).

### Interface tests

```sh
cd /home/ultron/Projects/ULTRON_INTERFACE_FINAL/interface
npm test
```

The package script runs `node --test`.

- Exit code: **0**
- Result: **145 tests, 145 passed**
- Failures/skips: **0 / 0**

### Isolated browser and real-render acceptance

```sh
cd /home/ultron/Projects/ULTRON_INTERFACE_FINAL
PYTHONPATH=/home/ultron/Projects/ULTRON_INTERFACE_FINAL \
CRE_PHASE3_TEMP=/tmp/cre-phase3-e2e-PFEF0l-run5 \
.venv/bin/python \
/tmp/cre-phase3-e2e-PFEF0l-run5/phase3_acceptance.py
```

- Exit code: **0**
- Result: **all harness assertions passed**
- Renderer: **3 real FFmpeg render invocations** (initial render for project 1, initial render for project 2, one plan revision).
- Lost response: **injected after first operation completed**; reload recovery reused that operation ID and did not invoke the renderer again.
- Artifact transfer failure: **one injected timeout**; completed state remained completed and subsequent retrieval succeeded.
- Browser video readiness: **directly observed** `readyState=4`, `videoWidth=1280`, `videoHeight=720` for the recovered preview and revised preview.
- Cross-owner/unknown-ID equivalence: status returned `RENDER_OPERATION_UNAVAILABLE` for both; artifact returned `RENDER_ARTIFACT_UNAVAILABLE` for both.
- v9 behavior: `RENDER_RECOVERY_NOT_ENABLED`; schema remained v9, with no startup migration.

The initial synthetic input was 108,493 bytes, 12.0 seconds, H.264 192x108 plus mono AAC 48 kHz, SHA-256 `2a2109aef49316972ad25d4f565baecd07219dc7c67373ed6d29e3bb40c43ffa`.

The first persisted output was 143,356 bytes with SHA-256 `ca538a32af12808a7c1723ce9d6e501afbe068c1a8a2aba9da52bad10032619e`. Its probed streams were H.264 1280x720 at 30 fps and AAC stereo at 48 kHz; duration was 9.021333 seconds. Browser-side byte-count and SHA-256 validation succeeded before playback. The revised output probed as H.264 1280x720 at 30 fps and AAC stereo at 48 kHz; duration was 21.021333 seconds.

The initial plan used `cre-creative-plan-v2`, had six segments, and recorded scene boundaries at 6.0 and 9.0 seconds. Revision history advanced from 1 to 2 with distinct plan digests. All observed media-process exit codes in the harness were zero.

## Browser diagnostics and limitations

Chrome recorded **8 network-source console errors**: **2 HTTP 404** and **6 `net::ERR_CONNECTION_REFUSED`**. They did not block the tested workflow, but the harness did not record the failing URLs, so these cannot be attributed conclusively or counted as a clean-console pass.

The first recovered project's revision button remained hidden after reload. The test therefore created a second project in the same disposable browser session and used its visible revision control to verify plan revision and rendering. This confirms revision rendering, but does **not** prove that an operator can revise the same project immediately after reload recovery.

The browser workflow used synthetic media and did not prove semantic scene understanding, editorial quality, production FFmpeg behavior, publication, real-user authorization, or deployment readiness. The test used the actual local renderer; it did not use a fake renderer. No browser owner-isolation test with a real second principal was performed; cross-owner equivalence was checked through the gateway with a separate test principal.

## Harness iterations

Preliminary disposable runs exposed and corrected harness-only issues: a missing test-helper import, a mistaken `await` on a synchronous service call, and a per-page dispatch counter reset after browser reload. Those attempts did not alter application files. The final run above completed with exit code 0. Its intentional artifact-transfer timeout emitted an expected gateway error log; the operation remained completed and retrieval succeeded.

## Cleanup

The successful run and four preliminary attempts wrote their databases, generated media, artifacts, browser profiles, test credentials, harnesses, and evidence under five exact `/tmp/cre-phase3-e2e-PFEF0l*` directories. All five paths were checked to resolve directly under `/tmp` and not be symlinks, then individually removed. A follow-up existence check confirmed cleanup. No application data path was a cleanup target.
