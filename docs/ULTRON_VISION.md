# ULTRON Product Vision

## Purpose

ULTRON is a local-first operator assistant that helps a person understand
system state, organize goals, plan work, and carry out explicitly bounded
operations. It should make useful capabilities available through one coherent
interface without obscuring which model, service, data source, or permission
produced a result.

## Product principles

1. **Operator authority:** consequential, external, financial, or
   configuration-changing actions require the applicable authorization and
   confirmation. A generated plan is not itself permission to execute.
2. **Truthful status:** distinguish unavailable, stale, inferred, simulated,
   pending, failed, and verified information. Never substitute invented
   telemetry for missing data.
3. **Traceable work:** associate requests, plans, task attempts, operations,
   outcomes, and verification evidence with stable identifiers and durable
   records where appropriate.
4. **Bounded autonomy:** autonomous work proceeds in explicit, inspectable
   cycles with limits, policy checks, and a safe way to pause or recover.
5. **Privacy by design:** keep credentials out of browser storage, logs, and
   operation records; make persistence, retention, and deletion understandable
   to the operator.
6. **Graceful degradation:** a failed optional model, voice engine, or
   integration must not be represented as healthy or silently treated as an
   equivalent capability.
7. **Incremental evolution:** improve the existing console and service
   boundaries in small, testable slices; do not replace working systems merely
   to make the architecture look uniform.

## Intended experience

The operator should be able to:

- See current backend/service health and clearly labeled runtime telemetry.
- Ask questions and receive an explicit error when authentication, the model,
  or a required dependency is unavailable.
- Review proposed actions and their risk before approving execution.
- Inspect task and operation progress, including uncertain outcomes that need
  reconciliation rather than an automatic retry.
- Use voice as an optional presentation channel whose active engine, reference
  conditioning, and playback status are reported honestly.
- Keep financial activity in simulation unless separately and deliberately
  authorized; the current standalone financial bootstrap is paper-only.

## Success measures

Product progress should be measured by verified outcomes rather than by
component count:

- Authenticated end-to-end tests demonstrate the real operator workflow.
- Every consequential action has an identifiable policy decision, durable
  lifecycle, and independently checkable outcome.
- Missing or stale data remains visibly unavailable instead of appearing as a
  zero or a healthy status.
- Recovery preserves the original failure and surfaces any failure to restore
  task state.
- Memory has documented retention, export, and deletion behavior before it is
  treated as a mature personal assistant feature.
- Voice identity, naturalness, intelligibility, and latency are evaluated
  separately using real, approved audio.

## Non-goals and boundaries

- ULTRON does not act as an unsupervised authority over the operator's machine,
  accounts, or finances.
- A planned action, model response, or successful HTTP response is not proof
  that an action completed safely.
- Optional integrations are not treated as available until their real
  authenticated health and data paths have been verified.
- The separate OMEGA project and its runtime/data are outside the scope of
  this product vision and this workspace audit.
