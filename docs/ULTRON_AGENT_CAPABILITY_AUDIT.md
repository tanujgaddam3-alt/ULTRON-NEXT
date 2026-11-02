# ULTRON Agent Capability Audit

## Scope and method

This audit follows the source-defined agent classes, registry, selectors,
workflow components, composition-root wiring, request handlers, tests, and
operator-console rendering. It does not infer runtime behavior from a class
name or declared capability. No services, model endpoints, financial actions,
or OMEGA integration were invoked.

The status below distinguishes a class that exists from an agent that performs
its advertised work. "Registered" means it is in the in-memory
`AgentRegistry`; it does not mean healthy, selected, or called for a user
request.

## Request-path findings

1. Authenticated `CHAT_MESSAGE` reaches
   `InterfaceGateway._handle_chat_message()`, builds conversation context,
   adds a registry-name snapshot, then calls
   `conversation.handle_chat_async()`. It does not call
   `AgentOrchestrator.dispatch()`.
2. `DISPATCH_COMMAND` is routed through the gateway's execution path and
   skills. `FileSkill` can invoke `CodingAgent` for generated file contents;
   that agent generates and statically validates text but does not write or
   execute the generated code itself. The file skill performs the eventual
   write through its file service.
3. `Container` registers planning, research, and security agents, and later
   registers coding. `AgentOrchestrator` and `AgentWorkflowExecutor` are
   constructed, but source search found no runtime call to
   `agent_orchestrator.dispatch()` or `workflow_executor.execute()`. Both
   dispatch surfaces now require an injected authorization callback and block
   by default; the application container does not configure one.
4. The autonomy container constructs `PrimeAgent()` with its default
   `PrimeCoordinator`, which owns a separate empty registry. The autonomy
   planner consumes the resulting goal text but constructs its own `Plan`;
   it does not execute the Prime workflow.
5. `PrimeExecutor` only reports candidate agents as assigned and returns
   `"completed"` without calling them. `WorkflowCoordinator` similarly emits
   synthetic completed step records. `PrimePipeline` is not wired into the
   composition root in the inspected source.
6. The operator-console agent page receives registry entries, renders a
   generic role, and explicitly states that per-agent status and capabilities
   are not published. This is honest about the available data, but it is not
   agent observability.

## Capability matrix

