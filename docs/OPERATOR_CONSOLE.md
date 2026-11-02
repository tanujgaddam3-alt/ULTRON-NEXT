# ULTRON Operator Console

`interface/index.html` is the browser entry point. The console uses the
existing WebSocket provider, bridge, contract validators, and voice client.
It adds read-only agent and skill inventories, runtime event inspection,
operation history, trading telemetry views, and diagnostics without
introducing another frontend framework or order-submission API.

## Reachable capability inventory

| Capability | Runtime source and contract | Authentication / actions | Console behavior and limits |
| --- | --- | --- | --- |
| Backend transport | Default local helper `ws://127.0.0.1:8081/ultron/stream`, upstream gateway `ws://127.0.0.1:8080/ultron/stream`; `connected`, `AUTHENTICATE`, `SUBSCRIBE_ACK`, `PING` | The helper verifies the local unlock passphrase, then authenticates upstream with the protected random token. `?auth=token` selects the existing direct-token flow. | The helper never sends the gateway token to browser JavaScript. Both modes remain loopback-only; the dashboard writes neither credential to web storage. |
| Runtime overview | `cognitiveState`, `command`, `world`, `runtimeEvents` | Snapshot subscription and pushed updates | Missing fields remain unknown. Browser receipt times are not described as backend event times. |
| Agents | `cognitiveState.agents`, entries contain `name` and generic `role` | Read-only subscription | Registration is listed. The gateway publishes no per-agent health, capabilities, or activity, so the console labels health as unreported and adds no enable/execute controls. |
| Host and skills | `device.hardware`, `device.assessment`, `device.capabilities[]` with skill `name`, `version`, and `actions` | Read-only subscription | The Tools & Skills view is a catalog only. Skill registration does not imply readiness; listed actions are not invoked. The registry does not publish per-action permissions, parameter schemas, or execution history. Host metrics that cannot be read are null, and the device assessment is `NOT_ASSESSED`; no health threshold is claimed. Host process-level telemetry is not exposed. |
| Tasks and workflows | Runtime constructs task/workflow services, but gateway has no task list, lifecycle, cancel, or retry message | Not operator-reachable | No Tasks & Workflows page or task controls are added. Diagnostics state this limitation. |
| AI provider status | Chat can call the configured provider, but the gateway publishes no provider/model identity, health/readiness, or loaded-model state | Chat requires a server-authenticated session | No model-health or provider identity claim is presented. Diagnostics reports status as not exposed. |
| Chat runtime context | Authenticated requests about system/runtime statistics receive the existing gateway runtime state and most recent host snapshot with its source timestamp when reported | Added only after server-side session authentication; no financial or OMEGA telemetry is passed to chat | Missing fields are omitted and identified as unreported. Chat does not expose repository inspection or tool execution and must not claim either occurred. |
| Tools and skills execution | `device.capabilities` is inspectable; gateway dispatch supports a limited set of mapped verbs and approval-gated operations | Consequential requests require authenticated server approval; no generic “invoke any skill” API | The catalog has no invoke buttons. Existing Operations approval dialog remains gated and shows only server-proposed `SUGGESTED` operations. No trading submission is exposed. |
| Runtime events | `runtimeEvents[]`; records include `eventId`, `type`, `summary`, provenance, and optional source/severity/timestamp metadata. Gateway retains a bounded in-memory event buffer. | Read-only snapshot and pushed updates | Activity supports search, severity/source/type filters, time ranges when a valid backend timestamp is present, and per-event inspection. `timestamp` is the source event time or gateway fallback; `at` is gateway normalization time. Browser receipt time is displayed separately. This is not persistent log search. |
| Operation lifecycle history | `operation[]`; empty arrays are valid snapshots and are also sent to unauthenticated clients to withhold records | Authenticated view required for the stored operation list; approvals/reconciliation use existing authenticated actions | Active operations and persisted history are displayed separately. An unauthenticated empty list is described as withheld, not as proof no operations exist. Every lifecycle record is validated before display. |
| Trading telemetry | `trading.financial`: initialization, validated market snapshot, execution mode/source, broker account snapshot, configured risk-component names, and broker trade ledger | Requested only after WebSocket authentication; gateway withholds snapshots and filters broadcasts for unauthenticated clients | Read-only Trading Overview, Market Data, Portfolio & Positions, and Trading History. A failed or incomplete account snapshot is reported with an error and null values, not as an empty account. Unknown P&L, risk ratios, losses, and benchmark comparisons remain null. No order submission is exposed. Decision details, individual risk results, order statuses, and strategy configuration are not published. Validated bid/ask and finite non-negative volume are included only when the provider snapshot supplies them. |
| OMEGA system state | Authenticated `omega` snapshot from the ULTRON gateway, backed by OMEGA's read-only `/state` and `/capabilities` responses: OMEGA CLI version/status/health, environment profile, model inventory, evidence-ledger sample, capability registry, facts, and database anchor | WebSocket operator authentication plus the gateway's server-side `OMEGA_INTEGRATION_TOKEN`; gateway polls only the loopback token-gated service | Separate OMEGA Overview, Intelligence & Learning, Improvement Evidence, and Diagnostics pages. They render reported fields only. Capability data may be last-known while the link is degraded. A model listing is not a loaded-model claim; the database anchor is displayed without its path. Proposal lifecycle, learning activity, and database contents are not exposed. |
| OMEGA ↔ ULTRON integration | The gateway's `omega` payload includes link/authentication state, token-configuration presence, last attempt/success times, measured bridge latency when available, failure details, and a bounded HTTP exchange trace with direction, endpoint, timestamp, latency, HTTP status, and response validation | Authenticated WebSocket snapshot only; the browser does not probe the service or receive the integration token | Integration Link and Integration Exchanges distinguish the current connection from OMEGA's own reported health. The HTTP trace is not a durable audit log or synchronization contract. OMEGA's own `communicationTrace` is shown separately and is not presented as gateway HTTP evidence; no gateway-generated events are inserted into it. Unmeasured latency and an unreported OMEGA trace remain unavailable, not zero or empty. No synchronization or bidirectional connectivity is inferred. |
| Voice service | Separate loopback HTTP API: `GET /api/v1/health`, `/engines`, `/styles`; `POST /api/v1/speak` | Voice API is independent of the gateway and is not started by `main.py` | Overview and Voice report health checked directly from the API; the Voice view also reports returned catalogs. “Generate and play” uses the existing normal-style voice client and playback object. Health/catalog availability does not prove audio playback. |
| Diagnostics | WebSocket subscription state, chat authentication state, runtime snapshot, device snapshot, voice health, and browser-side bridge validation events | Read-only; voice checks issue GETs only | Connected, authenticated, ready, unavailable, unknown, and unsupported states remain distinct. Host snapshot receipt is not process health. Contract violations are labeled as browser validation, not backend log records. |
| Configuration | No gateway configuration read/write action | Unsupported | No editable settings or credential fields are exposed. |

