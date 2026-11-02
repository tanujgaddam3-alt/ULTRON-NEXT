# Atlas Architecture Explorer Findings Validation

**Status:** read-only investigation  
**Observation basis:** Atlas cache snapshot `2026-10-27T13:02:33Z`; filesystem and source checks performed against the workspace on 2026-10-28.  
**Scope:** correlate Atlas Architecture Explorer cache findings with the current workspace and prior ULTRON audits. This is not a replacement architecture, comprehensive dynamic-import audit, or approval to change runtime state.

## Executive summary

- **VERIFIED:** Atlas's inspectable cache contains 8,669 indexed files, 18 grouping buckets, 7,259 import edges, 884 circular-dependency records, 370 dead-code candidates, and zero layer-violation records.
- **VERIFIED:** Atlas's UI count of 884 "issues" is the sum of circular-dependency records and layer violations. In this cache, all 884 are cycle records; they are not 884 independently validated product defects.
- **VERIFIED:** The 370 dead-code candidates are unique and all reside in the nested `f5-env/lib/python3.12/site-packages/` tree under voice reference staging. All 370 files exist and their current size and modification time match the cache within filesystem timestamp precision. They are environment-package files, not confirmed unused ULTRON source.
- **VERIFIED:** All 884 recorded cycle paths are within `site-packages`. A representative vendor cycle is present in source. The separate project analyzer reports two first-party deferred-import cycles that Atlas does not report as cycles; neither is demonstrated to cause a runtime failure.
- **PARTIALLY_VERIFIED:** The current files and directory layout explain much of the count difference, but Atlas indexes a filtered subset. The 8,669 cache count must not be presented as the total number of workspace source files.
- **UNVERIFIED:** Dynamic loading, plugin discovery, script/configuration reachability, and exact per-package use of each nested environment candidate were not exhaustively traced.
- **BLOCKED:** No authenticated browser verification, live database operation, service operation, or runtime source attestation was performed.

No application files were changed, removed, moved, or renamed. No live process, credential, or database was accessed. The existing comprehensive limitation audit and its deliverables were not edited. No tests were run for this read-only investigation.

## Atlas cache and count reconciliation

The local [`.atlas-cache.json`](../.atlas-cache.json) is version 3, names this workspace as its root, and records the snapshot timestamp above. The installed extension is Atlas Architecture Explorer 0.1.5. Its README documents the local cache; the extension command list has no findings-export command. The cache is nevertheless inspectable and contains the exact candidate paths and cycle records, so this report does not infer a list from the UI. Detector behavior was checked in the installed extension source: cycle DFS in `dist/extension.js` around lines 438–470, dead-code reachability around lines 475–511, dead-zone grouping around lines 1219–1255, scan/cache filters around lines 1460–1640, and the UI issue-count expression in `dist/webview.js` around line 23671.

| Measure | Atlas cache | Current workspace observation | Interpretation |
|---|---:|---:|---|
| Indexed files | 8,669 | 144,854 regular files total | Different scopes; the latter includes environments, generated/runtime material, and other non-source files. |
| Indexed language files | 8,669 | 51,660 files with the scanned source extensions | Atlas is a filtered subset, not a full workspace language-file census. |
| Import edges | 7,259 | Not directly comparable | Edges exist only when the detector resolves an import to an indexed node. |
| System groupings | 18 | 18 cache groups | These are Atlas classification buckets, not 18 verified ULTRON runtime subsystems. |
| Circular-dependency records | 884 | 884 in the cached graph | All recorded cycle paths are under `site-packages`. |
| Dead-code candidates | 370 unique paths | 370 current files | Every candidate is in the nested voice reference environment; none is confirmed unused. |
| Layer violations | 0 | 0 in the cached graph | No architecture rules were configured in the project inventory, so zero does not establish that layer boundaries were checked. |