| Agent / component | Responsibility; actual inputs and outputs | Dependencies, tools, context, and permissions | Failure and verification evidence | Overlap, gap, improvement, acceptance, status |
|---|---|---|---|---|
| `BaseAgent` contract | Defines `info()`, `supports(AgentTask)`, and abstract `execute(AgentTask) -> AgentResult`. Tasks carry goal, requested capabilities, constraints, and context. | No tools, memory, or authorization are provided by the base contract. | No contract tests existed before this work. Capability matching previously accepted any one requested capability and treated capability-free agents as generic matches. | Now requires all requested capabilities; a generic task matches only an agent with at least one declared capability. Tested in `tests/agents/test_agent_routing.py`. Contract still has no standard timeout, cancellation, provenance, or workflow state fields. **Partial**. |
| `AgentRegistry` / `AgentOrchestrator` | Registry stores agents by name and discovers matches. Orchestrator ranks candidates and returns the selected agent's result for an `AgentTask`. | Uses an in-memory registry and `AgentCapabilityScorer`; no tool grants, approval context, persistent state, or agent health probe. | No focused tests for registry dispatch or orchestration were found. Exceptions from `agent.execute()` are not isolated by `AgentOrchestrator`. | Capability-selection behavior is improved through `BaseAgent.supports`; runtime call sites for `dispatch()` were not found. Registry registration is not health evidence. Add registry dispatch/error tests and wire it only behind the trusted boundary. **Constructed, not integrated**. |
| `planning` / `PlanningAgent` | Accepts `AgentTask`, previously returned a templated “Planning completed” string without making a plan. | No planner dependency, task store, memory, skills, or execution permission. | Previously returned success without planning evidence; no agent-specific tests were found. Now returns `success=False`, `status=blocked`, and declares no supported capabilities until an implementation is injected. | A real plan must include validated task IDs, dependencies, acceptance criteria, constraints, and outputs. Acceptance: invalid dependencies are rejected and no plan is marked complete without those fields. **Blocked / stub**. |
| `research` / `ResearchAgent` | Accepts `AgentTask`, previously returned a templated “Research completed” string without source retrieval. | No search provider, knowledge retriever, source record, freshness metadata, or memory boundary. | Previously returned success without evidence; no agent-specific tests were found. Now reports blocked and has no declared capabilities. | Requires permitted source adapters and provenance/freshness; claims must cite retrieved evidence. Acceptance: deterministic conflicting-source and unavailable-source fixtures. **Blocked / stub**. |
| `security` / `SecurityAgent` | Accepts `AgentTask`, previously returned a templated completion string without scanning or analyzing. | No analyzer, repository input, security-review integration, permission checker, or tool call. | Previously returned success without analysis evidence; no agent-specific tests were found. Now reports blocked and has no declared capabilities. | Must not imply a security review occurred until actual analysis and evidence are produced. Keep read-only by default. Acceptance: fixture findings and failure cases. **Blocked / stub**. |
| `coding` / `CodingAgent` | Receives a task goal and context; selects language, calls configured `llm_provider.generate`, validates output with `QualityChecker`, and performs at most two repairs by default. Returns generated source and validation metadata. | Depends on injected LLM provider, language selector, and quality checker. `FileSkill` explicitly binds it and invokes it for natural-language file generation. Generated code is not executed by this agent. No agent-specific memory. | `tests/coding/test_agent.py` covers generation, context, no-provider and provider errors, empty goals, repair success, and repair limit. Errors return failure; provider exceptions currently include their message in the result/log path. | Declared capabilities were broader than actual behavior; narrowed to `code_generation` and `code_validation`. It does not implement repository-wide coding, review, debugging, test execution, or deployment. Acceptance should include output-size/provider timeout, language-specific validation, safe write scope, and independent tests. **Implemented for bounded generation; partial**. |
| `PrimeAgent` / `PrimeCoordinator` | Turns command into `UserIntent` and a `Workflow` containing matching agent names and generic text steps. Default coordinator creates a new empty registry. | Optional orchestrator is stored but `process()` does not call it. No memory, execution permission, or persistence. | No tests found. It does not execute agents and the workflow has no task IDs, dependencies, criteria, or terminal evidence. | Overlaps with `AgentOrchestrator` and autonomy planning. Acceptance: use the actual registry, validate workflow structure, execute only through a trusted coordinator, and report real results. **Planning facade only; autonomy path disconnected**. |
| `PrimeExecutor` / `PrimePipeline` / `PrimeEvaluator` | `PrimeExecutor` labels names “assigned” and reports completion without execution. `PrimePipeline` delegates to a `WorkflowCoordinator`; evaluator scores success plus nonempty output. | No persistence, resource budgets, authorization, or independent verifier. Pipeline has no discovered composition-root call site. | No focused tests. Current result score does not verify correctness or evidence. | Overlaps with orchestration/workflow executor. Remove success-shaped status and use evidence-backed verification before wiring. Acceptance: execution records and independent criteria checks. **Disconnected / misleading completion behavior**. |
| `AgentWorkflowEngine` / `DependencyResolver` | Converts a list of agent names into sequential steps with hard-coded ordering and previous-step dependencies. | Uses static order map, not task/capability evidence. No shared context contract or persistence. | No tests found. | The executor now validates references/cycles and honors dependencies, but the engine still assumes every step depends on its immediate predecessor. Acceptance: dependencies arise from explicit task requirements and independent steps may remain independent. **Constructed, not integrated; partial**. |
| `AgentWorkflowExecutor` | Executes registered agents sequentially, passing step objective plus workflow context and collecting `AgentResult`s. | Requires an injected dispatch authorizer that must return literal `True`; absent, denied, or exceptional authorization blocks before `agent.execute()`. No trusted authorizer is configured in the application; there is also no deadline, budget, cancellation, or durable workflow record. | Tests cover dependency ordering, blocked dependents, independent steps, failures, unavailable agents, cycles, malformed references, workflow IDs, exception redaction, and authorization absent/denied/failed/allowed. It is constructed but no call site to `execute()` was found. | Fixed to avoid false `completed`, honor dependencies, fail closed at dispatch, and return explicit completed/failed/blocked/unavailable metadata with workflow/task/execution IDs. It still does not independently verify results or authorize tools beyond the injected callback. **Corrected component; not integrated**. |
| `WorkflowCoordinator` / monitor / recovery | Coordinator fabricates completed records; monitor stores appended entries in memory; recovery returns a retry suggestion. | No event source, persistence, authorization, retry limit, dedupe, or reconciliation evidence. | No tests found. | Retry advice is not safe execution authority; coordinator bypasses actual agent results. Acceptance: use only genuine execution records, bounded retries, and uncertainty reconciliation. **Stub / unsafe to trust for completion**. |
| Selectors, team, communication, performance | Multiple implementations exist: `AgentCapabilityScorer`, `AgentScorer`, two adaptive selectors, team builder/manager, communication bus/router, `AgentPerformanceMemory`, and `PerformanceStore`. Inputs are capabilities, tasks, or messages; outputs are selected names/scores, teams, messages, and in-memory records. | In-memory only; no workflow privacy scope, provenance, durable metrics, access controls, or selection-policy integration into runtime. | The single `test_agent_evolution.py` test records one success, sends one message, and checks a success rate; it does not test routing, privacy, scoring calibration, or reliability. | Substantial overlap in selectors/scorers and performance stores. No measured baseline exists; defaults such as average score `0.5` are not execution evidence. Consolidate only after defining a single supported runtime path. **Experimental / disconnected**. |
| `PersonalAssistantAgent` | Stores optional task/reminder/note/calendar/file/workflow services and exposes a status method. | Implements neither the required `BaseAgent.execute()` method nor a tool policy. No runtime wiring found. | No tests found; status output is not health evidence. | Cannot be instantiated as a concrete `BaseAgent` because `execute()` remains abstract. Acceptance: define a narrow contract and tested service calls before registration. **Disconnected / abstract**. |
| `VisionAgent` | Optional processor, detector, OCR, and memory; `analyze(image)` returns objects/text. | Dependencies are injected; no policy or provenance contract. | No tests or runtime use found. `execute()` is not implemented, so it remains abstract. | Not part of registry. Acceptance: validate image inputs, evidence/source metadata, privacy, and failure behavior. **Disconnected / abstract**. |
| `VoiceAgent` | Optional recognizer, wake detector, speaker, memory; `process(audio)` and `respond(text)`. | Separate from the active voice HTTP/TTS service; no registry use found. | No agent-specific tests or runtime call sites found. `execute()` is not implemented, so it remains abstract. | Do not mistake this wrapper for the active production voice path. **Disconnected / abstract**. |
| `ControlAgent` | `process(text)` parses a command, checks an injected permission component, and routes only `"allow"` decisions. | Requires parser, permission checker, and router; no registry or composition-root wiring found. | No focused tests or call sites found; permission behavior is unverified. `execute()` is not implemented, so it remains abstract. | Must be validated against the trusted execution policy before use. **Disconnected / abstract; unsafe to enable**. |
| `CognitionAgent` / `CognitionManager` | Optional metrics, reflection, optimization, and alignment collaborators; manager exposes `status()`. | Not a `BaseAgent`; no invocation from the agent registry found. `status()` reports online based on class presence rather than dependency readiness. | No agent-evaluation tests or runtime registry path found. | Overlaps with cognition, reflection, and self-improvement services. Status should distinguish unavailable collaborators. **Helper / not an orchestrated agent**. |
| `FinancialAgent` | Coordinates financial data, analysis, regime, risk, reasoning, decision, memory, optional strategy/backtest/controller components; returns staged analysis results. | Wired by `Container`/financial bootstrap and financial skill, outside `AgentRegistry`. No financial action was invoked in this audit. Earlier bootstrap tests establish paper-only construction for the standalone runtime; this audit does not re-verify order behavior. | Financial-specific tests exist elsewhere, but no agent-to-registry or approval equivalence was established here. | Keep simulation-only and behind the existing financial authorization path. Do not expose it as a generic agent tool. **Domain orchestrator; registry-disconnected**. |

