# ULTRON Blocker Closure Plan

**Plan status:** Proposed; no gate has passed by virtue of this plan.  
**Decision:** **NO-GO** for live maintenance, deployment, or release acceptance.  
**Mission scope:** planning and read-only source/report verification only. No application files, services, credentials, or databases were changed or accessed.

## Executive decision and first action

The first actionable blocker is **ULTRON-CLOSE-00: establish authoritative runtime ownership and recovery authority**. The latest recorded observation says manager PID 6943 still parented the four recorded services while the expected manager lease and control socket were absent. Their process identities matched the then-current state records, but this does not prove which manager source revision is loaded or establish a supported handoff. A PID, listener, missing lease, or missing socket is not ownership evidence.

The responsible operator must first locate and identify the original manager owner/terminal or an equivalent authenticated startup/ownership record. This step is read-only. If the owner, service identities, or safe original-manager shutdown route remain ambiguous, stop and require operator intervention; do not start a competing manager, adopt processes, recreate state artifacts, or signal any process.

Engineering may prepare isolated release, backup-rehearsal, test, and CRE work once its source scope is explicitly selected. None of that closes the live ownership gate, authorizes a live backup, or proves production readiness.

## Evidence basis and known inconsistencies

This plan was based on the reports listed in the mission and a read-only review of the associated manager, release/backup utilities, gateway/authentication, CRE status/preview, frontend rendering, and focused tests.

### Findings used without upgrading their status

- The [runtime ownership audit](./ULTRON_RUNTIME_OWNERSHIP_AUDIT.md) and [recovery readiness record](./ULTRON_RECOVERY_READINESS.json) describe a verified ownership anomaly, unresolved cause, unknown loaded source, and no supported live adoption or handoff.
- The [release manifest](./ULTRON_RELEASE_MANIFEST.json) is explicitly preliminary: no approved complete allowlist, release ID, archive, or rollback artifact exists. The release builder requires an explicit allowlist; it cannot determine file closure.
- The SQLite backup tool uses SQLite's online backup API and has recorded temporary WAL-mode rehearsal evidence. This does not constitute a backup of any live ULTRON database.
- The authentication, gateway, CRE status, scene-preview, and frontend contracts have historical isolated test evidence. The reports consistently say the authenticated live browser and live service source identity are unverified.
- CRE status in source reports processing `NOT_CONFIGURED`, rendering preview-only or not configured, publishing `MOCK_ONLY`, and analytics unavailable. These are capability limits, not evidence that the services should be made to report healthy.
- Atlas's 370 candidate files are nested reference-environment files. They are not confirmed unused and are explicitly excluded from cleanup recommendations.

### Report/source discrepancies to preserve and resolve

1. **Atlas report observation date — inconsistent.** [ATLAS_ARCHITECTURE_EXPLORER_VALIDATION.md](./ATLAS_ARCHITECTURE_EXPLORER_VALIDATION.md) says its filesystem checks occurred on 2026-10-28, which is later than this plan's current session date, 2026-10-27. Its cache snapshot is dated 2026-10-27T13:02:33Z. Do not treat the stated 2026-10-28 filesystem check as a current, independently dated observation without correction/provenance.
2. **Transformers advisory — scope differs and the blanket conclusion is unresolved.** [ULTRON_DEPENDENCY_SECURITY_REVIEW.md](./ULTRON_DEPENDENCY_SECURITY_REVIEW.md) says CVE-2026-5241 lists 5.2.0 and does not list 4.57.6. The comprehensive audit and LIM-012 also cite GHSA-29pf-2h5f-8g72/CVE-2026-4372 and a GHSA record with ranges that include 4.57.6. These are not established to be the same advisory/range. Reconcile each identifier and exact package source separately; neither “all Transformers is vulnerable” nor “4.57.6 is clear” is supported.
3. **pypdf advisories — coverage differs.** The comprehensive audit and LIM-012 report three high advisories for selected-environment pypdf 6.20.0, with 6.21.2 listed as fixed; the specialized dependency review notes pypdf but does not include that advisory assessment. Root requirements are unpinned and the production environment is unknown. Treat as a release-scope review item, not confirmed production exposure.
4. **CRE persisted gallery — source/description mismatch.** The system inventory and runbook describe persisted scene-gallery delivery. In the inspected implementation, `CRE_STATUS_REQUEST` returns persisted scene evidence metadata without `dataBase64`, and `renderCreAudioEvidence.js` displays metadata only. `operator-console.js` displays image bytes returned by the script-preview response. Thus a generated preview can display an image, but current evidence does not establish a persisted-image gallery/read path. This is a confirmed contract gap against the requested persisted-gallery acceptance, not proof of a broken preview route.
5. **Historical tests versus current audit.** The comprehensive audit states that no tests ran for that audit; recovery/release reports quote earlier isolated suite results. These are compatible when labeled by run and scope, but none is a fresh result for this plan and none proves live behavior.

