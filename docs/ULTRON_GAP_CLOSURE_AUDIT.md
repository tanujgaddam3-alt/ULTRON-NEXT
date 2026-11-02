# ULTRON Gap-Closure Audit

## Audit scope and evidence rules

This is a source-and-test baseline for the gap-closure mission, not a claim
that every capability is production-ready. The primary task, executor,
gateway, authentication, memory, knowledge, voice, financial bootstrap,
self-improvement, launcher, and runtime-documentation paths were inspected.
The current safe test baseline covers execution, task recovery, gateway
authorization/operation lifecycle, and the terminal skill.

No production services were started or contacted. The gateway test suite
starts and cleans up one isolated loopback WebSocket fixture on an ephemeral
test port. No authenticated browser session was attempted. No OMEGA
project/runtime/data or optional OMEGA integration path was accessed. The
tagged `.env.financial`, production voice configuration, and
approved reference audio were not read or changed. No order was submitted.
Previous launcher/frontend results referenced below are historical evidence,
not live checks in this mission.

**Status definitions:** VERIFIED requires appropriate implementation and test
evidence for the stated behavior; IMPLEMENTED_UNVERIFIED means code exists but
live or adequate integration evidence is missing; PARTIAL means only part of
the ideal behavior exists; MISSING means the required capability is absent;
BLOCKED means verification or implementation cannot safely proceed under the
current constraints; NOT_APPLICABLE requires a justified reason.

## Capability inventory

