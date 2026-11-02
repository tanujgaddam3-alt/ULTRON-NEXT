# ULTRON System Intelligence Mission 1

**Investigation time:** 2026-10-27 21:53:46 +05:30  
**Report status:** `VERIFIED` for source tracing and the listed test outcomes; live authenticated dashboard verification is `UNVERIFIED`.  
**Deployment decision:** **NO-GO**. No release, rollback, backup, restore, ownership, or authorization gate is passed by this report.  
**Scope:** Read-only source review, safe focused tests, and passive OS observations. No services were started, stopped, signalled, adopted, or contacted; no credentials or databases were accessed; no application code or runtime state was changed.

## Executive summary

The architecture has a real, authenticated System Intelligence request path. After an authenticated WebSocket subscription, the browser requests an inventory; the gateway checks the configured operator identity; the collector obtains host and process facts and returns source labels and observation timestamps. The frontend shows the returned evidence and preserves an inventory as visibly stale after disconnection. The fixed helper-restart control is separately guarded by authentication and manager-owned process/socket checks. None of those source-level facts establishes that the current live dashboard session is working.

Current passive observation did **not** find the historical manager/service PIDs (6943, 6970, 7065, 7071, 7075), or listeners on the previously reported ports 8000, 8080, 8081, and 8765. This only establishes that those particular PIDs and listeners were absent at the observation time; it does not identify a replacement stack or establish that the overall host/application is down. No browser page was shared, and authenticated live verification was not performed.

Two concrete integration defects were confirmed:

1. `SystemIntelligence.collect()` can return overall `VERIFIED` based on host observations while ignoring service `processStatus` and `readiness`. Thus a false-green inventory summary is possible even when its service records are stale or unavailable.
2. The handoff contract is out of sync. The UI and tests expect `managerHandoff`, and the runtime test imports `_manager_handoff_status`, but neither the function nor the response field exists in the collector source. The focused collector test module therefore fails during collection with `ImportError`.

The highest-value proposed fix is to complete the System Intelligence aggregate contract: make the overall status reflect service identity/readiness and manager ownership evidence, provide the documented `managerHandoff` field (or deliberately revise all consumers/tests to the supported contract), and add deterministic tests for stale/ambiguous ownership and false-green prevention. Do not enable an operational action from this inventory until those tests pass and live manager ownership is independently established.

## Architecture and end-to-end data flow

```text
OS / application state
  ├─ System Intelligence: /proc, /proc/meminfo, /proc/net/tcp{,6},
  │  platform APIs, disk usage, optional nvidia-smi, protected manager files
  ├─ Runtime gateway: runtime.state, agent registry, in-memory operation deque,
  │  recent event deque, world observer snapshot
  └─ Gateway handlers: authenticated request/periodic channel snapshots
        ↓
  Authenticated WebSocket gateway and response/channel payloads
        ↓
  WebSocketUltronProvider (local auth helper endpoint by default)
        ↓
  Bridge / channel state → operator-console.js renderers
```

The provider defaults to `ws://127.0.0.1:8081/ultron/stream`. It sets the bridge provenance to `LIVE` after `SUBSCRIBE_ACK`; this means the subscription was acknowledged, not that every value is current or that all subsystems are healthy. The separately requested System Intelligence inventory is sent only after the provider reports an authenticated session. The gateway derives the principal from the WebSocket authenticator and rejects missing or mismatched identity.

### Field-by-field source-of-truth matrix