## Dependency graph

```text
Operator identifies original manager owner / safe recovery authority (C00)
  ├─> Select exact intended release scope and runtime contract (C01)
  │     ├─> Resolve license/advisory/dependency inventory and lock (C02)
  │     ├─> Resolve CRE product scope and persisted-image contract (C03)
  │     │     └─> Implement/test only approved isolated CRE gap, if needed (C04)
  │     └─> Protected release allowlist can be finalized
  ├─> Isolated SQLite WAL backup/restore rehearsal (C05; can run independently)
  └─> Source-to-running-manager association remains UNKNOWN unless direct
        launch/owner evidence exists; otherwise intervention-required

C01 + C02 + C03 + C04 -> build/check candidate release + rollback evidence (C05)
C05 -> isolated qualification against restored release (C06)
C03 + C06 -> isolated authenticated CRE E2E verification (C07)
C00 + C02 + C04 + C05 + C06 + C07 + explicit maintenance authorization -> live preflight (C08)
C08 pass -> authorized maintenance/deployment only (C09; separate authorization)
C09 pass -> operator-authenticated live acceptance (C10)
```

The isolated SQLite rehearsal can proceed independently of manager ownership, but must use temporary databases and remains isolated evidence. A live backup is a separate operator-authorized action after identity, release, storage, schema, and maintenance authority are established. Likewise, a local candidate-stack test does not certify the currently running manager or live databases.

## Safe recovery and live-maintenance gate

All boxes must have evidence before a separate maintenance plan may request execution. This plan itself authorizes none of them.

### Ownership and identity

- [ ] Operator identifies the original manager owner and terminal/session, or provides an authenticated owner/startup record.
- [ ] Read-only preflight captures manager and each service's PID, process start ticks, executable real path, working directory, command identity, process group, parentage, state-record association, and listener binding/owner. Recheck for identity changes immediately before any separately authorized operation.
- [ ] The expected owner/state files and control channel are examined without creating, deleting, or repairing them. The current anomaly is explicitly reconciled or declared **INTERVENTION_REQUIRED**.
- [ ] Loaded source identity is bound to the intended release by reliable launch-time provenance, immutable release directory/build identity, or equivalent evidence. A current path/hash, mtime, PID, or port alone is insufficient to attest already loaded Python code.
- [ ] The source-supported stop/control path is documented and confirmed with the original owner. Current reports say no live adoption/handoff is supported. Do not invent a lease transfer or use a new manager to take ownership.
- [ ] If any process identity is stale, reused, ambiguous, or does not match expected command/cwd/process group/listener ownership: **STOP; no signal, restart, adoption, or state edit.**

### Release, rollback, and data recovery