The 51,660-file comparison counts regular files ending in `.py`, `.js`, `.mjs`, `.cjs`, `.ts`, `.tsx`, `.go`, `.rs`, `.java`, or `.cs`. The cache contains 8,641 `.py`, 11 `.js`, 16 `.mjs`, and 1 `.ts` files. Atlas filters hidden directories, known environment directories, and files outside its supported scan depth. For example, the workspace contains 18,251 matching files under the root `.venv` and 32,196 under `ultron/voice/reference_staging`; Atlas includes only a subset of the latter. Fifteen `.py`, `.js`, or `.ts` files outside those two trees are in hidden `.backups` or `.ultron_patch_backups` directories, which Atlas does not index. Thus the UI's 8,669 count is a count of the cache's indexed nodes, not a count of all source files or all ULTRON files.

All 8,669 cached paths were checked against current filesystem size and modification time; there were no mismatches using a 2 ms timestamp tolerance. This verifies cache metadata freshness only. It is not a content hash attestation: the cache's `hash` values are non-cryptographic, and the extension's incremental dirty check uses size and modification time.

### Atlas groupings

| Atlas grouping | Indexed files |
|---|---:|
| Tests | 819 |
| Auth System | 89 |
| API Layer | 49 |
| Middleware | 26 |
| Data Layer | 45 |
| Services | 37 |
| State Management | 21 |
| Validators | 3 |
| Types | 52 |
| Configuration | 15 |
| UI Components | 59 |
| Utilities | 302 |
| Projects | 24 |
| Root | 10 |
| Interface | 3 |
| Scripts | 8 |
| Ultron | 7,103 |
| Other | 4 |

These are grouping labels assigned by the extension. In particular, the `Ultron` group includes nested environments below the `ultron/` directory, while the `Tests` group includes third-party package content. These labels should not be used as evidence that the workspace has exactly 18 functional systems.

## Investigation A — dead-file candidates

The complete, exact 370-path register is in [ATLAS_DEAD_FILE_CANDIDATES.json](./ATLAS_DEAD_FILE_CANDIDATES.json). The register preserves Atlas grouping, current presence, size, modification time, classification, and an evidence status for each path.

| Atlas grouping | Candidates | Independent classification |
|---|---:|---|
| Ultron | 353 | Nested Python environment package files; not confirmed unused ULTRON application source. |
| Utilities | 16 | Nested Python environment package files; not confirmed unused ULTRON application source. |
| Tests | 1 | `mpmath/tests/__init__.py` inside the nested third-party environment; package test-support content, not a first-party ULTRON test. |
| **Total** | **370** | **0 confirmed unused; 370 potentially used or unknown at per-package runtime level.** |

All candidates are under:

`ultron/voice/reference_staging/high-fidelity-voice-match-20261018T0650Z/f5-env/lib/python3.12/site-packages/`

This location supports classifying the files as nested-environment package content. It does **not** prove whether every package file is imported by a staging script, loaded dynamically, used by a package entry point, or required for recovery. The reference environment is separately documented as optional and outside the managed Edge voice path, but no exhaustive candidate-by-candidate package-use analysis was performed. No candidate was deleted or recommended for deletion.

Atlas's dead-code detector uses a static import graph and entrypoint/name heuristics. It seeds reachability from recognized entry points and nodes with no reverse dependents, then follows resolved imports. An import that does not resolve to an indexed node cannot form an edge. Python dynamic imports, plugin registries, package entry points, scripts, external launchers, configuration-driven loading, resource files, and some re-exports may not be represented. A path marked unreachable is therefore a **candidate**, not proof of dead code.

## Investigation B — the 884 reported issues

Atlas defines its displayed issue total as `circularDeps.length + layerViolations.length`. The cache has zero layer violations, so all 884 displayed issues are circular-dependency records.

