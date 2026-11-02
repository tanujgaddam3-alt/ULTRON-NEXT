# CRE Phase 4.2.1 — Independent Verification

## Executive verdict

**OVERALL: PARTIAL — the disposable-clone migration rehearsal passed: v7→v9→v10,
integrity and foreign-key checks, and runtime preflight all succeeded. The
configured database remains at v7 and is still incompatible with the v10
runtime. No migration was run against it; production migration requires
separate explicit approval.**

Most positive lifecycle claims are supported by isolated tests: explicit
migration sequencing, atomic rollback, cross-process lease exclusion, lease
reacquisition after process exit, operation-conditional claiming, and safe
dry-run-only retention. The required regression suites also pass.

The initial audit reproduced two schema-integrity gaps on temporary databases:

1. Runtime preflight accepts a structurally malformed `content_schema_meta`
   table if it happens to contain `(singleton=1, schema_version=10)`.
2. The visual schema verifier accepts a no-op trigger when it has the required
   trigger name. Re-running the visual migration reports success, despite the
   identity immutability guard not functioning.

The follow-up changes validate the canonical v10 metadata table definition,
column constraints and exact singleton/version row, and compare every required
visual-analysis trigger definition with the canonical definition. Regression
tests reject malformed metadata, missing triggers, and same-named ineffective
triggers; the valid identity trigger is also shown to block identity mutation.

The read-only WAL sidecar behavior remains: `mode=ro` can use or create
SQLite `-shm`/`-wal` sidecars. The approved inspection found the configured
database uses WAL mode; sidecars existed afterward, but their pre-inspection
state was not recorded, so creation cannot be attributed to this inspection.
During the later online-backup rehearsal, the main-file and WAL byte hashes
remained unchanged; the SHM byte hash also remained unchanged, but its
modification time advanced during SQLite read-only access. Avoiding such
sidecar access by asserting immutable storage could weaken consistency when a
WAL writer is active.

The continuation also added nested transaction savepoints, enforced one
worker object per store/lease and prevented store closure with an active
analysis claim. Cache reuse is disabled until provider and analysis
implementation identity can be independently attested. A loopback gateway
integration test started the real `serve_interface` path against a temporary
migrated v10 database and returned authenticated CRE status. It did not start
the configured/live service.

## Evidence labels

- **Direct test/probe:** executed against test fixtures or disposable
  `TemporaryDirectory` databases/processes during this audit.
- **Code inspection:** read-only findings from the listed implementation.
- **NOT VERIFIED:** unavailable evidence or a check that could not safely be
  attributed.

## 1. Change inventory and workspace integrity

### Git and baseline

**Status: PARTIAL / NOT VERIFIED.**

- **Direct inspection:** no `.git` directory or file was found at the workspace
  root or its parent directories (`/home/ultron/Projects`,
  `/home/ultron`, `/home`); a bounded search of the workspace found none.
- `GIT_DIR`, `GIT_WORK_TREE`, and `GIT_COMMON_DIR` were unset.
- `git rev-parse --show-toplevel` and `git status --short --branch` both failed
  with “not a git repository.”
- No alternative repository metadata location was discovered in the checked
  paths. A complete search of all user/system Git configuration was not
  performed.
- **Conclusion:** there is no trustworthy before/after Git baseline here.
  Complete change attribution, dirty-file status, and untracked-file
  determination are **NOT VERIFIED**.

### Nine files listed by the prior report

The implementation report listed these nine paths:

1. `ultron/application/content/store.py`
2. `ultron/application/content/status.py`
3. `ultron/application/content/visual_analysis.py`
4. `ultron/domain/content_visual_analysis.py`
5. `tests/application/test_content_visual_analysis.py`
6. `tests/application/test_content_visual_analysis_schema.py`
7. `tests/application/test_content_status.py`
8. `docs/CRE_PHASE4_IMPLEMENTATION_ROADMAP.md`
9. `docs/CRE_PHASE4_2_INTEGRATION_READINESS.md`

Those paths currently exist. The new report created for this audit is a tenth
known path relative to that reported list:
`docs/CRE_PHASE4_2_1_INDEPENDENT_VERIFICATION.md`.

Whether any other files changed during the earlier implementation is
**NOT VERIFIED** because Git metadata/baseline is absent. A bounded filesystem
inventory found existing `__pycache__`/`.pyc`, `.pytest_cache`, `.bak`, and
`.atlas-cache.json` items. Their creation time and attribution cannot be
reliably established here; none were deleted or treated as implementation
changes. The mandated test command was run with bytecode and pytest cache
writing disabled.

## 2. Schema and migration safety

### Version authority and entry points

**Status: PARTIAL; unsafe cache reuse disabled.**

**Code inspection:**

- `ContentStore.SCHEMA_VERSION` is base schema v7.
- `PUBLISHING_ATTEMPT_SCHEMA_VERSION` is v9.
- `PRIVATE_RENDER_OPERATION_SCHEMA_VERSION` and
  `RUNTIME_SCHEMA_VERSION` are v10.
- `VISUAL_ANALYSIS_SCHEMA_VERSION` is extension v2, separately stored in
  `content_visual_analysis_schema_meta`.
- Runtime preflight in `status.py` requires exactly `RUNTIME_SCHEMA_VERSION`.
- Explicit order is publishing attempts v7/v8→v9, private render operations
  v9→v10, then visual extension creation or v1→v2 migration.
- These explicit migration methods use the store transaction, and the
  interrupted-DDL test demonstrates rollback of DDL and version changes.