- [ ] Release owner approves an exact source/runtime file closure; each required dynamic resource, service command, config, schema, frontend asset, and external runtime prerequisite is accounted for.
- [ ] Credentials, `.env*`, live databases and WAL/SHM, virtual environments, caches, generated/unrelated files, and the voice reference environment are excluded unless a separately justified, approved runtime requirement says otherwise. Do not delete or label Atlas candidates unused.
- [ ] Dependency lock/SBOM is generated for the exact supported runtime and platform. Direct/transitive packages, optional environments, advisories, and licenses are classified separately.
- [ ] An independently justified rollback release is identified. Do not call the 14-file pre-change baseline or an arbitrary ZIP a known-good rollback bundle.
- [ ] Candidate and rollback archives each pass exact-member and SHA-256 manifest verification, archive integrity checks, and restoration into a new isolated directory. Restored files match the manifests; required startup/configuration checks run without live credentials or data.
- [ ] SQLite backup tool is qualified on temporary WAL-mode databases. Before future live use, operator approves database paths, expected schema/application/user versions, owner-only destination/storage, timestamp window, and restore destination. Use online backup API; preserve originals and WAL/SHM.
- [ ] Any authorized live backup is integrity-checked and compared for expected schema/version, file size, and SHA-256; a separate restore copy passes integrity/schema checks. Never infer WAL coverage from copying only the active database file.

### Governance and stop conditions

- [ ] Explicit operator authorization names the exact maintenance window, release ID/hash, rollback ID/hash, database(s), service action(s), and post-checks. Authorization for read-only inspection or helper restart does not imply authorization for broader maintenance/deployment.
- [ ] A durable, secret-free maintenance journal has prechecks, operator, timestamps, decisions, stop conditions, and recovery outcomes.
- [ ] Preflight stops on owner/source mismatch, ambiguous PID/start identity, unexpected listener, stale state, missing/invalid rollback, failed backup/restore, schema mismatch, failed tests, or absent authorization.
- [ ] Post-maintenance checks confirm service identity and readiness, unrelated service identity preservation where applicable, authenticated status, freshness/error behavior, and rollback readiness. No publication, trading, OMEGA access, or unrelated service changes are in scope.

## End-to-end verification contract