| Atlas heuristic severity | Cycle records | What the label means |
|---|---:|---|
| High | 345 | Cycle length is at most 2 nodes. |
| Medium | 291 | Cycle length is 3–4 nodes. |
| Low | 248 | Cycle length is greater than 4 nodes. |

These labels are based on cycle length, not exploitability, outage likelihood, security severity, or production impact. Every cached cycle path is within nested `site-packages`. A representative cycle is source-confirmed in the staged `accelerate` package: `utils/imports.py` imports `utils/environment.py`, which locally imports `utils/imports.py`. This validates that the detector can find a real third-party import cycle; it does not establish a defect in ULTRON's first-party runtime.

The project analyzer records two first-party deferred-import cycles:

1. [authenticator.py](../ultron/security/identity/authenticator.py) imports `AuthorizationContext` from [authorization.py](../ultron/security/identity/authorization.py), which locally imports `WebSocketAuthenticator` inside a validation function.
2. [container.py](../ultron/infrastructure/container.py) imports `CapabilityMetrics`; [capability_metrics.py](../ultron/improvement/evaluation/capability_metrics.py) locally imports the container inside its measurement path.

These relationships are **PARTIALLY_VERIFIED**: the source-level module/function imports are present, but no runtime failure was demonstrated. Atlas does not report these as cycle records. This is a detector-coverage disagreement, not proof that either analyzer is authoritative or that either cycle is harmful. Preserve the current imports unless a runtime problem and safe dependency direction are demonstrated by focused tests.

### Issue-category findings

The cache exposes circular dependencies and layer violations as the issue categories relevant to its displayed count. It does not expose a separate 884-item catalog of defects with independent descriptions or production severity. The cycle paths are available in the cache and represented by the candidate/cycle data; their high/medium/low labels are the length heuristic above. There are no layer-violation records to sample.

| Category | Detector behavior | Evidence-based disposition | False-positive / false-negative risk |
|---|---|---|---|
| Circular dependency | DFS over resolved edges between indexed nodes; emits cycle paths; severity derives from cycle length. | **VERIFIED:** 884 cache records, all in nested `site-packages`; one representative package cycle matches source. No first-party runtime defect established. | Deferred imports may be cycles without import-time failure; duplicate/overlapping cycle paths may inflate record counts. Unresolved/dynamic imports or unindexed nodes may hide cycles. |
| Layer violation | Counts configured layer-rule violations. | **VERIFIED:** 0 records in the cache. | The project inventory states no architecture rules are configured; zero is not evidence that architectural layers were validated. |
| Dead-code candidate | Uses entrypoint/root heuristics and reachability through indexed import edges. | **VERIFIED:** 370 candidates, all in a nested environment. **NOT VERIFIED:** actual per-package use. | Dynamic/plugin/config/script loading can be missed; broad folder groupings can misrepresent environment code as first-party. |

## Investigation C — actual subsystem boundaries

The map below reconciles Atlas's folder-oriented buckets with the existing [ULTRON_SYSTEM_INVENTORY.json](./ULTRON_SYSTEM_INVENTORY.json), [ULTRON_COMPREHENSIVE_LIMITATION_AUDIT.md](./ULTRON_COMPREHENSIVE_LIMITATION_AUDIT.md), and [ULTRON_INTEGRATION_GAP_REGISTER.json](./ULTRON_INTEGRATION_GAP_REGISTER.json). Entry points and tests are source/inventory evidence; they do not prove which release a live process loaded.