- The legacy `ContentStore` initializer still contains automatic migrations
  from base versions 1–6. The current runtime preflight rejects those
  versions before runtime construction, but the general-purpose store
  constructor itself is not purely non-migrating for those legacy inputs.

**Direct tests:** supported migration ordering and repeat calls passed.
Temporary tests preserved a project/source relationship and a synthetic
private-render operation/event record across the visual migration. Foreign-key
and integrity checks passed. Required visual tables, index, and trigger names
occurred once after the tested normal migrations. No existing content or
render-operation table is dropped/recreated by the v7→v9→v10 path. The
visual v1→v2 path adds columns and replaces only its extension metadata table
inside a transaction.

### Visual trigger definition verification

**Initial audit: FAIL. Follow-up: PASS for required trigger definitions.**

`_verify_visual_analysis_schema()` now compares normalized `sqlite_master.sql`
for each required trigger against the canonical definitions used to create
them. A missing, altered, or same-named no-op definition is rejected; the
migration no longer treats `CREATE ... IF NOT EXISTS` as proof that an
existing object is valid.

**Follow-up tests:** the normal identity trigger rejects an identity-changing
UPDATE with `sqlite3.IntegrityError`; each of the five required triggers was
replaced in turn with a same-named no-op and migration rejected it; a missing
identity trigger was also rejected. These tests run against temporary SQLite
databases. Index SQL and table/foreign-key/uniqueness/check definitions remain
outside this narrowly targeted trigger-verification fix.

### Compatibility and malformed states

**Status: PARTIAL.**

- **Direct tests:** migration-order rejection, v7/v8/v9/v10 handling, older
  and future version rejection, missing/malformed version columns and rows,
  repeat migration, and synthetic interrupted migration rollback passed.
- **Initial direct probe:** a malformed metadata table defined only as
  `content_schema_meta(singleton, schema_version)` with row `(1, 10)` passed
  `_verify_existing_database()`.
- **Follow-up tests: PASS:** runtime preflight now requires the canonical
  metadata table DDL, exact `PRAGMA table_info` shape, exactly one row with
  integer `singleton=1`, and integer `schema_version=10`. Tests reject
  missing constraints, a missing primary key, extra columns, duplicate rows,
  and text-valued version metadata; the supported v10 path still passes.
  Disposable-file bytes and schema objects are unchanged by rejected preflight.
- **NOT VERIFIED:** exhaustively testing preservation of every historical
  content table, existing index definition, and constraint for every supported
  legacy version is beyond the observed focused tests. The configured database
  was not read during those tests; its later approved read-only inspection is
  recorded in section 12.

### Startup schema mismatch and nested transaction error

**Status: FAIL for current-runtime compatibility; database integrity checks
PASS.**

**Code inspection:** `service_entrypoint.py` sets `ULTRON_CRE_DATABASE` to
`<repository>/data/ultron_cre.db` by default. The gateway rejects a configured
path outside that dedicated default. Runtime preflight requires schema v10
exactly and fails closed before `ContentStore` construction; it does not
migrate. The approved read-only inspection of
`/home/ultron/Projects/ULTRON_INTERFACE_FINAL/data/ultron_cre.db` found one
canonical `content_schema_meta` row with integer singleton 1 and integer
schema version 7. The database therefore does not meet the current v10 runtime
gate. The earlier schema-mismatch log is consistent with this result. The
inspection did not establish the running service's environment; the launcher
source selects the code-defined default unless overridden, and the gateway
rejects a non-default CRE path.

`ContentStore._transaction()` previously issued `BEGIN IMMEDIATE` every time,
so entering it while its connection was already in a transaction reproducibly
raised SQLite's “cannot start a transaction within a transaction”. It now
uses uniquely named savepoints for nested scopes. A disposable-database
regression proves inner rollback preserves outer work and outer rollback
reverts a released inner savepoint. The old gateway traceback is unavailable;
therefore this source defect is fixed, but its presence on that exact
historical gateway call path is **NOT VERIFIED**. The schema mismatch and
unsupported nested transaction behavior are separate failure modes.

## 3. Worker lease and recovery safety

### Lease behavior

**Status: PARTIAL.**

**Code inspection:** lease uses `flock(LOCK_EX | LOCK_NB)` on a stable,
database-adjacent lock file; no PID is written or used. `O_NOFOLLOW` is used
where available. Lease release unlocks and closes the exact file descriptor
and does not unlink the lock path.

**Direct disposable-process probe:**

- A second process using a separate `ContentStore` could not acquire the lease
  while the first held it.
- The lock file remained after an owner process exited without closing its
  store.
- A new process then acquired the same lock successfully. A stale regular lock
  file therefore does not permanently block progress; kernel lock ownership,
  not file existence, determines liveness.
- PID reuse cannot misidentify a lease owner because PIDs are not used.

The store now reserves its worker lease for one worker object. A second worker
object on the same store is rejected. Claim/complete/fail transitions require
the owning token. `stop()` refuses to release the lease while a claim is
active, and `ContentStore.close()` refuses while any claim is active.

### Recovery and operation transitions

**Status: PASS for exclusive worker-object and process ownership, active-claim
closure prevention, and exercised state transitions; PARTIAL for broader
runtime lifecycle.**

- `start()` calls `prepare_visual_analysis_worker()`; the store refuses
  claim/complete/fail until recovery completes.
- Recovery conditionally changes only `analyzing` rows to
  `reconciliation_required`, appending an event. Repeating recovery finds no
  further matching rows.
- Atomic state-conditional claim tests yield one winner for concurrent claims
  on one operation.
- A subprocess test exits uncleanly after claiming; the next process acquires
  the OS lease and reconciles the interrupted operation rather than rerunning
  inference.
