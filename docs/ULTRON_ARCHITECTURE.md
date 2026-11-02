# ULTRON Architecture

## Scope and evidence

This document describes the current workspace structure observed in source.
It is not a claim that every external dependency or optional integration is
running. On 2026-10-27, read-only probes verified the recorded gateway,
frontend, auth-helper, and Edge voice processes against their process-start
identities and readiness checks. This does not establish which source revision
those processes loaded. The dashboard browser remained unauthenticated and
showed cached CRE data as stale.

## Runtime components

```text
Operator browser
  ├── frontend served at 127.0.0.1:8000
  └── WebSocket ── local auth helper at 127.0.0.1:8081
                         └── authenticated WebSocket
                               └── interface gateway at 127.0.0.1:8080
                                     ├── conversation/context + configured LLM
                                     ├── runtime telemetry and operation handlers
                                     ├── durable task/operation lifecycle
                                     └── optional integrations (not assumed healthy)

Voice API at 127.0.0.1:8765 ── configured voice engine

Application/container wiring
  ├── cognitive runtime and event bus
  ├── goals and bounded autonomy cycle
  ├── task manager/SQLite queue → TaskService → Executor
  ├── skill registry/router → permission, dispatch, audit, recovery
  └── memory, knowledge, learning, and improvement services

Standalone financial bootstrap
  └── PaperBroker → PaperTradingEngine

Separate financial dashboard
  └── run_financial_dashboard.sh → Streamlit → application Container singleton
        ├── FinancialAgent → dashboard analysis
        └── optional Alpaca paper-account reads
```

## Request and execution paths

### Browser, authentication, and chat

The operator console connects to the local auth helper by default. The helper
verifies the local unlock passphrase before opening an upstream gateway
connection using the existing gateway credential. The gateway derives
operator identity from the authenticated connection, validates chat payloads,
checks model availability, adds authenticated runtime telemetry when
requested, applies a bounded model timeout, and emits structured result or
error statuses. The conversation manager adds user and assistant messages to
SQLite-backed history and can recall memory context.

The history store keeps the latest messages for a session when building
context, but the inspected store has no retention or deletion interface.
Memory privacy lifecycle is therefore a product and operational gap, not an
assumed capability.

### CRE status view

The operator console can request a bounded CRE status snapshot through the
existing authenticated gateway using `CRE_STATUS_REQUEST`. The gateway derives
the principal from its authenticated WebSocket session; the backend explicitly
maps that principal to the same owner ID because `ContentRightsService`
requires asset and record ownership to match `AuthorizationContext.principal`.
The owner identity is therefore the configured gateway principal, not a
frontend-supplied value. A separate owner-ID namespace would require a broader
change to CRE authorization and write services. The read-only projection uses
the CRE store's owner-filtered queries
and returns job, lifecycle, publishing, and event summaries without raw audit
details. Processing and rendering services are reported as not configured
unless they are actually wired, publishing is explicitly reported as
mock-only, and analytics remain unavailable until instrumented.

The local service entrypoint selects `data/ultron_cre.db` as the dedicated CRE
store. Gateway startup initializes or opens it through `ContentStore` only
when gateway authentication is configured; an existing database at that path
must pass the CRE schema check. This startup behavior does not establish that
the database has been initialized on a particular installation.

### Goals, tasks, and operations

`AutonomyLoop.run_cycle()` performs one bounded observe, select, plan, policy,
execute, reflect, and goal-update cycle. It checks the `create_task` policy
label before execution and can operate in planning-only mode.

`TaskManager` uses a task queue and exposes atomic SQLite claims, lifecycle
transitions, verification linkage, and interrupted-task recovery.
`TaskService` is the application boundary for durable task execution. The
authenticated gateway supports one bounded task shape: an operator approves a
canonical plan of up to eight `run_command` steps, limited to bare `pwd` and
`whoami`, with dependencies on earlier steps. The exact plan is bound into the
parent operation grant. After the parent grant is claimed, the trusted
authenticator derives narrower signed child grants only for commands present
in that plan. Each grant is persisted disabled and parent-bound, activated
atomically with the child operation's transition to executing, and claimed
once by `SkillManager` immediately before its step dispatch.

The task and operation stores must use the same durable SQLite database.
Task identity, plan, parent operation, and each completed step receipt are
persisted; the executor also writes dispatch audit records. Successful task
execution remains uncertain until the local task verifier independently
checks each `pwd`/`whoami` output. Child operation records track each
dispatched step's execution result; steps not dispatched after an earlier
failure are cancelled. The parent task remains unverified until verifier
evidence is persisted. The durable task manager supports pre-dispatch
cancellation and records in-flight cancellation requests with the task and
parent-operation cancellation marker. A queued, ready, or claimed task can be
cancelled without dispatch. Once a child dispatch claim exists, cancellation
does not interrupt the active skill: it stops later steps and leaves the task
uncertain pending reconciliation. An interrupted executing task is not
automatically retried. This narrow gateway-to-task path is not general
authorization for tasks or model-generated plans. `Executor` routes plan
actions through registered skills and contains separate permission, audit,
and retry/recovery boundaries. Exceptions after skill dispatch are treated
as potentially side-effecting and are not blindly replayed.

