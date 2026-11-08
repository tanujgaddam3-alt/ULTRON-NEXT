# CRE v7-to-v10 Production Migration Runbook

**Status: REVIEW ONLY — NOT AUTHORIZED, NOT EXECUTED**

This runbook describes a possible maintenance procedure based on the
disposable-clone rehearsal. It is not approval to modify the configured CRE
database. A separate explicit approval is required before any service-control
or database-mutating step.

**Evidence classification:** the results in
[`CRE_PHASE4_2_1_INDEPENDENT_VERIFICATION.md`](./CRE_PHASE4_2_1_INDEPENDENT_VERIFICATION.md)
are historical disposable-clone evidence. They are not current production
observations, do not establish production object deltas or migration success,
and do not replace maintenance-window evidence.

## Ordered GO checklist

Cutover remains **NO-GO** until every item below is evidenced and signed by the
named owner:

1. **Manager ownership:** host administrator supplies parent/identity/lease/socket evidence and the authorized recovery actor.
2. **Writer and restart quiescence:** host administrator supplies system/user service, timer, cron, container, integration, manual-process, and inaccessible-`/proc` evidence plus the approved maintenance hold.
3. **Maintenance approval:** operator records the named window, scope, approver, start, stop, and rollback authority.
4. **Release provenance:** release owner supplies the deployed revision, approved release bundle, independently stored checksum, and source identity.
5. **Rollback compatibility:** release owner supplies a separately tested v10-compatible rollback bundle and checksum; the original v7 database is not a runtime rollback mechanism.
6. **Database evidence:** operator supplies fresh disposable-copy validation, integrity/FK/schema evidence, main/sidecar identity, and a manifest for the intended v10 copy.
7. **Service identity:** manager/service operator supplies lease, socket, PID/start-tick, executable, working-directory, process-group, listener-owner, and readiness evidence.
8. **Rollback rehearsal:** operator supplies successful restoration/restart evidence for the tested compatible rollback release and confirms the original database remains untouched.

Any `UNKNOWN` item, stale identity, unresolved writer, unverified restart source,
missing approval, or untested rollback prevents GO. Repository tooling can
record evidence but cannot prove host state or compatibility.

## Known facts and hard stop conditions

- The configured path is
  `/home/ultron/Projects/ULTRON_INTERFACE_FINAL/data/ultron_cre.db`.
- The last authorized read-only inspection found content schema v7,
  `user_version=0`, `integrity_check=ok`, and no foreign-key violations.
- The disposable-clone migration sequence passed: v7→v9 via
  `ContentStore.migrate_publishing_attempt_schema()`, then v9→v10 via
  `ContentStore.migrate_private_render_operation_schema()`.
- The project service manager supports `start`, `stop`, `restart-gateway`,
  `quiesce-gateway`, `resume-gateway`, and read-only `status`. Its `stop`
  action calls `stop_managed()` for the managed stack; it is not gateway-only.
  `restart-gateway` starts a replacement and is not quiescence. The new
  quiesce/resume actions are described below. Their existence, or a status
  snapshot, does not establish that the expected manager instance is active
  or that the complete CRE writer inventory has been stopped.
- Source inspection shows the gateway is the application runtime that opens
  `ContentStore`. The complete set of live writers is unknown: scheduled jobs,
  operator scripts, other processes, or tools may also write. Do not proceed
  until the authorized operator inventories and stops every writer.
- The backup utility's SQLite `mode=ro` source preserves the main/WAL content
  in the rehearsal, but SQLite advanced the SHM sidecar mtime. Read-only
  access is therefore not guaranteed to be free of filesystem metadata
  effects.
- `ContentStore` construction is not a read-only inspection operation. Its
  constructor creates/opens a database, configures WAL, and runs
  `_initialize_schema()`, which can create or update schema objects. The
  production migration API shown below uses that constructor; its
  initialization effects are part of the migration's schema change and must
  be included in the approved baseline and expected-object comparison.
- CRE `content_schema_meta.schema_version` and SQLite `PRAGMA user_version`
  are separate fields. The runtime gate requires CRE schema v10.
  `ultron_sqlite_backup.py --expected-user-version` checks SQLite
  `user_version`; it does not assert CRE schema v7, v9, or v10.

## Fixture-only schema validator

[`scripts/cre_schema_validator.py`](../scripts/cre_schema_validator.py) is a
read-only engineering validator for an explicitly supplied, existing database
file under the system temporary directory. It rejects the configured
production path (including resolved aliases) before opening a database,
requires an absolute path, rejects symlinks, and never creates, initializes,
copies, restores, replaces, or activates a database. It is not a production
preflight command and must not be pointed at the configured database or at
any path outside the temporary directory.

For an independently created temporary fixture only:

```sh
cd /home/ultron/Projects/ULTRON_INTERFACE_FINAL
PY=/home/ultron/Projects/ULTRON_INTERFACE_FINAL/.venv/bin/python
"$PY" -m scripts.cre_schema_validator baseline --database "$FIXTURE_DB"
"$PY" -m scripts.cre_schema_validator recovery \
  --database "$RECOVERY_FIXTURE" \
  --expected-cre-schema-version 7 \
  --expected-user-version 0
```

`baseline` emits a sorted expected schema-object manifest and actual/expected
comparison for the current source-generated initialization-v7,
v7-to-v9, and v9-to-v10 layers. It compares normalized SQL definitions for
tables, indexes, and triggers; reports missing, unexpected, or changed
objects; and emits table row counts plus aggregate foreign-key relationship,
integrity, and FK-violation checks without returning application rows. The
expected schemas are generated from current source using an in-memory
`ContentStore`; the supplied database is opened separately with SQLite
`mode=ro` and `query_only=ON`. The tool does not instantiate `ContentStore`
against the supplied file.

The manifest is a source-derived exact fixture contract, not a guarantee of
production's net object delta. Its initialization-v7 layer intentionally
separates objects present after current `ContentStore` initialization from
the explicit migration layers. Extra features such as visual-analysis schema
extensions are reported as unexpected and are outside this v7-to-v10
contract. SQLite read-only access can still create or update WAL shared-memory
sidecar metadata; the tool therefore only accepts temporary fixtures and does
not claim zero filesystem metadata effects for WAL-mode files.

`recovery` checks the requested CRE metadata version, SQLite `user_version`,
integrity, and foreign keys on a separately supplied temporary candidate. A
v7 candidate is checked directly against expected v7 metadata; the runtime
v10 preflight is not invoked. The tool never activates or replaces the
candidate. Neither subcommand establishes writer quiescence, backup
protection, production readiness, migration approval, rollback authorization,
or service cutover authorization. This tool does not provide a production
table/count baseline procedure: an approved, host-safe production baseline
procedure remains a prerequisite.

## Command classifications

- **READ-ONLY:** inspection and validation commands below. SQLite may still
  access WAL sidecars; `immutable=1` is prohibited.
- **SERVICE-CONTROL:** stopping or starting a gateway/writer. The project
  provides `quiesce-gateway` and `resume-gateway` for the manager-owned
  gateway only. Other writers still require separately reviewed and approved
  controls.
- **DATABASE-MUTATING:** the migration commands below. They must not be run
  without separate explicit approval, verified backup, a maintenance window,
  and confirmed writer quiescence.
- **BACKUP-OUTPUT:** the backup command opens its source read-only but creates
  backup and manifest files. Destination, privacy, capacity, retention, and
  the backup-output action require separate explicit approval.
- **RECOVERY-RESTORE:** restore writes a new candidate and requires separate
  explicit rollback authorization. Candidate creation does not authorize
  cutover; cutover also requires its own explicit authorization.

## Preconditions and maintenance window

Plan for at least **30 minutes** of maintenance, plus an operator-approved
extension/abort window. The estimate is provisional; rehearsal proves the
sequence on a clone, not the duration on an offline production database.

Before entering the window:

1. Obtain separate written approval for each applicable action: production
   backup-output creation and its destination/retention policy, each database
   migration stage, service control, recovery restore, and any cutover.
   Record the approved scope and accountable operator for each; this runbook
   does not assign owners or grant authorization.
2. Confirm the configured path and resolved target match the expected file;
   reject symlinks or any path mismatch.
3. Inventory the launcher service record and process identities (PID, process
   start ticks, executable, command identity, and working directory) without
   exposing environment values. Identify gateway and every process/job/tool
   capable of opening this database for writes. Do not read `.env` files.
4. Stop the gateway and all identified writers using approved controls.
   Record operator, timestamps, service states, process identities before
   and after, and confirmation that no CRE writer remains.