- Completion validates the target owner/operation, expected `analyzing` state,
  source digest, frame timestamps, and model/provider identity. Failure
  transitions also use an owner and state condition.
- Completed rows are not selected by recovery.
- No automatic inference rerun is present.
- **NOT VERIFIED:** no end-to-end worker execution was run because the
  lifecycle is not runtime-wired and this is an audit-only task.
- **Limitation:** the worker API still does not own or join a provider
  inference thread. The active claim guard prevents graceful `stop()`/store
  close from releasing the lease while claimed inference may be in flight, but
  abrupt process death remains reconciled as uncertain work, not exactly-once.
- **Conclusion:** exactly-once inference is not guaranteed and is not claimed.

## 4. Cache isolation and correctness

**Status: PARTIAL.**

**Code inspection:** cache fingerprint covers request fields other than
operation and idempotency IDs, including owner, project, asset ID/SHA-256,
provider/model/version, prompt/schema/sampler version strings, ordered
timestamps, and configured context/output/image limits. Request idempotency
includes the idempotency key and remains distinct from cache identity.

**Direct tests:** fingerprint invalidation tests cover changed owner/source
identifiers, source digest, provider/model/digest, prompt/schema/sampler
version, timestamps, and inference limits. Same-key replay/conflict behavior
remains covered. New requests are always queued and do not inherit completed
results; a regression test verifies a matching completed result is not reused.

**Unresolved identity limitations:**

- A model version is required to look like a SHA-256 digest, but the request
  model digest is not independently authenticated by the lifecycle store. The
  Ollama adapter can report an installed digest, but no runtime wiring binds
  each persisted request to that adapter result.
- Prompt and sampler identity are caller-supplied version labels. The cache
  fingerprint does not hash the actual prompt text, adapter options, or
  sampler implementation. If implementation changes without a corresponding
  version-label change, a stale result could be reused.
- The same obligation applies to any new provider configuration or analysis
  input that materially affects output but is not represented in
  `VisualAnalysisRequest`.
- **NOT VERIFIED:** actual output equivalence for identical fingerprints is
  not established by persistence tests; no new model inference was performed.

Because model/provider/prompt/sampler identities are not bound to independently
verified execution evidence, cache-hit reuse has been disabled. It must remain
disabled until the provider-reported immutable digest and analysis
implementation/configuration identity are bound at the execution boundary.

## 5. Runtime preflight safety

**Status: PASS for metadata structure/value gating and database-byte
read-only behavior; PARTIAL for filesystem side effects.**

**Direct tests/probes against temporary paths:**

- Missing database: rejected, and the missing path remained absent.
- Supported v10 database: accepted.
- Older/unsupported version: rejected without migration.
- Malformed metadata definitions and values: rejected by the follow-up
  regression cases described in section 2.
- A clean supported WAL database had identical main-file SHA-256 before and
  after preflight, and no schema metadata changes were observed.
- However, preflight created `-shm` and zero-byte `-wal` sidecar files when
  opening that clean database read-only. Thus schema/data remained unchanged,
  but filesystem side effects occur.

The preflight uses SQLite `mode=ro` and does not call migration methods. It
does not repair or mutate the main database. WAL sidecar creation is not
resolved: using SQLite `immutable=1` would make an unsafe assumption about
concurrent writes and WAL consistency. No startup or metadata query was run
against the configured path.

## 6. Retention safety

**Status: PASS for dry-run-only behavior; deletion NOT APPLICABLE.**

**Code inspection and direct tests:**

- No deletion method or destructive cleanup path was found in the visual
  lifecycle.
- Retention requires explicit owner/cutoff policy, filters by owner, uses
  deterministic ID ordering, excludes `queued`/`analyzing`, and uses a strict
  older-than boundary.
- Records with audit events or cache dependents are reported as protected, not
  candidates. Normal lifecycle records have events, so the planner is
  deliberately conservative.
- Repeated planning is read-only and deterministic. The test confirmed the
  cutoff boundary is not treated as expired and the database row remains
  present.
- **Limitation:** because no deletion is implemented and the product retention
  policy is undefined, this is a planning report only—not operational cleanup.

## 7. Initial independent regression verification

**Command executed, exit code 0:**

```text
PYTHONDONTWRITEBYTECODE=1 /home/ultron/Projects/ULTRON_INTERFACE_FINAL/.venv/bin/python -m pytest -p no:cacheprovider -q tests/application/test_content_visual_analysis.py tests/application/test_content_visual_analysis_schema.py tests/application/test_content_status.py tests/application/test_content_visual_intelligence.py tests/application/test_content_highlights.py tests/application/test_content_intelligence.py tests/application/test_content_lifecycle.py tests/application/test_content_rendering.py tests/application/test_content_private_render_operations.py tests/application/test_content_publishing_attempts.py tests/application/test_content_publishing.py tests/application/test_content_rights.py tests/application/test_content_processing.py tests/application/test_content_script_preview.py
```

**Result: 350 passed, 0 failed, 0 skipped.**

This was the baseline result before the targeted follow-up changes.

## 8. Findings and unresolved risks