The WebSocket gateway exposes `CANCEL_TASK` only for the exact task created by
the authenticated session's active `execute_task` operation. The request uses
a unique correlation ID and a signed, one-use grant bound to the principal,
session, parent operation, `cancel_task_before_dispatch` action, and task ID.
Grant consumption, cancellation request, task transition/event, and parent
operation cancellation evidence commit in one SQLite transaction. Replayed,
expired, revoked, mismatched, or cross-session requests are rejected. This
does not interrupt a skill that may already have run: such tasks remain
`UNCERTAIN` and are not automatically retried. No browser/UI cancellation
control is wired or verified.

Gateway operations have correlation-ID deduplication and explicit lifecycle
and approval paths. These controls are separate from the task queue; complete
equivalence of policy and verification across every entry point remains an
audit requirement.

For a gateway approval, the one-use grant row and operation `APPROVED` state
are committed in one SQLite transaction. The state update is conditional on
the stored operation ID, correlation ID, request fingerprint, and
`SUGGESTED` state. If the update fails, the grant insert rolls back too. This
closes the grant-consumed/approval-not-persisted split for that gateway
transition only. It does not include executor dispatch, external side
effects, task state, or reconciliation; interrupted work remains subject to
conservative uncertainty and execution is not exactly-once.

Operation persistence enforces an explicit lifecycle transition table,
immutable operation/correlation/request identity, and an atomic lifecycle event
for each state transition. Approval is the only `SUGGESTED -> APPROVED` path
and must persist its trusted grant in the same transaction. Final grant claims
require the operation to remain `EXECUTING`; the claim and its lifecycle event
are atomic. Recovery moves interrupted root operations to `UNCERTAIN` in one
transaction and is idempotent. If a lifecycle event cannot be persisted, the
associated state/grant transition rolls back. If rollback itself fails, the
original database exception is preserved with a sanitized cleanup-failure
note. These SQLite guarantees cover database records only, not an external
effect and its result write.

Current verification evidence for the gateway-cancellation follow-up: the
focused gateway/task/executor/recovery/service-manager suite passed **191
tests**, the standalone unchanged service-manager Ctrl+C regression passed
**1/1**, and the broad simulation-only suite passed **389 tests** with one
container-instantiating improvement test deselected and one non-blocking
deprecation warning. These are tests, not live service, browser, or
external-effect verification. Remaining release blockers and exact commands
are recorded in [ULTRON_IMPLEMENTATION_REPORT.md](./ULTRON_IMPLEMENTATION_REPORT.md).

### Authorization and final dispatch

The gateway requires the concrete `WebSocketAuthenticator` trust authority;
arbitrary callback-based authorizers are rejected. It persists a one-use
approval grant bound to the authenticated principal/session, operation and
correlation identifiers, request fingerprint, purpose, exact mapped action,
dispatch-parameter fingerprint, and expiry. It revalidates that grant
immediately before the mapped executor or task dispatch. A task approval
binds the complete canonical step list; the authenticator derives only
per-step `run_command` grants matching that list and the active parent grant.
Each grant travels as a frozen `AuthorizationContext` through `Executor` and
`SkillManager`. The
executor checks the single-step plan and exact action/parameters; the skill
manager revalidates the signed context against the active session and
atomically claims the persisted grant immediately before invoking the skill.
Session validation and this persisted claim are serialized against session
revocation by the authenticator lock.
An invalid or replayed final claim is reported as blocked before skill
invocation and is not retried. Supported paths are gateway `execute` →
`run_command` and bounded `execute_task`. Bare `whoami` was added to the
terminal policy's no-confirmation read-only commands for this task scope.

This context is not a general-purpose permission token. Generic executor
calls without it retain existing behavior, and direct task, agent, workflow,
storage, financial, and self-improvement boundaries do not consume this
contract. Only the exact gateway `execute_task` entry point derives per-step
grants from a claimed, signed parent plan. Generic task submissions,
autonomy, agent, and workflow dispatch remain blocked from consequential
execution; a task or model-generated plan cannot manufacture authorization.