5. Confirm no open descriptors remain for the database, `-wal`, or `-shm`
   using an approved local process-inspection method. If writer inventory,
   process identity, or quiescence is uncertain, abort. Do not infer this
   from a single empty `lsof`/`/proc` sample.
6. Confirm the operator-approved backup destination is private, has enough
   space, does not overlap the repository's `data` directory, and does not
   already contain the chosen run-specific output directory.
7. Record the maintenance start time, database and sidecar sizes/mtime/hashes,
   both version fields, and the approved pre-migration table/count and
   relationship baseline. Keep output sanitized; do not dump application
   records. The fixture-only validator above is not a substitute for this
   production baseline.

## Step A — preflight and v7 backup

The following are **READ-ONLY**, except that SQLite may touch WAL sidecar
metadata. `BACKUP_PARENT` must be pre-existing, private, and operator-approved;
the backup utility creates a unique child directory with mode 0700. Use a new
run identifier instead of reusing an existing backup name.

```sh
cd /home/ultron/Projects/ULTRON_INTERFACE_FINAL
DB=/home/ultron/Projects/ULTRON_INTERFACE_FINAL/data/ultron_cre.db
BACKUP_PARENT=/approved/private/backup-parent
RUN_ID=cre-v7-pre-migration-YYYYMMDDTHHMMZ
PY=/home/ultron/Projects/ULTRON_INTERFACE_FINAL/.venv/bin/python

# READ-ONLY: explicitly report application version and stop on failed checks.
sqlite3 -readonly -batch -noheader "file:$DB?mode=ro" <<'SQL'
.timeout 5000
PRAGMA query_only=ON;
SELECT 'schema_version=' || schema_version
  FROM content_schema_meta WHERE singleton=1;
SELECT 'user_version=' || user_version FROM pragma_user_version;
SELECT 'integrity=' || integrity_check FROM pragma_integrity_check;
SELECT 'foreign_key_violation=' || "table" || ':' || "rowid"
  FROM pragma_foreign_key_check;
SQL

# READ-ONLY source; creates protected backup output files.
"$PY" scripts/ultron_sqlite_backup.py backup \
  --source "$DB" \
  --destination-dir "$BACKUP_PARENT/$RUN_ID" \
  --name cre-v7 --expected-user-version 0

# READ-ONLY verification of the created backup and protected manifest.
"$PY" scripts/ultron_sqlite_backup.py verify \
  --backup "$BACKUP_PARENT/$RUN_ID/cre-v7.sqlite" \
  --manifest "$BACKUP_PARENT/$RUN_ID/cre-v7.manifest.json"

# READ-ONLY: additionally verify content schema v7 and foreign-key check
# against the backup. Require integrity=ok and no foreign-key output.
sqlite3 -readonly -batch -noheader \
  "file:$BACKUP_PARENT/$RUN_ID/cre-v7.sqlite?mode=ro" <<'SQL'
.timeout 5000
PRAGMA query_only=ON;
SELECT 'schema_version=' || schema_version
  FROM content_schema_meta WHERE singleton=1;
SELECT 'integrity=' || integrity_check FROM pragma_integrity_check;
SELECT 'foreign_key_violation=' || "table" || ':' || "rowid"
  FROM pragma_foreign_key_check;
SQL
```

Proceed only if the preflight and backup both show schema v7, `user_version=0`,
integrity `ok`, zero foreign-key rows, and the backup tool reports
`outcome=VERIFIED`. Preserve the v7 backup unchanged for rollback. If any
check fails or differs from the last observed state, stop and investigate.

## Step B — v7 to v9

**DATABASE-MUTATING — NOT TO BE RUN without separate approval.** Run only
after the preconditions and v7 backup are verified. This is the exact explicit
API used in the rehearsal; ordinary runtime startup does not migrate.

```sh
cd /home/ultron/Projects/ULTRON_INTERFACE_FINAL
DB=/home/ultron/Projects/ULTRON_INTERFACE_FINAL/data/ultron_cre.db
PY=/home/ultron/Projects/ULTRON_INTERFACE_FINAL/.venv/bin/python

"$PY" - <<'PY'
from pathlib import Path
from ultron.application.content.store import ContentStore

database = Path("/home/ultron/Projects/ULTRON_INTERFACE_FINAL/data/ultron_cre.db")
store = ContentStore(database, recover_interrupted_jobs=False)
try:
    row = store._connection.execute(
        "SELECT schema_version FROM content_schema_meta WHERE singleton=1"
    ).fetchone()
    if row is None or row["schema_version"] != 7:
        raise RuntimeError("Precondition failed: database is not schema v7")
    print(f"publishing_migration=v{store.migrate_publishing_attempt_schema()}")
finally:
    store.close()
PY
```

Expected output: `publishing_migration=v9`. On any exception, unexpected
output, process interruption, or non-v9 result, stop. Do not retry blindly
and do not reopen the database with `ContentStore` merely to inspect it:
construction may itself run schema initialization. Preserve the current main
file and sidecars for diagnosis. Obtain an independently approved read-only
inspection procedure for the production database, establish its actual CRE
schema and SQLite `user_version`, integrity/FK state, and compare the schema
objects with the authorized baseline before any decision to continue. The
migration runs transactionally, but transaction rollback alone does not prove
the effective production state after interruption.

## Step C — v9 checkpoint and v9 to v10

After confirming v9, create and verify a separate v9 backup. The v10 migration
tool requires a separate backup at the same schema version.

```sh
cd /home/ultron/Projects/ULTRON_INTERFACE_FINAL
DB=/home/ultron/Projects/ULTRON_INTERFACE_FINAL/data/ultron_cre.db
BACKUP_PARENT=/approved/private/backup-parent
PY=/home/ultron/Projects/ULTRON_INTERFACE_FINAL/.venv/bin/python

# READ-ONLY: require exact v9, integrity ok, and no foreign-key output.
sqlite3 -readonly -batch -noheader "file:$DB?mode=ro" <<'SQL'
.timeout 5000
PRAGMA query_only=ON;
SELECT 'schema_version=' || schema_version
  FROM content_schema_meta WHERE singleton=1;
SELECT 'integrity=' || integrity_check FROM pragma_integrity_check;
SELECT 'foreign_key_violation=' || "table" || ':' || "rowid"
  FROM pragma_foreign_key_check;
SQL

# READ-ONLY source; creates protected v9 backup output files.
RUN_V9_ID=cre-v9-pre-v10-YYYYMMDDTHHMMZ
"$PY" scripts/ultron_sqlite_backup.py backup \
  --source "$DB" \
  --destination-dir "$BACKUP_PARENT/$RUN_V9_ID" \
  --name cre-v9 --expected-user-version 0
"$PY" scripts/ultron_sqlite_backup.py verify \
  --backup "$BACKUP_PARENT/$RUN_V9_ID/cre-v9.sqlite" \
  --manifest "$BACKUP_PARENT/$RUN_V9_ID/cre-v9.manifest.json"

# READ-ONLY: require exact v9 and valid integrity/FK state in the backup too.
sqlite3 -readonly -batch -noheader \
  "file:$BACKUP_PARENT/$RUN_V9_ID/cre-v9.sqlite?mode=ro" <<'SQL'
.timeout 5000
PRAGMA query_only=ON;
SELECT 'schema_version=' || schema_version
  FROM content_schema_meta WHERE singleton=1;
SELECT 'integrity=' || integrity_check FROM pragma_integrity_check;
SELECT 'foreign_key_violation=' || "table" || ':' || "rowid"
  FROM pragma_foreign_key_check;
SQL

# DATABASE-MUTATING — NOT TO BE RUN without separate approval.
"$PY" -m tools.migrate_cre_private_render_operations "$DB" \
  --backup "$BACKUP_PARENT/$RUN_V9_ID/cre-v9.sqlite"
```

Expected migration output: `CRE private render operation schema is now v10.`
Stop on any error or unexpected schema. Do not run
`migrate_visual_analysis_schema()` as part of this migration; that extension
is separate and not needed for v10 runtime preflight.

## Step D — post-migration validation

All commands in this section are **READ-ONLY**. Require all checks to pass
before considering a separately authorized service restart:

```sh
sqlite3 -readonly -batch -noheader "file:$DB?mode=ro" <<'SQL'
.timeout 5000
PRAGMA query_only=ON;
SELECT 'schema_version=' || schema_version
  FROM content_schema_meta WHERE singleton=1;
SELECT 'user_version=' || user_version FROM pragma_user_version;
SELECT 'integrity=' || integrity_check FROM pragma_integrity_check;
SELECT 'foreign_key_violation=' || "table" || ':' || "rowid"
  FROM pragma_foreign_key_check;
SQL

# DISPOSABLE-COPY-ONLY CHECK. This command does not migrate, restore, modify,
# authorize live migration, or authorize service cutover.
"$PY" -m scripts.cre_runtime_preflight \
  /home/ultron/cre-v10-test-20261101-232148/cre-test.db
```