| ID | Status | Finding |
|---|---|---|
| A-1 | PASS (follow-up) | All required visual-analysis triggers are checked against canonical SQL; tests reject missing and same-named ineffective triggers, and the identity guard is exercised. Index and table-constraint definitions are not part of this targeted trigger fix. |
| A-2 | PASS (follow-up) | Runtime preflight checks canonical v10 metadata DDL, exact columns, singleton cardinality/value, and integer v10 value; malformed fixtures are rejected without main-file changes. |
| A-3 | PARTIAL | SQLite read-only preflight may create `-shm`/`-wal` sidecars on a clean WAL database. Avoiding them safely with concurrent WAL writers was not established; main database bytes remain unchanged. |
| A-4 | PARTIAL | Worker-object ownership and graceful active-claim closure are now enforced; no provider inference thread is owned/joined, and abrupt exit remains uncertain work requiring reconciliation. |
| A-5 | PARTIAL / MITIGATED | Provider/model/prompt/sampler implementation identity is not independently attested. Cache reuse is disabled, preventing stale cache hits pending a trusted identity binding. |
| A-6 | NOT VERIFIED | Complete file-change attribution, untracked-file inventory, and implementation-generated artifact attribution are unavailable without a Git baseline. |
| A-7 | FAIL | Approved read-only inspection found configured schema v7; current runtime requires v10. Integrity and foreign-key checks passed, but runtime preflight will reject the database. |
| A-8 | PASS (source fix; historical path unverified) | Nested `_transaction()` calls now use savepoints; nested rollback/commit boundaries pass disposable-database regression tests. Exact historical gateway traceback is unavailable. |
| A-9 | PASS (disposable gateway) | Real loopback `serve_interface` startup, authentication, and CRE status succeeded with a temporary v10 database; configured/live service startup remains NOT VERIFIED. |

The schema integrity defects and generic nested-transaction defect have
regression coverage. WAL sidecars remain a documented operational limitation.
The worker and cache risks are reduced by exclusive worker ownership/guards
and disabling reuse, but provider-thread lifecycle and trusted cache identity
remain incomplete.

## 9. Conditions before runtime integration

Do not integrate the visual-analysis lifecycle until a separately authorized
change and verification establishes all remaining prerequisites:

1. Decide whether read-only preflight sidecars are acceptable in the target
   deployment filesystem. Do not use immutable mode without proving
   concurrent-WAL safety.
2. The approved read-only inspection found schema v7. The disposable-clone
   rehearsal of v7→v9→v10, including backup verification, data-count
   comparison, integrity, foreign-key, and runtime-preflight checks, is
   recorded in section 13. The configured database remains v7. Production
   migration requires separate explicit approval and an approved maintenance
   window. Retain a protected v7 backup and a verified v9 checkpoint for
   rollback; do not add the visual extension unless separately required. No
   production migration step has run.
3. Before enabling cache reuse, bind provider-reported immutable model digest
   and prompt/sampler/config implementation identities to each execution.
4. If a real inference worker is wired, require its task/thread to stop and
   join before releasing the process lease; current close guards prevent
   graceful release while claimed work remains active.

Planner, gateway, UI, and runtime worker integration remains outside this
audit.

## 10. Safety restrictions respected

- The configured application database was opened read-only for the approved
  schema/integrity inspection and project-supported online backup. Its main
  database and WAL byte hashes remained unchanged. SQLite updated the `-shm`
  sidecar modification time during read-only access, although its byte hash
  remained unchanged; this sidecar metadata touch is disclosed in section 13.
  The configured database was not migrated, replaced, or deleted.
- `.env.financial`, credentials, financial data, and private media were not
  accessed.
- No service was restarted; no Ollama configuration/model was changed.
- No package was installed and no application startup against the configured
  application database or production configuration was run; runtime startup
  regression coverage used a patched temporary database path only.
- Tests and probes used the repository's isolated `tmp_path` fixtures or
  automatically cleaned `TemporaryDirectory` SQLite databases and child
  processes.
- No services were restarted or deployed; no model, package, trading, or
  financial-execution configuration was changed.
- A prior session reported accidental credential exposure from an environment
  file. Those credentials should be treated as compromised and rotated; this
  task did not inspect or reproduce them.
- All new regression tests used disposable `tmp_path` SQLite databases.
- Git metadata remains absent, so complete attribution of workspace changes
  and untracked files cannot be established.

## 11. Continuation verification

### Files touched in this continuation

- `ultron/application/content/store.py`
- `ultron/application/content/visual_analysis.py`
- `tests/application/test_content_visual_analysis.py`
- `tests/application/test_content_visual_analysis_schema.py`
- `tests/interface/test_interface_authentication.py`
- `docs/CRE_PHASE4_2_1_INDEPENDENT_VERIFICATION.md`

These are the files edited during this continuation. There is still no Git
metadata, so this is not a complete workspace change inventory.

### Focused CRE regression command

```text
PYTHONDONTWRITEBYTECODE=1 /home/ultron/Projects/ULTRON_INTERFACE_FINAL/.venv/bin/python -m pytest -p no:cacheprovider -q tests/application/test_content_visual_analysis.py tests/application/test_content_visual_analysis_schema.py tests/application/test_content_status.py
```

**Result: 57 passed, 0 failed, 0 skipped.**

### Isolated gateway startup test

```text
PYTHONDONTWRITEBYTECODE=1 /home/ultron/Projects/ULTRON_INTERFACE_FINAL/.venv/bin/python -m pytest -p no:cacheprovider -q tests/interface/test_interface_authentication.py -k gateway_starts_cre_runtime_against_isolated_v10_database
```

**Result: 1 passed, 11 deselected.**

### Combined CRE and gateway regression command