| Functional subsystem | Entry points / direct dependencies | Registration, dynamic loading, interfaces, persistence, and tests | Integration evidence / status |
|---|---|---|---|
| Main application and composition | `main.py`; `ultron/application/runtime/service_entrypoint.py`; `ultron/infrastructure/container.py`; Python application dependencies including `websockets`. | `Container` composes application services. `main.py` starts an interactive CLI/gateway path; the manager explicitly launches the service entrypoint instead. Operation/task/history stores use SQLite; other memory stores are separate. Tests under `tests/runtime/`, `tests/interface/`, and broader `tests/`. | **VERIFIED:** two distinct entry paths; do not start both against the same ports. Container registration does not establish that all agent/orchestrator dispatch paths are reachable. |
| Launcher, service manager, runtime ownership | `start-ultron.sh`, `stop-ultron.sh`, `scripts/ultron_service_manager.py`; Python, Linux `/proc`, and Unix sockets. | Services are explicitly declared by the manager rather than discovered as plugins: gateway `127.0.0.1:8080`, frontend `:8000`, auth helper `:8081`, Edge voice `:8765`. PID/start-tick identity, manager lease/control socket, per-service readiness. Tests: `tests/runtime/test_service_manager.py`, `test_maintenance_transition.py`. | **VERIFIED IN SOURCE; UNRESOLVED LIVE ANOMALY:** prior read-only evidence found a manager parent and four recorded services while lease/socket were absent. Cause and loaded source are unknown. See [ULTRON_RUNTIME_OWNERSHIP_AUDIT.md](./ULTRON_RUNTIME_OWNERSHIP_AUDIT.md). |
| Local authentication and gateway | `ultron/application/runtime/local_auth_helper.py`; `ultron/application/runtime/interface_gateway.py`; WebSocket library. | Helper is an explicit manager service and connects to the gateway after private local unlock. Gateway request handlers enforce server-side principal/authorization; owner identity is not a trusted browser field. Auth state/throttle use local protected storage. Tests: `tests/interface/test_local_auth_helper.py`, `test_interface_authentication.py`, gateway authorization/execution tests. | **VERIFIED IN SOURCE/TEST INVENTORY; LIVE AUTH UNVERIFIED.** Do not infer current live response or owner isolation from tests alone. |
| Frontend and operator console | `interface/index.html`, `interface/operator-console.js`, `interface/services/providers/websocketProvider.js`, `interface/contracts/contracts.js`; browser ES modules, no npm runtime dependency declared in `interface/package.json`. | Static module loading and explicit WebSocket provider, not a server-side plugin scan. Provider connects through helper/gateway; contract validators and stale-state logic are frontend boundaries. Tests: `npm test --prefix interface`, including WebSocket, CRE status, and evidence rendering tests. | **PARTIALLY_VERIFIED:** source contracts and prior test evidence exist; authenticated browser, live freshness, image decoding, reconnect, and owner-isolation verification remain incomplete. |
| System Intelligence | `ultron/application/runtime/system_intelligence.py`; authenticated gateway handlers; local manager control channel for supported operations. | Gateway handler/request registration is explicit. Collects bounded host/process/listener observations and persists owner-only diagnostics under launcher state. Tests: `tests/runtime/test_system_intelligence.py`, `tests/interface/test_interface_gateway_system_intelligence.py`, frontend runtime-event tests. | **PARTIALLY_VERIFIED:** snapshot contracts are tested. Diagnostic collection persists observations, so it is not strictly read-only; it is not a general host-control interface. |
| CRE and persisted evidence | `ultron/application/content/service.py`, `status.py`, `processing.py`, `intelligence_service.py`, `interface_gateway.py`; local SQLite and preview assets. | Gateway exposes explicit status/preview handlers; CRE runtime setup is conditional on `ULTRON_CRE_DATABASE`. Preview paths may require local FFmpeg/FFprobe, `libflite`, Pillow/font, and provisioned Whisper model files; these are not established by static registration. Owner-filtered DTOs use dedicated `data/ultron_cre.db`; source schema versions include content 7, publishing attempts 9, scene contract 2. Tests: `tests/application/`, `tests/interface/test_interface_gateway_cre_status.py`, frontend CRE/evidence tests. | **VERIFIED SOURCE LIMITATION:** runtime processing is not configured, rendering is preview-only, publishing is mock-only, analytics unavailable. **LIVE FLOW UNVERIFIED:** no authenticated scene-gallery/hash/decode check. |
| Trading and financial dashboard | `ultron/financial/bootstrap.py`, `financial_main.py`, `run_financial_dashboard.sh`, `dashboard/financial_dashboard.py`; optional Streamlit/Alpaca integrations. | Separate explicit Streamlit entrypoint, not discovered or served by the operator console. Financial construction is simulation-bound; paper account reads use paper mode. Experience persistence comes from the application container. Tests: `tests/financial/`, `tests/dashboard/test_financial_dashboard_contract.py`. | **VERIFIED SOURCE SEPARATION:** Streamlit dashboard is not the local-unlock operator console. No order/account operation was performed in this investigation. |
| Voice | `ultron/voice/server.py`; managed CLI explicitly selects `--engine edge`. | Edge is the managed service choice. XTTS/DSP dependencies are optional/lazy paths; the nested reference environment is not registered as the managed voice engine. Loopback HTTP service on `127.0.0.1:8765`. Tests: `tests/voice/`, frontend voice client/playback tests. | **PARTIALLY_VERIFIED:** managed Edge path is distinct from nested XTTS environment. Existing audit records static CORS/auth concerns; no voice runtime was touched and those concerns were not re-tested here. |
| Persistence, schemas, migrations | Operation/task stores, CRE `ContentStore`, history/memory/experience stores; explicit SQLite configuration. | Stores bind to configured paths; CRE initialization is conditional and schema versions are declared in source. SQLite/WAL persistence keeps CRE and operations data separate. Online-backup utility uses SQLite's backup API. Tests include `tests/runtime/test_sqlite_backup.py` and subsystem persistence tests. | **UNVERIFIED LIVE DATA:** no live DB was opened, backed up, migrated, or modified. Schema/test evidence is not a production backup or restore. |
| Release, backup, maintenance and recovery | `scripts/ultron_release_bundle.py`, `scripts/ultron_sqlite_backup.py`, runtime and recovery runbooks. | Release tool requires an explicit caller-supplied file allowlist; it does not infer a deployable closure. SHA-256 manifest, archive verification/restoration, and protected online SQLite backup/schema checks are explicit steps. Tests: `tests/runtime/test_release_bundle.py`, `test_sqlite_backup.py`, maintenance tests. | **BLOCKED FOR DEPLOYMENT:** [ULTRON_RELEASE_MANIFEST.json](./ULTRON_RELEASE_MANIFEST.json) is preliminary, with no approved complete allowlist, release ID, or verified rollback archive. |
| OMEGA integration boundary (separate, read-only) | ULTRON adapter in `ultron/application/runtime/interface_gateway.py`; OMEGA workspace/runtime is outside this investigation. | Adapter uses configured endpoint settings and token-bearing requests; no OMEGA source/runtime was inspected, no request was made, and no integration was activated. | Prior source audit reports a configured URL can be non-loopback while docs claim loopback-only. **PARTIALLY_VERIFIED from prior source audit.** Preserve this as a credential-destination review item. |
| Optional learning and reference environments | Learning PDF loaders; `ultron/voice/reference_staging/`; root, optional, and nested dependency manifests. | Learning loaders are imported/registered by their surrounding application workflow; the nested voice workflow is separate. Exact plugin, script, package-entry-point, and configuration reachability is not closed by ordinary import search. | **UNVERIFIED COMPLETE CLOSURE:** retain uncertain files and optional environments; do not treat lack of a static edge as proof of dead code. |