| Capability / ideal behavior | Actual implementation and evidence | Exact gap, risk, dependency | Acceptance and verification | Status |
|---|---|---|---|---|
| **Operator console and launcher** — truthful, accessible, responsive operator workflow; safe one-command startup and shutdown | Existing console and `start-ultron.sh`/`stop-ultron.sh`; service manager verifies service-specific readiness and tracks owned processes. Historical evidence: frontend tests 114/114, launcher tests 16/16, and a prior cold-start/shutdown pass recorded in prior work. | This mission did not run the browser or services; current authenticated data flow, accessibility tree, and current process state are unverified. | Isolated launcher suite; desktop/tablet/mobile browser checks; authenticated acceptance against owned local services only. | IMPLEMENTED_UNVERIFIED |
| **Authentication and server authorization** — derive identity from trusted credentials, expire/revoke sessions, bind approvals to exact requests, redact secrets | `WebSocketAuthenticator` stores a token digest and binds short-lived sessions to sockets. Gateway approval grants bind operation ID, request fingerprint, purpose, principal, action/resource scope, and expiry. A signed immutable context now passes through executor and skill dispatch; the final persisted grant claim is one-use. Focused authentication and gateway tests cover binding and rejection. | This path is only the gateway `execute` → `run_command` mapping; it is not equivalent to every direct task/autonomy/skill caller. Current credential file modes and live helper/gateway state were not checked. Previously exposed token rotation remains an out-of-band operator task. | Tests bind context to exact parameters and reject signature/scope changes; final-claim failure blocks before skill invocation. Broader entry-point matrix, file-mode inspection, and protected out-of-band token rotation remain open. | PARTIAL |
| **Unified action governance** — every consequential action receives an enforceable policy decision at dispatch; denied work never dispatches | The gateway accepts only the concrete `WebSocketAuthenticator`; arbitrary callbacks are rejected. Its one-use grant binds principal/session, operation and request IDs, request fingerprint, purpose, expiry, mapped action, and exact dispatch-parameter fingerprint. The signed immutable context is checked against the single-step executor plan and revalidated/claimed in `SkillManager` immediately before skill invocation. The bounded `execute_task` path binds a complete canonical `pwd`/`whoami` plan to a parent grant and derives narrower signed, one-use child grants for each step. A rejected or replayed claim does not invoke the skill. Protected direct file/project/artifact, financial, system, and self-improvement boundaries remain fail-closed. | The added task path is limited to authenticated gateway dispatch, a same-database durable queue, and the explicitly allowlisted read-only plan. Autonomy, generic task submissions, agent/workflow, legacy-control, direct storage, and financial boundaries do not consume the shared context; protected operations remain blocked there. | Focused integration tests demonstrate a valid two-step task with persisted per-step checkpoints and independent verification; invalid scope is rejected before task creation; revocation before child-grant derivation blocks execution; interruption remains uncertain and is not retried. Direct/agent paths remain blocked. | PARTIAL |
| **Task and operation lifecycle** — durable IDs, idempotency, auditable transitions, verified outcomes, conservative crash recovery | SQLite operation records deduplicate correlation IDs, enforce an explicit transition table, atomically persist lifecycle events and grant claims, and recover interrupted operations to `UNCERTAIN`. Parent grant consumption and approval persistence remain one transaction. The bounded task path requires a shared SQLite file, persists the exact plan and per-step receipts/events, and independently verifies `pwd`/`whoami` output. Manager-level cancellation persists task intent and a parent-operation cancellation marker. | The external effect and its task checkpoint cannot share an atomic transaction. A crash at or after dispatch remains uncertain and is never automatically replayed; exactly-once external execution is not claimed. Trigger-based task tests prove rollback at create/event, claim, start, checkpoint, uncertain, cancellation, and removal transitions; child activation rollback and pre-dispatch cancellation recovery are also tested. Selected operation tests now inject approval, final-claim, recovery, execution-result audit, and reconciliation-finalization failures. This is not exhaustive coverage of the operation store or external-effect timing windows. Generic tasks and non-read-only plans remain blocked. | Extend operation-store failure injection across every transition and verifier/result-persistence window; add safe independently verifiable reconciliation for uncertain external effects. | PARTIAL |
| **Authenticated browser-to-backend workflow** — real authenticated chat, status, approval, error, recovery, voice, and simulation UX | Gateway has structured chat/operation statuses; frontend and auth-helper paths exist. Historical frontend unit/layout evidence is available. | No authenticated browser session was used in this mission. Credentials, configured model reachability, current services, and live data were not verified. User acceptance requiring live credential/model input is therefore blocked. | Run browser E2E only with operator-approved local services and credentials; assert real authenticated chat/error, status provenance, operation approval, reconnect, and session expiry. | BLOCKED |
| **Truthful telemetry and observability** — missing/stale/unknown/simulated/verified data stays distinguishable, with provenance and safe diagnostics | Gateway exposes runtime context and operation lifecycle payloads; service-specific health endpoints exist; operation/task execution writes structured audit metadata. | Full telemetry fields and presentation semantics were not checked across every frontend channel. Freshness/provenance is not consistently represented in knowledge and market-data results. Log retention and disk-exhaustion behavior remain unverified. | Contract tests for missing, stale, malformed, contradictory, simulated, and failed data; assert no missing values become healthy or zero; inspect redaction and disk-full behavior in isolated fixtures. | PARTIAL |
| **Memory, history, and privacy** — authorized export/deletion and retention across source stores and derived data | Conversation history persists user/assistant messages in SQLite and retrieves the latest ten by default. `MemoryManager` has working, episodic, semantic, and procedural interfaces; working/episodic/procedural stores are in-memory and its semantic path uses simple SQLite category/key/value rows. Separate semantic-memory models can hold source/confidence metadata. Experience records include situation/action/result/success/lesson/context/time; a JSON store appends them. | These pieces are separate, not a coherent persistent-memory contract. Provenance, confidence, applicability, correction, retention, deletion/export, access controls, backup semantics, and derived-store invalidation are inconsistent or absent. `ExperienceStore.load()` returns an empty list on read/JSON errors, which can mask corrupted records and is not authoritative recovery evidence. None of these data may grant permission. | Define record provenance/source identity, observation time, confidence and scope/applicability, correction/supersession, retention, user export/deletion, derived-store invalidation, and failure visibility before unifying stores. Test each against isolated stores; preserve existing infrastructure. | PARTIAL |
| **Model and chat reliability** — identify selected provider/model; bound requests; preserve history integrity and report distinct failures | The container wires `ProviderManager` to one local `OllamaProvider` (`qwen2.5-coder:7b` default, 120-second provider timeout). The gateway bounds input to 12,000 characters, applies a 125-second request timeout, and reports timeout/unavailable/invalid-response/failure statuses. The provider interface can be replaced. | There is no task classifier, privacy-aware local/external selection, external provider fallback, model capability evaluation, response-size bound, or explicit cross-model context contract. Live provider/model selection and failure behavior were not verified. Model output has no execution authority. | Add source-labeled model selection and failure tests; bound output; test context portability and privacy policy without treating model selection as permission. | PARTIAL |
| **Bounded autonomy and goals** — stable run state, budgets, pause/cancel, durable progress, safe resume | `AutonomyLoop.run_cycle()` performs one observe/rank/plan/policy/execute/reflect/update cycle; it supports planning-only when execution is absent. `AutonomyPolicy` labels selected safe/confirm/denied actions. | There is no observed durable run ID, iteration/time budget, pause/cancel, restart-safe resumption, or cycle deduplication. The policy checks `create_task`, not every step dispatched by the wired `ExecutionEngine`. | Add isolated cycle/state tests for planning-only, budget, denial, interruption, resume dedupe, no progress, and explicit terminal states after unified dispatch authorization exists. | PARTIAL |
| **Agent routing and orchestration** — specialized agents selected by capabilities, dependencies executed in order, truthful structured results, independent verification | Capability matching and workflow dependency ordering exist; several unimplemented specialists report blocked. `AgentOrchestrator` and `AgentWorkflowExecutor` now report blocked and do not dispatch because no trusted operation-grant integration exists. Arbitrary boolean callback injection was removed; the application does not treat a caller callback as authorization. | Agent orchestration is not wired into the chat-to-execution path; no gateway operation grant can authorize exact agent/tool/resource scope; no independent result verifier, durable workflow lifecycle, or real specialist execution evidence. | Test that candidate selection never dispatches absent an actual trusted grant integration, that dependency failures block dependents, and that any future integration binds exact task/action/resource and independently verifies outcomes. | PARTIAL |
| **Research and integration provenance** — expose source, retrieval/source timestamps, freshness, limitations, and errors | Knowledge ingestion performs extraction/fusion/validation/consolidation; retrieval combines direct repository search with graph expansion. | Retrieved items in the inspected code do not themselves establish source provenance, retrieval timestamp, or freshness. OMEGA integration remains untouched and unverified. | Local-fixture provenance round-trip and stale/missing source tests; no external calls. OMEGA stays blocked and is not part of this mission's live verification. | PARTIAL |
| **Voice reliability** — actual engine readiness, synthesis errors, and browser playback remain separate | Voice API health reports actual engine, readiness, and clone flag; `speak` returns WAV or a structured failure. Historical work verified Edge cold-start readiness and separately validated isolated XTTS. | This mission did not call the voice API or listen to audio; browser playback completion, interruption, malformed audio, and current production engine are not re-verified. | Safe unit tests for not-ready, synthesis failure, invalid/empty WAV, timeout/size bounds, and browser playback state; preserve Edge configuration/reference audio. | IMPLEMENTED_UNVERIFIED |
| **Financial simulation safety** — no live or paper order, accurate simulated labeling, fail-closed broker | The application container requires `ULTRON_EXECUTION_BACKEND=simulation`, constructs `PaperBroker`/`PaperTradingEngine`, and keeps market-data selection separate. Provider-backed submit/cancel methods reject dispatch unless the broker is exactly simulation mode; the launcher supplies simulation only when the gateway environment has no explicit backend selection. Regression coverage is in `tests/financial/trading/test_provider_execution_fail_closed.py` and the trading suites. | This mission did not run trading paths or inspect `.env.financial`; broader UI labels, market-data freshness, and authenticated browser presentation are not verified. No order was submitted. | Continue isolated backend-contract tests and simulated-vs-real presentation checks without invoking order methods. | IMPLEMENTED_UNVERIFIED |
| **Self-improvement and code modification** — proposal, authorization, workspace restriction, backup, patch validation, tests, rollback, evidence | Proposal generation and validation components exist. `PatchApplier` has no callback-based approval or file-write methods and rejects mutation until a trusted exact-patch grant integration is implemented. | No trusted exact-patch approval, isolated baseline/evaluation lifecycle, verified backup integrity, rollback/recovery, or post-change independent verification. Mutation is deliberately unavailable, including direct applier calls. | Tests prove direct patch application and manager-mediated application fail without mutating a temporary workspace; a boolean callback cannot be injected. Keep mutation disabled until approval binds exact patch digest, resource scope, principal, expiry, and one-use consumption; then prove backup, tests, rollback, and verification. | PARTIAL |
| **Launcher and service recovery** — safe process ownership, readiness, rollback, logs, graceful shutdown | Service manager and shell launchers exist; prior isolated tests cover process identity, conflicts, startup rollback, and readiness. Prior cold-start verified four services then stopped launcher-managed processes. | No live listener inspection or current startup was performed in this mission; those historical results do not prove current availability. Interrupted shutdown, log retention, and disk exhaustion require additional tests. | Re-run isolated service-manager suite; verify stale state and permissions in temp directories. Live use only after explicit service/port inspection and no disruption. | IMPLEMENTED_UNVERIFIED |