```text
PYTHONDONTWRITEBYTECODE=1 /home/ultron/Projects/ULTRON_INTERFACE_FINAL/.venv/bin/python -m pytest -p no:cacheprovider -q tests/application/test_content_visual_analysis.py tests/application/test_content_visual_analysis_schema.py tests/application/test_content_status.py tests/application/test_content_visual_intelligence.py tests/application/test_content_highlights.py tests/application/test_content_intelligence.py tests/application/test_content_lifecycle.py tests/application/test_content_rendering.py tests/application/test_content_private_render_operations.py tests/application/test_content_publishing_attempts.py tests/application/test_content_publishing.py tests/application/test_content_rights.py tests/application/test_content_processing.py tests/application/test_content_script_preview.py tests/interface/test_interface_authentication.py
```

**Result: 376 passed, 0 failed, 0 skipped; exit code 0.**

The controlled gateway test used a temporary v10 database, temporary operation
database, generated test credential, and loopback listener. It authenticated a
WebSocket and received `CRE_STATUS_RESULT` with `success=true` and
`status=AVAILABLE`. It did not use or inspect the configured application
database and is not evidence that the actual configured gateway service
started.

### Syntax and static diagnostics

- Pylance syntax checks: `store.py`, `status.py`, and
  `test_content_visual_analysis_schema.py` each reported no syntax errors.
- Pylance Problems diagnostics for the same three Python files: no errors
  found.

### Revised acceptance

The targeted schema-integrity defects and nested transaction handling are
**PASS** on disposable databases. Worker-object exclusivity and active-claim
guards are tested; cache reuse is disabled pending trusted identity evidence.
At this point in the prior continuation, the verdict was **FAIL for
configured-database compatibility**: schema v7 failed the required v10 runtime
preflight. Section 13 records the later clone-only rehearsal. It does not
change the configured database's version or verify the configured gateway.
WAL sidecar behavior and provider-thread shutdown/identity binding remain
limitations. The configured gateway/live service was not started or tested.
Do not begin financial/trading integration or production CRE integration.

## 12. Approved configured-database read-only inspection

### Configuration and scope

**Code inspection:** `service_entrypoint.py` uses
`<repository>/data/ultron_cre.db` as its default. `interface_gateway.py`
rejects a configured CRE path other than that default. The exact path inspected
was `/home/ultron/Projects/ULTRON_INTERFACE_FINAL/data/ultron_cre.db`.
`ULTRON_CRE_DATABASE` was unset in the inspection command environment; no
environment values were printed. The environment of any already-running
service was not inspected, so its active override state is **NOT VERIFIED**.
No `.env` file was opened.

The operator explicitly approved read-only checks with possible WAL sidecars.
The connection used SQLite `-readonly` and URI `mode=ro`, plus
`PRAGMA query_only=ON`. The checks read the SQLite journal mode, user version,
content schema metadata definition and row, visual metadata table presence,
database pages for `integrity_check`, and foreign-key relationships for
`foreign_key_check`. They did not select content, asset, render, credential,
private-media, or financial records; run migrations; write schema/data; or
start the gateway.

### Commands and evidence

Inspection run window: **2026-11-01 06:41–06:42 UTC**. The pre-check and
post-check file metadata samples were at 06:41:15Z and 06:42:41Z.

```sh
sqlite3 -readonly -batch -noheader \
  'file:/home/ultron/Projects/ULTRON_INTERFACE_FINAL/data/ultron_cre.db?mode=ro' <<'SQL'
.timeout 5000
PRAGMA query_only = ON;
SELECT 'journal_mode=' || journal_mode FROM pragma_journal_mode;
SELECT 'user_version=' || user_version FROM pragma_user_version;
SELECT 'meta_table_sql=' || sql FROM sqlite_master
  WHERE type='table' AND name='content_schema_meta';
SELECT 'meta_row_count=' || count(*) FROM content_schema_meta;
SELECT 'meta=' || typeof(singleton) || ':' || singleton || ','
  || typeof(schema_version) || ':' || schema_version
  FROM content_schema_meta;
SELECT 'visual_meta_table_count=' || count(*) FROM sqlite_master
  WHERE type='table' AND name='content_visual_analysis_schema_meta';
SELECT CASE WHEN count(*) = 1 AND max(integrity_check) = 'ok'
  THEN 'integrity_check=ok' ELSE 'integrity_check=failed' END
  FROM pragma_integrity_check;
SELECT 'foreign_key_violation_count=' || count(*)
  FROM pragma_foreign_key_check;
SQL
```

Sanitized output:

```text
journal_mode=wal
user_version=0
meta_table_sql=CREATE TABLE content_schema_meta (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    schema_version INTEGER NOT NULL
                )
meta_row_count=1
meta=integer:1,integer:7
visual_meta_table_count=0
integrity_check=ok
foreign_key_violation_count=0
```

Results: the canonical metadata table shape and singleton row are present, but
the stored schema version is **7**, not the runtime-required **10**. SQLite
integrity check returned `ok`; foreign-key check found zero violations.
`PRAGMA user_version` is 0; this application tracks its content migration
version in `content_schema_meta`. The optional visual-analysis extension
metadata table is absent.

The main database file was a regular 10,989,568-byte file with mtime
`2026-10-29 21:27:49.020873009 +0530` both before and after the checks. No
content hash was computed. After inspection, `ultron_cre.db-wal` existed as a
zero-byte file and `ultron_cre.db-shm` existed at 32,768 bytes. Because their
pre-inspection existence was not recorded, it is **NOT VERIFIED** whether
these sidecars were created or updated by this inspection. They were not
removed.

### Compatibility verdict and migration boundary

**FAIL:** the configured database is not compatible with the current CRE
runtime's v10 preflight. The integrity and foreign-key checks passed, but they
do not make schema v7 acceptable. At the time of this inspection, no
migration, backup, clone, recovery, or gateway startup had been performed.
Section 13 records a subsequent source-read-only backup and clone-only
rehearsal; the configured/live gateway and its active database environment
remain **NOT VERIFIED**.

