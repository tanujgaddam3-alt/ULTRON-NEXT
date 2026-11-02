# CRE Phase 4.2 — Integration Readiness

## Status

**Stage 2.1 verdict: PARTIAL.**

Schema ordering, runtime gating, isolated migration behavior, worker recovery,
atomic claims, exact-identity cache reuse, and dry-run retention checks are
implemented and tested. Destructive retention is intentionally unavailable.
The configured application database was not inspected, so its compatibility
with the new runtime gate is unknown. The visual-analysis worker remains
unwired, as required for this stage.

## Original mismatch and root cause

`ContentStore.SCHEMA_VERSION` is the base content schema (v7), not the latest
runtime foundation. Publishing-attempt and private-render capabilities are
explicitly layered at v9 and v10. The visual-analysis extension is separately
versioned and requires the v10 private-render foundation.

`status.py` compared the database version for exact equality with v7. This
rejected a valid v10 database even though `ContentStore` recognizes v10 and the
visual extension requires it. Conversely, an absent database previously
passed preflight and could be created at v7 during runtime startup, below the
required private-render foundation.

## Implemented correction and migration contract

- `SCHEMA_VERSION` remains the base schema version; the publishing-attempt and
  private-render constants continue to describe their explicit migration
  layers. `RUNTIME_SCHEMA_VERSION` names the v10 runtime foundation.
- Runtime preflight is read-only and requires an existing database at exactly
  v10. Missing, malformed, older, intermediate, or newer schema states fail
  closed before runtime construction. Startup does not run the explicit v7→v9,
  v9→v10, or visual-analysis migrations.
- Fresh isolated databases begin at v7. Explicit order is:
  1. `migrate_publishing_attempt_schema()` — v7 or transitional v8 to v9.
  2. `migrate_private_render_operation_schema()` — v9 to v10.
  3. `migrate_visual_analysis_schema()` — add the visual extension at v2, or
     explicitly upgrade visual-extension v1 to v2.
- Migration steps run in SQLite transactions, verify their resulting schema,
  and are idempotent at their supported target version. Unsupported steps are
  rejected. The visual v1→v2 migration adds separate request and cache
  fingerprints and updates rows transactionally.
- Missing or malformed metadata is rejected rather than inferred or silently
  repaired. Migration interruption tests confirm DDL and version changes roll
  back together.

### Compatibility matrix

| Database state | Runtime startup | Explicit migration/action |
|---|---|---|
| Database absent | Rejected; no implicit database creation | Provision a disposable/base v7 database, then migrate explicitly |
| Base v7 | Rejected by runtime preflight | Publishing migration may advance to v9 |
| Transitional v8 | Rejected by runtime preflight | Publishing migration may complete v8→v9 |
| Publishing v9 | Rejected by runtime preflight | Private-render migration advances to v10 |
| Runtime v10 | Accepted by preflight | Visual-analysis extension remains a separate explicit migration |
| Visual extension v1 on main v10 | Runtime preflight accepts main v10; visual APIs remain unavailable until extension migration | Explicit visual v1→v2 upgrade |
| Unsupported old, malformed, or future version | Rejected | No automatic downgrade or repair |

This work did not inspect the configured database. The matrix describes code
behavior, not the current application database state.

## Lifecycle recovery and worker boundary

- A worker must call `start()` before claim, completion, or failure operations.
- Startup takes an exclusive `flock` lease adjacent to the database. A second
  worker process on the same host cannot acquire it. Store close releases it;
  process exit also releases the kernel lock.
- While holding the lease, startup reconciles all persisted `analyzing`
  operations to `reconciliation_required` before setting the claim-ready flag.
  Reconciliation is performed once per `ContentStore` instance.
- Reconciliation does not rerun inference. It preserves the operation,
  request, prior events, and failure/reconciliation state for diagnosis.
- Claims use an atomic state-conditional update inside a write transaction;
  competing claim attempts yield one successful claimant. Completion and
  failure require the matching analysis ID, owner, and expected `analyzing`
  state. State transitions append audit events.
- This is **not exactly-once inference**. A process may finish inference and
  exit before persisting its result; that operation requires reconciliation.
  Multi-host coordination is unsupported because the lease is a local file
  lock. The worker is not wired into runtime startup or exposed through the
  gateway.

## Cache correctness

Idempotency and reusable-result identity are separate:

- The idempotency fingerprint includes the idempotency key and request payload,
  excluding only the new operation ID. Replays return the original operation;
  a changed payload with that key conflicts.
- The cache fingerprint excludes operation and idempotency IDs, but includes
  owner, project, asset ID and SHA-256, provider, immutable model digest,
  prompt/schema/sampler versions, ordered timestamps, context/output limits,
  and image-dimension limit.