## Execution entry-point enforcement matrix

| Entry point | Current enforcement | Valid authorized execution demonstrated? | Remaining evidence/gap |
|---|---|---|---|
| Authenticated gateway operation | Exact `WebSocketAuthenticator` type required; short-lived one-use grant binds principal/session, operation ID/correlation IDs, request fingerprint, purpose, mapped action, dispatch-parameter fingerprint, and expiry. SQLite atomically consumes the grant with the matching `SUGGESTED` → `APPROVED` transition and prevents grant-ID replay. A signed immutable context is validated at the executor and skill boundaries, and the persisted grant is claimed before skill invocation. | Yes, isolated tests use a real `WebSocketAuthenticator` session and cover mapped `execute` for safe read-only `pwd`, including independent local verification. | The scoped grant is enforced only at this gateway mapping; it is not a reusable context for independent generic/direct callers. Only commands admitted by the independent terminal policy can execute. |
| Broad cognitive gateway verbs (`ask`, `plan`, `debug`) | Rejected before proposal persistence; legacy operations are rejected at approval and operation-dispatch boundaries. The generated-plan execution method was removed. | No; intentionally unavailable. | Requires exact generated-plan review, per-step action/resource authorization, and final-dispatch revalidation. |
| Generic `Executor` / `ExecutionPermission` / `SkillManager` | Executor validates a supplied immutable context against one exact plan step; SkillManager revalidates the signed session and performs one persisted final claim. Without a context, protected actions remain denied. | Gateway `execute` and its bounded `execute_task` child-step path are demonstrated; independent callers cannot issue a trusted grant. | Keep non-gateway task, agent, workflow, and generic executor entry points blocked absent an equivalent trusted grant. |
| Direct skills and file/project/artifact services/repositories | Sensitive operations deny before I/O or persistence; path containment and exclusive creation are defense-in-depth, not authorization. | No protected operation is enabled. | Exact scope verification is missing at final storage boundaries; preserve denial. |
| Financial controller and broker adapters | Application container requires explicit simulation mode and uses local `PaperBroker`; provider-backed submit/cancel is rejected. | No order path; no live or paper order was submitted. | No trusted grant contract binds controller/provider/resource scopes. Simulation restriction is not authorization. |
| Task queue and autonomy | Authenticated gateway `execute_task` supports a canonical `pwd`/`whoami` plan, derives exact per-step grants, uses the same durable SQLite file for task and operation data, persists checkpoints, and verifies successful results. Other task producers still use the legacy path; autonomy's policy is not a grant validator for generated steps. | Isolated integration tests demonstrate two-step execution, persisted progress, independent verification, session revocation rejection, interruption uncertainty, and no automatic retry. No authenticated browser or production-runtime task execution was performed. | Extend the authorization contract to generic tasks/autonomy only where the same parent grant, exact scope, durable lifecycle, and final dispatch claim are enforced. |
| Agent orchestrator/workflow | Candidate matching/dependencies are local logic; actual dispatch is blocked. Boolean callback constructor arguments are rejected. | No agent dispatch is enabled. | Requires exact task/action/resource grant binding and independently verified results. |
| Self-improvement | Patch applier has no approval callback or file-write methods and refuses mutation. | No patch mutation is enabled. | Trusted exact-patch approval, baseline evaluation, backup integrity, rollback, and independent post-change verification are absent. |
| CLI/automation and legacy control | Generic terminal restrictions apply where routed through the guarded executor; alternate legacy/automation paths are not proven equivalent. | No consequential authorized path established. | Inventory live callers; keep unverified paths blocked/disabled and test direct lower-level calls. |

## Baseline test evidence

Using the workspace-selected Python 3.14 interpreter:

- Before this mission's terminal hardening: execution, tasks, gateway
  authorization, gateway execution, and gateway operation suites: **49
  passed**.
- After the behavior change, the first regression run exposed three tests
  asserting the old `shell=True`/arbitrary-interpreter behavior (**3 failed,
  53 passed**). Their contracts were updated to test non-shell invocation,
  explicit confirmation, and pre-dispatch failure truthfully.
- Final focused run:
  `ultron/skills/terminal/tests/test_terminal_skill.py`,
  `tests/test_execution.py`, `tests/test_tasks.py`,
  `tests/interface/test_interface_gateway_authorization.py`,
  `tests/interface/test_interface_gateway_execution.py`,
  `tests/interface/test_interface_gateway_operations.py`, plus all tests in
  `tests/improvement/test_improvement_pipeline.py` except the container-
  instantiating pipeline smoke test: **77 passed, 1 deselected**.