The archived `voice_backup_...` copy and vendor files under staged voice
environments are not active ULTRON agent implementations and were excluded.

## Implemented in this workstream

- Capability matching now requires every requested capability. Tasks without
  explicit requirements only match agents that declare a nonempty capability
  set. `CodingAgent` now declares only code generation and local code
  validation, which match its actual implementation.
- Planning, research, and security stubs no longer advertise unsupported
  capabilities or return successful completion for work they do not perform.
  They return explicit blocked results.
- Workflow models now carry a workflow ID. The executor rejects duplicate,
  unknown, self-referential, and cyclic dependencies before dispatch; processes
  steps in dependency order; skips dependents after prerequisite failures;
  distinguishes partial, failed, blocked, and completed workflows; and
  attaches workflow, task, step, and execution IDs. Agent exception messages
  are not copied into results.
- Self-improvement patch application requires a strict exact-patch approval
  callback at the `PatchApplier` mutation boundary and a backup before file
  modifications. Neither production-container patch applier has an approval
  callback, so direct and manager-mediated mutation fail closed. This is a
  protective block, not an implemented operator-approval workflow.

## Tests and evidence

Focused run with workspace Python 3.14:

- `tests/agents/test_agent_routing.py`
- `tests/agents/test_workflow_executor.py`
- `tests/coding/test_agent.py`
- `ultron/agents/tests/test_agent_evolution.py`
- all tests in `tests/improvement/test_improvement_pipeline.py` except its
  container-instantiating pipeline smoke test
