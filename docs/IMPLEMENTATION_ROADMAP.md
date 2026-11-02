# Implementation Roadmap

Priorities: **P0** blocks safe operation or reliable recovery; **P1** is
required for dependable operator workflows; **P2** is capability maturation.
Complexity is an initial sizing estimate, not a schedule commitment.

## P0 — Surface interrupted-task recovery failure

- **Status:** Complete in this change.
- **Objective:** Keep the original execution/planning exception primary while
  making a failed recovery visible.
- **Files:** `ultron/application/task_service.py`,
  `tests/test_execution.py`.
- **Dependencies:** None.
- **Acceptance:** A test forces both the task failure and recovery failure;
  the original error propagates and includes a sanitized recovery note. A
  secondary child-operation cleanup failure must likewise not replace the
  primary grant-persistence failure.
- **Tests:** Focused task execution test plus the execution/task regression
  suite, including child-grant cleanup fault injection.
- **Rollback:** Revert only this exception-note behavior and its regression
  test; do not restore silent recovery suppression.
- **Complexity:** S.

## P0 — Unify execution authorization across entry points

- **Status:** In progress and fail-closed. The gateway now accepts only the
  concrete `WebSocketAuthenticator`; its persisted one-use grants bind the
  authenticated principal/session, operation, request fingerprint, purpose,
  exact mapped action, dispatch-parameter fingerprint, and expiry, and are
  carried as a frozen context through `Executor` and `SkillManager`. The
  executor checks exact operation/action/parameters, and the skill manager
  revalidates the active session and makes a final persisted one-use claim
  before skill invocation. A rejected final claim is blocked without a skill
  call or retry. This applies to gateway `execute` → `run_command` and the
  bounded authenticated `execute_task` path described below; protected
  operations on other surfaces remain blocked. Executor and direct
  `SkillManager` dispatch now also fail closed for unclassified actions;
  `get_project` was added to the protected set after the source-derived
  registry matrix exposed the alias.
- **Objective:** Demonstrate that gateway approvals, autonomy policy, CLI/task
  execution, and skill dispatch enforce one explicit risk/permission contract.
- **Completed slices:** Removed automatic permission for arbitrary
  interpreters/developer/package-manager commands; `run_command` is gated by
  `ExecutionPermission`, tokenized, launched without a shell, and rejects
  custom working directories. Known side-effect skill actions and unscoped
  filesystem reads/listing are blocked at
  `ExecutionPermission` and `SkillManager.execute` pending trusted
  authorization. Concrete file, project, application-launch, financial-skill,
  and self-improvement methods also deny direct invocation. Project and
  artifact managers/repositories/services repeat the denial at their storage
  boundaries. The application container requires explicit simulation mode;
  provider-backed order submission/cancellation fail closed. `FileManager`
  blocks direct reads, path-existence checks, and writes; project file path
  resolution rejects traversal/symlink escapes and creates exclusively.
  `PatchApplier` has no callback-based approval or write path and always
  refuses mutation until the trusted exact-patch grant mechanism exists.
  Broad `ask`/`plan`/`debug` gateway operations are rejected because their
  approval would not bind model-generated steps. Agent dispatch and workflow
  execution likewise block until they can consume the trusted operation grant;
  arbitrary boolean callbacks are rejected. Focused tests prove no dispatch
  or file mutation on these paths. The gateway scope test proves that session
  revocation at the final dispatch boundary prevents executor invocation.
  Gateway grant consumption and its conditional `SUGGESTED` → `APPROVED`
  update now commit atomically; failure-injection tests prove neither state
  persists alone and that the executor is not called.
  A signed immutable context is now consumed by executor and skill dispatch
  for the narrow gateway mapping; tests verify exact scope, tampering, and
  pre-invocation rejection at both the executor scope check and final claim.
- **Bounded task slice:** authenticated gateway `execute_task` now accepts
  only a canonical, explicitly reviewed `pwd`/`whoami` plan. One approved
  parent operation binds the full plan; the trusted authenticator derives
  narrower signed per-step grants, persisted atomically and claimed at the
  final skill boundary. The task and operation stores must share one durable
  SQLite database; task plan, step receipts, and step events persist. A local
  verifier independently checks successful output. Child operation records
  are marked executing before dispatch and retain returned execution outcomes
  before separate task checkpoint writes. Later undispatched steps are
  cancelled after failure. Fault injection verifies partial child-grant
  persistence failure cancels undispatched child rows without task creation,
  and checkpoint failure after dispatch remains uncertain without replay.
  Restart/interruption remains conservative and never retries an uncertain
  dispatched step.
  Generic task submissions, autonomy, agent, and workflow dispatch remain
  blocked from consequential work.
- **Remaining files:** Extend the validated context only to entry points that
  can enforce the same trusted authority and exact scope at their final
  side-effect boundary; integrate agent dispatch only when a real agent and
  each child action can be authorized at dispatch; inventory legacy
  control/automation paths; and expand lifecycle/recovery coverage beyond
  current task checkpoints. Do not use arbitrary callbacks as substitutes
  for that trust root.