- Current combined regression run for terminal/executor/task/gateway,
  capability routing, workflow execution, coding, agent evolution, and
  self-improvement components: **99 passed, 1 deselected**. The deselected
  smoke test constructs the application `Container`.
- At an intermediate stage, before arbitrary agent authorization callbacks
  were removed, the targeted terminal/executor/task/gateway, agent
  routing/workflow, coding, and non-container improvement suites passed:
  **103 passed, 1 deselected**. The deselected smoke test constructs the
  application `Container`; this result predates the final callback rejection.
- The P0 dispatch/patch-boundary suite passed **118 tests, 0 failed; 1
  Container-instantiating test deselected**. Its initial run had 4 test
  fixture registration failures, corrected without changing policy
  expectations.
- Earlier final run including the terminal skill suite passed **127 tests,
  0 failed; 1 Container-instantiating test deselected**.

These are unit/component and local SQLite tests, not authenticated E2E tests.

Prior atomic-approval regression (historical baseline; workspace Python 3.14.4):

- Gateway authorization and execution suites: **41 passed**.
- A concurrent-approval regression verifies that two simultaneous approvals
  dispatch the same operation only once.
- Updated broad governance regression: **289 passed, 0 failed, 1
  deliberately deselected**, with one non-blocking
  `websockets.legacy` deprecation warning.
- Failure injection at the approval write boundary confirms that an SQLite
  persistence error leaves the operation `SUGGESTED`, inserts no consumed
  grant, and never calls the executor.
- An initial broad command used an incorrect test-file path and exited before
  collecting tests; the corrected command completed successfully.

## Ranked residual gaps

1. **Critical — unified dispatch authorization.** Inventory all action
   registrations and callers; propagate the existing gateway's exact-scope
   grant as a trusted final-dispatch context to generic executor, direct
   storage, autonomy/task, financial, CLI/automation, and agent workflow
   paths. The gateway's `execute` command and one bounded `execute_task`
   mapping receive exact-scope grants; generic task, autonomy, agent,
   workflow, and direct boundaries remain unintegrated and protected.
2. **Critical — agent orchestration integration.** Route work only through real
   specialist implementations; prevent agent delegation from bypassing the
   trusted execution boundary.
3. **Critical — self-improvement governance and recovery.** Wire a trusted
   exact-patch approval verifier, verify backup integrity, and prove
   recoverable patch/rollback lifecycle before enabling mutation.
4. **High — history/memory privacy lifecycle.** Add authenticated export,
   deletion, retention, and derived-store semantics.
5. **High — durable cross-lifecycle fault matrix.** The gateway's grant claim
   and `APPROVED` transition are atomic, but persistence failures and crash
   windows across task, operation, reconciliation, and dispatch boundaries
   still need coverage.
6. **High — truthful provenance/freshness.** Extend knowledge and telemetry
   contracts to source/timestamp/freshness/error state.
7. **Medium — model response bound and authenticated chat verification.**
8. **Medium — autonomy run budgets and restart-safe cancellation/resumption.**
9. **Medium — current authenticated E2E, voice playback, and responsive
   accessibility verification; blocked until safe local prerequisites exist.**

## Current-slice outcome

The terminal command runner now auto-allows only bare `pwd`, `ls`, and
`whoami`, launches
argv with `shell=False`, and requires confirmation for argument-bearing or
otherwise unclassified commands, including Python interpreters, pytest, Git,
and package managers. `ExecutionPermission` blocks those commands before
skill dispatch and blocks custom working directories. Missing executables are
reported as not dispatched, and the gateway treats that evidence as a failure
rather than an uncertain side effect. Agent capability matching now requires
all requested capabilities; unimplemented specialists report blocked instead
of success, and workflow dependencies are validated. Agent dispatch remains
blocked until trusted operation grants can authorize exact agent work;
arbitrary boolean callbacks are rejected. The self-improvement applier now
refuses all mutation and exposes no file-writing methods until a trusted
exact-patch grant is available. Gateway `ask`, `plan`, and `debug` requests
are rejected because their operation approval did not bind model-generated
plan steps. Known protected actions remain denied at generic dispatch and
direct file/project/artifact boundaries. The gateway's narrow mapped `execute` operation has isolated authenticated
tests for safe `pwd` and binds/revalidates the exact mapped action and
dispatch-parameter fingerprint. A separate bounded `execute_task` entry
binds an explicitly approved `pwd`/`whoami` plan to one parent grant and
derives exact child grants for each step. The durable task record stores the
plan and step checkpoints in the same SQLite database as the operation
store; results are independently verified before task completion. Generic
task submissions, autonomy, agent, and workflow execution do not receive
this authorization. Financial
operations remain simulation-only and provider-backed order calls are
rejected. This is a defensive, partially verified execution foundation, not
a unified authorization system or production-readiness finding.

Prior integrated follow-up regression command, run with
`ULTRON_EXECUTION_BACKEND=simulation` for this test process only, covered
terminal, direct storage, executor/task, permissions, financial fail-closed
adapters, gateway operation/auth/financial paths, agents/workflows,
self-improvement, authentication helper/chat, and isolated launcher behavior:
**287 passed, 0 failed, 1 deselected**, with one non-blocking
`websockets.legacy` deprecation warning. The deselected test is
`tests/improvement/test_improvement_pipeline.py::test_improvement_pipeline`,
which instantiates the application container. The same command without the
test-only simulation setting failed during collection because the container
correctly rejects an unspecified execution backend. No live service or
browser verification was performed.

The subsequent gateway persistence follow-up atomically commits the one-use
grant record and operation `APPROVED` state. Focused gateway authorization and
execution tests passed **41/41**; the updated broad run passed **289 tests,
0 failed, 1 deselected**. The transaction does not include the external
executor, task queue, or reconciliation flow, and gives no exactly-once
guarantee for external effects.

## Signed dispatch-context follow-up