| Subsystem | Entry points and runtime identity | Interface/authentication | Expected state and failure behavior | Tests and evidence to close |
|---|---|---|---|---|
| Manager and services | `start-ultron.sh` → `scripts/ultron_service_manager.py`; manager state includes PID/start ticks. Runtime source must be associated with an exact release; manager must be authoritative owner. | Owner-only state, lifetime lease, Unix control socket, service-specific readiness. No adoption is supported without validated transfer. | Each process identity agrees across `/proc`, state, parentage, and service definition; helper/gateway listeners bind expected loopback addresses. Ambiguity is intervention-required. | `tests/runtime/test_service_manager.py`, `test_maintenance_transition.py` on exact candidate source; preserve unrelated PIDs for helper-only actions. Live non-secret identity/readiness record and operator owner evidence. |
| Local authentication | `ultron/application/runtime/local_auth_helper.py`, manager auth service on `127.0.0.1:8081`; gateway `interface_gateway.py` on `127.0.0.1:8080`. | Private normal dashboard unlock; exact allowed origin; server-side token/principal/session; no credential in logs or report. | Unlock and authenticated WebSocket status succeed; disallowed origin, expired session, helper unavailable, and gateway unavailable remain explicit failures. | `tests/interface/test_local_auth_helper.py`, `test_interface_authentication.py`; browser evidence of private unlock outcome, authenticated connection, session-expiry/reconnect, sanitized console, and approved loopback/origin only. |
| Gateway and System Intelligence | `ultron/application/runtime/service_entrypoint.py`, `interface_gateway.py`, `system_intelligence.py`. | Authenticated request handlers; backend derives principal from session. System Intelligence reports observation source/time/status and can persist diagnostic observations. | UI marks current only after successful fresh response; stale, unavailable, unknown, and errors are not replaced with healthy defaults. Do not call collection side-effect-free. | `tests/interface/test_interface_gateway_system_intelligence.py`, `tests/runtime/test_system_intelligence.py`, `interface/tests/runtimeEvents.test.mjs`; compare displayed rows to the same response's observation timestamps/status and sanitized error evidence. |
| CRE status | `ContentStatusService` and `InterfaceGateway._handle_cre_status`; frontend `requestCreStatus` / status contract. | `CRE_STATUS_REQUEST` requires authenticated principal; client-supplied owner is rejected; owner mapping/store queries are server-side. | `AVAILABLE` includes generated time and bounded owner-filtered records; missing service is `UNAVAILABLE`, invalid/unauthorized are distinct. Frontend retains last snapshot only as `STALE`/`UNAUTHORIZED` when refresh fails. | `tests/interface/test_interface_gateway_cre_status.py`, `interface/tests/creStatus.test.mjs`, `interface/tests/websocketProvider.test.mjs`; non-secret correlation/status/timestamp record and owner-filtered response evidence. |
| CRE synthetic preview and scene imagery | `CRE_SCRIPT_PREVIEW_REQUEST` through `interface_gateway.py` to `OriginalScriptPreviewService`/scene renderer; `operator-console.js` renders the preview gallery. | Authenticated request; use one clearly synthetic original local script, no publication and no external fetch. The preview response contains scene `dataBase64`; persisted status returns metadata/hash without image bytes. | Preview-only must stay labeled preview-only. For each generated image, verify response hash, format, byte count, renderer/contract bindings, scene status, positive declared dimensions, and actual browser decode (`naturalWidth`/`naturalHeight` nonzero). Image failures are visible; no placeholder counts as evidence. | `tests/interface/test_interface_gateway_cre_status.py` (synthetic preview, PNG signature/hash, persisted metadata comparison), `interface/tests/websocketProvider.test.mjs`, and UI component tests. Actual browser image decode and backend-vs-display hashes/dimensions recorded. |
| Persisted CRE gallery | Existing status DTO: `ContentStatusService`/`list_audio_evidence`; frontend `renderCreAudioEvidence.js`. Runtime identity must be the same candidate/live release under test. | Status read is authenticated and owner-scoped, but current DTO omits `dataBase64`; current renderer displays persisted metadata only. A durable image retrieval contract is not established by the reviewed files. | Do not claim persisted images are displayed based solely on preview images. First approve whether persistent-image retrieval is required and its bounded, owner-scoped transport; then test authorization and integrity. | Existing tests prove current readback metadata/hash and that base64 is omitted. New tests are required if a retrieval path is approved: cross-owner denial, non-image/oversize/tamper rejection, content/hash binding, browser decode and error/stale handling. |
| CRE capability state | `ultron/application/content/status.py`, gateway, dashboard contracts. | Backend reports explicit service availability. Rights/evidence and preview do not by themselves establish legal clearance. | Processing remains `NOT_CONFIGURED`; rendering is `LOCAL_SCRIPT_PREVIEW_ONLY` or unavailable; publishing `MOCK_ONLY`; analytics unavailable. UI must preserve those states and never equate mock publication with real publication or revenue. | `tests/interface/test_interface_gateway_cre_status.py`, `interface/tests/creStatus.test.mjs`, `interface/tests/renderCreAudioEvidence.test.mjs`; assert exact state/error/next-action and stale indicators. |
| Owner isolation | Gateway principal → `ContentOwnerMapping` → owner-filtered store. | No client-selected owner. Live claim requires a separately authorized second principal; otherwise use a clearly labeled isolated fixture for automated proof only. | Principal A cannot read principal B's status, preview, or stored evidence; authorization failures remain non-success. | Existing backend owner-scope tests plus new/retained two-principal fixture tests; for live claim, authorized second account, sanitized principal labels, cross-owner denial result. |
| Release and data recovery | `scripts/ultron_release_bundle.py`; `scripts/ultron_sqlite_backup.py`; exact restored release and DB schema. | Explicit allowlist and checksums; SQLite online backup, protected destination and separate restore. | No live service action until candidate and rollback archive restore, isolated DB restore, and schemas qualify. | `tests/runtime/test_release_bundle.py`, `tests/runtime/test_sqlite_backup.py`, exact manifest/member checks, restore SHA-256/schema/integrity results. These tests are isolated evidence, not live backup proof. |