The source-backed protected-action set is centralized in
`ultron/security/permission/rules.py`. It covers project/file mutations,
unscoped file reads and listings, project/artifact reads and mutations
(including the registered `get_project` alias),
application launch/close, paper-trade dispatch, and self-improvement. The
executor and skill-manager enforce the deny-by-default rule, and final
skill/file/project/artifact methods repeat it against direct method calls.
Project file path resolution rejects traversal and symlink escapes, and file
creation uses exclusive creation to prevent overwrites. No trusted grant is
propagated to release these operations.

Executor dispatch and direct `SkillManager.execute` also require an explicit
`UNTRUSTED_SAFE_ACTIONS` classification when no signed context is present.
The current container-wired safe registry actions are read-only analysis,
system snapshots, self-analysis, and `run_command` (with its separate
no-shell command allowlist); a newly registered or otherwise unclassified
action is blocked before the skill method. A signed context currently
authorizes only the exact gateway `run_command` dispatch. The allowlist does
not authorize protected project/file/financial/improvement actions.
The current seven statically enumerated container-wired skill classes expose
17 action names: 12 protected actions (`build_project`, `close_application`,
`create_file`, `create_project`, `get_project`, `improve_self`,
`list_directory`, `list_projects`, `open_application`, `paper_trade`,
`read_file`, and `write_file`) and five explicit safe classifications
(`analyze`, `get_disk_usage`, `get_system_info`, `run_command`, and
`self_analyze`). This is a static registry snapshot; dynamically registered
plugins and the complete effects of every listed action are not certified by
that classification test.

Child grants derived for a task are persisted disabled and tied to their
parent operation. Immediately before dispatch, a transaction activates only
the exact grant and advances its child operation, conditional on the parent
still executing. Final grant claims require that activation and an executing
parent. Startup migration backfills parent linkage from persisted child
records and disables those legacy child grants; restart recovery cancels
undispatched children even when no grant row was persisted, and leaves
dispatched or executing children uncertain. Tests inject child-grant
persistence and cleanup failures, reopen the operation store, and confirm a
stranded grant cannot be claimed or dispatched. Additional tests inject
interruption after activation but before the final skill claim, session
revocation, child-grant expiry, and parent cancellation between activation
and final claim; these are rejected without invoking the skill. A forced
child-operation persistence failure during activation rolls back grant
activation and leaves it unclaimable. The durable cancellation API is
manager-level and does not interrupt an external effect already in flight.

The service manager now waits for and reaps the startup process it owns after
readiness is interrupted, while retaining process-identity checks for
unowned processes. The Ctrl-C readiness regression passes. No pre-existing or
project runtime service was started, stopped, or contacted; service-manager
test fixtures are isolated test-owned processes.

The remaining release architecture is to reuse the trusted authorizer and
operation store for additional explicitly supported entry points, carrying
the immutable operation context to each final side-effect boundary and
revalidating exact action/resource scope there. Until those integrations
exist, protected actions stay blocked; the static deny path is not an approval
provider.

Broad gateway operations named `ask`, `plan`, and `debug` previously invoked
`CognitiveRuntime.run_cycle()`, which executes a model-generated plan after
approval of only the high-level request. That is not exact-plan authorization.
Those verbs are now rejected before proposal persistence, and legacy persisted
operations with those verbs are rejected at approval and execution boundaries.
The gateway no longer advertises them as supported operations. A plan-specific
review/approval lifecycle is still absent.

### Standalone financial dashboard

`run_financial_dashboard.sh` starts the separate Streamlit financial
application; it is not served by the authenticated operator-console gateway.
The launcher defaults `ULTRON_EXECUTION_BACKEND` to `simulation` while
preserving an explicitly configured value, which the application rejects
unless it is exactly `simulation`. Streamlit is a direct runtime dependency
declared in `requirements.txt`.

The dashboard reuses the process-level `ultron.infrastructure.container`
singleton. Constructing another `Container` from the cached dashboard factory
would open duplicate application repositories and runtime stores in the same
Streamlit process. FinancialAgent results are returned in the flat contract
(`financial_analysis`, `regime`, `risk`, `market`, and `decision`); the
dashboard adapter accepts that contract and the older nested analysis shape.
The dashboard records local receipt time separately from provider freshness,
which is not supplied by that analysis response.

The Alpaca panel requests account, position, and order snapshots with the
provider's paper-mode client. It shows a local timestamp only after all three
reads succeed. It does not expose live-money execution. The existing
`FinancialAgent.paper_trade` authorization guard remains in force; a dashboard
button is not authorization. This Streamlit application does not inherit the
operator console's WebSocket/local-unlock authentication and must be treated as
a separate local operator surface.

The current Container-backed dashboard path is covered by
`tests/dashboard/test_financial_dashboard_contract.py`. The separate
`ultron/financial/bootstrap.py` path remains active through `financial_main.py`;
it is not treated as a duplicate because its provider and persistence defaults
differ.

### Release and persistence boundary