`AuthorizationContext` is a frozen transport value reconstructed from the
trusted signed grant. The executor rejects mismatched operation/correlation,
multi-step plans, action, or exact parameters before dispatch. `SkillManager`
rechecks the signer/session and atomically claims the persisted one-use grant
before invoking `run_command`. A rejected final claim is distinguished from
an exception after a side effect may have started: it is reported `BLOCKED`
with `NOT_DISPATCHED`, is not retried, and the terminal skill is not called.
The integration includes gateway `execute` → `run_command` and the narrowly
bounded gateway `execute_task` path. Generic task, autonomy, agent/workflow,
direct storage, financial, and self-improvement paths remain blocked or
unintegrated.

Focused result: **51 passed** across gateway authorization, execution, and
authentication suites. This includes executor-level exact-resource mismatch
rejection before any skill invocation. Broad simulation-only result: **285
passed, 1
deselected**, with one non-blocking `websockets.legacy` deprecation warning.
The deselected case is the application-container-instantiating
`tests/improvement/test_improvement_pipeline.py::test_improvement_pipeline`.
An initial broad command exited before collection because it named a
nonexistent test file; the corrected command passed. Five changed Python
files passed Pylance syntax validation. No live services, browser, financial
orders, OMEGA, production voice configuration, or credentials were accessed.
Production readiness remains **NOT PRODUCTION READY**.

## Authenticated task-execution follow-up

The bounded authenticated `execute_task` path now records each dispatched
child operation's execution result and marks later, undispatched steps
cancelled when an earlier step does not complete. The parent task remains
unverified until independent verifier evidence is persisted. This does not
expand task authority beyond the approved bare `pwd`/`whoami` plan; generic
tasks and agent/workflow dispatch remain blocked.

Focused task/gateway/execution regressions passed **79/79**. The broad
simulation-only regression command passed **290 tests, 0 failed, 1
deselected**, with one non-blocking `websockets.legacy` deprecation warning.
The deselected test is
`tests/improvement/test_improvement_pipeline.py::test_improvement_pipeline`,
which instantiates the application container. This is isolated test evidence,
not authenticated browser or production-runtime verification. Production
readiness remains **NOT PRODUCTION READY**.

## Source-backed execution-entry-point inventory

This inventory follows current callers and dispatch code; it does not treat a
class name or a registered capability as proof that a runtime path is active.
The app-container registry was enumerated from its actual `skills.register`
calls without constructing the container. No live service, broker, or
external integration was exercised.

| Entry point and reachable work | Identity, scope, and final boundary | Persistence, recovery, and evidence | Status / missing acceptance |
|---|---|---|---|
| Authenticated gateway `DISPATCH_COMMAND` → `execute` → `run_command`; `execute_task` → bounded `run_task` | `WebSocketAuthenticator` derives the principal/session. Persisted grants bind operation/correlation/request fingerprint, purpose, action, resource fingerprint and expiry. Gateway revalidates; executor checks exact single-step scope; `SkillManager` revalidates and atomically consumes the grant immediately before the skill. Task children inherit only exact approved `pwd`/`whoami` steps. | SQLite operation rows, grant claims, task row, child operation rows, step events/checkpoints and verifier evidence. Ambiguous dispatch stays uncertain; correlation IDs suppress duplicate task requests. | **PARTIAL, component-tested.** No authenticated browser/runtime acceptance. Child grant and checkpoint fault tests now establish no dispatch on grant-persistence failure, retained execution evidence after checkpoint failure, cancellation of undispatched steps, and no duplicate dispatch. |
| Generic `Executor` / `Dispatcher` / direct `SkillManager.execute` | No authenticated identity is normally supplied. Static protected actions are denied; only an explicit safe-action allowlist can run without a grant. A signed context is currently accepted only for exact `run_command`. Newly registered/unknown action names fail closed. | Execution audit stores action/event/outcome and allowlisted metadata, not raw parameters. No generic durable operation recovery contract is attached to every caller. | **PARTIAL, tests added.** Existing custom action plugins must be explicitly classified and must not be consequential; no plugin registry review or signed extension contract exists. |
| Generic `TaskService.execute_next_task`, `TaskManager`, goal/task translators and `GoalService` producers | Queue producers can persist arbitrary task descriptions, but the legacy planner maps only a small set of title strings to `pwd`; executor policy then blocks actions outside the safe allowlist. These task records do not carry trusted operator authorization. | SQLite claims are atomic; claimed-before-execution can return to ready, while execution interrupted after the dispatch boundary becomes uncertain. Successful execution requires linked verification before completion. Task persistence is not transactionally coupled to operation approval or external execution. | **Reachable but bounded/fail-closed.** Arbitrary generated descriptions are not permission; coverage for every producer and cross-store failure is incomplete. |
| `AutonomyLoop`, cognitive runtime, `ExecutionService` / `ExecutionEngine` | Autonomy and cognitive-cycle plans reach `Executor` without a signed operation context. The executor allowlist blocks protected and unknown actions but permits the enumerated safe set. The gateway rejects broad model-plan verbs that would authorize generated steps indirectly. | Goal/cycle/task state is not a shared one-use operation-grant lifecycle; no safe automatic retry of uncertain execution is established. | **Not authorized for consequential work.** Planning-only behavior is distinguishable; full autonomy persistence, budgets and cancellation remain unverified. |
| Agent orchestrator and workflow executor | Agent selection and workflow metadata do not confer permission. Current orchestrator returns blocked before agent dispatch; workflow executor returns blocked for each available step and does not call the agent. | Workflow/task identifiers are returned by the blocked result, but there is no durable trusted operation record or completed-agent evidence. | **Explicitly blocked.** Must remain so until a real agent and each child action validate the same grant at final dispatch. |
| Container-registered skills: System, Terminal, File, Project, Self-awareness, Improvement, Financial | Container source registers these skills. `AUTHORIZATION_REQUIRED_ACTIONS` blocks file/project/artifact mutation and access, app launch/close, `paper_trade`, and `improve_self`; direct methods repeat the denial. `get_project` is included because it is a registered read alias. `run_command` has its own no-shell allowlist. Remaining registered actions are explicitly safe-classified. | Direct storage boundaries deny sensitive file/project/artifact operations before I/O. The narrow gateway skill call consumes a grant; other safe calls have no per-operation approval or durable authorization record. | **PARTIAL.** Table-driven coverage asserts every action in the current container-wired skill classes has a protected or explicit-safe classification; direct sentinel coverage exists for selected methods. Additional indirect repository paths still need exhaustive tests. |
| Direct project/file/artifact services, managers and repositories | No trusted context reaches these direct APIs. Their guarded protected methods reject before reads/writes; path containment and exclusive create are additional controls, not authorization. | Project/artifact SQLite persistence exists, but blocked operations do not create records. No authorization grant or recovery lifecycle is attached. | **Blocked for sensitive actions.** Existing sentinel/path tests; repository-wide direct-call matrix is incomplete. |
| Financial skill/agent/controller/broker | `paper_trade` is denied at financial skill and agent entry points. Controller construction enforces a local simulation broker; provider-backed/live orders are not authorized by gateway grants. The standalone autonomous trading runtime is a separate CLI path, simulation-only, and can initiate simulated orders when deliberately run. `PaperBroker.submit/cancel_order` directly mutate a local simulated portfolio. | Trading controller has separate idempotency/reconciliation journals and simulation state; no shared `AuthorizationContext` is checked at those broker boundaries. | **Financial operations remain excluded/blocked from this task.** No live or paper order was submitted. Order-producing runtime paths were not run; simulation mode alone is not operator authorization. |
| Self-improvement proposal and `PatchApplier` | Proposal/generation is not approval. `PatchApplier.apply` always returns failure and has no write method. | No patch is applied; trusted approval, backup, validation, rollback and post-change recovery are unavailable. | **Mutation explicitly blocked.** Do not run the container-instantiating improvement smoke test. |
| CLI/service manager, TradingView webhook and automation scripts | Service manager starts/stops only on explicit operator invocation; not run here. The autonomous-trading and TradingView webhook scripts are separately reachable simulation/trading surfaces and do not propagate the gateway context. Other verification scripts do not establish execution authorization. | Trading webhook/runtime has separate event identity/journal/idempotency; lifecycle behavior is not equivalent to operation-store grants. | **Unverified/financially excluded.** No service was restarted and no webhook/order path was exercised. |