| Displayed field | Frontend / contract | Backend source | Value class and time semantics | Stale/failure behavior | Evidence and status |
|---|---|---|---|---|---|
| Host CPU and memory usage (overview) | `renderOverview()` reads the `world` channel and displays `world.system.cpu_usage` / `memory_usage`; `validateWorld()` validates the outer object shape | Gateway `_build_world()` calls `world.refresh()` and `world.summary()`; `ultron/world/observers/system.py` samples `psutil.cpu_percent(interval=0.1)` and `psutil.virtual_memory().percent`; world state records `last_updated` | Direct local observer values; `world.observedAt` is derived from world state update time, not browser receipt time | A failed world snapshot returns explicit null-valued fields and an error. When transport is no longer live, overview adds a `STALE · last received` label using browser receipt time | Source traced; no live observation now: `PARTIALLY_VERIFIED` |
| System Intelligence CPU and memory | `renderSystemIntelligence()` renders `observations` | Collector CPU entry is `os.cpu_count()` plus `os.getloadavg()`; memory is parsed from `/proc/meminfo` | Direct host readings at collector invocation; each observation's `observedAt` is collector wall-clock time. CPU is capacity/load, not CPU utilization percentage | Missing memory is `UNAVAILABLE`; missing load average is null; inventory is requested on auth and can be manually refreshed | Source traced; current live inventory not fetched: `VERIFIED` at source level |
| Process inventory and service status | System Intelligence observation cards and service cards | Collector scans bounded `/proc`; service records compare PID, kernel start ticks, process group, executable, working directory, command markers, and recorded state; listener is separately read from `/proc/net/tcp{,6}` | Direct process/listener observations; process arguments are withheld and replaced by a SHA-256 fingerprint. A listener is not proof of application readiness or manager ownership | Missing/mismatched recorded process becomes `STALE` or `UNKNOWN`; listener absent gives `UNAVAILABLE`; listener present still yields readiness `UNKNOWN` | Source traced and focused tests inspected: `VERIFIED` at source level |
| Runtime state and cycle count | Overview reads `cognitiveState.behavior`, `running`, and `cycleCount` | Gateway reads `runtime.state`; `IDLE`/`ACTIVE` are derived from running/cycle count | Direct application runtime state, but no source-observation timestamp is added to this channel payload | Snapshot absence renders unavailable; global transport provenance changes, but a live connection is not a readiness probe | Source traced: `PARTIALLY_VERIFIED` |
| Active-operation count | Overview reads `command.activeOperations.length` | Gateway filters its in-memory operation deque to selected lifecycle states; persisted operation records seed the deque at gateway initialization | Real gateway operation registry projection; not a count of arbitrary OS processes or all work | Missing channel is `UNKNOWN`; no authenticated live payload observed in this mission | Source traced: `PARTIALLY_VERIFIED` |
| Registered agents and agent health | Overview displays `cognitiveState.agents.length`; agent view consumes agent records | Gateway calls `agent_orchestrator.registry.available()` | Registry-backed names/count; current cognitive-state payload provides names and role, but no per-agent health/readiness measurement | Registry exception reports `agents: null` plus `agentsError`; UI does not infer a healthy status from an agent count | Source traced: `PARTIALLY_VERIFIED` |
| Gateway connection and heartbeat | Connection indicator uses bridge provenance; event view consumes `runtimeEvents` | Provider marks `LIVE` after `SUBSCRIBE_ACK`; gateway heartbeat emits runtime `running`, `cycle_count`, uptime, source, and gateway timestamp | WebSocket acknowledgment is gateway transport/subscription evidence. Heartbeat contains direct runtime values, but is not a full service-health/readiness check | Provider reports offline on close/error and reconnects after 5 seconds; cached channel values use browser-received timestamps and some renderers mark them stale | Source traced; current live transport absent: `PARTIALLY_VERIFIED` |
| Recent runtime events | Event renderer and `runtimeEventTime()` | Gateway event bus plus bounded deque (up to 60), with gateway/backend `at` timestamps | Real events when emitted; timestamp is event/gateway time, while “received at” labels are browser receipt time | Undated events are excluded from time filtering; disconnection is reflected by transport provenance, but event history may remain visible | Existing frontend tests pass; source traced: `VERIFIED` at contract/source level |
| OMEGA health and ULTRON link | Overview reads `omega.status` and `omega.integration.linkState` | Gateway's configured OMEGA integration client; absent credentials/configuration or failed poll degrades/withholds integration result | Potentially external service-reported state; the current operator-reported `UNKNOWN` / `NOT_CONFIGURED` is historical, not re-observed here | Unknown/authentication-required states remain distinct; no OMEGA contact was made | Current state `UNVERIFIED`; no internal OMEGA investigation performed |
| System diagnostics and helper action | Diagnostics view renders diagnostic records/action; restart button is enabled only with authenticated provider, `AVAILABLE` inventory and available action | Collector creates service mismatch diagnoses; gateway handler checks principal. Restart request carries a fixed operation (`restart-auth`) and validated operation ID through the manager control socket | Diagnostic records include collector `observedAt`; operational events are read from manager state. Collector persists bounded diagnostic records in its own protected SQLite store when collecting | Errors return explicit failed/unavailable state; restart result is followed by a new inventory request. No action was executed | Authenticated/fixed-action code path traced; action outcome and current ownership `UNVERIFIED` |
| Manager handoff | UI reads `data.managerHandoff`; runbook documents handoff fields | No `managerHandoff` field/function is present in `SystemIntelligence.collect()` source | Expected but absent contract; cannot report ownership transfer eligibility from this snapshot | UI says handoff status unknown when absent; runtime test module cannot collect because it imports missing `_manager_handoff_status` | Confirmed source/test contract mismatch: `UNAVAILABLE` |