- **40 passed, 0 failed; 1 deselected**.

Pylance diagnostics and Python syntax checks reported no issues in changed
source and test files. One non-blocking `websockets.legacy` deprecation
warning appeared during pytest.

These are unit/component tests. No live model, browser, agent workflow,
financial operation, service, or external integration was exercised.

## Highest-priority remaining work

1. Wire one supported orchestration entry point into a real operator request
   path, with task decomposition and explicit dependencies; do not connect
   synthetic agents as if they were functioning specialists.
2. Integrate a trusted authorization provider with the agent dispatch
   callbacks. `AgentOrchestrator` and `AgentWorkflowExecutor` now fail closed
   when authorization is absent, denied, or cannot be verified, but the
   application container has no trusted provider and neither surface is on a
   user request path.
3. Implement or remove the planning, research, and security stubs. Research
   requires source provenance; security review requires actual analysis
   evidence; planning requires structured task/dependency validation.
4. Replace `PrimeExecutor`/`WorkflowCoordinator` success-shaped placeholders
   and require independent verification, persistence, cancellation, budgets,
   bounded retries, and duplicate-work protection.
5. Consolidate duplicate selectors/performance stores, then measure a
   deterministic baseline before using historical performance in routing.
6. Publish real per-agent availability/activity only after instrumentation
   exists. The current UI correctly reports registration without claiming
   health, but no per-agent activity source exists.
7. Connect a trusted exact-patch approval verifier to the patch-applier
   boundary before enabling mutation. Verify backup integrity, rollback, and
   test failure recovery in temporary workspaces.

This work does not establish general intelligence, production readiness, or
fully integrated multi-agent operation.