The current statically enumerated container-wired action names from the seven
registered skill classes are:

- **Protected; denied without a supported trusted grant:** `build_project`,
  `close_application`, `create_file`, `create_project`, `get_project`,
  `improve_self`, `list_directory`, `list_projects`, `open_application`,
  `paper_trade`, `read_file`, `write_file`.
- **Explicitly safe-classified at the generic dispatch gate:**
  `analyze`, `get_disk_usage`, `get_system_info`, `run_command`,
  `self_analyze`. `run_command` remains additionally restricted to bare
  `pwd`, `ls`, or `whoami` with no custom working directory.
- **Unclassified in that enumerated set:** none.

This is the current static registry snapshot, not a claim that all effects of
each action have been independently reviewed or that optional/dynamic
registrations are covered. Registration does not grant permission: new
action names remain denied until explicitly classified. Protected action
names also include repository operations not present in this current
container-wired set.

### Registry-wide enforcement test matrix

`tests/test_execution.py` now derives the current container-wired action set
from the skill classes registered by `Container` and asserts that each action
is in exactly one of the protected-action or explicit-safe classifications.
Table-driven cases cover all protected names plus an unknown newly registered
action at `ExecutionPermission`; a recording skill proves that direct skill
manager and executor dispatch of a new unclassified action produces no call.
The permission-policy matrix also checks every ALLOW, CONFIRM, and DENY rule
plus an unknown action. This is source-driven component acceptance, not proof
that every external plugin or direct method is represented by the current
container registry.

Gateway authorization regressions already cover missing/untrusted authority,
unauthenticated session, approval fingerprint mismatch, post-review payload
mutation, expiry, revocation immediately before executor dispatch, final
claim rejection, grant replay, concurrent approval, and no-reexecution
reconciliation. Direct file/project/financial/improvement tests assert
sentinels remain untouched. This delivery adds child-grant persistence and
post-dispatch checkpoint failure tests. Wrong-principal, separate-session
substitution, and every direct repository alias are not established by this
matrix and remain release criteria.

### Recovery acceptance matrix status

### Operation lifecycle transition and persistence map

`SQLiteOperationStore` accepts only the transitions below. `update()` checks
the persisted lifecycle column against the serialized operation, rejects
identity/request-scope mutation, and commits the row and its lifecycle event
together. `SUGGESTED -> APPROVED` is excluded from generic updates and must use
the atomic trusted-grant-plus-approval transaction.

| From | Allowed target states | Persistence/effect boundary |
|---|---|---|
| `PLANNED` | `SUGGESTED`, `FAILED`, `CANCELLED` | Operation row plus lifecycle event in one transaction. |
| `SUGGESTED` | `APPROVED`, `FAILED`, `CANCELLED` | Approval requires persisted one-use grant plus `AUTHORIZATION_APPROVED` event in the same transaction. |
| `APPROVED` | `EXECUTING`, `FAILED`, `UNCERTAIN`, `CANCELLED` | Child execution activation atomically enables its child grant and marks the child `EXECUTING`; root state transition and event are conditional and transactional. |
| `EXECUTING` | `COMPLETED`, `VERIFIED`, `UNVERIFIED`, `UNCERTAIN`, `FAILED` | Final one-use grant claim requires `EXECUTING` and records `FINAL_DISPATCH_CLAIMED` atomically. Skill call/result and SQLite persistence are not one transaction; missing response/result evidence stays uncertain. |
| `COMPLETED` | `VERIFIED`, `FAILED`, `UNCERTAIN` | Reconciliation lifecycle update, evidence, and completion event commit together. |
| `UNVERIFIED` | `VERIFIED`, `FAILED`, `UNCERTAIN` | A later verification transition is allowed; absent or conflicting evidence cannot establish success. |
| `UNCERTAIN` | `VERIFIED`, `FAILED` | Reconciliation only; uncertain work cannot re-enter dispatch or be automatically replayed. |
| `VERIFIED`, `FAILED`, `CANCELLED` | none | Terminal states. |