If migration is considered, obtain separate approval before any write
operation. The existing proposal is: make an online SQLite backup that
includes WAL-visible committed state; verify backup integrity and foreign
keys; compare critical counts/relationships and rehearse the documented
v7→v9 then v9→v10 sequence on a clone; then, only after separate explicit
approval and an approved maintenance window, execute the corresponding
migration on the configured database, verify again, and retain untouched
backups for rollback. The clone rehearsal has run; no production migration
step has been authorized or executed.

The only repository file edited by me during the inspection and rehearsal
continuations is `docs/CRE_PHASE4_2_1_INDEPENDENT_VERIFICATION.md`. No
application source, tests, or configuration were edited. Without Git
metadata, this is not a complete workspace change inventory. Backup and clone
artifacts are confined to the private session-state directory listed in
section 13.

## 13. Approved v7-to-v10 disposable-clone migration rehearsal

### Migration implementation and exact order

**Code inspection:** `ContentStore.SCHEMA_VERSION` is 7,
`PUBLISHING_ATTEMPT_SCHEMA_VERSION` is 9,
`PRIVATE_RENDER_OPERATION_SCHEMA_VERSION` and `RUNTIME_SCHEMA_VERSION` are
10. The rehearsal followed the implemented explicit APIs, not a new migration
path:

1. `ContentStore.migrate_publishing_attempt_schema()` accepts exact v7 or
   transitional v8. For v7 it creates the publishing-attempt foundation,
   advances transactionally through v8, runs
   `_upgrade_publishing_attempt_schema_v9()`, rejects unsafe legacy terminal
   failures lacking pre-upload provider evidence, updates the unresolved-job
   index and guards, then verifies v9.
2. `ContentStore.migrate_private_render_operation_schema()` requires exact
   v9, verifies the publishing schema, creates private-render operation and
   event tables, advances metadata to v10, and verifies the result.
3. `migrate_visual_analysis_schema()` was **not run**. That is a separate
   optional extension, not required by runtime schema-v10 preflight.

`ContentStore` construction calls `_create_tables()` before those explicit
migrations. On this clone it also added the empty
`content_edit_plan_revisions` table and its three append-only/sequencing
triggers. This is the existing initialization behavior, not an invented
migration. Existing project/source/segment records were preserved. The
separate test `test_plan_table_initialization_preserves_existing_project_records`
also exercises this initialization behavior across stored schema labels.

### Backup and clone provenance

The project-supported tool is `scripts/ultron_sqlite_backup.py`. Inspection
confirmed that it opens the source with a SQLite URI `mode=ro`, uses the
SQLite online backup API (so committed WAL-visible state is included), checks
integrity and schema metadata, compares source metadata before/after, and
creates protected output files. `tests/runtime/test_sqlite_backup.py` includes
a WAL database test that verifies main-file and WAL hashes remain unchanged.
The focused backup and migration tests passed before rehearsal.

The configured source path was
`/home/ultron/Projects/ULTRON_INTERFACE_FINAL/data/ultron_cre.db`. The
destination root was
`/home/ultron/.copilot/session-state/6c0d4392-c1c2-4bdb-a136-99a535f24fd4/cre-v7-rehearsal-20261101`
under a mode-0700 session directory. Both clone paths were resolved and
checked distinct from the configured database and its `data` directory before
restoring. The backup and clone files are mode 0600; their parent directories
are mode 0700.

No process holding the source database/WAL/SHM descriptors was observable
through `/proc` immediately before backup. This does not prove that no
service could open it concurrently, but SQLite's online backup API provides a
consistent database snapshot under concurrent writers. No service was
contacted, started, stopped, or restarted.

The exact backup command was:

```sh
cd /home/ultron/Projects/ULTRON_INTERFACE_FINAL
/home/ultron/Projects/ULTRON_INTERFACE_FINAL/.venv/bin/python \
  scripts/ultron_sqlite_backup.py backup \
  --source /home/ultron/Projects/ULTRON_INTERFACE_FINAL/data/ultron_cre.db \
  --destination-dir /home/ultron/.copilot/session-state/6c0d4392-c1c2-4bdb-a136-99a535f24fd4/cre-v7-rehearsal-20261101/backup \
  --name cre-v7 --expected-user-version 0
```

The project verifier returned `outcome=VERIFIED`, size **10,989,568 bytes**,
mode **0600**, and backup SHA-256
`1e4948a9ce026e0f98484435815b094b6372e34eb305e3413a39500dabb8c461`.
The manifest reported WAL journal mode, `user_version=0`, integrity `ok`,
application ID 0, and a schema fingerprint matching its source pre/post
checks. A separate read-only query on the backup found content schema v7,
50 user tables, `integrity_check=ok`, and zero foreign-key violations.

The verified backup was restored twice using the project's `restore` command:

```sh
REHEARSAL_ROOT=/home/ultron/.copilot/session-state/6c0d4392-c1c2-4bdb-a136-99a535f24fd4/cre-v7-rehearsal-20261101
/home/ultron/Projects/ULTRON_INTERFACE_FINAL/.venv/bin/python \
  scripts/ultron_sqlite_backup.py restore \
  --backup "$REHEARSAL_ROOT/backup/cre-v7.sqlite" \
  --manifest "$REHEARSAL_ROOT/backup/cre-v7.manifest.json" \
  --destination "$REHEARSAL_ROOT/clones/cre-v7-pristine-input.sqlite" \
  --expected-user-version 0

/home/ultron/Projects/ULTRON_INTERFACE_FINAL/.venv/bin/python \
  scripts/ultron_sqlite_backup.py restore \
  --backup "$REHEARSAL_ROOT/backup/cre-v7.sqlite" \
  --manifest "$REHEARSAL_ROOT/backup/cre-v7.manifest.json" \
  --destination "$REHEARSAL_ROOT/clones/cre-v7-migration-rehearsal.sqlite" \
  --expected-user-version 0
```