Compare post-migration table counts and key relationships against the approved
pre-migration values. Existing table counts must not change during the offline
migration; newly introduced migration tables should be empty unless an
approved, evidence-backed expectation says otherwise. Compare actual v9/v10
schema objects against the recorded production baseline and approved
source-derived definitions. Historical rehearsal counts and object names are
references only; they do not prove the production delta. The fixture-only
validator cannot be run against the production database. Re-run backup
verification on both retained backups. Record results, timestamps, SQLite
version, both schema version fields, backup hashes, and sanitized output. Do
not start the gateway as part of this procedure; service startup requires
separate authorization and verification.

## Rollback and abort criteria

**Rollback must not overwrite an open or existing database.** Stop immediately
and trigger the approved rollback process if any of the following occurs:

- backup creation/verification fails or source/version preconditions differ;
- either migration raises, returns the wrong version, or is interrupted;
- integrity, foreign-key, runtime preflight, table-count, or relationship
  validation fails;
- the migration creates unexpected objects/data or changes existing records;
- any writer becomes active unexpectedly, or service/process identity is
  ambiguous.

Preserve the failed database and all sidecars as evidence. Keep writers
stopped. After separate explicit rollback authorization, use the project
restore utility to create a **new, non-existing recovery file** from the
verified v7 backup. Utility verification covers its manifest/checksum,
SQLite `user_version`, schema fingerprint, and integrity, but not CRE metadata
or foreign-key violations. Obtain and use an approved read-only procedure to
verify exact CRE v7 metadata and zero foreign-key violations. The fixture-only
`recovery` command can validate only a candidate located under the system
temporary directory; it cannot validate an approved destination elsewhere or
authorize cutover:

```sh
# DATABASE-WRITING only to a new recovery destination; requires separate
# explicit rollback authorization. This does not replace the configured DB.
RECOVERY=/approved/recovery/location/ultron_cre.rollback-candidate.sqlite
"$PY" scripts/ultron_sqlite_backup.py restore \
  --backup "$BACKUP_PARENT/$RUN_ID/cre-v7.sqlite" \
  --manifest "$BACKUP_PARENT/$RUN_ID/cre-v7.manifest.json" \
  --destination "$RECOVERY" --expected-user-version 0
```

Do not use `restore` with the configured database path. A cutover from the
recovery candidate requires its own approved, service-specific procedure that
keeps every writer stopped and handles the main database and WAL/SHM sidecars
as one consistent recovery point. The current service-manager interface does
not establish a verified live-writer inventory; therefore this runbook
intentionally supplies **no generic rename/replace or external
service-control command**. The project-owned gateway quiesce command below
does not stop external writers. If the separately approved cutover procedure
is unavailable, preserve the recovery candidate and escalate without
replacing anything.

## Operator record and evidence gaps

Record:

- authorizing ticket/person, maintenance start/end, and operator names;
- actual manager/service mechanism and gateway PID/start ticks before/after;
- complete writer inventory, stop confirmations, and no-open-writer evidence;
- source and sidecar sizes, mtimes, hashes before/after backup and migration;
- v7 and v9 backup manifests/hashes and backup verification results;
- migration output, pre/post schema versions, table counts, integrity/FK
  checks, runtime preflight, and any abort/rollback actions.

Still unverified until the maintenance operator supplies evidence:

- actual running manager/gateway identity and effective database path;
- every live or scheduled CRE writer and its stop state;
- whether the v7→v9 transition can complete on production data (the clone
  rehearsal passed, but production data-specific blockers remain possible);
- configured gateway startup after a separately approved migration.

**Production migration remains not authorized and not executed. Separate
explicit approval is required before any production service-control or
database-mutating action.**

## 16. Source-backed quiescence and writer investigation

This section records a repository-only investigation. It does **not** establish
the current host service state, inspect the configured database, or prove that
any writer is stopped.

### Supported service controls

The project launcher defines `gateway`, `frontend`, `auth`, and `voice` in
[`scripts/ultron_service_manager.py`](../scripts/ultron_service_manager.py).
The supported wrappers are [`start-ultron.sh`](../start-ultron.sh), which
executes the manager's `start` action, and [`stop-ultron.sh`](../stop-ultron.sh),
which executes `stop`. The `stop` action calls `stop_managed()`; that method
walks the manager's recorded services and stops each verified launcher-owned
process. It is therefore **stack-wide**, not a gateway-only stop. Ctrl+C in
the manager's supervision loop also calls `stop_managed()`.

The manager now provides explicit `quiesce-gateway` and `resume-gateway`
actions in addition to `restart-gateway`. They are requests to the already
running manager over its protected local control socket. Quiesce validates the
managed gateway's recorded PID/start ticks, executable, working directory,
command identity, process group, and unique expected listener before it
persists intent and signals only that gateway. It never uses stack-wide
`stop_managed()` for the gateway-only operation. Resume requires the
persistent quiesced marker and healthy, identity-verified manager-owned
unrelated services, then launches through `_start_service()` and checks
readiness and listener ownership.

The manager's CLI commands are **SERVICE-CONTROL** and require an already
running launcher manager. From the project root, after separate operator
authorization:

```sh
# SERVICE-CONTROL: stop only the manager-verified gateway.
"$PWD/.venv/bin/python" scripts/ultron_service_manager.py \
  quiesce-gateway --root "$PWD" --operation-id cre-maintenance-YYYYMMDD

# SERVICE-CONTROL: explicitly launch and verify the gateway after maintenance.
"$PWD/.venv/bin/python" scripts/ultron_service_manager.py \
  resume-gateway --root "$PWD" --operation-id cre-resume-YYYYMMDD
```

These commands do not verify external CRE writers or authorize a database
backup/migration. External writer state may be recorded only through the
separate, human-operated attestation control:

```sh
# CONTROL-SOCKET/CLI: record an operator attestation for the active quiesce ID.
"$PWD/.venv/bin/python" scripts/ultron_service_manager.py \
  attest-gateway-writers --root "$PWD" \
  --operation-id cre-writer-attestation-YYYYMMDD \
  --attestation-file /approved/path/recovery-writer-attestation.json
```

The attestation JSON must contain only the strict fields documented in
`scripts/ultron_service_manager.py`: the active quiesce operation ID, operator
name, UTC timestamp, complete writer and restart-source inventories, evidence
references, explicit stopped/held confirmations, and an explicit completeness
check. An attestation is an auditable human assertion labeled
`OPERATOR_ATTESTED`; it is not software-generated proof that external writers
are absent. A bare `writersStatus: VERIFIED` state edit or command line field
is not accepted. The attestation is invalidated if the active quiesce operation
ID changes or its timestamp becomes stale.

Before recording the attestation, the manager must hold its current verified
manager lease, confirm that the original gateway PID/start-tick identity has
exited (including an explicitly distinguished PID reuse), and confirm that
the configured gateway port has no listener. It persists these checks beside
the operator attestation as manager recovery evidence. `resume-gateway`
requires that matching evidence and repeats the lease, process-identity, and
listener checks immediately before launch. This evidence does not establish
that external database writers or restart-capable supervisors are stopped;
that remains the operator's separately attested responsibility.

Do not use the stack-wide `stop` action as a substitute.
`restart-gateway` is also not a substitute because it starts a replacement.

The manager also supports a read-only status snapshot:

```sh
# READ-ONLY: inspect manager records, listener ownership, and readiness.
"$PWD/.venv/bin/python" scripts/ultron_service_manager.py \
  status --root "$PWD"
```

This command does not acquire the manager lease, start or stop services, write
manager state, or read the CRE database or service environment. It reads the
protected manager state record, checks process identity, uses `ss` to observe
configured listeners, and runs a service readiness probe only when the
recorded process identity and expected listener ownership both verify.
Missing, stale, malformed, or ambiguous information is reported as such; an
unverified process is not health-probed. `managerOwnershipStatus` reports
whether the lease is held by this manager, another recognized project manager,
an unknown process, or cannot be verified. For a quiesced gateway, the status
also reports the observed old-process identity and listener state.
`writersStatus=OPERATOR_ATTESTED` is reported only when a current matching
operator attestation is stored; it is not independent software verification
of external writers. Since manager state may change concurrently, this is an
observation rather than a lock-held snapshot or proof of host-wide service
ownership.