The dashboard's default password flow uses the local helper on port 8081. The
helper accepts connections only from `http://127.0.0.1:8000`, applies a
persistent lockout across reconnects, and does not open the upstream gateway
socket until the local passphrase is verified. It then proxies the gateway's
authenticated session, preserving the gateway's session expiry and channel
authorization. See [`RUNTIME_OPERATIONS.md`](./RUNTIME_OPERATIONS.md) for
passphrase setup, recovery, direct-token fallback, and credential rotation.

The provider subscribes to `cognitiveState`, `vitalMatrix`, `device`,
`command`, `operation`, `runtimeEvents`, and `world`; it adds both `trading`
and `omega` only after the WebSocket session authenticates. The gateway also
enforces those access rules for initial snapshots and broadcasts. Unauthenticated
responses contain only an authentication-required status, never financial or
OMEGA system data. The gateway can name additional snapshot domains, but
unsupported subscription names receive only a generic availability response;
those are not presented as real capabilities. The pipeline channel remains
outside the current console.

The OMEGA service's `/state` endpoint is token-gated and aggregates real
read-only CLI results. The gateway polls `/capabilities` after a successful
state read, on its own 60-second cadence, and exposes the registry separately
from OMEGA's CLI-reported communication trace. If the OMEGA token is not
configured, the gateway does not make an unauthenticated request; it reports
`NOT_CONFIGURED`. A failed state refresh after a prior successful read reports
`DEGRADED` and preserves the previous state and capability snapshot only as
last-known data. OMEGA self-health and ULTRON-to-OMEGA link state are displayed
separately, including on the system overview.