- **Dependencies:** Map every reachable action entry point and action registry.
- **Acceptance:** Table-driven tests cover every registered action through
  each reachable entry point; consequential actions cannot bypass approval;
  denied actions never dispatch; evidence records the decision.
- **Tests:** Registry-wide unit policy matrix, gateway authorization tests, task/CLI
  execution tests, and negative integration tests.
- **Rollback:** Keep the existing stricter gate active; revert only a
  refactor if compatibility breaks.
- **Complexity:** L.

## P1 — Define memory privacy lifecycle

- **Status:** Not started.
- **Objective:** Give operators understandable retention, deletion, and
  export controls for persisted conversation and memory data.
- **Likely files:** `ultron/conversation/history/store.py`,
  `ultron/conversation/manager.py`, memory stores/services, authenticated
  gateway/UI controls, and documentation.
- **Dependencies:** Decide retention defaults, deletion scope, and backup
  treatment before implementation.
- **Acceptance:** Authenticated tests prove session deletion/export and
  retention; SQL remains parameterized; local files are created with the
  documented permissions; errors are visible.
- **Tests:** Store lifecycle tests, restart tests, gateway authorization
  tests, and permission tests.
- **Rollback:** Feature-gate new controls; preserve existing records until
  deletion semantics are approved.
- **Complexity:** M.

## P1 — Add authenticated end-to-end acceptance

- **Status:** Not started.
- **Objective:** Verify real browser authentication, chat, telemetry
  availability semantics, voice health, and simulation telemetry without
  invoking external side effects.
- **Likely files:** `tests/interface/`, `interface/tests/`, runtime test
  fixtures, and `docs/RUNTIME_OPERATIONS.md`.
- **Dependencies:** Isolated local service fixtures and protected test
  credentials; no OMEGA polling.
- **Acceptance:** Tests assert authenticated and unauthenticated behavior,
  real data vs unavailable fields, structured chat errors, Edge engine
  identity, and separate integration-health vs integration-link status.
- **Tests:** Browser and gateway integration suites using local fixtures;
  optional live verification only when ports/processes are safe.
- **Rollback:** Test-only; production services/configuration remain unchanged.
- **Complexity:** M.

## P1 — Complete operation/task recovery matrix

- **Status:** Partially complete. The durable manager-level cancellation
  lifecycle now covers pre-dispatch cancellation and conservative in-flight
  handling. Trigger-based fault injection covers task creation/event, claim,
  start, checkpoint, uncertain transition, cancellation, and removal rollback;
  child-grant activation rollback is also tested. The current inventory and
  tests cover gateway grant-approval atomicity, duplicate task correlation,
  concurrent task claims, partial child-grant failure, cleanup-error
  preservation, stranded child-grant invalidation after restart, checkpoint
  failure, and conservative interruption. Pending and claimed cancellation
  survive restart without becoming claimable; cancellation after a dispatch
  claim leaves uncertainty and stops later steps. The service-manager Ctrl+C
  readiness regression is fixed and passes. Selected operation-store fault
  injection now covers approval, final dispatch claim, interruption recovery,
  execution-result audit, reconciliation finalization, and rollback-cleanup
  failure. Exhaustive operation-store failure windows, external-effect
  reconciliation, and all-state restart coverage remain open. Latest focused
  authorization/gateway/task/executor/service-manager run passed **184/184**;
  latest broad simulation-only suite passed **401**, with the unsafe
  container-instantiating smoke test deselected.
  Gateway startup now detects a durable task schema in the shared database
  and coordinates operation/task recovery in one SQLite transaction, even
  without an optional task service; when configured, the service queue must
  match that database. Isolated tests cover restart/idempotence, no replay of a
  dispatched or potentially executed task, injected event-write rollback,
  orphan/inconsistent records, and concurrent recovery. The broad simulation
  rerun passed **401 tests, 1 deselected, 0 failed**; the unchanged Ctrl+C
  regression passed **1/1**. Full crash-window coverage and external-effect
  reconciliation remain open.
  The authenticated gateway `CANCEL_TASK` action is implemented and covered
  for identity/session binding, expiry/revocation, replay, concurrency,
  persistence faults, restart recovery, task-start/dispatch races, and an
  in-flight skill. Browser/UI wiring remains out of scope and unverified.
  Cancellation does not interrupt an in-flight skill; potentially executed
  work remains `UNCERTAIN` and is not retried.
- **Objective:** Prove restart, duplicate correlation ID, dispatch uncertainty,
  reconciliation, verification, and recovery outcomes across task and
  gateway stores.
- **Likely files:** `ultron/application/runtime/operation_store.py`,
  `ultron/executive/tasks/manager.py`,
  `ultron/executive/tasks/sqlite_queue.py`, gateway lifecycle handlers, tests.