## Findings and exact causes

### SI-1 — Overall inventory can be green while service ownership is not

**Status: `VERIFIED` (source defect).** In [system_intelligence.py, lines 415–425](../ultron/application/runtime/system_intelligence.py#L415), the collector calculates top-level `status` from `observations` only. It separately gathers `services`, including `processStatus` and `readiness` ([lines 227–278](../ultron/application/runtime/system_intelligence.py#L227)), and stores diagnostic records, but those service facts do not participate in the top-level result. When ordinary host observations succeed and service records are stale/missing, the returned inventory can still be `VERIFIED`. The UI displays this value as the System Intelligence summary state ([operator-console.js, lines 184–200](../interface/operator-console.js#L184)). This is the precise false-green root cause; no live instance was needed to establish the source logic.

### SI-2 — Manager handoff contract is absent and collector tests cannot import

**Status: `VERIFIED` (source/test integration defect).** [test_system_intelligence.py, lines 5–8 and 89–198](../tests/runtime/test_system_intelligence.py#L5) imports `_manager_handoff_status` and later requires a `managerHandoff` member. The function is not defined in [system_intelligence.py](../ultron/application/runtime/system_intelligence.py), and `collect()` does not return the member ([lines 415–455](../ultron/application/runtime/system_intelligence.py#L415)). [operator-console.js, lines 292–300](../interface/operator-console.js#L292) checks for it but falls back to “Manager handoff status is unknown.” [RUNTIME_OPERATIONS.md, lines 257–260](./RUNTIME_OPERATIONS.md#L257) documents the expected handoff evidence fields. Running the focused collector test command failed during collection:

```text
ImportError: cannot import name '_manager_handoff_status'
from 'ultron.application.runtime.system_intelligence'
```

This is not a test assertion failure: none of the tests in that module ran. The gateway tests are separately runnable and passed.

### SI-3 — Inventory freshness is connection/request-state based, not age based

**Status: `VERIFIED` (source limitation).** [websocketProvider.js, lines 463–480](../interface/services/providers/websocketProvider.js#L463) marks the stored inventory `STALE` while a refresh is pending or fails; socket close marks it stale and reconnects at lines 251–270. It requests a new inventory after an authenticated subscribe acknowledgment at lines 228–234 and offers manual refresh. However, a successful snapshot stays `AVAILABLE` for as long as the authenticated socket remains connected, even if its `generatedAt` is old. [operator-console.js, lines 184–200](../interface/operator-console.js#L184) prints the generation time but does not calculate an age threshold. Backend `observedAt` and browser `channelTimes` are different clocks/semantics; do not treat browser receipt time as source observation time.

### SI-4 — “Connected” and heartbeat are narrower than application health

**Status: `VERIFIED` (source semantics).** The UI's `CONNECTED` state follows provider provenance after subscription acknowledgment ([websocketProvider.js, lines 228–230](../interface/services/providers/websocketProvider.js#L228); [operator-console.js, lines 1613–1660](../interface/operator-console.js#L1613)). Heartbeat reports runtime `running`, cycle count, and gateway uptime ([interface_gateway.py, lines 356–413](../ultron/application/runtime/interface_gateway.py#L356)). Neither is a health/readiness check for every service, and `running=False` can coexist with a responsive gateway heartbeat. The code displays runtime state separately, which is appropriate; the connection indicator must not be used as an overall health claim.

### SI-5 — Live System Intelligence and operation status remain unverified

**Status: `UNVERIFIED`.** At 2026-10-27 21:53:46 +05:30, a passive `ps` check of the five historical PIDs returned no rows. A passive `ss -ltn` check of ports 8000, 8080, 8081, and 8765 returned only its header and no matching listener. These results do not prove that no replacement or unrelated service exists. No current manager/service identity, authenticated browser session, or inventory response was obtained.

## System Intelligence controls and permission boundary

- **Read-only inventory:** `SYSTEM_INTELLIGENCE_REQUEST` accepts an empty payload and requires an identity from the configured `WebSocketAuthenticator` whose principal matches the configured operator principal. Collection failures return a failure response rather than fabricated data.
- **Operational action:** the dashboard exposes only the fixed helper-only restart action. The provider refuses to submit it without authentication. The gateway validates correlation and operation ID, verifies the same configured principal, then requests `restart-auth` through the manager's Unix control socket. The collector advertises it only when the manager socket/lease checks and recorded helper identity are acceptable.
- **Arbitrary commands:** the System Intelligence restart action does not accept an LLM command or shell string. It sends a fixed operation name and validated identifier to the manager.
- **Identity/audit/result:** collector checks recorded PID plus start ticks, executable, cwd, process group and command marker before exposing verified service identity. The manager is the authority for the restart operation and its control-operation records; the dashboard refreshes inventory after a successful response and surfaces failures.
- **Recovery limitation:** no live manager ownership was established in this mission. Do not use the action. The code does not establish a general rollback/restore path for service restart; recovery remains subject to manager ownership and the separate release/recovery gates.
- **Persistence caveat:** inventory collection is read-only with respect to live service state and the live operations database, but `SystemIntelligenceStore.record_diagnostics()` persists bounded diagnostic records to a separate protected `system-intelligence.db`. No live database was opened by this mission; this behavior is identified from source, not exercised against live state.

## Runtime observation record

| Observation | Result | Timestamp semantics / limitation |
|---|---|---|
| Historical PIDs 6943, 6970, 7065, 7071, 7075 | No rows returned by `ps -p ... -o pid,ppid,lstart,comm` | Passive process-table snapshot at 2026-10-27 21:53:46 +05:30. Historical PIDs are absent; replacement identity is unknown. |
| Previously reported ports 8000, 8080, 8081, 8765 | No matching listening sockets in `ss -ltn` | Passive listener snapshot at the same time. A listener absence is not a service health diagnosis. |
| Dashboard browser/session | Not observed | No browser page was shared; no authentication prompt, credentials, cookies, or private browser state were accessed. |
| Current backend snapshot / heartbeat | Not observed | No listener was present on prior ports, so no current authenticated or unauthenticated WebSocket request was attempted. |
| Historical operator snapshot | Previously reported backend-connected state, runtime IDLE, active operations 0, four registered agents, OMEGA UNKNOWN/NOT_CONFIGURED and gateway heartbeat `running=False`, `cycle_count=0` | Historical operator-provided evidence only. Not revalidated and not represented as current fact. |

## Tests and verification

### Executed

| Command | Result | What it proves |
|---|---|---|
| `.venv/bin/python -m pytest -q tests/runtime/test_system_intelligence.py tests/interface/test_interface_gateway_system_intelligence.py` | **Failed at collection**, exit code 2: missing `_manager_handoff_status` import | Proves the collector test module is currently not collectable. No tests in that combined run executed. |
| `.venv/bin/python -m pytest -q tests/interface/test_interface_gateway_system_intelligence.py` | **2 passed** | Fake-WebSocket tests prove operator authentication gates inventory, an authorized test request returns collector evidence, malformed restart requests are rejected, and an unavailable manager failure does not leak credential text. These are isolated tests, not live service verification. |
| `node --test interface/tests/runtimeEvents.test.mjs interface/tests/layout.test.mjs` | **20 passed, 0 failed, 0 skipped** | Frontend tests prove event filtering/time behavior and structural/UI assertions, including System Intelligence controls and stale-state render branches. They do not prove live backend delivery, fresh inventory, browser decoding, or production health. |
| `runTests` tool on the two Python file paths | **No tests found** | Tool did not discover Python tests; it is not counted as a test pass. The explicit pytest command above was then run. |

### Inspected but not executed

- `tests/runtime/test_system_intelligence.py` intends to cover measured host sources, unavailable GPU status, stale PID identity/diagnostics, command-argument redaction, manager handoff ownership ambiguity, and non-mutation of manager records. Because import collection fails, those intended assertions are not current executable evidence.
- The authentication helper, runtime gateway, manager, broader application, and complete frontend suites were not run. No full-suite or production-readiness claim is made.

## Prioritized integration fix (proposed; not implemented)

**Priority 1 — Complete the truthful System Intelligence inventory contract.** Make the inventory-level status derive from required service process identity and readiness plus manager-ownership/handoff evidence, rather than host-observation success alone. Return the documented `managerHandoff` result consistently with the UI and tests, or explicitly revise all consumers and documentation to a single supported contract. Add deterministic tests for stale/missing service identities and ambiguous/missing manager ownership to ensure those states can never produce overall `VERIFIED`. Keep listener presence distinct from readiness and never upgrade a service to healthy based only on a port.

### Acceptance criteria and proposed regression tests

1. A fixture with valid host observations but a stale or missing required service process returns a non-`VERIFIED` top-level status and a diagnostic tied to that service.
2. An ambiguous manager set or parent mismatch returns `OWNERSHIP_UNCERTAIN`; absent control ownership returns `HANDOFF_BLOCKED`; no state files are rewritten by collection.
3. Every inventory contains a typed `managerHandoff` object (or every UI/test/documentation consumer is updated together to an explicit replacement contract).
4. The collector test module imports and all targeted collector tests execute.
5. Authenticated gateway tests still reject unauthenticated requests; no operational restart is invoked in these tests.
6. Frontend renders `UNKNOWN`, `STALE`, `UNAVAILABLE`, or the returned degraded state correctly and does not infer health from transport or listener presence.
7. System Intelligence source observation time remains distinct from browser receipt time; a freshness threshold or explicit refresh age policy is tested before a snapshot remains `AVAILABLE` indefinitely.

**Safe implementation prerequisites:** fix the test/source contract mismatch first; review tests in temporary state only; inspect the exact changed diff; preserve operator authentication and manager ownership gating; run the focused Python and frontend tests; then run diagnostics. Do not restart services. Any live check must wait for the established ownership, release, rollback, backup/restore, and operator authorization gates.

## Safety limitations and unresolved questions

- The current source revision loaded by a live gateway is not established.
- Current process ownership is not established; the old PID observations do not identify a replacement manager.
- No authenticated live browser snapshot, current gateway heartbeat, or current System Intelligence result was available.
- The collector's readiness value is intentionally `UNKNOWN` when a listener exists; no supported application-level readiness probe is established by this investigation.
- System Intelligence has no dedicated frontend schema validator; the provider checks only that a response payload exists and `observations` is an array before setting it `AVAILABLE`.
- No safe live operational action, manager handoff, rollback, or service recovery was performed or verified.
- Deployment remains **NO-GO** until independent runtime ownership, exact release, rollback artifact, backup/restoration, and operator authorization gates are satisfied.

## Source and test references

- [System Intelligence collector](../ultron/application/runtime/system_intelligence.py) — `/proc` readers, identity comparisons, collection, status aggregation, protected diagnostic persistence, manager socket checks, and fixed helper request.
- [Interface gateway](../ultron/application/runtime/interface_gateway.py) — heartbeat, world refresh, authenticated inventory/restart handlers, runtime state, world snapshot, and active-operation projection.
- [System observer](../ultron/world/observers/system.py) and [world state](../ultron/world/state.py) — CPU/memory sampling and source observation timestamp.
- [Operator console](../interface/operator-console.js) — overview, System Intelligence cards, diagnostics, connection state and action enablement.
- [WebSocket provider](../interface/services/providers/websocketProvider.js) — local endpoint, authentication/subscription, inventory refresh, stale state and reconnect.
- [Frontend contracts](../interface/contracts/contracts.js) — cognitive, world, operation and runtime-event validators; no inventory validator is present.
- [Collector tests](../tests/runtime/test_system_intelligence.py) and [gateway tests](../tests/interface/test_interface_gateway_system_intelligence.py).
- [Runtime operations guide](./RUNTIME_OPERATIONS.md) — documented manager handoff fields and recovery constraints.

**Final disposition:** System Intelligence is `PARTIALLY_VERIFIED` at source/contract level. Current live dashboard/backend linkage is `UNVERIFIED`; no code fix was made; deployment remains **NO-GO**.