| Fault/restart boundary | Expected durable evidence and recovery | Current evidence |
|---|---|---|
| Approval persistence fails | Atomic approval transaction leaves neither grant nor APPROVED state; executor count stays zero. | Existing gateway failure-injection test. |
| Approved work fails before dispatch / session revoked / grant expires | No final skill invocation; no automatic retry. | Existing gateway tests assert zero executor/skill calls for tested rejection boundaries. |
| Child-grant persistence fails partway through a plan | No task claim or dispatch; already created SUGGESTED/APPROVED child operation rows are cancelled; parent remains uncertain. | New isolated gateway/task fault-injection test. The cancelled grant rows expire and remain unusable because no child context is returned or dispatched. |
| Child-grant persistence returns false and cleanup also fails | Preserve the primary authorization/persistence failure, create no task, invoke no skill, and make any stranded child grant unusable after restart. | Gateway fault injection verifies the sanitized cleanup diagnostic and zero dispatches. Store-reopen recovery cancels the undispatched child and the final grant-claim boundary rejects the stranded grant. |
| Child-grant persistence partially succeeds or leaves a child without a grant, then process restarts | Backfill parent linkage for legacy records, disable derived grants until exact-step activation, and cancel undispatched children under inactive parents even if grant persistence never completed. | SQLite migration/recovery coverage verifies both an unclaimable stranded grant and cancellation of a child operation with no persisted grant; no skill is invoked. |
| Ctrl-C during service readiness | Stop and reap only the startup process owned by this manager; preserve identity-checked handling for unowned processes. | `test_ctrl_c_during_readiness_stops_the_current_startup_process` passes in focused and broad regression runs. |
| Interruption after child activation, before final skill claim | Mark parent and child uncertain on restart, disable child authority, and do not replay. | Gateway regression interrupts at the executor boundary, reopens the store, and verifies uncertainty, unclaimed grant, and duplicate suppression. |
| Child-grant activation fails while persisting the child operation transition | Roll back grant activation and child transition; do not invoke the skill; retain a conservative task state. | Trigger-injected SQLite failure verifies the grant remains disabled/unclaimed, the child is cancelled, the task is uncertain, and the skill sentinel is untouched. |
| Revocation, expiry, parent cancellation, or task cancellation after child activation | Final authority validation/claim rejects before the skill call. | Table-driven gateway regression covers revocation, expiry, and parent cancellation; gateway cancellation before final child claim leaves the task cancelled and invokes no skill. Gateway cancellation is exposed as a WebSocket action, but no browser/UI control is wired or verified. |
| Pre-dispatch cancellation of pending or claimed task, followed by restart | Persist cancelled state, cancellation intent, and audit; do not reclaim or dispatch. | Table-driven restart test covers both pending and claimed states and verifies the cancellation marker and absence from claims. |
| Cancellation after a dispatched step begins | Do not claim to interrupt the active skill; stop later steps and preserve uncertainty for reconciliation. | Controlled integration test requests cancellation from the running skill, proves one dispatch, records its step receipt, leaves the task uncertain, and cancels the undispatched next step. No arbitrary in-flight external effect was interrupted. |
| Grant consumed and effect might have started, then process/skill response is lost | Preserve uncertain state; do not replay; require reconciliation. | Existing interruption/restart and duplicate-correlation tests; child operation is marked executing before dispatch and uncertain if result was not durably recorded. |
| Effect returned, task checkpoint write fails | Child operation retains returned execution result; task remains uncertain without a checkpoint or verified success; duplicate request does not redispatch. | New isolated checkpoint-write failure test. |
| Approval/final-claim/recovery lifecycle event write fails | Roll back the associated grant or operation transition atomically; no skill invocation; retry/recovery must not convert absent evidence to success. | New trigger-injected tests cover approval, final dispatch claim, and interruption recovery event failures; recovered state is idempotent and the stale grant cannot be claimed. |
| Skill returns but execution-result audit persistence fails | Keep the outcome UNCERTAIN, do not automatically retry, and preserve a recovery decision without recording a successful result. | New executor test invokes a side-effect sentinel once, forces result-audit failure, and confirms one attempt, UNCERTAIN, and no EXECUTION_RESULT record. |
| Reconciliation verifier succeeds but final evidence/event persistence fails | Roll back verification and lifecycle updates, keep the operation UNCERTAIN, and do not rerun a pending reconciliation automatically. | New injected reconciliation-event failure test confirms the uncertain state and PENDING reservation survive; repeated request does not call the verifier again. |
| Verification absent, conflicting or unavailable | No verified success; leave operation/task uncertain. | Existing verifier failure/conflict/reconciliation tests. |
| Queued/claimed/executing/uncertain restart | Pre-dispatch claim may return to READY; executing work becomes UNCERTAIN; uncertain work cannot be claimed. | Existing SQLite restart tests. |
| Concurrent task claim | One worker claims each task attempt. | Existing multiprocess atomic task-claim test. |
| All remaining operation-store transition failures and mid-plan expiry/revocation windows | Must remain non-success and must not replay a possibly completed step. | Partially tested; the new cases cover selected approval, final-claim, audit, reconciliation, and recovery boundaries, not every store transaction or external-effect timing window. |

No external side effect is claimed exactly-once. SQLite operations and a
subprocess dispatch are separate transaction domains.

### Historical source-audit verification

The concrete unknown-action bypass was closed: `ExecutionPermission` now
allows only explicit safe action names (with the separate terminal command
policy), and `SkillManager.execute` applies the same classification to direct
calls. The source-derived registry test exposed the registered `get_project`
alias as previously absent from the protected-action set; it is now denied
alongside `read_project`.