## Investigation D — risk-ranked recommendations

These are evidence and test gates, not implementation work. The priorities follow the requested risk ordering. No fix is proposed for a cache candidate solely because Atlas labels it unreachable.

| Priority | Evidence and recommendation | Affected components / prerequisites | Required tests and rollback considerations |
|---|---|---|---|
| 1 — data loss / unsafe operation | **BLOCKED:** no approved full release or verified rollback artifact; live SQLite databases were not backed up. Establish an exact release scope and protected online backup/restore before any deployment or maintenance. | Release owner, runtime dependency closure, SQLite schema expectations, explicit operator authorization. Preserve live DB and WAL/SHM files. | Rehearse release verification/restoration and online backup/restore on isolated data; before live maintenance, verify protected backup integrity, schema/version, size, and SHA-256. Roll back only to a separately verified bundle and database recovery point. |
| 2 — authentication / authorization | **PARTIALLY_VERIFIED:** authenticated browser flow, CRE owner isolation, and the previously identified token-bearing OMEGA destination restriction remain unresolved. Complete normal private unlock verification; constrain and test token-bearing destinations before any such request. | Auth helper, gateway, dashboard, CRE evidence, ULTRON OMEGA adapter; authorized operator/second principal; do not inspect or modify OMEGA. | Test exact origin allowlists, auth failure/expiry/reconnect, stale/error handling, owner isolation with authorized principals/fixtures, and no-egress on rejected destination. Roll back any adapter policy change without broadening the allowlist. |
| 3 — runtime ownership / recovery | **VERIFIED ANOMALY, CAUSE UNKNOWN:** a manager parent and four recorded services were observed while expected lease/socket artifacts were absent. Do not start a competing manager, adopt processes, or signal them. Establish the original owner/terminal and a separately reviewed maintenance procedure. | Service manager, owner-only state, original manager operator, verified release/backup prerequisites. | Existing identity/ownership tests plus interrupted-recovery tests; a maintenance plan must stop on ambiguous process identity. Rollback must preserve existing supervisor authority or require explicit intervention rather than guessing ownership. |
| 4 — broken end-to-end integration | **UNVERIFIED:** no authenticated live data flow from gateway to current CRE scene evidence was demonstrated. Complete the documented browser procedure without modifying runtime state. | Auth helper, WebSocket gateway, CRE status/evidence, frontend rendering, operator availability. | Verify response freshness, errors, backend/displayed hashes and dimensions, each image's decode, reconnect, and stale-state transitions. Require an authorized second principal or supported fixture before claiming owner isolation. |
| 5 — dashboard correctness and capability status | **VERIFIED SOURCE CONTRACT; LIVE DISPLAY UNVERIFIED:** unavailable CRE processing/rendering/publishing/analytics must remain explicit; cached data must remain stale until refreshed. | CRE backend projection, frontend contracts/providers, authenticated browser. | Backend contract and rendering tests, including unavailable/error/stale cases; no rollback concern unless a source change is later approved, in which case revert only that isolated display change. |
| 6 — test coverage / detector disagreement | **PARTIALLY_VERIFIED:** Atlas and the project analyzer disagree on first-party cycles; dynamic loading and optional-environment closure are incomplete. Investigate only cycles tied to a reproduced import/runtime failure. | Python module owners, analyzer configuration, staging entry points, import/plugin configuration. | Add focused import-order/runtime tests if a failure is reproduced; preserve existing lazy imports unless a tested change proves safe. Roll back only the implicated dependency change. |
| 7 — maintainability / dead-code candidates | **VERIFIED FALSE-POSITIVE RISK:** all 370 candidates are environment package files, not confirmed dead application code. Retain them unless a specific package is proven unused across scripts, plugin loading, configuration, and recovery. | Optional voice staging and its environment owner. | If cleanup is ever proposed, inventory package/entry-point dependencies and run the relevant isolated staging tests. Preserve a checksummed environment backup/restore path; do not bulk-delete from Atlas labels. |
| 8 — documentation / inventory | **PARTIALLY_VERIFIED:** Atlas group names can be mistaken for functional architecture; existing readiness and runtime findings remain unresolved. Keep this report separate and update source inventories only after reproducible evidence changes. | Architecture documentation and named subsystem owners. | Review source links and status labels; no application rollback required. Preserve the existing audit records and unresolved status until direct evidence closes them. |