Both restores returned `RESTORED_AND_VERIFIED`. The pristine input and backup
retained the same SHA-256 after rehearsal; only the separately named
`cre-v7-migration-rehearsal.sqlite` clone was opened for mutation.

The online backup uses source `mode=ro` and did not change the configured
database main-file or WAL byte hashes. Before/after hashes were respectively
`aeea0eff2e210cb3c3628b96868cb33b278ed30fb58fd1c9ce05cea36f48ee48` and
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
The SHM byte hash remained
`fd4c9fda9cd3f9ae7c962b0ddf37232294d55580e1aa165aa06129b8549389eb`, but
its mtime advanced from `2026-11-01 12:11:54 +0530` to
`2026-11-01 12:20:47 +0530` during the read-only backup access. Thus no main
database/WAL content change was observed, but SQLite touched SHM sidecar
metadata. This limitation prevents claiming absolutely no filesystem
metadata side effect.

### Rehearsal results

The v7 baseline was captured from both clones before migration:

```text
pristine: schema=v7, integrity=ok, foreign_key_violations=0, tables=50
migration: schema=v7, integrity=ok, foreign_key_violations=0, tables=50
initial_clone_table_counts_match=true
baseline_rows_content_assets=8
baseline_rows_content_edit_projects=4
baseline_rows_content_publishing_jobs=0
baseline_rows_content_clip_renders=0
```

Only the work-clone resolved path
`/home/ultron/.copilot/session-state/6c0d4392-c1c2-4bdb-a136-99a535f24fd4/cre-v7-rehearsal-20261101/clones/cre-v7-migration-rehearsal.sqlite`
was passed to the migration methods. The exact ordered results were:

```text
migrate_publishing_attempt_schema=v9
migrate_private_render_operation_schema=v10
final_schema=v10, integrity=ok, foreign_key_violations=0
runtime_preflight=PASS
```

After migration, all **50/50** pre-existing table row counts matched the v7
baseline; no pre-existing table definitions changed and no tables were
removed. Six new tables were empty:
`content_edit_plan_revisions` (created by initialization),
`content_publishing_attempts`,
`content_publishing_attempt_secret_refs`,
`content_publishing_attempt_events`,
`content_private_render_operations`, and
`content_private_render_operation_events`. Schema comparison found 6 new
indexes and 21 new triggers, all belonging to the expected attempt, private
render, and edit-plan tables. Final key row counts remained assets **8**,
projects **4**, publishing jobs **0**, and clip renders **0**. Final
`integrity_check` was `ok`, foreign-key violations were zero, and read-only
runtime preflight accepted v10. The backup and pristine clone SHA-256 remained
unchanged.

### Tests and verdict

Focused backup and migration tests:

```text
PYTHONDONTWRITEBYTECODE=1 /home/ultron/Projects/ULTRON_INTERFACE_FINAL/.venv/bin/python -m pytest -p no:cacheprovider -q tests/runtime/test_sqlite_backup.py tests/application/test_content_visual_analysis_schema.py tests/application/test_content_publishing_attempts.py tests/application/test_content_private_render_operations.py
```

**77 passed, 0 failed, 0 skipped.**

Broad backup, migration, CRE, and interface regression command:

```text
PYTHONDONTWRITEBYTECODE=1 /home/ultron/Projects/ULTRON_INTERFACE_FINAL/.venv/bin/python -m pytest -p no:cacheprovider -q tests/runtime/test_sqlite_backup.py tests/application/test_content_visual_analysis.py tests/application/test_content_visual_analysis_schema.py tests/application/test_content_status.py tests/application/test_content_visual_intelligence.py tests/application/test_content_highlights.py tests/application/test_content_intelligence.py tests/application/test_content_lifecycle.py tests/application/test_content_rendering.py tests/application/test_content_private_render_operations.py tests/application/test_content_publishing_attempts.py tests/application/test_content_publishing.py tests/application/test_content_rights.py tests/application/test_content_processing.py tests/application/test_content_script_preview.py tests/interface/test_interface_authentication.py
```

**379 passed, 0 failed, 0 skipped.** The interface test suite includes an
isolated gateway-startup test; no configured/live service was started. The
configured database main/WAL/SHM byte hashes remained as recorded above after
the test run.

**Rehearsal verdict: PASS on the verified disposable clone. Overall
configured-runtime verdict: PARTIAL.** The migration sequence and v10 runtime
preflight work on the clone, but the configured database is still v7. The
service's active environment and live gateway startup remain **NOT VERIFIED**.
No production migration, service action, dependency change, or integration
work was performed.

## 14. Production migration approval request — NOT EXECUTED

The rehearsal supports proposing, but does not authorize, production
migration. Proposed maintenance window: one **30-minute low-traffic window**
chosen by the operator, with all CRE database writers quiesced and the gateway
stopped by an independently authorized operator before any migration write.
No date/time is scheduled. Confirm the actual service manager, process
identity, and absence of open database handles before proceeding. Keep the
v7 backup immutable and verify it before and after the operation.

