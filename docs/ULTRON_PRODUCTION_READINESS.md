# ULTRON Production Readiness

## Assessment

**NOT PRODUCTION READY.** This status is based on documented gaps in
[ULTRON_GAP_CLOSURE_AUDIT.md](./ULTRON_GAP_CLOSURE_AUDIT.md), not on component
count or successful code changes. Production readiness requires verified
authorization across every reachable execution path, safe self-improvement
boundaries, privacy lifecycle controls, and authenticated operating-path
evidence that is not available from the current test run.

## Capability status

### IMPLEMENTED AND VERIFIED (limited scope)

- Terminal runner executes only the automatically allowed bare `pwd`/`ls`/`whoami`
  commands, without a custom working directory or shell interpretation;
  `ExecutionPermission` applies that policy before terminal skill dispatch.
  Executor and direct `SkillManager` dispatch now also require an explicit
  safe-action classification; unknown/newly registered actions fail closed.
  The current container-wired action matrix exposed and fixed the missing
  protected classification for the registered `get_project` alias. The
  current registry snapshot has 17 action names across the seven statically
  enumerated skill classes; dynamic plugin registration is not covered.
  Latest focused authorization/recovery/executor/task/service-manager
  validation passed 191 tests. Latest broad simulation-only validation passed
  389 tests, with only
  the container-instantiating improvement smoke test deselected. This is not
  full action-governance or multi-agent integration verification.
- Task recovery failure is surfaced as a sanitized note without replacing the
  original task exception. Evidence: `tests/test_execution.py` included in
  the 59-pass focused run.
- On the narrow gateway approval path, one-use grant consumption and the
  operation's `APPROVED` transition are persisted atomically. Failure-injection
  coverage verifies that a database write failure rolls back both records and
  prevents executor invocation. This is limited to gateway approval and does
  not make dispatch or external effects atomic.
- Operation lifecycle updates now enforce an explicit transition table and
  immutable operation/request identity; each committed transition has a
  transactionally recorded lifecycle event. Approval cannot use the generic
  updater, and the final persisted grant claim requires an `EXECUTING`
  operation. New failure-injection tests prove selected approval, final-claim,
  and restart-recovery event failures roll back their state/grant updates.
  Recovery is idempotent for those tested interruption cases. A skill result
  audit-write failure and a reconciliation-finalization write failure both
  remain uncertain and do not trigger replay. Rollback cleanup failure
  preserves the primary database error and adds a sanitized note. These are
  selected test boundaries, not an exhaustive operation-store proof.
- A bounded authenticated gateway `execute_task` plan supports exact,
  dependency-ordered bare `pwd`/`whoami` steps. Focused integration coverage
  demonstrates two-step execution through the durable task queue, per-step
  one-use grants, persisted checkpoints, independent verification, and
  completed task state. Each child operation records its step result; later
  undispatched steps are cancelled after failure. Session revocation before
  child-grant issuance blocks before task creation; an interruption after
  dispatch remains uncertain across restart and is not retried. This is
  isolated integration evidence, not authenticated browser or
  production-runtime evidence.
- Fault-injection coverage now establishes that a second child-grant
  persistence failure creates no task and invokes no skill, while all
  already-created step-operation records are marked cancelled. A successful
  dispatch followed by checkpoint-write failure preserves the child operation
  result, leaves the task uncertain without a checkpoint or verified outcome,
  cancels later steps, and does not redispatch on duplicate correlation.
  These tests do not establish every persistence failure window or exactly-once
  external execution.
- Child grants are parent-bound and disabled until the exact step is
  atomically activated before dispatch. Restart migration disables legacy
  derived grants; recovery cancels undispatched children and preserves
  uncertainty for executing ones. Fault injection verifies that a stranded
  grant after cleanup failure cannot be claimed after database reopen and
  produces no skill call. Final-boundary tests cover post-activation session
  revocation, expiry, parent cancellation, and interruption before skill
  claim, plus rollback when child-operation persistence fails during
  activation. The latter leaves the grant disabled and invokes no skill.
- A durable manager-level cancellation API and an authenticated gateway
  `CANCEL_TASK` protocol action record pre-dispatch cancellation with task and
  operation audit evidence. The gateway grant binds principal, session,
  parent operation, exact task ID, and the permitted cancellation action;
  correlation IDs and grant IDs are replay-protected. Pending cancellations
  survive restart and cannot be claimed. Expired, revoked, mismatched, and
  cross-session requests are rejected. Cancellation after a child dispatch
  claim does not interrupt the active skill; it stops later steps and leaves
  the task uncertain. Browser/UI wiring is not implemented or verified, and
  arbitrary in-flight effects are not safely interruptible.