### CRE capability boundary

For safe verification, first close only the existing authenticated local preview/status path. The current source supports synthetic local script preview, technical scene evidence, and owner-scoped metadata readback; it does not establish a full operational intake-to-processing pipeline.

Keep the following explicit unless a separately approved product mission implements and tests them:

- Discovery and fetching of media are not established. A supplied URL/reference is not discovery, verified ownership, or legal clearance.
- Processing is reported not configured; transcription/model files and runtime exposure are not established.
- Publishing is mock-only. A mock acknowledgement is not external publication. No publishing operation is authorized here.
- Analytics/revenue are unavailable. Do not infer analytics or revenue from job, rights, review, or upload state.
- If persisted scene pixels are a required gallery capability, current status metadata is insufficient. Product/architecture approval is needed for an authenticated owner-scoped retrieval contract before implementation.

## Ordered remediation missions

The detailed machine-readable version is [ULTRON_BLOCKER_CLOSURE_PLAN.json](./ULTRON_BLOCKER_CLOSURE_PLAN.json). Missions are planning units; they are not authorizations to operate.

### ULTRON-CLOSE-00 — Resolve runtime ownership and identity

- **Classification:** confirmed runtime anomaly; source identity and cause unknown.
- **Prerequisites:** none; this is the first gate.
- **Objective/scope:** operator-led read-only evidence collection to identify original manager owner/terminal, reconcile parent/state/process identities, and find trustworthy launch-time source/release provenance.
- **Likely files:** none; evidence record only, if operator permits recording it.
- **Explicit exclusions:** no stop/restart/signal/adoption; no creating/removing lease/socket/state records; no live DB or credential access.
- **Tests/acceptance:** no test can prove ownership of the live process. Source-owned unit tests remain regression evidence only. Gate passes only with authenticated owner/startup evidence and exact service identity records; if not, status is `INTERVENTION_REQUIRED`.
- **Recovery/rollback:** preserve running supervision and state; if ownership ambiguity persists, no live operation.
- **Closure evidence/operator:** owner/terminal record, timestamped identity tuple for manager and all four services, source provenance, and explicit statement of supported shutdown/control path. Requires authorized operator.

### ULTRON-CLOSE-01 — Approve release scope and runtime closure

- **Classification:** confirmed release blocker, with unknown full file/dependency closure.
- **Prerequisites:** source/release owner approval; C00 evidence for any claim tying a candidate to current runtime.
- **Objective/scope:** identify exact intended source snapshot, service commands/configuration, runtime assets, migrations/schema expectations, dynamically loaded modules/resources, and files required by supported entrypoints.
- **Likely files:** approved file allowlist, release inventory/manifest, dependency inventory; possibly `docs/ULTRON_RELEASE_MANIFEST.json` only in a future implementation task.
- **Explicit exclusions:** no treating the 14-file baseline or existing arbitrary ZIP as deployable; no credentials, live DB/WAL/SHM, venv/cache, unrelated generated content, or Atlas candidate deletion.
- **Tests/acceptance:** static/import/config/package-data closure review; no unknown required path; hash every selected file; explicit excluded-scope record.
- **Recovery/rollback:** source snapshot remains preserved; do not overwrite existing artifacts.
- **Closure evidence/operator:** release owner signs source identity, allowlist, runtime scope, and exclusions. Operator/release owner required.

### ULTRON-CLOSE-02 — Resolve dependency, advisory, and license gates