## Investigation E — correlation with previous audits

The following prior findings remain open and were not duplicated or marked resolved:

| Prior finding | Current correlation | Evidence/status |
|---|---|---|
| Manager ownership, missing lease/control socket, and unknown live source identity | Atlas graph provides no runtime ownership or source-version evidence. | **UNRESOLVED** — [ULTRON_RUNTIME_OWNERSHIP_AUDIT.md](./ULTRON_RUNTIME_OWNERSHIP_AUDIT.md), [ULTRON_RECOVERY_READINESS.json](./ULTRON_RECOVERY_READINESS.json). |
| No approved complete release and known-good rollback identity | Atlas cache is not a release manifest and does not attest all runtime files/dependencies. | **BLOCKED** — [ULTRON_RELEASE_MANIFEST.json](./ULTRON_RELEASE_MANIFEST.json) remains preliminary. |
| Live database backup/restore prerequisites | Atlas does not inspect database health or provide recovery evidence. | **UNVERIFIED LIVE** — only isolated backup rehearsals are referenced by prior reports. |
| Authenticated browser, CRE freshness/gallery, and owner isolation | Atlas static graph cannot establish current browser authentication or rendered evidence correctness. | **NOT VERIFIED** — [ULTRON_AUTHENTICATED_VERIFICATION_RUNBOOK.md](./ULTRON_AUTHENTICATED_VERIFICATION_RUNBOOK.md). |
| CRE workflow completeness | Atlas's groups do not change the source-level status that processing is not configured, rendering is preview-only, publishing is mock-only, and analytics are unavailable. | **CONFIRMED SOURCE LIMITATION; LIVE STATUS UNVERIFIED** — comprehensive audit and integration-gap register. |
| PyMuPDF licensing | Atlas does not resolve legal/compliance approval or exact deployed dependency scope. | **APPROVAL BLOCKER** — [ULTRON_DEPENDENCY_SECURITY_REVIEW.md](./ULTRON_DEPENDENCY_SECURITY_REVIEW.md). |
| Transformers advisory | Atlas does not establish whether nested staging scripts reach the affected model-loading path. Prior review found version 4.57.6 and insufficient evidence to call CVE-2026-5241 applicable. | **APPLICABILITY UNCONFIRMED** — retain targeted review; do not report confirmed exposure or mark the optional environment clear. |
| OMEGA token-bearing destination and other integration gaps | Atlas does not inspect or authorize external requests. OMEGA remains a separate read-only boundary. | **UNRESOLVED** — prior source/documentation mismatch remains open. |

The earlier audit reports and registers remain unchanged. The new candidate register and this report are separate Atlas-specific deliverables.

## Validation limits and final disposition

- Atlas findings were read from the workspace cache and checked against extension behavior. No findings export command was available.
- The candidate path list is complete for the 370 cached records; this is not a complete audit of every file Atlas did not index, every dynamic import, or every one of the 884 cycle records' runtime consequences.
- No application fix, cleanup, service operation, browser action, database operation, dependency installation, or test run was performed.
- Current files and cached size/mtime metadata matched at inspection time; this does not establish file-content identity, runtime-loaded source, or deployability.

**Disposition:** Atlas's counts and the location of its dead-code candidates are **VERIFIED**. The implication that the 370 candidates are unused ULTRON code is **NOT VERIFIED** and unsupported by their location; the cycle count is a structural detector output, not a defect count. Runtime ownership, release/rollback, live database recovery, authenticated browser acceptance, CRE evidence freshness, and dependency approval remain unresolved.

**Safest next action:** maintain **NO-GO**. Have the responsible operator establish manager ownership and approve an exact release/recovery scope before any maintenance. Separately, schedule normal private browser verification only when an authorized operator is present; do not use Atlas findings to justify cleanup or runtime changes.