The manager records PID and process start ticks. Its process matcher also
checks the executable, working directory, command identity, and managed
process group; its listener check uses `ss` and requires the expected listener
to belong uniquely to the recorded process. These checks are useful identity
evidence, but the service state file alone is not proof of current host state.
The manager state directory is `$XDG_STATE_HOME/ultron/interface-launcher`
when `XDG_STATE_HOME` is set, otherwise `~/.local/state/ultron/interface-launcher`;
the service record is `services.json`. The status command does not inspect the
effective `ULTRON_CRE_DATABASE` environment value or prove all writers stopped;
those remain separate host-operator checks. It does not inspect any environment
values.

Source references for these behaviors:

- `scripts/ultron_service_manager.py`: production service definitions
  (`_production_services`); process identity (`_read_proc`,
  `_process_matches`, `_record_for`); stack stop (`stop_managed`); gateway
  restart/quiesce/resume (`request_gateway_control_operation`,
  `_restart_gateway`, `_quiesce_gateway`, `_resume_gateway`); persistent state
  handling (`_recover_interrupted_control`, `start_all`); unexpected-exit
  handling (`supervise`); and CLI choices (`_main`).
- `start-ultron.sh` and `stop-ultron.sh`: supported launcher wrappers.

### Database path and CRE writer entry points

The managed gateway's `service_entrypoint` uses `setdefault` to supply
`ULTRON_CRE_DATABASE` as `<project-root>/data/ultron_cre.db` when the variable
is absent. The gateway rejects a configured path that resolves elsewhere.
The manager's protected `~/.config/ultron/cre-runtime.json` allowlist does not
include a database-path setting; however, the manager inherits its own
environment for the gateway, so an operator still must verify the live
gateway's effective `ULTRON_CRE_DATABASE` value without dumping its
environment. The runtime's `create_content_runtime()` opens the code-defined
dedicated path through `ContentStore`.

The repository source scan found these CRE-capable database entry points:

1. The gateway runtime creates the shared `ContentStore`; gateway CRE
   operations using this runtime can write to the configured content database.
2. `tools/migrate_cre_private_render_operations.py` constructs a
   `ContentStore` and performs an explicit schema migration when invoked. It
   is an operator-invoked tool, not evidence of a scheduled host job.
3. `scripts/ultron_sqlite_backup.py` reads the source for an online backup and
   writes a separate backup destination; it is not a source-database writer
   when used as documented.

No systemd `.service` or `.timer` definition is present in the repository
search. This does not inventory host-level system/user units, cron, external
supervisors, ad hoc processes, or tools outside the workspace. The source scan
therefore cannot establish the complete live or scheduled writer set.

Source references:

- `ultron/application/runtime/service_entrypoint.py`: default
  `ULTRON_CRE_DATABASE` setup.
- `ultron/application/runtime/interface_gateway.py`: configured-path
  validation and gateway runtime construction.
- `ultron/application/content/status.py`: code-defined database path and
  `ContentStore` construction.
- `scripts/ultron_service_manager.py`: inherited gateway environment and
  allowlisted CRE runtime settings.
- `tools/migrate_cre_private_render_operations.py`: explicit migration
  `ContentStore` construction.
- `scripts/ultron_sqlite_backup.py`: online source-read/backup-output
  utility.

### What an authorized operator must verify

All commands in this section are labels for a future, separately authorized
maintenance procedure; none was executed during this investigation.

- **READ-ONLY:** use the host's approved process-inspection tools to identify
  the manager and gateway, then reconcile the gateway PID/start ticks,
  executable, command identity, working directory, and process group with the
  manager's `services.json` record. Confirm the listener on loopback port 8080
  is owned only by that same process. Do not treat an absent or stale manager
  record as proof that no gateway is running.
- **READ-ONLY:** inspect only the gateway process's `ULTRON_CRE_DATABASE`
  entry (not its complete environment). If absent, compare with the
  `service_entrypoint` default. Confirm the resolved path matches the
  dedicated path enforced by the gateway. Do not read `.env` files or print
  other environment values.
- **READ-ONLY:** identify processes with an open descriptor to the database
  and its WAL/SHM sidecars, and inspect host system/user service units,
  timers, cron entries, and any external supervisor that could launch a CRE
  writer. Include application, maintenance, migration, backup, and
  operator-launched tools in the inventory. A process-name search alone is
  insufficient because a writer need not have “CRE” in its command line.