- **Classification:** confirmed approval requirement plus partially verified scanner findings; production applicability unknown.
- **Prerequisites:** C01 exact runtime and optional-environment scope.
- **Objective/scope:** resolve PyMuPDF AGPL/commercial approval, exact root and transitive lock/SBOM, pypdf advisory status for the selected version, and Transformers advisories by exact identifier and reachable loader path.
- **Likely files:** dependency lock/SBOM and possibly `requirements.txt` or separate optional lock; advisory/license disposition record.
- **Explicit exclusions:** no dependency changes/install/upgrade in this planning mission; do not remove PyMuPDF/PDF behavior or treat 4.57.6 as either categorically clear or categorically exploitable; keep XTTS staging separate from managed Edge.
- **Tests/acceptance:** installed package metadata/lock comparison; relevant compatibility and advisory tests in future scoped dependency changes; license approval recorded against deployment/distribution model.
- **Recovery/rollback:** each dependency change gets a separately verified lock and revertible source diff; no speculative upgrades.
- **Closure evidence/operator:** signed legal/compliance disposition, exact lock/SBOM, advisory table and reachable call-path evidence. Counsel/compliance and release owner required.

### ULTRON-CLOSE-03 — Decide CRE supported capability and persisted-gallery contract

- **Classification:** confirmed source capability limits; confirmed persisted-image contract gap against requested gallery acceptance.
- **Prerequisites:** C01 source scope; CRE/product owner defines whether persisted images are required, beyond preview return images.
- **Objective/scope:** freeze the supported boundary: authenticated status, local synthetic preview, persisted evidence metadata, image-byte retrieval requirement, and unavailable processing/publishing/analytics states.
- **Likely files:** if implementation approved later, likely gateway/CRE status or asset retrieval code, frontend contracts/provider and operator-console renderer, focused backend/frontend tests; exact file change requires design review.
- **Explicit exclusions:** no live media intake/fetch, publishing, revenue claims, OMEGA, or weakening of rights/approval gates. No persisted gallery implementation is assumed in this plan.
- **Tests/acceptance:** tests define expected status payload and image retrieval contract, expiry and error states, owner isolation, hash/dimension bindings, image tamper/oversize denial.
- **Recovery/rollback:** keep existing status/preview behavior; any future endpoint/UI addition must be independently revertible and remain owner-scoped.
- **Closure evidence/operator:** approved capability statement and contract decision. CRE/product owner and rights-policy owner required.

### ULTRON-CLOSE-04 — Qualify isolated SQLite backup/restore

- **Classification:** existing backup utility and historical rehearsal are test evidence; live backup is unverified.
- **Prerequisites:** current backup-tool source identity; temporary test database/schema expectations. No live authority needed for this isolated rehearsal.
- **Objective/scope:** freshly rehearse a temporary WAL-mode DB using the online backup API, owner-only destination, integrity/schema/version/size/hash verification, separate restore, and source/WAL preservation.
- **Likely files:** none if existing tool/tests pass; otherwise only `scripts/ultron_sqlite_backup.py` and `tests/runtime/test_sqlite_backup.py` in a future implementation mission.
- **Explicit exclusions:** never point rehearsal at live databases; no opening, copying, checkpointing, migrating, backing up, or modifying live DBs.
- **Tests/acceptance:** `tests/runtime/test_sqlite_backup.py`; verify WAL remains, backup matches expected schema/application/user version, destination/manifest modes are 0700/0600, restored DB passes integrity and schema/hash checks.
- **Recovery/rollback:** temp-only; preserve source fixture for comparison and discard only test-owned temp outputs.
- **Closure evidence/operator:** fresh test output and sanitized rehearsal manifest. Engineering can execute in isolation.

### ULTRON-CLOSE-05 — Build candidate and independently verified rollback artifacts

- **Classification:** confirmed release/rollback blocker.
- **Prerequisites:** C01 approved file closure; C02 dependency/licensing decision; a separately known-good rollback source/identity; C04 backup-tool qualification.
- **Objective/scope:** use `scripts/ultron_release_bundle.py` with exact allowlists to create candidate and rollback bundles, SHA-256 manifests, verify members/archive, and restore each into separate empty directories.
- **Likely files:** external allowlist/release evidence; eventual release manifest/dependency lock. Tool source is likely unchanged.
- **Explicit exclusions:** no deploy, no use of the 14-file archive as a rollback, no arbitrary ZIP relabeling, no live data in artifacts.
- **Tests/acceptance:** `tests/runtime/test_release_bundle.py`; byte-identical reproducibility for same input, exact member set, tamper rejection, independent archive checksum, restored-file hashes and startup/import checks on restored candidate.
- **Recovery/rollback:** rollback bundle must be independently known-good and its restored runtime contract rehearsed; if unavailable, gate remains NO-GO.
- **Closure evidence/operator:** release IDs, archive hashes, manifests, test output, restored path hashes, and owner approval. Release owner required.