The following command sequence is for review only. It has **not** been run
against the configured database. `BACKUP_PARENT` must be a pre-existing,
operator-approved private directory; the utility creates each unique child
directory with mode 0700. Stop if the configured schema is no longer v7,
`user_version` is not 0, integrity/FK checks fail, or any backup verification
fails.

```sh
cd /home/ultron/Projects/ULTRON_INTERFACE_FINAL
DB=/home/ultron/Projects/ULTRON_INTERFACE_FINAL/data/ultron_cre.db
BACKUP_PARENT=/home/ultron/.local/state/ultron
PY=/home/ultron/Projects/ULTRON_INTERFACE_FINAL/.venv/bin/python

# Read-only gate: expected content schema v7, SQLite user_version 0.
sqlite3 -readonly -batch -noheader "file:$DB?mode=ro" \
  "PRAGMA query_only=ON; SELECT schema_version FROM content_schema_meta WHERE singleton=1; PRAGMA integrity_check; PRAGMA foreign_key_check;"

# First protected online backup before the v7->v9 mutation.
"$PY" scripts/ultron_sqlite_backup.py backup \
  --source "$DB" \
  --destination-dir "$BACKUP_PARENT/cre-v7-pre-migration-20261101" \
  --name cre-v7 --expected-user-version 0
"$PY" scripts/ultron_sqlite_backup.py verify \
  --backup "$BACKUP_PARENT/cre-v7-pre-migration-20261101/cre-v7.sqlite" \
  --manifest "$BACKUP_PARENT/cre-v7-pre-migration-20261101/cre-v7.manifest.json"

# Explicit v7->v9 migration only; do not run unless the backup verified.
"$PY" - <<'PY'
from pathlib import Path
from ultron.application.content.store import ContentStore
db = Path("/home/ultron/Projects/ULTRON_INTERFACE_FINAL/data/ultron_cre.db")
store = ContentStore(db, recover_interrupted_jobs=False)
try:
    current = store._connection.execute(
        "SELECT schema_version FROM content_schema_meta WHERE singleton=1"
    ).fetchone()[0]
    if current != 7:
        raise RuntimeError(f"Expected schema v7, found v{current}")
    print(f"publishing_migration=v{store.migrate_publishing_attempt_schema()}")
finally:
    store.close()
PY

# Back up v9 separately because the supported v9->v10 tool requires a matching
# pre-migration backup.
"$PY" scripts/ultron_sqlite_backup.py backup \
  --source "$DB" \
  --destination-dir "$BACKUP_PARENT/cre-v9-pre-v10-20261101" \
  --name cre-v9 --expected-user-version 0
"$PY" scripts/ultron_sqlite_backup.py verify \
  --backup "$BACKUP_PARENT/cre-v9-pre-v10-20261101/cre-v9.sqlite" \
  --manifest "$BACKUP_PARENT/cre-v9-pre-v10-20261101/cre-v9.manifest.json"

# Explicit v9->v10 migration requires that verified, same-version backup.
"$PY" -m tools.migrate_cre_private_render_operations "$DB" \
  --backup "$BACKUP_PARENT/cre-v9-pre-v10-20261101/cre-v9.sqlite"

# Read-only post-migration checks; require content schema v10, integrity ok,
# and zero foreign-key rows before any gateway start is separately approved.
sqlite3 -readonly -batch -noheader "file:$DB?mode=ro" \
  "PRAGMA query_only=ON; SELECT schema_version FROM content_schema_meta WHERE singleton=1; PRAGMA integrity_check; PRAGMA foreign_key_check;"
"$PY" -c 'from pathlib import Path; from ultron.application.content.status import _verify_existing_database; _verify_existing_database(Path("/home/ultron/Projects/ULTRON_INTERFACE_FINAL/data/ultron_cre.db")); print("runtime_preflight=PASS")'
```

Rollback proposal: retain the verified v7 online backup and v9 checkpoint;
restore the chosen backup to a **new sibling recovery file** using the
project's `restore` command; verify its checksum, integrity, foreign keys, and
version; only then, under separate explicit approval with all writers stopped,
perform a controlled cutover that preserves the failed database and handles
WAL/SHM files consistently. Never restore over an open or existing database
file. Exact cutover/rollback commands require confirmation of the service
manager and approved filesystem locations and are not provided as executable
steps here. No production command above has been executed.

## 15. Operator runbook and service-control limitation

The concise operator procedure is in
[`CRE_V7_TO_V10_PRODUCTION_MIGRATION_RUNBOOK.md`](./CRE_V7_TO_V10_PRODUCTION_MIGRATION_RUNBOOK.md).
It separates read-only, service-control, and database-mutating actions and
provides preconditions, backup/verification, migration, post-check, abort, and
rollback steps. It is review-only and is not authorization to execute those
steps.

**Additional source inspection:** `scripts/ultron_service_manager.py` accepts
`start`, `stop`, and `restart-gateway`. Its `stop` action invokes
`stop_managed()` for the launcher-managed stack; it is not gateway-only.
`restart-gateway` performs a restart and is not a quiesce operation. No
supported gateway-only stop command was established. Therefore the exact
production service-control command and complete writer-stop procedure remain
**NOT VERIFIED**; the runbook requires a separately approved service-control
method and directs the operator to stop if one is unavailable. The live
gateway environment/status and the complete set of live/scheduled CRE writers
remain **NOT VERIFIED**. No service-control or production migration command
was run.

The only file changed for this documentation continuation is
`docs/CRE_V7_TO_V10_PRODUCTION_MIGRATION_RUNBOOK.md`, in addition to this
report. No source, tests, configuration, service, or database files were
changed. Git metadata remains unavailable, so complete workspace attribution
cannot be made.