- A SQLite trigger-based task persistence matrix injects failures at task
  creation/event, claim/event, start/event, checkpoint/event,
  uncertain-transition/event, cancellation/event, and removal/event
  boundaries. The operation-store tests now cover selected approval,
  final-claim, interruption recovery, result-audit, and reconciliation
  persistence boundaries. Neither matrix exhausts every transition, verifier
  failure mode, or external-effect timing window.
- The service-manager Ctrl-C readiness test now passes. The manager waits for
  and reaps the startup process it owns; the test was not weakened.
- Latest focused gateway authorization/execution/operation, task, executor,
  and service-manager suite: **184 passed, 0 failed**. The unchanged Ctrl+C
  readiness regression also passed independently (**1 passed**). The focused
  run includes the gateway-level test that cancels while a skill is blocked
  in flight and verifies `UNCERTAIN`, no later dispatch, and no duplicate
  retry.
- Latest broad simulation-only suite: **390 passed, 1 deselected, 0 failed**;
  the sole deselection is the application-container-instantiating improvement
  smoke test. One non-blocking `websockets.legacy` deprecation warning.

Gateway cancellation is component-tested, not browser-verified. Cancellation
does not stop an in-flight skill or external effect; such work remains
`UNCERTAIN`, later steps are prevented, and no automatic retry occurs.
Selected SQLite fault boundaries and authorization/replay/concurrency/restart
cases pass, but exhaustive persistence/crash windows and independent
reconciliation remain release gates. Production readiness remains
**NOT PRODUCTION READY**.

### IMPLEMENTED BUT NOT END-TO-END VERIFIED

- Gateway auth, persisted operation approval, correlation-ID protection, task
  queue/recovery, voice API readiness, launcher, responsive UI, and
  paper-only standalone financial construction have source and component-test
  implementations. Historical launcher/frontend evidence exists, but current
  services and authenticated browser behavior were not tested here.

### PARTIALLY IMPLEMENTED

- Unified execution governance across gateway, autonomy, direct executor,
  task, automation, and skills. Source-identified consequential actions and
  patch mutation now fail closed at central dispatch/mutation boundaries.
  The gateway itself now requires the concrete `WebSocketAuthenticator` and
  binds its one-use grant to the mapped `run_command` action plus a
  fingerprint of exact dispatch parameters. A signed immutable context is
  checked by the executor and revalidated with a persisted one-use claim at
  the skill invocation boundary. This includes the gateway's `execute` →
  `run_command` path and a bounded `execute_task` path whose exact plan is
  approved as one parent operation and whose per-step grants are derived by
  the trusted authenticator.
  Direct FileSkill, FileManager, ProjectSkill, application-launch,
  FinancialSkill, ImprovementSkill, project repository, and artifact
  operations are also guarded; project file target resolution rejects
  traversal/symlink escapes. The application container explicitly requires
  simulation mode, and provider-backed order submit/cancel paths fail closed.
  The context is operation-bound and claimed once at dispatch. Generic task
  submission, autonomy, agent, workflow, and direct service boundaries still
  do not consume it; protected actions remain denied there. Legacy control/automation entry
  points remain a separate audit requirement. Atomic persistence of gateway
  grant consumption and `APPROVED` prevents a database failure from leaving
  only one of those records committed; it does not make external execution
  atomic or exactly once.
- The newly tested coordinated gateway startup path covers only the narrow
  shared-SQLite task/operation recovery case. Crash recovery beyond that path,
  including other dispatch/reconciliation transitions, non-gateway task
  stores, and external side-effect boundaries, remains unverified.
- Memory/history privacy, export, deletion, retention, and derived-store
  handling.
- Model/provider identity and bounded response behavior.
- Autonomy budgets, cancellation, run persistence, and resumability.
- Research/knowledge source provenance and freshness.
- Self-improvement proposal/validation exists, but mutation is disabled.
  `PatchApplier` no longer accepts a caller-provided boolean callback and has
  no file-write methods. Trusted exact-patch approval, backup verification,
  baseline evaluation, and rollback remain unavailable/unverified.
- Agent orchestration is only partially implemented. Capability matching and
  workflow dependency handling have focused tests, but the orchestration is
  not integrated into chat. Dispatch is blocked until agents can consume a
  trusted operation grant; arbitrary boolean callbacks are not accepted.
  Several advertised specialists remain blocked stubs.
- End-to-end telemetry freshness, availability, simulation, and provenance
  presentation.

### NOT IMPLEMENTED OR NOT ESTABLISHED BY EVIDENCE