### ULTRON-CLOSE-06 — Qualify the exact restored candidate

- **Classification:** missing release-bound test evidence; historical tests are not fresh.
- **Prerequisites:** C05 restored candidate and exact dependency environment.
- **Objective/scope:** run focused manager/auth/gateway/CRE/System Intelligence/persistence tests and the scoped broader simulation/frontend suites against the candidate (or record any non-runnable optional suite as a separate blocker).
- **Likely files:** no source changes if pass; exact tests are named in JSON.
- **Explicit exclusions:** no live service/database calls, credentials, order submission, publication, OMEGA requests, or suppressing tests/guards.
- **Tests/acceptance:** service manager and maintenance tests; auth helper/authentication; CRE status/preview and System Intelligence; release/backup tests; frontend suite; relevant schema/persistence tests. Record fresh command, count, skips, failures, and optional-dependency collection errors separately.
- **Recovery/rollback:** if a test requires source changes, return to C01/C02/C05 and rebuild; never treat a modified unmanifested tree as the qualified release.
- **Closure evidence/operator:** test report bound to release ID/hash and dependency lock.

### ULTRON-CLOSE-07 — Verify isolated authenticated E2E and CRE evidence

- **Classification:** current browser verification missing; persisted-image gallery source gap identified.
- **Prerequisites:** C03 contract approval, C05/C06 candidate, isolated ports, temporary CRE DB, synthetic script fixture.
- **Objective/scope:** test auth helper → authenticated gateway → CRE status/preview → frontend. Verify generated preview and persisted metadata; if C03 approved and implemented a persisted-image retrieval path, verify its image bytes separately.
- **Likely files:** tests under `tests/interface/` and `interface/tests/`; browser harness/fixtures; any contract changes already approved under C03.
- **Explicit exclusions:** no production credentials/data, live services, external fetch, publication, or owner-isolation claim based solely on one principal.
- **Tests/acceptance:** backend and frontend tests plus isolated real-browser run; synthetic local script; exact correlation/status and generatedAt; current vs stale/error state; compare backend and displayed hashes/dimensions; every intended image must decode with nonzero natural dimensions; owner A/B fixture proves denial and no leakage.
- **Recovery/rollback:** isolated processes and temp data only; tear down only test-owned resources; preserve artifacts/evidence.
- **Closure evidence/operator:** sanitized screenshots/console summaries and machine-readable fixture results tied to release hash. Engineering can run isolated acceptance; live second-principal verification still requires authorized operator.

### ULTRON-CLOSE-08 — Authorize live recovery preflight

- **Classification:** operator authorization and live evidence absent.
- **Prerequisites:** C00–C07 passed; protected backup destination and expected live schemas approved; original manager owner and safe control route established.
- **Objective/scope:** separately approved read-only recheck of all current process identities/source association, followed only by separately authorized online backups and maintenance preparation.
- **Likely files:** no source changes; protected external backup/journal artifacts only under a separately approved task.
- **Explicit exclusions:** this plan does not authorize or execute live DB access/backup, signals, stop/restart, manager handoff/adoption, or deployment.
- **Tests/acceptance:** all recovery-gate checklist items above; identity recheck immediately before any operation, backup integrity/restore to separate destination, exact bundle/rollback IDs, and written stop/rollback plan.
- **Recovery/rollback:** stop on any mismatch; keep existing stack untouched. If no supported operation through original owner exists, intervention-required.
- **Closure evidence/operator:** explicit signed authorization naming exact scope/window/actions and preflight evidence. Responsible operator required.