## Trading boundary

`InterfaceGateway._build_trading()` provides a financial telemetry snapshot
from the configured broker, validated market-data service, and controller.
The console shows only fields actually present in that snapshot. The current
gateway command dispatcher has no mapping to the registered financial skill's
`paper_trade` action; therefore the console does not propose, approve, or
submit orders. The data also does not include decision/analysis records,
individual risk-check outcomes, authoritative order status, or strategy
configuration. Bid, ask, and volume are shown only when returned and validated
by the current provider snapshot. Cached quotes are marked stale when their
source timestamp exceeds the adapter's freshness limit; browser receipt time
is not used to make stale quotes look current. The History page is a broker trade-ledger
snapshot, not durable complete order history or proof of order lifecycle.

The backend's `liveEnabled` telemetry is derived from the selected broker's
live-order capability and its existing submission gate; the console never
changes that setting. A reported live execution mode is displayed as reported
and does not enable any UI execution control. Current market snapshots include
provider validation time, age, and the adapter freshness limit; browser
receipt time is displayed separately and is not presented as market freshness.

## Safety and data semantics

- Chat is enabled only when both the WebSocket subscription is live and the
  server has authenticated the operator.
- A connected transport is not a successful chat authentication.
- Agent or skill registration is not readiness or health.
- A configured model is not reported as loaded or available without an
  authoritative status contract.
- Host/device snapshot receipt is not reported as host or process health.
- Vital-matrix health is `NOT ASSESSED` without an authoritative health check;
  anomaly counts are `NOT INSTRUMENTED` without a detector. Missing registry
  and performance measurements are unavailable rather than zero.
- Missing or failed device, account, and OMEGA measurements remain null, with
  source failures reported separately where available.
- Skill registration is not a permission grant or a record of successful
  execution; action permissions, argument schemas, and execution history are
  not published by the catalog.
- Financial account and broker-ledger telemetry is withheld from
  unauthenticated WebSocket sessions, including broadcast updates.
- A validated market snapshot is not a trading decision or an order.
- Configured risk-component names are not presented as successful risk checks.
- A trade-ledger entry is not presented as an authoritative current order
  status or verified order outcome.
- The interface has no trading execution or live-order control.
- A successful voice health response does not mean playback has been heard.
- The browser does not synthesize events, progress, task state, provider state,
  configuration values, or operation outcomes.
- Approval remains explicit, server-authorized, and limited to a supplied
  operation record. State changes appear only after a backend update.
- The mock provider is used by isolated unit tests only; it is not connected
  by the production page.

## Local run and verification boundary

Serve the project using the existing local static-file server and open
`interface/`. Keep the interface gateway and voice API bound to loopback.
The gateway and voice service are separate processes; see
[`RUNTIME_OPERATIONS.md`](./RUNTIME_OPERATIONS.md) for existing runtime
instructions. Do not take over ports already owned by another process.

An unauthenticated live telemetry connection verifies only the subscription
and displayed snapshots. It does not verify chat, operation approval, or
speech playback. A complete chat-to-audio check requires the operator
credential and a running voice API; audio playback requires a successful
generation and browser playback event.
