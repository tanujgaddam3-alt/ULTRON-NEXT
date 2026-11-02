# Operator-Assisted Authenticated Integration Verification

This runbook verifies real read-only data flow after an operator completes the
normal local-unlock workflow. It does not authorize service restart, trading,
publishing, credential changes, or OMEGA modification.

## Current status

- **Operator authentication:** NOT VERIFIED. The shared dashboard previously
  displayed the normal unlock dialog and "Chat authentication unavailable";
  the operator was unavailable to unlock privately.
- **Authenticated gateway, CRE, scene gallery, and owner isolation:** NOT
  VERIFIED.
- **Current source tests:** isolated service-manager, authentication-helper,
  gateway CRE/System Intelligence, release/backup, and frontend unit tests
  passed during this mission; these do not substitute for authenticated
  live-browser evidence.
- **No credential was requested, entered, logged, copied, or inspected.**

## Safety and evidence collection

1. Use only the already approved local dashboard origin and its normal
   **Unlock ULTRON** UI. The operator enters the passphrase directly in the
   browser; never put it in chat, shell history, a form field outside that UI,
   browser storage, logs, screenshots, or this runbook.
2. Do not bypass helper authentication, add an origin, manually edit tokens,
   or use a direct unauthenticated gateway connection.
3. Record only non-secret outcomes: timestamp, connection/status labels,
   backend observation times, artifact IDs/hashes, stale/error text, and test
   fixture identifiers. Do not record session tokens or raw credential-bearing
   frames.
4. If the helper/gateway is unavailable, state is stale, owner identity is
   ambiguous, or the browser requests an unsupported operation, stop and
   report the exact visible blocker. Do not restart services as part of this
   verification.

## Procedure and expected evidence

| Step | Operator procedure | Evidence to record |
|---|---|---|
| 1. Normal authentication | Unlock only through the dashboard's private local-unlock dialog. Confirm the helper is the browser's loopback endpoint and the origin is the approved dashboard origin. | Whether unlock completed; visible non-secret error if not. Never record the passphrase. |
| 2. Gateway connection | Wait for the authenticated WebSocket provider to report connected. Confirm a fresh authenticated status request/response completes. | Provider state, event/status name, observation time, and whether reconnect was needed. No raw auth frames. |
| 3. Frontend status | Inspect the connection indicator and stale/disconnected banner before and after the fresh response. Confirm cached content remains visibly stale until replaced by current data. | UI labels and timestamps before/after; no assumption that a visible cached value is fresh. |
| 4. Backend observations | Open System Intelligence and request its supported inventory refresh. Compare each displayed source, status, and `observedAt` with the returned snapshot. | Snapshot time, manager/service status labels, source attribution, and errors. This is read-only and does not request handoff. |
| 5. OMEGA status | Observe only the OMEGA status field delivered through the authenticated ULTRON interface, if present. Do not open or modify the separate OMEGA workspace/runtime. | Whether the field is present, timestamp/source, and whether it is stale/unknown. Do not infer OMEGA health from ULTRON process state. |
| 6. Trading provenance/freshness | Inspect dashboard data source and its provider/as-of time. Confirm simulation/paper restrictions are visible. Do not submit orders or invoke trade actions. | Provenance, data timestamp, stale/error/simulation labels, and any visible blocker. |
| 7. CRE mission state | Open CRE status and record mission/state/blockers from the authenticated response. Confirm the page is not presenting a cached response as current. | Status response time, mission state, blocker/error fields, and stale indicator. |
| 8. Persisted scene artifacts | Open the scene gallery. For each actual persisted image, compare artifact identity, dimensions, and SHA-256 with the current backend evidence response. In browser developer tools, verify each image has `naturalWidth > 0` and `naturalHeight > 0`. | Artifact IDs, backend hashes, displayed hashes, dimensions, load/decode result, and evidence status. Do not substitute placeholder/mock imagery. |
| 9. Errors and stale state | Use an existing supported test fixture or naturally occurring unavailable status; do not corrupt live service state. Confirm failed/old observations show error/stale/unknown rather than healthy/zero defaults. | Exact state and user-visible text. If no fixture is available, mark this case NOT VERIFIED. |
| 10. Owner isolation | Use only an authorized second principal or a supported isolated multi-owner test fixture. Verify owner A cannot read owner B's CRE evidence and that server-side authorization rejects cross-owner access. | Fixture/principal labels only, authorization result, and relevant automated test evidence. Do not claim live owner-isolation verification without a second authorized principal. |
| 11. Browser console | Review console after the above steps for connection, decode, stale-state, and contract errors. Exclude any secret-bearing data from notes. | Error count, sanitized error summaries, and whether any image/network request failed. |
| 12. Reconnect and expiry | Without stopping services, use the normal browser reconnect path (for example, refresh/reopen the dashboard). Observe whether the helper reconnects and whether cached data becomes stale. Allow normal session expiry or use a supported test fixture; do not alter auth configuration or force logout through an unsupported path. | Reconnect outcome, whether re-unlock was required, stale-to-fresh transition, and sanitized expiry message. |

## Completion record

Fill out a separate operator verification record with the run timestamp and
steps completed. Mark each step **PASS**, **FAIL**, or **NOT VERIFIED**; include
the supporting observation. A test-fixture result must be labeled as a fixture,
not live evidence. A dashboard response alone does not establish live service
source revision. A second principal is required for an owner-isolation claim.

Current unresolved operator actions: complete private local unlock, perform
authenticated read-only status/CRE/gallery checks, and use an authorized second
principal or supported fixture for owner isolation. Until then, end-to-end
integration remains NOT VERIFIED.