### ULTRON-CLOSE-09 — Separate maintenance/deployment authorization and execution

- **Classification:** not authorized by this planning mission.
- **Prerequisites:** C08 passed and a new explicit operator approval for the exact change.
- **Objective/scope:** execute a separate maintenance runbook against the approved release, rollback, and database recovery point; verify service readiness and unrelated-service preservation.
- **Likely files:** none in this plan; deployment artifacts only under a separately approved change.
- **Explicit exclusions:** no implicit authority for voice, trading, publishing, OMEGA, or unrelated service changes.
- **Tests/acceptance:** maintenance-specific pre/post checks; only the planned service scope changes; actual release ID/hash and service identity match; rollback tested/available; abort on any ambiguity.
- **Recovery/rollback:** invoke only the approved rollback procedure with verified bundle and database recovery point.
- **Closure evidence/operator:** operator-signed maintenance journal, runtime process/source IDs, tests and post-checks. Operator execution and approval required.

### ULTRON-CLOSE-10 — Authenticated live post-maintenance acceptance

- **Classification:** live browser evidence missing; does not precede authorization/deployment.
- **Prerequisites:** C09 completed and source/release identity verified; normal helper/gateway ready; authorized operator and, for live owner isolation, authorized second principal.
- **Objective/scope:** privately unlock through the existing dashboard workflow; verify gateway connection, fresh CRE/System Intelligence response, stale/error behavior, synthetic preview if allowed by the separate maintenance/verification authorization, image and persisted-gallery route, reconnect, console, and owner isolation.
- **Likely files:** separate operator evidence record only.
- **Explicit exclusions:** no credential collection/logging, no direct unauthenticated gateway, no publishing, trading, OMEGA, DB mutation, or restart.
- **Tests/acceptance:** follow [ULTRON_AUTHENTICATED_VERIFICATION_RUNBOOK.md](./ULTRON_AUTHENTICATED_VERIFICATION_RUNBOOK.md); each image loads/decodes at nonzero dimensions; hashes and dimensions match current backend evidence; UI marks failures/staleness accurately; no owner isolation claim without second principal.
- **Recovery/rollback:** stop verification on auth, identity, freshness, hash, or authorization mismatch; report blocker; do not retry by changing credentials or restarting services.
- **Closure evidence/operator:** sanitized browser record with timestamp, release identity, response times/statuses, hash/dimension/decode results, console summary, and authorized principal labels only. Operator required.

## Explicitly deferred product work

The following are not prerequisites for verifying the currently supported local preview path, provided the UI continues to represent them accurately. They need separate product/authority approval and their own mission if requested:

- Configure/complete media processing and transcription: define lawful source intake, rights evidence, model/runtime asset provisioning, bounded processing, cancellation/recovery, owner scope, and UI/error contracts.
- Production publishing: requires a real provider, protected credentials, approval/rights/kill-switch enforcement, durable external-operation recovery, and a separately authorized sandbox/live test. Mock remains mock.
- Analytics/revenue: requires an approved platform/source, attribution, time range, freshness, and error semantics. Until then remain unavailable.
- Discovery, source fetching, cross-owner evidence access, broad agent-routing changes, voice hardening, OMEGA destination changes, trading/account access, and cleanup are outside this closure sequence unless separately authorized.

## Current status and first next action

No gate passed during this planning mission. No tests were run for this plan. Historical test counts remain historical and are not restated as fresh.

**First actionable blocker:** an authorized operator identifies the original manager owner/terminal and provides read-only evidence for the manager/service identity and launch-time source provenance. If that cannot be established, record **INTERVENTION_REQUIRED** and keep the live stack untouched.

After that, the release owner and compliance owner can select a precise release scope and resolve dependency/licensing status. Engineering can then safely proceed with temporary WAL database rehearsals, candidate artifact verification, and isolated CRE contract/gallery work without touching live state. Any live backup, stop/restart, handoff, deployment, or live authenticated acceptance requires additional explicit operator authorization at its own gate.