- **Dependencies:** Authorization contract review.
- **Acceptance:** Fault-injection tests show task-store transition rollback,
  no unauthorized or duplicate dispatch, no success-shaped fallback, durable
  uncertainty, and explainable recovery at each covered persisted transition.
  Coordinated gateway startup recovery now reconciles both durable stores
  atomically for the tested task-operation path. Complete operation-store
  fault coverage, all-state restart coverage, and external-effect
  reconciliation evidence remain required before calling the matrix complete.
- **Tests:** SQLite restart/fault tests and gateway operation lifecycle tests.
- **Rollback:** Preserve conservative uncertain states and disable any new
  automation that cannot establish verification.
- **Complexity:** L.

## P2 — Establish research provenance and freshness

- **Status:** Not started.
- **Objective:** Carry source, retrieval time, freshness, and confidence
  through ingestion, retrieval, model context, and user-visible answers.
- **Likely files:** `ultron/knowledge/ingestion.py`,
  `ultron/knowledge/retrieval.py`, `ultron/knowledge/context_provider.py`,
  related repositories and tests.
- **Dependencies:** Define source trust and stale-data policy.
- **Acceptance:** Answers can be traced to stored source records; stale or
  absent evidence is explicitly reported; ingestion errors are not silently
  treated as success.
- **Tests:** Provenance round-trip, stale-source, missing-source, and
  integration-failure tests.
- **Rollback:** Keep new provenance metadata optional for old records; avoid
  deleting existing knowledge.
- **Complexity:** L.

## P2 — Bound and observe autonomy runs

- **Status:** Not started.
- **Objective:** Add explicit run budgets, pause/cancel, resumable state, and
  operator-visible progress around the existing one-cycle autonomy loop.
- **Likely files:** `ultron/autonomy/loop.py`, goal/task stores, runtime
  gateway/UI, tests, and operations documentation.
- **Dependencies:** P0 authorization; P1 recovery matrix.
- **Acceptance:** Runs are bounded by configured policy, survive restart
  without duplicate execution, expose waiting/failed/uncertain states, and can
  be paused safely.
- **Tests:** Cycle-budget, cancellation, restart, duplicate-dispatch, and
  permission-denial tests.
- **Rollback:** Disable scheduling while keeping manual one-cycle planning
  available.
- **Complexity:** L.

## P2 — Verify self-improvement approval and rollback

- **Status:** Mutation blocked. The patch applier has no callback-based
  authorization or write path, preventing arbitrary booleans from enabling
  self-modification. Proposal generation and validation remain available;
  backup integrity, baseline evaluation, rollback, and post-change verification
  are not established.
- **Objective:** Demonstrate that code-change proposals cannot be applied
  without explicit review and that backup, tests, and rollback are reliable.
- **Likely files:** `ultron/improvement/` apply/security/validation components,
  `ultron/infrastructure/container.py`, and tests.
- **Dependencies:** P0 authorization and auditable operation lifecycle.
- **Acceptance:** End-to-end fixture proves proposal-only default, approval
  before mutation, pre-change backup, required tests, and successful rollback
  after injected failure.
- **Tests:** Temporary-repository integration tests; never mutate the live
  workspace during destructive test cases.
- **Rollback:** Disable patch application; retain read-only analysis.
- **Complexity:** L.

## P1 — Integrate reliable agent orchestration

- **Status:** Not execution-enabled. Capability matching and dependency
  validation are implemented, but neither orchestrator dispatch surface
  accepts arbitrary boolean callbacks as authorization. Agent work remains
  blocked until the existing gateway operation-grant authority can validate
  exact agent/action/resource scope at dispatch.
- **Objective:** Route appropriate operator tasks through functioning
  specialists using structured dependencies, real execution records,
  independent verification, and the trusted authorization boundary.
- **Evidence:** See `docs/ULTRON_AGENT_CAPABILITY_AUDIT.md`. The current chat
  path does not dispatch through `AgentOrchestrator`; the workflow executor is
  constructed but has no runtime caller; the default autonomy `PrimeAgent`
  uses an empty registry.
- **Remaining files:** Agent composition/runtime gateway, workflow contracts,
  authorization boundary, specialist implementations, persistence, UI
  telemetry, and focused integration tests.
- **Dependencies:** P0 unified execution authorization and real planning,
  research, and independent verification implementations.
- **Acceptance:** Deterministic end-to-end fixture proves capability routing,
  dependency order, denied dispatch, failure and partial states, verified
  completion, persistence, and truthful operator status. Live model and
  browser acceptance requires explicit local prerequisites.
- **Tests:** Routing matrix, workflow fault matrix, authorization bypass
  matrix, and a real-component integration test with isolated storage.
- **Rollback:** Keep unimplemented specialists unregistered/ineligible and
  leave agent workflow opt-in until the authorization and status contract
  passes.
- **Complexity:** L.