- Model identity must be a SHA-256 digest (with optional `sha256:` prefix),
  avoiding reuse through mutable model tags.
- Only a completed result with an exact owner-scoped fingerprint can be reused.
  A cache hit creates a new completed operation ID, copies the validated result
  with its original source/frame provenance, stores the source operation ID,
  and appends a cache-hit event. It does not claim work or invoke inference.
- Changed source identity/content or any included model/configuration input
  invalidates the cache. Version owners must increment prompt, schema, or
  sampler versions when those behaviors change.

## Retention and cleanup

`VisualAnalysisRetentionPolicy` requires explicit owner-scoped cutoffs for each
terminal state. `plan_visual_analysis_retention()` is dry-run only, uses strict
“older than” boundary comparisons, excludes active work, and separates
audit-protected, cache-referenced, and unreferenced candidates. It performs no
deletion.

**Destructive cleanup: BLOCKED / NOT IMPLEMENTED.** The product retention
period, legal/audit retention requirements, and evidence/artifact lifecycle are
not defined. The current subsystem stores typed results in SQLite and has
append-only transition events; no visual artifact deletion path is introduced.
Records with audit or cache references are never reported as candidates.

## Tests and diagnostics

Final command executed:

```text
/home/ultron/Projects/ULTRON_INTERFACE_FINAL/.venv/bin/python -m pytest -q tests/application/test_content_visual_analysis.py tests/application/test_content_visual_analysis_schema.py tests/application/test_content_visual_intelligence.py tests/application/test_content_highlights.py tests/application/test_content_intelligence.py tests/application/test_content_lifecycle.py tests/application/test_content_rendering.py tests/application/test_content_private_render_operations.py tests/application/test_content_publishing_attempts.py tests/application/test_content_publishing.py tests/application/test_content_rights.py tests/application/test_content_processing.py tests/application/test_content_script_preview.py tests/application/test_content_status.py
```

**Result: exit 0; 350 passed; 0 failed; 0 skipped.**

The test set covers visual analysis, visual intelligence, highlight ranking,
content lifecycle, rendering, private render operations, publishing attempts,
publishing, rights, processing, script preview, runtime schema gating, and
isolated schema migration. Schema tests use only `tmp_path` SQLite databases.
They cover fresh v7 initialization, ordered/idempotent upgrades, unsupported
versions, malformed/missing metadata, interrupted DDL rollback, content and
render-operation record preservation, extension indexes/triggers, and absence
of duplicate schema objects.

The focused lifecycle/schema command was also run:

```text
/home/ultron/Projects/ULTRON_INTERFACE_FINAL/.venv/bin/python -m pytest -q tests/application/test_content_visual_analysis.py tests/application/test_content_visual_analysis_schema.py
```

**Result: exit 0; 37 passed; 0 failed; 0 skipped.**

During implementation, one full regression run initially reported 14 failures
because the schema verifier's required-object query omitted two newly added
triggers; a focused run then exposed one legacy-fixture setup issue. Both were
corrected. The final full command above and focused command both passed.

Additional verification:

- `/home/ultron/Projects/ULTRON_INTERFACE_FINAL/.venv/bin/python -m py_compile ultron/application/content/store.py ultron/application/content/status.py ultron/application/content/visual_analysis.py ultron/domain/content_visual_analysis.py tests/application/test_content_visual_analysis.py tests/application/test_content_visual_analysis_schema.py tests/application/test_content_status.py` — **exit 0**.
- Pylance Problems diagnostics on all changed Python files: **no errors**.
- `ruff --version` — **exit 127** (`ruff: command not found`); no tool was installed.

No test result here establishes the current configured database version or
live runtime behavior.

## Files changed in this task

- `ultron/application/content/store.py`
- `ultron/application/content/status.py`
- `ultron/application/content/visual_analysis.py`
- `ultron/domain/content_visual_analysis.py`
- `tests/application/test_content_visual_analysis.py`
- `tests/application/test_content_visual_analysis_schema.py`
- `tests/application/test_content_status.py`
- `docs/CRE_PHASE4_IMPLEMENTATION_ROADMAP.md`
- `docs/CRE_PHASE4_2_INTEGRATION_READINESS.md`

## Remaining blockers and safety record

- Git status could not be collected: the workspace path has no `.git`
  directory, so `git status` fails. Changes were kept surgical and existing
  content was preserved, but a repository-level diff inventory could not be
  independently verified.
- The configured application database was not opened, inspected, migrated, or
  backed up. Its version remains unknown; no migration was attempted against
  it.
- The visual-analysis lifecycle remains unwired to runtime workers, planner,
  gateway, and UI. Stage 3 was not started.
- No application service was restarted or deployed. Ollama was not restarted
  or reconfigured. No model was installed.
- `.env.financial`, private media, and financial data were not accessed.