- A single enforceable action-governance contract proven by a table-driven
  registry-wide test matrix. The gateway `execute` path and bounded
  `execute_task` path have a trusted
  principal/session, operation, request, action, resource-fingerprint, purpose,
  expiry, and one-use grant; its immutable context is validated by the
  executor and skill manager, with a persisted claim immediately before skill
  invocation. The task implementation is limited to allowlisted read-only
  steps and shared durable SQLite storage. It is not propagated into generic
  task, autonomy, agent, workflow, or direct service boundaries, and legacy
  control/automation paths remain unverified.
  Protected operations remain blocked there.
- Complete plug-in and direct-method enforcement coverage. The new action
  matrix derives the current container-registered skill action names and
  checks their safe/protected classification, but it does not enumerate every
  dynamically added plugin, direct repository alias, CLI entry point, or
  external adapter.
- Exact plan/action/resource grant propagation into agent tasks, direct
  services/repositories, and financial boundaries. Broad gateway
  `ask`/`plan`/`debug` operations are rejected because approving the prompt
  would not approve model-generated steps.
- User-facing conversation-history export/deletion/retention lifecycle.
- Proven end-to-end self-improvement proposal→approval→backup→patch→test→rollback
  lifecycle in an isolated workspace.
- Current authenticated browser acceptance and verified live chat/runtime
  telemetry.

### BLOCKED BY SAFETY OR ENVIRONMENT CONSTRAINTS

- Authenticated E2E requires an approved local service/session/model
  environment. No services were started, stopped, or restarted. A read-only
  process listing observed pre-existing ULTRON service-manager processes;
  none was contacted or otherwise interacted with.
- OMEGA status/data, polling, credentials, source, and runtime are outside
  scope and were not accessed.
- `.env.financial` was not read; live or paper orders are prohibited.
- Production voice settings and approved reference audio remain unchanged.

## Required release gates

1. Pass a registry-wide policy matrix proving authorization at dispatch for
   every consequential action and every actual execution entry point.
2. Extend the tested narrow gateway context to additional supported execution
   entry points only where the final boundary can validate the same trusted
   `WebSocketAuthenticator` and persisted grant, binding principal, operation,
   exact action/resource, fingerprint, expiry, and one-use consumption.
   Arbitrary callbacks, booleans, and model output are not trust providers.
3. Prove modified, expired, replayed, or cross-scope approvals cannot dispatch;
   prove uncertain side effects cannot retry before reconciliation.
4. Prove self-improvement writes are approved, workspace-contained, backed up,
   validated, and recoverable after test/rollback failures.
5. Implement and test authorized history/memory export, deletion, retention,
   and derived-store behavior.
6. Verify missing/stale/failed data is never presented as healthy, zero, or
   real financial activity.
7. Run authenticated browser acceptance with safe local prerequisites and
   explicit evidence for chat, operation approval, telemetry, voice, and
   reconnection.
8. Run relevant regression suites and separately document any blocked external
   paths.

The source-backed entry-point inventory, authorization matrix, and per-stage
recovery evidence are maintained in
[ULTRON_GAP_CLOSURE_AUDIT.md](./ULTRON_GAP_CLOSURE_AUDIT.md). Current status
remains **NOT PRODUCTION READY**.

Until these gates pass, ULTRON must not be described as production-ready.

## Coordinated gateway startup recovery evidence

Gateway startup detects an existing task schema and reconciles task and
operation records in one SQLite transaction, even when the optional task
service is not configured. If the service is configured, startup fails closed
unless its durable task queue shares the operation-store database. Interrupted parent
operations become `UNCERTAIN`; tasks without child-dispatch evidence become
cancelled, while claimed dispatch, executing/uncertain child operations,
existing uncertainty, or step results preserve `UNCERTAIN`. Child grants are
disabled, recovery events are atomic with state changes, and repeated
recovery does not replay tasks. Orphaned or inconsistent records prevent
startup rather than being guessed into a safe state.

Temporary-database tests cover restart across both stores, duplicate and
concurrent recovery, injected task-event failure with full transaction
rollback, orphan/inconsistent records, and claim prevention. Focused tests
passed **108/108**; the broad simulation-only suite passed **401 passed,
1 deselected, 0 failed** on rerun; the unchanged Ctrl+C regression passed
**1/1**. The initial broad run's single transient service-manager fixture
failure passed in isolation and on rerun.

The project scaffold boundary uses component-aware resolved paths and has
tests for traversal, sibling-prefix, root/nested symlink escapes, and normal
in-root creation. A concurrent local symlink race is not covered. This
recovery evidence does not establish exactly-once external effects, stop
in-flight work, cover all SQLite crash/disk-full windows, or reconcile
uncertain external effects. Production readiness remains **NOT PRODUCTION
READY**.