Historical verification for the unknown-action change: focused governance
tests passed **192 tests**, and the broad simulation-only suite passed **341
tests, 0 failed, 1 deselected** after a prior transient service-manager
fixture failure. Fresh results for the current recovery-error follow-up are
recorded below; these counts supersede the historical result and are not
cumulative.

### Child-grant recovery and service-manager follow-up — 2026-10-22

Derived child grants are persisted with parent-operation linkage and remain
disabled until an atomic pre-dispatch transition confirms that the parent is
still executing. Final grant claims require activation and an executing
parent. Startup migration backfills linkage from existing child records and
disables legacy derived grants. Restart recovery cancels undispatched
children (including children with no persisted grant) and preserves
uncertainty for executing children. Regressions reopen the database after
cleanup failure and interruption before final skill claim, proving stranded
authority cannot be claimed or replayed. A table-driven final-boundary test
also rejects session revocation, expiry, and parent cancellation after
activation without invoking the skill.

The service-manager regression was caused by signaling a startup-owned child
and polling `/proc` identity without waiting/reaping its `Popen`; an
exited-but-unreaped child could remain visible to that check. `_stop_record`
now waits/reaps its owned `Popen` and retains identity-checked fallback for
unowned processes. The original Ctrl-C test remains unchanged and passes.

Latest focused authorization, recovery, executor, task, and service-manager
regression command:

```bash
ULTRON_EXECUTION_BACKEND=simulation /home/ultron/Projects/ULTRON_INTERFACE_FINAL/.venv/bin/python -m pytest tests/interface/test_interface_gateway_authorization.py tests/interface/test_interface_gateway_execution.py tests/interface/test_interface_gateway_operations.py tests/test_tasks.py tests/test_execution.py tests/runtime/test_service_manager.py::ServiceManagerTests::test_ctrl_c_during_readiness_stops_the_current_startup_process -q
```

Result: **169 passed, 0 failed**. The service-manager Ctrl-C readiness
regression is included; its original test remains unchanged. New tests assert
atomic rollback on approval, final-claim, and restart-recovery audit failures;
uncertainty after execution-result audit failure; verifier-result persistence
failure; and preservation of the primary exception when SQLite rollback also
fails. Invalid lifecycle jumps and immutable identity changes are also denied.

The broad simulation-only command recorded verbatim in the implementation
report passed **375 tests, 0 failed, 1 deselected**, with one non-blocking
`websockets.legacy` deprecation warning. The sole deselection is
`tests/improvement/test_improvement_pipeline.py::test_improvement_pipeline`,
excluded because it instantiates the application container. No test was
skipped; the improvement smoke test was deselected explicitly.

No live service, authenticated browser, external integration, OMEGA, voice
production path, or financial order was exercised. The test matrix does not
prove exactly-once external effects, does not interrupt an already-running
skill, and does not cover every operation-store persistence boundary.
The authenticated WebSocket `CANCEL_TASK` action uses a signed, short-lived,
one-use grant bound to the active principal/session, parent operation, exact
task ID, and `cancel_task_before_dispatch` action. A single SQLite transaction
consumes the grant and persists the cancellation request, task state, and
audit evidence. Authorization rejection, transaction rollback,
duplicate/concurrent requests, restart recovery, and start/dispatch races are
covered. A gateway-level in-flight test confirms the active skill is not
stopped, the task remains `UNCERTAIN`, later work is blocked, and a duplicate
task request does not retry. The browser/UI remains unchanged and unverified.
Exhaustive operation-store faults, arbitrary external-effect interruption,
and independent uncertainty reconciliation remain open. Production readiness
remains **NOT PRODUCTION READY**.

### Gateway cancellation validation — 2026-10-23

- Focused authorization/gateway/task/executor/service-manager command:
  **184 passed, 0 failed**.
- The cancellation-filtered gateway/task selection passed **22 tests** before
  the gateway in-flight regression was added; that regression separately
  passed **1/1**.
- Broad simulation-only suite: **390 passed, 1 deselected, 0 failed**, with
  one non-blocking `websockets.legacy` deprecation warning. The sole
  deselection is the container-instantiating improvement smoke test.
- Standalone Ctrl+C readiness regression: **1 passed**.
- No browser/UI path, production service, OMEGA resource, voice production
  configuration, or order path was exercised.

### Coordinated gateway startup recovery follow-up

Gateway startup detects and reconciles a durable task schema in the operation
database even when the optional task service is not configured. If configured,
the service must use a durable `SQLiteTaskQueue` with the same resolved
database identity. One `BEGIN IMMEDIATE` transaction reconciles interrupted root
operations, child operations and grants, related task rows, and recovery
events. Pre-dispatch task states are cancelled; child dispatch evidence,
executing/uncertain child state, pre-existing uncertainty, or step receipts
keep a task `UNCERTAIN`. Child grants are disabled before startup completes.
Missing parent records, orphaned child grants/operations, and inconsistent
task or operation identity/state fail closed. Repeated and concurrent
recovery are idempotent in the isolated tests, and injected task-event
failure rolls operation, grant, task, and event changes back together.

Validation: focused gateway authorization/execution/task/scaffold tests
passed **108/108**. The broad simulation-only command, including the
scaffolding tests, passed **401 passed, 1 deselected, 0 failed** on rerun;
the unchanged Ctrl+C regression passed **1/1**. An initial broad run had one
transient repeated-startup service-manager fixture failure; that test passed
when isolated and in the full rerun.

The separate project-scaffolding containment fix replaces a string-prefix
test with resolved `Path.relative_to` component checks against both the
Projects root and resolved project root. Traversal, sibling-prefix, project
root symlink, nested symlink escape, and valid in-root creation are covered.
The tests do not cover a local attacker racing path resolution against
directory creation. External side effects remain non-atomic; uncertain
effects still need independent reconciliation. All-state restart testing,
exhaustive persistence fault windows, browser acceptance, and production
evidence remain gaps. **NOT PRODUCTION READY**.