The source bundle utility accepts an explicit file allowlist, writes a
SHA-256 manifest, and verifies archive members against that manifest. The
SQLite backup utility uses SQLite's online backup API and checks integrity
and schema metadata against isolated temporary databases. Neither utility
was used against live databases during this audit. No deployable release
bundle was created because the workspace has no Git metadata from which to
establish the complete intended source revision and exact release allowlist.
The scoped pre-change archive is evidence preservation only, not a release or
known-good rollback artifact.

### Persistence and optional capabilities

- Task and operation lifecycles use SQLite-backed stores.
- Conversation history uses SQLite; memory and knowledge have separate
  repositories/services.
- The voice API is a separate service and reports engine/readiness through its
  own health contract. The prior runtime audit identified Edge as the
  production engine and XTTS as an isolated reference-conditioned option.
- The standalone financial bootstrap and application container construct
  `PaperBroker` and `PaperTradingEngine` only in simulation mode. The
  application container requires `ULTRON_EXECUTION_BACKEND=simulation`;
  provider-backed order submit/cancel calls fail closed. Market-data adapters
  can be selected independently and do not change the order broker.
- The container's `ProviderManager` uses one configured local `OllamaProvider`
  (`qwen2.5-coder:7b` by default). The provider interface permits replacement,
  but no task classifier, privacy-aware routing, external fallback policy,
  capability-based model selection, or cross-model context contract is
  implemented. Routing is not an authorization mechanism.
- `ConversationHistory` stores user/assistant messages in SQLite and returns
  the latest requested messages (default ten); there is no export, deletion,
  retention, or correction API. The separate `MemoryManager` exposes
  working/episodic/semantic/procedural stores; working, episodic, and
  procedural data are in-memory, while its semantic `MemoryRepository`
  persists simple category/key/value rows in SQLite. `ExperienceStore`
  appends JSON experience records and treats read/parse exceptions as an
  empty list, so it is not authoritative recovery evidence. These stores do
  not have unified provenance, correction, retention, or deletion semantics.
- `AutonomyLoop` performs a bounded single cycle and has a planning-only mode
  when execution is not configured. It is not a persistent autonomous
  operator: durable run IDs, budgets, pause/cancel, resume deduplication, and
  policy enforcement for every generated step are not established.
- Gateway source includes optional OMEGA polling paths. This audit did not
  invoke them or inspect OMEGA runtime, source, credentials, or data.

## Trust boundaries and safety controls

- Browser identity is not accepted from arbitrary client message fields;
  gateway identity comes from its authenticated connection.
- The local helper and gateway use owner-only local credential files as
  documented in [RUNTIME_OPERATIONS.md](./RUNTIME_OPERATIONS.md).
- Gateway operation records use correlation IDs to reject replay with
  different content and persist lifecycle evidence.
- Skill execution has an explicit permission check, dispatch boundary, audit,
  and conservative handling of post-dispatch failures.
- Agent orchestrator/workflow dispatch is currently blocked; capability
  selection and model-generated plans do not convey execution authority.
- `PatchApplier` currently has no mutation path. Exact-patch approval and
  recoverable apply/rollback remain release gates, not active capabilities.
- Financial standalone construction is paper-only.
- Optional integration health must remain distinct from the health of its
  integration link.

## Architectural gaps to close

1. Establish a single, tested action-governance contract across task,
   autonomy, gateway, CLI, skill, direct file-manager, and financial
   execution entry points.
2. Define memory/history retention, deletion, export, and local file-permission
   guarantees.
3. Verify model, gateway, auth-helper, voice, and frontend behavior through
   authenticated end-to-end tests without conflating service liveness and
   capability readiness.
4. Add provenance and freshness contracts for research/knowledge and
   externally sourced telemetry.
5. Keep autonomous scheduling, retries, resource limits, and pause/recovery
   semantics explicit; one bounded cycle is not a durable autonomous-run
   controller.

## Key source areas

- Gateway and WebSocket lifecycle:
  `ultron/application/runtime/interface_gateway.py`
- Local service startup:
  `scripts/ultron_service_manager.py`,
  `ultron/application/runtime/service_entrypoint.py`,
  `start-ultron.sh`, and `stop-ultron.sh`
- Application wiring:
  `ultron/infrastructure/container.py`
- Task and operation execution:
  `ultron/application/task_service.py`,
  `ultron/executive/tasks/manager.py`,
  `ultron/executive/tasks/sqlite_queue.py`,
  `ultron/execution/executor.py`
- Bounded autonomy:
  `ultron/autonomy/loop.py` and `ultron/autonomy/policy.py`
- Conversation persistence:
  `ultron/conversation/manager.py` and
  `ultron/conversation/history/store.py`
- Paper-only financial construction:
  `ultron/financial/bootstrap.py`