- **SERVICE-CONTROL:** [The manager's `quiesce-gateway` command](#supported-service-controls)
  stops only its verified gateway process while retaining supervision of
  unrelated services. Its response says `GATEWAY_STOPPED` and
  `writersStatus=NOT_VERIFIED`. It does not assert that every database writer
  has stopped. Resume must be explicit with `resume-gateway`. No generic
  `systemctl`, `kill`, or external service-control command is established by
  repository configuration.
- **READ-ONLY:** [The manager's `status` command](#supported-service-controls)
  reports repository-observable manager records, process identity, expected
  listener ownership, readiness-probe status, and persistent quiesce state.
  It does not establish that external or scheduled writers are absent/stopped,
  nor does it reveal the live gateway's effective database path.
- **DATABASE-MUTATING:** no command in this investigation. No backup,
  migration, restore, or configured-database access was performed.

Before any future maintenance, an authorized operator must produce host
evidence for the gateway identity and effective database path, enumerate all
live and scheduled writers and their owning supervisors, and show that each
writer is stopped and cannot automatically restart for the full window. Use
the quiesce command only after verifying the launcher manager is the intended
manager instance. Confirm the response is `GATEWAY_STOPPED`, the state file
contains `gatewayQuiesce.status=QUIESCED`, and no listener remains on the
gateway port. This proves only that the manager-identified gateway process
stopped; separately inspect all database/WAL/SHM open descriptors and
supervisors before treating the database as quiescent.

### Quiesce state and failure handling

- Before signaling, the manager writes a control-operation record and a
  `gatewayQuiesce.status=QUIESCE_REQUESTED` marker containing the old process
  identity. This prevents a second manager start from treating the operation
  as an ordinary gateway absence.
- Success removes the gateway from the manager's supervised process/state
  records, records `gatewayQuiesce.status=QUIESCED`, and returns
  `status=GATEWAY_STOPPED`, `gatewayStatus=STOPPED`,
  `writersStatus=NOT_VERIFIED`.
- A failed/ambiguous signal, changed process identity, occupied listener, or
  inconsistent state returns a refusal/failure status. Quiesce state is kept
  fail-closed as `NEEDS_OPERATOR`; do not clear it by editing the JSON file.
- A normal manager startup honors a valid `QUIESCED` marker and skips gateway
  startup. It does not silently resume. An inconsistent marker blocks
  startup for operator review. If quiesce was interrupted and the original
  process identity remains live, the manager does not signal it again and
  blocks startup. If the recorded PID is absent, it can finalize the
  quiesced marker without starting the gateway. A reused/ambiguous PID blocks
  startup.
- Resume is an explicit manager request. It refuses unless the marker is
  exactly `QUIESCED`, no gateway process/record exists, and all other
  configured services are manager-owned, identity-verified, running, and
  healthy. The replacement must pass the normal launch readiness and unique
  listener checks. Interrupted or failed resume does not automatically retry;
  it preserves quiesced state if the candidate is safely gone, otherwise
  requires operator intervention.
- Unexpected failure of an unrelated managed service still follows the
  existing stack recovery path. Quiescing the gateway does not weaken
  supervision of the other services.

The persistent manager status distinguishes requested/stopped states, but
there is deliberately no `WRITERS_VERIFIED_STOPPED` success status: only the
authorized operator's host-level inventory can establish that fact. Resume
should occur only after the operator has completed maintenance and explicitly
chooses to restore gateway service.

**Operational conclusion:** repository code now provides a supported,
manager-owned gateway-only quiesce/resume operation. Host runtime status,
active database path, external supervisors, and the complete CRE writer
inventory remain unverified. Production migration remains **not authorized
and not executed**.

### Read-only writer evidence collector

[`scripts/cre_writer_evidence.py`](../scripts/cre_writer_evidence.py) emits a
timestamped JSON snapshot. From the project root, use the project's Python
interpreter and provide the **already verified** launcher-manager state
directory explicitly:

```sh
cd /home/ultron/Projects/ULTRON_INTERFACE_FINAL
PY=/home/ultron/Projects/ULTRON_INTERFACE_FINAL/.venv/bin/python
"$PY" -m scripts.cre_writer_evidence \
  --root "$PWD" \
  --state-directory /absolute/path/to/verified/launcher-state
```

The state-directory argument is required. Do not guess it: confirm it from the
authorized deployment configuration without displaying environment values.
The collector does not read environment variables, process command lines, or
database contents; it does not open SQLite, probe listeners, contact a service
manager, or control processes/services. It only reads repository source,
manager state JSON, `/proc` process identity fields and open-descriptor links,
and systemd service-file `Restart=` directives from its configured standard
unit directories. The manager record comparison checks PID, start ticks,
process group, executable, and working directory, but deliberately does not
inspect command arguments; a matching record is reported as a partial match,
not a verified process identity.

The JSON fields include `generatedAt`, `overallStatus`,
`quiescenceStatus`, `databaseTarget.accessed`, `configuredServices`,
`knownWriterInventory`, `managerState`, `processSnapshot`, `restartPolicies`,
`scheduledAndExternalSources`, and `limitations`. Evidence sources use
`CHECKED`, `NOT_CHECKED`, or `COULD_NOT_VERIFY`; process rows include PID and
start ticks, and only descriptors matching the configured database or its
WAL/SHM sidecars are reported. Systemd file policies are observations of
files only, not the active system/user manager's loaded state; it reads only
top-level non-symlink `.service` files and does not resolve drop-ins, generated
units, symlink aliases, timers, containers, or other external supervisors.
The relationship between a discovered unit and a CRE writer is
`NOT_CHECKED`; unit running state is also not queried. Cron contents are not
read. `NOT_CONFIGURED_IN_FILE` does not prove a unit cannot be restarted by
another mechanism.

Exit code `0` means collection completed but `overallStatus` is still
`NOT VERIFIED`; exit code `2` means evidence was incomplete. The collector
never reports `VERIFIED`: one point-in-time scan cannot prove that all
application, maintenance, operator-launched, scheduled, or external writers
are absent, nor that none can restart during the window. An empty descriptor
list is not proof that no process can later open the database. The tool is
engineering evidence, not an authorization or a substitute for the operator's
complete host-specific inventory, maintenance hold, supervisor controls, and
ongoing observation.

Before any database-mutating step, the operator must separately document:

- every writer and its owning service/scheduler/operator, with a reliable
  process identity and effective database target;
- the approved control or maintenance hold for each writer and every
  restart-capable supervisor, plus independent confirmation that each hold
  remains active for the whole window;
- a second in-window process/open-descriptor and restart-source check, with
  time and evidence source, immediately before migration and after any
  interruption;
- the operator's interpretation and disposition of every `NOT_CHECKED`,
  `COULD_NOT_VERIFY`, partial-identity, and unknown external-source result.

If any writer, supervisor, database target, or process identity is unknown,
or the no-restart hold cannot be maintained and observed, stop; do not infer
quiescence from manager state, an empty process/descriptor snapshot, or this
collector's overall status.

For gateway authentication recovery, the authorized operator must supply a
single recovery packet containing evidence, not authorization inferred by this
repository:

1. Verified launcher-manager parent/PID, process group, lease, state-directory
   ownership/type, control socket, and source identity.
2. Complete effective database-path determination for the gateway and every
   deployment-specific writer, including database, WAL, and SHM identities.
3. Live process/open-descriptor and scheduled/external restart-source evidence
   for the whole maintenance hold, including system/user units and drop-ins,
   timers, cron, containers, integrations, manual launchers, and external
   supervisors. Resolve every `NOT_CHECKED`, `COULD_NOT_VERIFY`, unreadable
   process, unknown target, and partial identity without guessing.
4. Independent confirmation that each writer is stopped and each restart source
   is controlled for the recovery window, with a second in-window check
   immediately before resume. A zero descriptor-match result is not sufficient.
5. Explicit authorization naming the maintenance window, gateway resume
   operation ID, authorized actor, and rollback/abort owner. Authorization does
   not supersede evidence; unresolved evidence remains a stop condition.

Only after the operator records that recovery packet may the documented
manager-controlled resume command be run:

```sh
"$PWD/.venv/bin/python" scripts/ultron_service_manager.py \
  resume-gateway --root "$PWD" --operation-id cre-resume-YYYYMMDD
```

Verify gateway readiness and ownership with the read-only `status` command and
`ss`, then perform a real loopback browser unlock. Do not use `restart-gateway`
or stack-wide `start`/`stop` as a substitute for recovery authorization.

## 17. Read-only migration evidence packet

[`scripts/cre_migration_evidence_packet.py`](../scripts/cre_migration_evidence_packet.py)
assembles explicitly supplied JSON evidence into a timestamped review packet.
It does not invoke the schema validator, writer collector, backup utility,
database, service manager, or host control APIs. Supply their saved output or
separately prepared operator evidence as files; the packet tool only reads the
named files and creates the requested new packet file.

### Invocation and evidence index

From the project root:

```sh
cd /home/ultron/Projects/ULTRON_INTERFACE_FINAL
PY=/home/ultron/Projects/ULTRON_INTERFACE_FINAL/.venv/bin/python
"$PY" -m scripts.cre_migration_evidence_packet \
  --manifest /absolute/path/to/evidence-index.json \
  --output /absolute/path/to/new-review-packet.json
```

The output must be an absolute, new, non-existing file in an existing
directory; it is created with mode `0600`. The index is an absolute UTF-8 JSON
file, format 1, and each evidence file is a simple `.json` basename in the
same directory as the index. Use non-sensitive evidence filenames. Each entry
must have a unique safe `id`, category, classification, filename, and pinned
SHA-256 digest:

```json
{
  "format": 1,
  "evidence": [
    {
      "id": "cre-schema-current",
      "category": "cre_schema_metadata",
      "file": "current-schema-observation.json",
      "classification": "PRODUCTION_CURRENT",
      "sha256": "<64 lowercase hex characters>"
    }
  ]
}
```

The digest is checked against the exact bytes read from an already-open file
descriptor and passed to the JSON parser. The reader rejects symlinks,
non-regular files, path-containing filenames, duplicate JSON object keys,
duplicate evidence IDs, reused filenames, and files over 1 MiB; the index is
limited to 100 entries. It detects content differing from the digest in the
supplied index; it does not authenticate who produced the evidence or index.
Evidence is not copied into the packet. The packet includes supplied evidence
basenames (which are not automatically screened for personal or sensitive
text), calculated digests, normalized timestamps, classifications, aggregate or
whitelisted summaries, findings, and limitations; it omits raw evidence
paths, process identifiers, destinations, unrecognized fields, and
application records. The CLI status line prints only the output basename, not
the absolute output path. Leaf files are opened without following symlinks,
and each evidence digest covers the exact bytes parsed. The evidence directory
is not held through a directory file descriptor, however, so hostile
concurrent replacement of the containing directory is not ruled out. Run only
with a controlled evidence directory that is not concurrently writable; hashes
provide consistency with the supplied index, not provenance or authenticity.

Allowed classifications are `PRODUCTION_CURRENT`, `OPERATOR_ATTESTATION`,
`HISTORICAL_REHEARSAL`, `TEST_FIXTURE`, and `OTHER`. Only current production
measurements can satisfy database/schema measurement categories. Operator
attestations can supply operational inventory and approval categories.
Historical and fixture evidence is included for review but can never satisfy a
current production requirement. In particular, output from
[`scripts/cre_schema_validator.py`](../scripts/cre_schema_validator.py) is
fixture-only and is not production baseline evidence; the writer collector's
`NOT VERIFIED` snapshot cannot establish quiescence.

All evidence must have a timezone-aware JSON timestamp named `verifiedAt`,
`capturedAt`, `generatedAt`, `recordedAt`, `createdAt`, or `timestamp`.
`verifiedAt` takes precedence when present. The packet applies a fixed
24-hour freshness window. Any evidence timestamp later than packet generation
time is rejected; there is no future clock-skew allowance. For backup
verification, both `verifiedAt` and the backup's `createdAt` must be fresh;
the packet reports the latter under `additionalTimestampChecks`. Missing or
invalid timestamps are unverified; stale or future timestamps block review.

### Required categories and minimum JSON fields

Exactly one current record for each category is required. Document field names
are case-sensitive. All records need a valid timestamp as above. The evidence
index entry `id` must be unique; every writer `identityEvidenceRef` must identify
the `process_identity` entry ID and that entry must contain exactly one stopped
process with the same `writerId`. Process identity records require a `writerId`
along with PID/start ticks, process group, executable, and working directory.
Restart-source `evidenceRef` must identify
the `maintenance_hold` entry ID. Evidence for the production database must use
the same safe opaque `databaseId` in `cre_schema_metadata`, `schema_baseline`,
`writer_inventory`, `open_descriptors`, and `backup_verification`'s
`sourceDatabaseId`; mismatch blocks the packet. This ID is checked internally
but is not included in the packet.

| Category | Classification | Minimum content |
|---|---|---|
| `cre_schema_metadata` | `PRODUCTION_CURRENT` | `databaseId`, `creSchemaVersion: 7`, `sqliteUserVersion: 0`, `assessment: "VERIFIED"` |
| `schema_baseline` | `PRODUCTION_CURRENT` | same `databaseId`, `status: "PASS"`, both distinct version fields, `schemaObjects.matches: true`, `tableCounts` array, `relationships` array, and `checks.integrityCheck: "ok"` / `checks.foreignKeyViolationCount: 0` |
| `writer_inventory` | `OPERATOR_ATTESTATION` | same `databaseId`, `assessment: "VERIFIED"`, `quiescenceStatus: "VERIFIED"` or `"QUIESCED"`, `inventoryComplete: true`, `databaseTarget`, and non-empty `writers` records containing `writerId`, `identityEvidenceRef` (the process identity evidence ID), and `state: "STOPPED"` |
| `process_identity` | `OPERATOR_ATTESTATION` | `assessment: "VERIFIED"` and non-empty `processes`, each with integer `pid`, `startTicks`, and `processGroup`, string `executable` and `workingDirectory`, `identityStatus: "VERIFIED"`, and `writerState: "STOPPED"` |
| `open_descriptors` | `OPERATOR_ATTESTATION` | same `databaseId`, `assessment: "VERIFIED"`, `checkedDatabaseTarget`, `checkedSidecars` containing `"-wal"` and `"-shm"`, and `matches: []` |
| `restart_sources` | `OPERATOR_ATTESTATION` | `assessment: "VERIFIED"` and non-empty `sources`, each with `sourceId`, `evidenceRef` (the maintenance-hold evidence ID), and `restartStatus: "HELD"` or `"DISABLED"` |
| `maintenance_hold` | `OPERATOR_ATTESTATION` | `assessment: "VERIFIED"`, timezone-aware `holdStart` and `holdEnd`, and `ongoingObservation: true` |
| `backup_output_approval` | `OPERATOR_ATTESTATION` | `decision: "APPROVED"`, `scope: "backup-output-creation"`. This is distinct from destination/privacy/retention approval and is only a supplied, structurally checked claim. |
| `backup_destination_approval` | `OPERATOR_ATTESTATION` | `decision: "APPROVED"`, `scope: "backup-output-destination"`, plus destination, privacy requirements, and retention period. It does not approve backup-output creation. |
| `backup_verification` | `PRODUCTION_CURRENT` | same `sourceDatabaseId`, verification evidence with `verifiedAt`, `verificationMethod: "scripts/ultron_sqlite_backup.py verify"`, `outcome: "VERIFIED"`, `backup.file`, integer `backup.size`, `backup.sha256`, `backupMetadata.integrity: "ok"`, `backupMetadata.userVersion: 0`, and `sourceMetadata.userVersion: 0` |
| `migration_v7_to_v9_approval` | `OPERATOR_ATTESTATION` | `decision: "APPROVED"`, `scope: "cre-v7-to-v9-migration"`. Does not approve v9→v10. |
| `migration_v9_to_v10_approval` | `OPERATOR_ATTESTATION` | `decision: "APPROVED"`, `scope: "cre-v9-to-v10-migration"`. Does not approve v7→v9. |
| `rollback_approval` | `OPERATOR_ATTESTATION` | `decision: "APPROVED"`, `scope: "restore-to-new-recovery-candidate"` |
| `service_control_approval` | `OPERATOR_ATTESTATION` | `decision: "APPROVED"`, `scope: "gateway-service-control"`. This scope does not represent approval for controls on external or additional writers; those use the separate `external_writer_control_approval` category where required. |
| `external_writer_control_approval` | `OPERATOR_ATTESTATION` | `decision: "APPROVED"`, `scope: "external-writer-service-control"`. This does not approve gateway control. |
| `cutover_approval` | `OPERATOR_ATTESTATION` | `decision: "APPROVED"`, `scope: "gateway-cutover"` |

Approval entries also require `recordedAt` (or another accepted timestamp
field). The packet displays `decisionClaim` only as a supplied JSON claim and
sets `operatorApprovalStatus` to `SUPPLIED_UNVERIFIED`. For every approval category, structural validation requires the exact
configured action scope; a mismatch fails that record. This does not
authenticate approval. None of these JSON scope or decision claims authenticates signatures,
identities, ticket systems, approval policy, or whether approval remains valid.
CRE `schema_version` and SQLite
`user_version` are compared and reported separately; backup `userVersion` is
not treated as the CRE schema version. Contradictory schema values, hash
mismatches, failed checks, missing categories, stale evidence, and invalid
classifications are listed as findings.

The existing schema validator is restricted to temporary fixtures, and the
writer collector cannot establish production quiescence. This packet tool
does not add a production database inspection method or a host command for
capturing these attestations. A separately reviewed, approved evidence
capture procedure is still required to produce current production schema and
baseline evidence, and the deployment operator must supply independently
supported writer/hold evidence. Do not label fixture output as
`PRODUCTION_CURRENT` to satisfy the packet.

The packet state is `INCOMPLETE` when required categories or usable evidence
are missing, `BLOCKED` when evidence is contradictory, stale, tampered, or
fails its stated checks, and `READY_FOR_OPERATOR_REVIEW` only when all
category checks pass without findings. In the current implementation, the
builder always adds an `independent_verification` finding because it has no
trusted verifier, reports `independentlyVerifiedCount: 0`, and therefore
cannot emit `READY_FOR_OPERATOR_REVIEW` or exit code 0. A structurally complete
packet remains `INCOMPLETE` unless a future reviewed design supplies trusted
verification without treating supplied claims as proof. Any future
`READY_FOR_OPERATOR_REVIEW` state would still be a document assembly result
only: the packet reports `operatorReviewOnly: true`, `executionPerformed:
false`, and `executionAuthorization: "NOT_GRANTED"`. It would not authorize
migration or any related action and would not replace the separately gated
decisions in this runbook. Exit code 2 indicates `BLOCKED` or `INCOMPLETE`.

For hold evidence, the packet additionally checks that the supplied hold
interval includes packet generation time; this does not verify the control
mechanism or future continuity. `open_descriptors.matches: []` and stopped
process entries are operator-supplied assertions, not a live check performed
by the packet tool.

For `backup_verification`, preserve the output of the backup utility's
read-only `verify` action as the manifest fields and place it in an evidence
JSON record with `verifiedAt` and the exact `verificationMethod` value above.
The packet checks these fields and the evidence-file hash; it does not run
`verify_backup()` or read the backup file itself.

The packet reports evidence supplied, structurally validated, independently
verified, operator approval status, and execution authorization separately.
It checks required fields and supplied assertions; it does not independently
verify them. It cannot turn a point-in-time process or descriptor
snapshot into proof of continuous quiescence, independently prove a backup's
contents, authenticate operator approvals, or establish that a host-specific
writer inventory is complete. Those operator-controlled evidence and
approval gates remain in force. No production evidence packet was assembled
as part of implementing this tool.

## 18. Trusted evidence-verifier boundary: decision register and readiness

**Status: DESIGN/DECISION SUPPORT ONLY — NO VERIFIER IMPLEMENTATION OR
MIGRATION AUTHORIZATION**

This section turns the proposed trusted evidence-verification boundary into
an organization decision register and an implementation-readiness checklist.
It does not select trust roots, owners, keys, vendors, policies, or a
cryptographic standard. Where the repository establishes no accountable
owner, the role is explicitly marked **UNASSIGNED**. “Go” below means only
permission to begin engineering implementation after the listed decisions
are recorded; it is not permission to collect production evidence, create a
backup, migrate, restore, control services, or cut over.

### Decision register

| ID | Required decision and why it is needed | Accountable role | Acceptable decision evidence | Blocked until decided |
|---|---|---|---|---|
| V-01 | Designate who is authorized to establish and change the verifier trust roots. The packet currently accepts only supplied hashes and has no provenance trust. A trust root supplied inside the packet cannot authenticate itself. | Security/trust authority — **UNASSIGNED** | An organization-controlled, versioned trust policy naming the authoritative trust-store provisioning channel, approver(s), change-control record, and verification procedure. Bootstrap must chain to an already-authoritative organization-controlled channel outside the new verifier and evidence manifest; the verifier cannot bootstrap or approve its own root. | Any claim of independent verification; verifier-boundary implementation beyond an untrusted interface prototype. |
| V-02 | Define verifier-key custody, access control, rotation, overlap, compromise response, revocation, and maximum revocation-information age. A signed response is only as trustworthy as key governance and current revocation status. | Security/key custodian — **UNASSIGNED** | Approved key-management and revocation policy, with accountable roles, auditable lifecycle events, recovery procedure, and fail-closed behavior when status is unavailable or too old. No key material is required in this decision record. | Trust configuration, key acceptance, and verification when revocation cannot be established. |
| V-03 | Define how human approvers and their authority are authenticated, including role-to-action policy and how approvals are withdrawn or expire. JSON `decision: APPROVED` is not proof of identity or authorization. | Security/data approval authority — **UNASSIGNED** | Approved identity and approval-policy specification, identifying the authoritative approval record and authentication mechanism, permitted action scopes, expiry/revocation handling, and audit reference requirements. | Treating any approval as verified or allowing it to satisfy an action gate. |
| V-04 | Define separation of duties: who may gather evidence, operate the verifier, approve each action, and execute it; determine which combinations are prohibited. This mitigates self-approval and single-identity false assurance. | Migration approver and security authority — **UNASSIGNED** | Approved role/segregation matrix and workflow showing independent review points and enforcement/audit evidence. | Approval authentication and any claim that a reviewer is independent of evidence collection or execution. |
| V-05 | Approve verifier coverage and the authoritative source for each claim: local database/schema and recovery-candidate checks; backup artifact checks; host process/descriptor checks; restart-source coverage; and completeness of the writer inventory. A verifier must not attest beyond what it can observe. | Repository engineering, deployment/host operator, security — **UNASSIGNED** | A signed-off coverage matrix naming each evidence claim, verifier identity/service boundary, source of truth, limits, and required human attestation. Explicitly identify external hosts/supervisors that the verifier cannot see. | Marking claims `VERIFIED`; proving writer-inventory completeness or continuous quiescence. |
| V-06 | Define the maintenance-window identity, how it is approved and communicated, and the challenge/nonce issuance and one-use replay ledger. Evidence must not be replayable across packets, databases, actions, or windows. | Deployment/host operator and migration approver — **UNASSIGNED** | An approved maintenance-window procedure with a unique window reference, authorized time bounds, nonce issuance/consumption ownership, replay rejection and audit behavior, and recovery procedure. | Binding operational evidence and approvals to a specific window; accepting any reusable challenge or evidence. |
| V-07 | Define trusted time sources, timezone requirements, freshness limits, future-time handling, and the relationship between evidence capture time, verifier time, approval time, and packet time. | Security authority and deployment/host operator — **UNASSIGNED** | Approved time/freshness policy specifying authoritative clock source(s), skew/uncertainty bounds, maximum evidence and revocation age, and fail-closed treatment of future or incomparable timestamps. | Any freshness-based `VERIFIED` result or cross-system validity comparison. |
| V-08 | Define evidence, signature, approval, verifier-result, trust-policy, and revocation-record retention, access, privacy, and disposal rules. Current packet output includes evidence filenames and selected summaries. | Security/data owner — **UNASSIGNED** | Approved retention/access policy specifying record classes, authorized readers, audit retention, protected storage, privacy review, and disposal requirements. | Production verifier output, retained signed audit records, or inclusion of sensitive fields beyond an explicitly approved minimal set. |
| V-09 | Define outage and uncertainty behavior for verifier, identity/approval system, trust store, revocation service, or time source. Availability must not cause fail-open behavior. | Security authority and service owner — **UNASSIGNED** | Approved failure-mode policy; every unavailable or indeterminate trust dependency must yield non-verified/incomplete status and must not imply approval or authorization. Any bounded offline mode must define authenticated cache provenance and freshness. | Acceptance during outages or any offline verification mode. |
| V-10 | Define the independent-verification result semantics and what constitutes a verifier’s successful check for each category. A signature authenticates a statement, not the underlying truth or completeness. | Repository engineering and security — **UNASSIGNED** | Reviewed evidence-to-check specification, including outcomes, evidence digest binding, source identity, database/window scope, validity interval, limitations, and audit fields for each category. | Any counter increment to `independentlyVerifiedCount` or a transition to `READY_FOR_OPERATOR_REVIEW`. |
| V-11 | Define policy for missing or non-required evidence and whether `NOT_APPLICABLE` is allowed for any category. The current migration’s categories are required and cannot become optional by a producer’s JSON claim. | Migration approver and security/data owner — **UNASSIGNED** | Explicit approved category policy with authorized decision role, reason requirements, scope, and audit representation. Until adopted, all currently required categories remain required. | Any N/A status or removal/waiver of a current required category. |
| V-12 | Define whether the packet’s future `READY_FOR_OPERATOR_REVIEW` state may be introduced and how to keep it semantically separate from execution authorization. The current builder deliberately remains `INCOMPLETE` without trusted verification. | Migration approver and security authority — **UNASSIGNED** | Approved state-transition and user-facing language policy affirming review-only meaning, `executionAuthorization: "NOT_GRANTED"`, and separate action-specific authorization gates. | Changing the current fail-closed state behavior or describing a packet as migration-ready/authorized. |

Decision evidence must be maintained by the organization’s designated control
process, not embedded as unverified values in this runbook or a packet
manifest. This repository does not identify the decision owners, approvers,
trust root, key custodian, verifier service, identity provider, or policy
authority.

### Technical design choices after organization decisions

The following are engineering evaluations, not choices this runbook makes:

- Select an established signature/attestation envelope and canonical
  serialization only after the organization approves trust roots, identities,
  role policy, revocation, and audit requirements. No algorithm, standard,
  vendor, or key type is selected here.
- Specify a verifier API that accepts exact evidence bytes/digest, category,
  scope, database identity, maintenance-window ID, and a one-use challenge;
  returns verifier identity, trust/key reference, verification time, expiry,
  result, check profile, signed digest, scope and bindings, and an auditable
  attestation ID.
- Keep evidence status, structural validation, verifier status, approval
  status, and execution authorization as separate typed fields. A parsed JSON
  claim must never self-promote status.
- Bind the signature to the hash of the exact bytes parsed, not a
  reserialized document. Validate category, scope, database ID, window ID,
  timestamp, validity period, key status, and challenge consumption before
  accepting a result.
- Verify database and window identity consistently across schema, baseline,
  backup, host, maintenance-hold, and approval evidence. A valid signature
  for another database, action, or window is invalid for this packet.
- Keep the verifier boundary read-only and separate from any database,
  backup, process, service-control, or migration action. Approval verification
  authenticates a decision; it does not execute or authorize a migration.
- Review the packet output for data minimization, safe error messages, audit
  retention, and resistance to verifier-scope overstatement before
  implementation.

Proposed status meanings:

| Status | Meaning | Readiness effect |
|---|---|---|
| `VERIFIED` | A trusted verifier authenticated the attestation and all applicable digest, identity, scope, database/window, check-result, time, expiry, and revocation conditions passed. | May count only for that evidence claim; does not itself approve or authorize an action. |
| `SUPPLIED_UNVERIFIED` | Evidence or approval was supplied but no trusted verification was completed. | Cannot satisfy independent-verification or approval requirements. |
| `INVALID` | Signature, key trust, digest, scope, identity, database/window binding, policy, or required check failed or conflicted. | Blocks review readiness. |
| `EXPIRED` | Evidence, attestation, approval, trust/revocation information, or window validity exceeded approved limits. | Blocks review readiness; obtain new in-scope evidence/decision. |
| `NOT_APPLICABLE` | A category excluded only by an explicit, policy-authorized exception with scope, rationale, decision identity, and audit record. | Not available for the migration’s currently required categories unless V-11 is decided and the category policy is formally changed. |

Verifier outage, unknown trust state, unavailable revocation status, uncertain
time, unsupported evidence type, or unverifiable source coverage must remain
`SUPPLIED_UNVERIFIED` or otherwise incomplete; they must never fall back to
`VERIFIED`. `INVALID` and `EXPIRED` are not recoverable by changing a JSON
field; new evidence or an authoritative decision is required.

### Evidence collected only during an approved maintenance window

The following are not prerequisites for beginning an engineering design
review, but must be collected and checked during a separately authorized
maintenance window before they can support any future production decision:

1. Current production CRE schema metadata, SQLite `user_version`, schema
   manifest/definitions, aggregate table counts and relationships, integrity,
   and foreign-key evidence from an approved production-safe procedure. The
   repository’s schema validator is fixture-only.
2. Current source/database identity and the complete host writer inventory,
   with process identities, open-descriptor checks, restart-source status, and
   ongoing no-restart/quiescence observations. A manager snapshot or empty
   descriptor result alone is insufficient; human operator accountability
   for inventory scope remains necessary.
3. If separately authorized, backup creation and independent verification of
   the exact backup artifact, bound to the source database and maintenance
   window. Destination, privacy, capacity, and retention decisions precede
   creation.
4. Signed, fresh, action-scoped approval records for each action that may be
   requested. A migration-window record is not a migration approval, and
   approval for one action cannot satisfy another.

No evidence collection or production operation is authorized by this section.

### Action-specific approvals remain separate

The existing runbook’s separate action gates remain independent:

- backup-output creation **and** approved destination/privacy/retention;
- v7→v9 migration;
- v9→v10 migration;
- recovery restore to a **new** candidate;
- gateway service control, and any separately required controls for other
  identified writers;
- gateway cutover.

The packet now has structurally distinct categories for backup-output
creation, backup destination, each migration stage (v7→v9 and v9→v10),
rollback restore, gateway control, external-writer control, and cutover. Each
requires an exact action scope; a legacy broad `migration_approval` record is
unsupported and cannot satisfy either stage. These records remain
`SUPPLIED_UNVERIFIED`: the schema change does not authenticate approvers,
establish authority, or prove that a decision is valid. Backup destination,
each migration stage, rollback restore, gateway control, external-writer
control, and cutover remain separate action gates. Restore approval does not
authorize cutover. A verifier’s `VERIFIED` result never replaces these
action approvals or any separate execution authorization.

### Verifier-boundary implementation go/no-go

**GO to begin verifier-boundary engineering implementation only if all of the
following are recorded and approved by the organization:**

1. V-01 through V-12 have dispositions from the designated authorities;
   each owner/role is formally assigned where currently `UNASSIGNED`.
   Any unresolved trust, identity, replay, revocation, coverage, or
   fail-closed policy is a no-go.
2. Trust-root bootstrap, key custody/rotation/revocation, approval identity,
   role and separation-of-duties rules, trusted time, verifier coverage,
   maintenance-window/challenge protocol, retention, and outage behavior are
   specified in authoritative artifacts.
3. Engineering and security approve the per-category verification contract,
   data bindings, state semantics, threat model, and negative-test plan before
   implementing any trust decision.
4. The design has no self-asserted or self-signed trust root, no approval
   authenticated solely by a JSON claim, no replayable challenge, no
   acceptance of stale revocation information, no cross-scope/database/window
   reuse, and no fail-open behavior.
5. The design keeps `READY_FOR_OPERATOR_REVIEW` distinct from execution
   authorization, preserves separate action-specific approvals, and cannot
   perform migration, backup, restore, service control, or cutover.

**NO-GO** if any prerequisite is unresolved, trust material is introduced
without an approved bootstrap process, verification can be bypassed by
changing evidence JSON, or a verifier outage can yield a positive result.
The current packet remains `INCOMPLETE` with
`independentlyVerifiedCount: 0`; this design note does not change that
behavior.

Passing the engineering implementation gate is **not** migration approval,
production readiness, or execution authorization. Production execution still
requires every separate runbook prerequisite and fresh, in-window evidence.

### Traceability: proposal prerequisites to decisions and migration blockers

| Proposal prerequisite | Decision register | Related outstanding migration prerequisite in this runbook | Required before verifier implementation? | Required again for migration window? |
|---|---|---|---|---|
| Trust-root ownership and bootstrap independent of the manifest | V-01 | No production evidence currently has authenticated provenance; packet cannot independently verify evidence. | Yes | Verify the approved trust policy/version used; no trust-root change is implied. |
| Key custody, rotation, compromise handling, and revocation freshness | V-02 | Packet cannot authenticate verifier or approval identity; current status is always unverified. | Yes | Confirm verifier/approval keys and revocation state remain valid under the approved policy. |
| Approver identity, authority, role-to-action policy | V-03 | Separate categories now represent backup-output creation and destination/privacy/retention, each migration stage (v7→v9 and v9→v10), restore, gateway and external-writer control, and cutover; identities and authority remain unauthenticated. | Yes | Obtain fresh, authenticated decisions for the exact action and scope, including each migration stage and any required writer controls; do not infer missing approval from another category. |
| Separation of duties / prevent self-approval | V-04 | Operator approvals remain decisions separate from evidence collection and execution. | Yes | Record accountable approver and executor identities per approved procedure. |
| Verifier coverage and source-of-truth per claim | V-05, V-10 | Current schema/baseline capture procedure is missing; complete production writer/restart inventory is not established; backup contents are not independently checked. | Yes | Collect current source-backed schema, backup, host, and no-restart evidence; operator retains responsibility for uncovered sources and completeness. |
| Maintenance-window identity, challenge/nonce, replay ledger | V-06 | Pre-window approvals and current writer-quiescence evidence are required; current snapshots do not prove continuous hold. | Yes | Establish the authorized window and collect evidence bound to that window; maintain ongoing observation. |
| Trusted clock, freshness, future-time and expiry rules | V-07 | Packet currently has a 24-hour age limit and rejects future evidence, but no cross-system trusted time assurance is defined. | Yes | Capture fresh timestamps with approved time basis; refresh when stale or outside the window. |
| Audit retention and evidence privacy | V-08 | Backup destination/privacy/retention approval and evidence-report handling remain unresolved. | Yes | Obtain destination and retention approval before backup output; retain evidence under the approved policy. |
| Fail-closed verifier, identity, revocation, and time outages | V-09 | No current independent verifier; unavailable or unverified evidence cannot meet readiness. | Yes | An outage or uncertainty blocks—not waives—the relevant gate. |
| Exact bytes, digest, scope, database and maintenance-window binding | V-10, V-06 | Current packet verifies the bytes against a supplied manifest but does not authenticate origin; database/window reuse policy remains to be defined. | Yes | Bind current baseline, backup, writer/hold evidence, and approvals to their exact source and window. |
| Status semantics including `NOT_APPLICABLE` | V-10, V-11 | Current evidence categories are required; historical/fixture data cannot satisfy production evidence. | Yes | No category may be waived without an approved policy decision and explicit evidence. |
| Distinguish review-ready from execution authorization | V-12 | Runbook requires separate explicit approvals and actions; current packet is review-only and `executionAuthorization` is `NOT_GRANTED`. The packet has structurally distinct categories, but cannot authenticate their identities, authority, signatures, or continued validity. | Yes | Keep backup destination and creation, each migration stage, restore, gateway and other required service controls, and cutover gates separately authorized; packet state does not grant execution rights. |
| Negative tests and verifier-scope limitations | V-10, V-09 | No verifier implementation or trust configuration exists; current tests cover packet structure, not cryptographic trust. | Yes, test plan approved before implementation | Run engineering tests before any production proposal; tests do not replace in-window evidence or approval. |

### Implementation-readiness checklist

This checklist is for the verifier-boundary engineering gate only:

- [ ] Authoritative owners/roles are assigned for all currently `UNASSIGNED`
  decisions; no person or team is inferred from repository authorship.
- [ ] Trust-root bootstrap, protected custody, rotation, revocation, and
  stale-revocation behavior are approved.
- [ ] Approver identity, scope/role policy, revocation, and separation of
  duties are approved for each action.
- [x] Packet schema structurally represents backup-output creation separately
  from destination/privacy/retention, both migration stages separately,
  restore separately from cutover, and gateway control separately from
  external-writer controls. This does not authenticate approval identities,
  authority, or signatures; future verifier work must do so without treating
  structural validity as approval.
- [ ] Coverage matrix states what local verifier, external trusted system,
  and human operator each can and cannot establish.
- [ ] Window identity, one-use challenge/replay protection, trusted time,
  freshness, and expiry semantics are approved.
- [ ] Privacy, audit retention, access, and disposal requirements are
  approved.
- [ ] Typed status semantics and fail-closed outage behavior are approved;
  no JSON field can self-promote evidence or approvals.
- [ ] Exact-byte digest binding and category/scope/database/window binding
  are specified and independently reviewed.
- [ ] Negative tests cover forged/unknown/revoked keys, signature mismatch,
  altered bytes, replayed approval/challenge, wrong database/window/scope,
  expired verification, verifier/revocation outage, and self-asserted
  `VERIFIED`/`APPROVED` claims.
- [ ] User-facing packet language preserves review-only status and
  `executionAuthorization: "NOT_GRANTED"`; action-specific approvals remain
  distinct.

An unchecked item means **NO-GO for verifier-boundary implementation**. A
fully checked list would permit only the separately scoped engineering
implementation review. It does not establish production readiness, does not
permit production evidence collection, and does not authorize migration,
backup, restore, service/process operation, or cutover.
