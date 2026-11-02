# Gateway operation lifecycle

The live interface gateway records consequential operations in
`data/ultron_operations.db`. Set `ULTRON_OPERATION_DB` to override that path.
The SQLite file is created with owner-only permissions. The record contains the
submitted operation parameters and execution result; protect the database as
local operational data.

Gateway requests are deduplicated by `correlationId`. Clients that retry a
request must reuse its original correlation ID. Reusing an ID with different
command content is rejected. Records are retained so a restart does not forget
an earlier correlation ID and accidentally execute it again.

The WebSocket server binds to loopback only; non-loopback listener addresses
are rejected. Loopback is not treated as identity. When
`ULTRON_INTERFACE_AUTH_TOKEN` is absent, the server has no production
authenticator, hides approval-required actions, and rejects consequential
dispatch before creating an operation. Setting the token activates the
server-side `WebSocketAuthenticator` for the actual `serve_interface` path.
The server rejects a configured token unless it is at least 43 URL-safe
characters, suitable for a 32-byte `secrets.token_urlsafe` value. Configure
`ULTRON_INTERFACE_AUTH_PRINCIPAL` to the operator identity; if omitted, the
server assigns the fixed `local-operator` identity. Neither value is accepted
from a client message.

CRE rights review uses a separate gateway principal. Configure
`ULTRON_INTERFACE_CRE_REVIEWER_TOKEN` and
`ULTRON_INTERFACE_CRE_REVIEWER_PRINCIPAL` together in the protected service
environment; the token must be unique and at least 43 URL-safe characters, and
the reviewer principal must differ from the operator principal. The CRE runtime
derives its reviewer allowlist only from these additional authenticated
principals. A reviewer must use the direct gateway authentication flow
(`?auth=token`) so the gateway sees the reviewer credential and identity. The
local unlock helper intentionally continues to authenticate as the configured
operator and does not forward reviewer credentials. Rights approval remains
subject to the asset-specific evidence, authority, agreement, scope, and
independent-review checks enforced by CRE.

Provision the token out of band with a secret manager or protected service
environment. Generate a new value with Python's `secrets.token_urlsafe(32)`;
do not put it in source control, browser configuration, a URL, command-line
arguments, or logs. The default dashboard path uses the local auth helper at
`ws://127.0.0.1:8081/ultron/stream`; it sends a local unlock passphrase to that
helper, which authenticates upstream to the gateway at
`ws://127.0.0.1:8080/ultron/stream` with the existing random token. The token
is never sent to browser JavaScript or returned by a local endpoint. The
dashboard does not write either credential to web storage or application
configuration. `?auth=token` selects the existing direct-token browser flow.
The direct token field supports native password-manager autofill, but an
OS-protected credential manager is preferred if the token is saved outside
this application.

For deployments without a service-level secret manager, the service manager
also reads optional CRE gateway settings from
`~/.config/ultron/cre-runtime.json`. The directory must be owned by the
service user and have no group/other permissions; the regular configuration
file must be owned by that user and mode `0600`. Symlinks, files larger than
8 KiB, malformed JSON, unknown settings, and a partial reviewer credential
pair are rejected. Supported keys are
`ULTRON_CRE_FASTER_WHISPER_MODEL`,
`ULTRON_INTERFACE_CRE_REVIEWER_PRINCIPAL`, and
`ULTRON_INTERFACE_CRE_REVIEWER_TOKEN`. Values from the manager's inherited
environment take precedence. The manager passes these settings only to the
gateway child and includes the reviewer credential in log redaction. Do not
commit this file or copy its contents into diagnostics. Provision the reviewer
principal and credential through an authorized secret-management process;
the launcher does not generate them. The file has this JSON shape; replace the
reviewer placeholders only through the approved provisioning process:

```json
{
  "ULTRON_CRE_FASTER_WHISPER_MODEL": "/home/ultron/.cache/ultron/models/faster-whisper-base",
  "ULTRON_INTERFACE_CRE_REVIEWER_PRINCIPAL": "<approved-distinct-reviewer-id>",
  "ULTRON_INTERFACE_CRE_REVIEWER_TOKEN": "<secret-manager-provided-value>"
}
```

Open the dashboard through the documented HTTP origin, not as a `file://` page.
Browsers give local files an opaque `null` origin, which the helper rejects
during the WebSocket handshake with HTTP 403 before authentication is attempted.
This origin rejection is intentional; do not add `null` or arbitrary origins to
the helper allowlist.

The helper's verifier lives in
`~/.config/ultron/interface-unlock.json`, mode `0600`; it contains a random
salt and scrypt verifier, never the passphrase. New verifier version 2 stores
an NFC-normalized passphrase; legacy version 1 continues exact, unnormalized
comparison and is not rewritten automatically. Setup/change require a
20-character minimum; use a long, independently chosen passphrase. The helper
binds only to `127.0.0.1:8081` and accepts browser connections only from the
exact dashboard origin `http://127.0.0.1:8000`. It does not open an upstream
gateway socket before local-password verification. It then reads the
owner-only `interface-token` file, authenticates using the existing gateway
protocol, and proxies only that authenticated session. A persistent SQLite
throttle at `~/.config/ultron/interface-unlock-throttle.db` counts failures
across connections and helper restarts: five failures cause a 30-second
lockout, with subsequent lockouts doubling up to 15 minutes. Wrong passphrases,
active lockouts, and upstream gateway authentication failures have distinct
protocol statuses; the dashboard does not combine them into one error. The throttle
database is owner-only. Localhost is not a trust boundary against other
processes running as the same OS user; those processes can generally access
the token file already. Loopback WebSockets are unencrypted.
Browser-to-helper frames remain limited to 64 KiB. Authenticated gateway frames
are separately limited to 12 MiB so bounded local preview responses can include
the existing 4 MiB video and up to 2 MiB of persisted scene images; larger
frames are rejected.

The gateway authenticator still retains only the SHA-256 token digest and
compares fixed-size digests with `hmac.compare_digest`. When provisioned
through the environment, the raw token is present in the backend process
environment, but is not written to operation, approval, event, or response
records.
Authentication errors are generic, and five failed attempts close the
connection. The credential is not placed in an operation, approval, event, or
response record.

## Local development provisioning

### Terminal execution policy

The generic terminal skill does not interpret shell syntax. The executor
automatically permits only the exact, argument-free `pwd` and `ls` commands in
the process working directory, using `shell=False`. Commands with arguments,
custom working directories, shell operators, interpreters, package managers,
or other executables are blocked before terminal-skill dispatch until a
trusted, action-bound confirmation path is implemented. A gateway operation
approval by itself does not override this executor policy. This restricted
surface is intentional; do not work around it by invoking a shell from another
skill or execution path.

### Conversation history privacy limitations

Conversation history is persisted by `ConversationHistory` in
`data/conversation_history.db` relative to the process working directory
unless a caller supplies another database path. The current store has no
operator-facing retention, export, or deletion API. Treat the database as
sensitive local data and protect it with the same care as other operational
records. Do not delete or replace a live database as a troubleshooting step;
retention and deletion semantics must be implemented and verified before
claiming user-controlled erasure.

### One-command local startup

From the project root, run `./start-ultron.sh`. It checks the gateway
(`127.0.0.1:8080`), actual frontend (`127.0.0.1:8000`), local authentication
helper (`127.0.0.1:8081`), and voice API (`127.0.0.1:8765`) in dependency
order. A listener is reused only when both its ULTRON process identity and its
service-specific readiness response are verified. An unidentified or
unhealthy listener is reported as a conflict and is never killed.

The launcher reads the gateway token from the existing owner-only
`~/.config/ultron/interface-token` file and passes it only to the gateway
process environment; it does not print or save the token. The existing
owner-only unlock verifier is required for the local helper. The launcher
does not create, reset, or alter either credential. Services it starts write
separate, owner-only logs and PID records under
`~/.local/state/ultron/interface-launcher/` (or
`$XDG_STATE_HOME/ultron/interface-launcher/`). Startup failures identify the
service and its log; known credential values are redacted from captured output.
If a voice process must be started, the launcher uses the
documented Edge engine explicitly and does not start XTTS or edit the voice
configuration. It prepares Edge before serving health checks; the manual
`--no-preload` option is intentionally omitted because the voice API reports
`ready=false` until `engine.prepare()` completes. A reused healthy voice
service is reported by the engine name and clone flag returned by its health
endpoint.

For the gateway child only, an unset `ULTRON_EXECUTION_BACKEND` is set to
`simulation`; an explicit value is preserved, and the application rejects
anything other than `simulation`. The application container uses a local
`PaperBroker`, and provider-backed order submission/cancellation is disabled.
This startup default does not authorize a consequential skill action or
enable live or paper-provider orders.

The command remains in the foreground while it supervises services started
by that invocation. Press Ctrl+C to gracefully stop only those services.
`./stop-ultron.sh` stops verified launcher-managed processes across invocations;
pre-existing services are never included in launcher state and remain running.
Both commands are safe to repeat: healthy services are reused rather than
duplicated.

## Standalone financial dashboard

`./run_financial_dashboard.sh` is a separate Streamlit application and is not
part of the managed gateway/frontend/auth/voice service group. It defaults
`ULTRON_EXECUTION_BACKEND` to `simulation`; an explicitly configured value is
preserved and rejected by the application unless it is `simulation`.
Install `requirements.txt` in the project virtual environment so the
Streamlit executable exists. The dashboard reads Alpaca account snapshots
using paper mode only and reports the time of the last fully successful
account/positions/orders query. Its paper-trade action remains subject to the
existing financial authorization guard. The Streamlit surface does not use
the operator console's local-unlock authentication; keep it on a trusted local
operator session and do not expose its HTTP listener to an untrusted network.

## System intelligence and helper-only restart

The authenticated dashboard's **System Intelligence** view collects a
read-only host snapshot from platform APIs, `/proc`, `/sys`, the local
service-state file, and available local tools. Each measurement carries its
source, observation time, and status. Missing GPU tools and unavailable
application probes remain `UNAVAILABLE` or `UNKNOWN`; a process record or
open port alone is not reported as application readiness. Process command
arguments are not returned; the inventory reports a command fingerprint
instead. Diagnostic observations are stored in the owner-only
`system-intelligence.db` below the launcher state directory. Helper-restart
transactions and their transition audit remain in the manager's atomic
`services.json` state record.

When the current service manager owns every configured child, the view may
offer **Restart auth helper**. It is an explicit operation, not an automatic
recovery action. The authenticated configured dashboard operator sends a
unique operation ID through the gateway; the manager accepts only the fixed
`restart-auth` request over its owner-only UNIX socket. Linux kernel peer
credentials must report the manager's effective UID. This is an OS-user
boundary: another process running as the same local user has the same IPC
identity and can access the same owner-readable files.

The manager holds an exclusive lifetime lease, serializes the request with its
service state lock, verifies the full helper process identity and unique
loopback listener, and durably records the authorized intentional transition
before signaling. It uses pidfd signaling and bounded SIGINT, SIGTERM, and
SIGKILL escalation only against the verified helper. The supervisor does not
poll children concurrently during this transaction; it resumes normal polling
immediately afterward, so other service crashes retain their existing
stack-recovery behavior. A replacement is recorded as a candidate, checked
for process identity, the expected `127.0.0.1:8081` listener, and the existing
local-unlock WebSocket greeting. The service record and `SUCCEEDED` audit
transition are committed together only after those checks pass. Duplicate
operation IDs return the original durable result.

Stale identities, listener conflicts, unavailable pidfd signaling, timeouts,
or failed readiness do not trigger recovery of other services. On startup, an
interrupted transaction with a recorded replacement is rolled back only when
that candidate's process identity is verified and pidfd signaling is
available; the candidate is stopped, the transaction is marked failed, and
ordinary startup can proceed. A healthy original helper is left untouched and
the interrupted transaction is marked failed. Ambiguous identities, malformed
transactions, or unsafe shutdowns block startup for operator inspection; no
ambiguous process is adopted or signalled. The manager lease and control
channel are started by the new launcher invocation. This protocol does **not**
guarantee continued supervision after abrupt manager death: no external
guardian or manager handoff is installed. If control IPC cannot be opened, the
manager reports that helper restart is unavailable but continues ordinary
crash supervision.

Restarting the helper closes its local WebSocket sessions; the gateway and
other services are not restarted. The dashboard's existing reconnect logic
reconnects to the loopback helper and requests the local unlock passphrase
again when necessary. The helper's authentication, exact origin allowlist,
loopback bind, upstream gateway token handling, and frame limits are unchanged.
Do not add origins or place either credential in browser storage, logs, command
arguments, or the inventory.

## Gateway-only restart

The service manager supports an explicit gateway-only restart. Invoke it from
the same local OS account as the running manager:

```text
/home/ultron/Projects/ULTRON_INTERFACE_FINAL/.venv/bin/python /home/ultron/Projects/ULTRON_INTERFACE_FINAL/scripts/ultron_service_manager.py restart-gateway --root /home/ultron/Projects/ULTRON_INTERFACE_FINAL
```

This command sends a bounded `restart-gateway` request to the existing
owner-only manager socket; it does not acquire a second manager lease or
restart the dashboard, auth helper, voice service, or any other child. The
manager serializes the operation in its supervisor, verifies the gateway's
recorded PID and process-start ticks against the manager-owned child and
expected unique loopback listener, and checks every other configured child
identity before signaling. A durable intentional transition is written
before the gateway receives pidfd-targeted SIGINT, followed by bounded
SIGTERM and SIGKILL only if necessary. Normal crash supervision remains
enabled for all other services and resumes immediately after the transaction.

The replacement must retain a verified manager-owned process identity, be the
unique listener on the configured loopback gateway address, and pass the
existing gateway readiness probe before the operation reports success. A stale
record, listener conflict, unavailable pidfd signaling, shutdown timeout, or
readiness failure is reported as a failed operation; the manager does not
automatically restart or terminate unrelated services. Use
`--operation-id <stable-unique-id>` only when a caller must retry the same
request; reusing an ID returns its durable result. Gateway clients disconnect
during this operation and must reconnect through their existing authenticated
flow.

## Manager handoff status and deployment boundary

The authenticated System Intelligence inventory reports manager handoff
preflight as `HANDOFF_BLOCKED` or `OWNERSHIP_UNCERTAIN`; it never starts a
handoff. `RUNNING_OLD` means the identified manager remains the owner.
The `managerHandoff` object always includes `status`, `phase`, `detail`,
`observedAt`, `managerPids`, `eligible`, `ownershipStatus`, `leaseActive`, and
`controlSocketAvailable`. `eligible` is always false in this build because
there is no manager-to-manager handoff protocol. `OWNERSHIP_VERIFIED` means
only that one identified manager, its verified service children, the active
lease, and its control socket agree; it does not mean transfer is supported.
An absent manager with an active lease/socket, multiple managers, or a stale
or mismatched service identity is reported as `OWNERSHIP_UNCERTAIN`.
`HANDOFF_REQUESTED`, `OLD_QUIESCING`, `OWNERSHIP_TRANSFER_PENDING`,
`NEW_MANAGER_ADOPTING`, `NEW_MANAGER_READY`, and `HANDOFF_COMMITTED` are
transaction phases required for a future supported transfer, not evidence that
those transitions have occurred. A multiple-manager observation is
`OWNERSHIP_UNCERTAIN`; any service identity whose recorded process is not
verified or whose parent differs from the manager is also uncertain.

There is currently no manager-to-manager handoff implementation. A transfer
would require an explicit old-manager request, a durable transaction before
quiescence, exclusive lease transfer, verified adoption of every service,
service-specific readiness, and a commit acknowledgment before the old owner
exits. Failure before commit must leave the old manager supervising; if its
identity or transaction becomes ambiguous, neither manager may adopt or signal
services and operator intervention is required. Rollback must not remove lease,
service, or transaction records to force startup.

Legacy managers cannot participate: they do not expose the new authenticated
control channel or lease protocol and cannot acknowledge quiescence. Starting
the updated launcher while such a manager is running is explicitly rejected;
matching PIDs, healthy listeners, or a free lease file are not authorization to
adopt. Do not use `stop`, signals, or a competing `start` to work around this
block. A live deployment is a separate operator-authorized operation and
requires a separately implemented and tested handoff mechanism or an
explicitly approved maintenance procedure with its own recovery plan.

An absent lease file or control socket is not proof that ownership has been
relinquished. The updated manager rejects a live process record unless that
same manager instance has a matching `Popen` handle; a PID/start-time record
alone is insufficient to adopt. Stale process identities are never signalled
by the startup path. A record that identifies a live process outside the
current manager requires a separately validated ownership transfer or
operator-reviewed maintenance transition.

Read-only preflight: authenticate to the supported local dashboard origin,
open **System Intelligence**, and request **Refresh inventory**. Record
`managerHandoff.status`, `phase`, `detail`, `observedAt`, manager PIDs, and the
service process/readiness statuses from that same snapshot. Proceed with no
deployment unless a separately reviewed protocol explicitly reports a verified
handoff capability; `HANDOFF_BLOCKED` and `OWNERSHIP_UNCERTAIN` are stop
conditions. This refresh does not request a handoff or alter manager/service
records. The inventory keeps `hostStatus` separate from `serviceStatus`; its
`hostStatus` is `VERIFIED` only when every host observation is verified,
`DEGRADED` when an observation establishes a known failure, and `UNKNOWN` when
any observation is unavailable, unknown, stale, or otherwise inconclusive
without a known failure. Its top-level `status` cannot be `VERIFIED` when a
required host observation, service identity, readiness result, or manager
ownership is stale, missing, or uncertain. A listener only means that a
listening socket was observed: service readiness remains `UNKNOWN` unless a
service-specific readiness probe provides evidence.

## Controlled maintenance deployment runbook

This is a planned-interruption procedure for a legacy manager that cannot
cooperate with a handoff. **It has not been run against the live stack.** Use a
separately approved maintenance window. This procedure requires access to the
terminal from which the current `./start-ultron.sh` is running so the owning
manager can receive its own Ctrl+C shutdown request. If that terminal is not
available, stop; do not substitute a PID signal or a second manager.

### Lifecycle and persistent data

The manager starts configured services in this order: gateway (`127.0.0.1:8080`,
authenticated WebSocket greeting), frontend (`127.0.0.1:8000`, expected console
HTML), authentication helper (`127.0.0.1:8081`, local-unlock WebSocket greeting
with the exact dashboard origin), then voice (`127.0.0.1:8765`, HTTP health
with `ready=true` and an engine name). The dashboard's local connection uses
the helper, which authenticates/proxies to the gateway. Voice is an independent
API service; its readiness is not implied by the gateway. The manager retains
`Popen` handles only for services started by that manager. It does not adopt
healthy pre-existing services into its supervision loop.

On normal Ctrl+C, the manager stops recorded services in reverse service
record order using its existing bounded shutdown procedure, clears each
successfully stopped record, and exits. A failed/ambiguous stop can leave a
record or listener behind; that is an abort condition, not permission to start
another owner. Each startup readiness probe is bounded by the manager's
30-second per-service timeout. A failure during startup rolls back only
verified services started by that invocation; services the manager cannot
verify or stop remain an operator-intervention case.

The manager opens its control socket only after service startup and readiness
checks. If that initialization fails, it now rolls back only its own tracked
children in reverse order while retaining the manager lease. The original
control-channel error and per-service rollback outcomes are written to
`services.json` as `startupRecovery`. A fully successful cleanup is recorded
as `ROLLED_BACK`; an identity mismatch, residual process, or state-write
failure is recorded as `OPERATOR_INTERVENTION_REQUIRED`. In the latter case,
the manager keeps its lease and supervises remaining tracked children rather
than returning ownership. A later manager startup refuses adoption while that
intervention state remains. Resolve the residual process and state through an
operator-reviewed procedure; do not remove the marker or service records to
force startup.

CRE preview, review, audio-evidence, and scene records use
`data/ultron_cre.db`; gateway operations use `data/ultron_operations.db`.
The gateway may select another existing absolute CRE database file by setting
`ULTRON_CRE_DATABASE` in its launch environment. The selected file must be a
regular, non-symlink content database with the runtime-required schema; the
project data path remains the default. The service manager forwards this
setting only to the gateway process.
Manager state and logs are under
`~/.local/state/ultron/interface-launcher/` (or
`$XDG_STATE_HOME/ultron/interface-launcher/`). A maintenance journal is stored
there separately as `maintenance.json`. Service shutdown/startup must not
delete or replace CRE/operations databases, credentials, manager records, or
logs.

### Release bundle and database backup tools

`scripts/ultron_release_bundle.py` builds a reproducible ZIP from an explicit
operator-reviewed file list. It does not infer a deployable product inventory.
The list is a newline-delimited set of root-relative paths; the tool rejects
paths outside the root, symlinks, environment files, databases/WAL/SHM, virtual
environments, caches, logs, backups, and generated build directories. The ZIP
contains a SHA-256/size manifest, uses normalized archive metadata, and is
verified before it is published. Verify and restore are separate explicit
commands; restoration requires a new destination directory. The archive and
its `.sha256` sidecar are never evidence of a known-good release on their own.
The precise file list must be approved for the exact deployment, and its
reported test suite must pass before an operator independently accepts the
bundle:

```sh
cd /home/ultron/Projects/ULTRON_INTERFACE_FINAL
.venv/bin/python scripts/ultron_release_bundle.py build \
  --root "$PWD" \
  --files-from "$APPROVED_RELEASE_FILE_LIST" \
  --release-id "$APPROVED_RELEASE_ID" \
  --output "$RELEASE_OUTPUT/ultron-release.zip"
sha256sum --check "$RELEASE_OUTPUT/ultron-release.zip.sha256"
.venv/bin/python scripts/ultron_release_bundle.py verify \
  --archive "$RELEASE_OUTPUT/ultron-release.zip"
.venv/bin/python scripts/ultron_release_bundle.py restore \
  --archive "$RELEASE_OUTPUT/ultron-release.zip" \
  --destination "$EMPTY_TEMP_DIRECTORY/restored-release"
```

The allowlist is intentionally not checked into this runbook: source discovery
alone does not establish which exact files constitute an approved release.
No release bundle was produced as part of this mission. Do not copy a
test-generated archive into a deployment or rollback location.

Release manifests may additionally record `sourceRevision`, the exact supported
CRE schema list, configuration schema version, and a separately verified
rollback release plus instructions. These fields are evidence about the build;
they never declare compatibility by themselves. A rollback claim requires the
referenced release restored and tested against the supported runtime, with an
independent checksum record:

```sh
.venv/bin/python scripts/ultron_release_bundle.py build \
  --root "$PWD" \
  --files-from "$APPROVED_RELEASE_FILE_LIST" \
  --release-id "$APPROVED_RELEASE_ID" \
  --source-revision "$APPROVED_SOURCE_REVISION" \
  --supported-cre-schema 10 \
  --config-schema-version "$APPROVED_CONFIG_SCHEMA_VERSION" \
  --rollback-release-id "$APPROVED_ROLLBACK_RELEASE_ID" \
  --rollback-instructions "$APPROVED_ROLLBACK_INSTRUCTIONS" \
  --output "$RELEASE_OUTPUT/ultron-release.zip"

.venv/bin/python scripts/ultron_cutover_identity.py \
  --release-archive "$RELEASE_OUTPUT/ultron-release.zip" \
  --database "$APPROVED_DATABASE_PATH"
```

The identity command verifies the archive, hashes the database main file, and
hashes existing WAL/SHM sidecars without opening the database. Because a main
file hash alone can omit committed WAL state, it does not prove logical schema
or data identity. Obtain logical identity only through the separate disposable
copy procedure below.

For disposable-copy validation, never point the validator at an operational
SQLite file. Create a consistent backup into a new `/tmp` destination and
validate that destination; SQLite backup captures WAL state into the new main
file. Remove or isolate its sidecars before validation if present. This
procedure deliberately does not copy a live database file or open it directly:

```sh
mkdir -m 700 /tmp/ultron-cre-v10-validation-XXXXXX
.venv/bin/python scripts/ultron_sqlite_backup.py backup \
  --source "$V10_WORKING_COPY" \
  --destination-dir /tmp/ultron-cre-v10-validation-XXXXXX \
  --name cre-v10-disposable \
  --expected-user-version 0
.venv/bin/python scripts/cre_schema_validator.py recovery \
  --database /tmp/ultron-cre-v10-validation-XXXXXX/cre-v10-disposable.sqlite \
  --expected-cre-schema-version 10 \
  --expected-user-version 0
```

PASS proves the disposable copy's integrity, foreign-key state, schema, metadata,
and runtime preflight. It does not alter, replace, or validate the operational
main file/WAL pair; preserve separate evidence that the operational database
path is the intended copy. WAL/SHM files beside the disposable backup are
not evidence of an additional database writer and must not be copied verbatim
without SQLite backup handling.

`scripts/ultron_sqlite_backup.py` makes a consistent SQLite online backup while
the gateway remains available. It reads the source through SQLite's read-only
connection and online backup API; it never copies, checkpoints, migrates, or
replaces the source database or its WAL/SHM files. Each invocation creates a
new owner-only backup directory, writes a mode-0600 database and manifest,
records UTC time, source journal mode, user/application version, schema
fingerprint, integrity result, size, and SHA-256, then verifies the backup.
For a future authorized maintenance operation, first establish the expected
schema version for each named database from the approved release's migration
specification. Then run one backup per database into distinct, previously
unused directories:

```sh
mkdir -m 700 "$BACKUP_ROOT"
.venv/bin/python scripts/ultron_sqlite_backup.py backup \
  --source "$PWD/data/ultron_cre.db" \
  --destination-dir "$BACKUP_ROOT/cre" \
  --name "ultron_cre" \
  --expected-user-version "$APPROVED_CRE_USER_VERSION"
.venv/bin/python scripts/ultron_sqlite_backup.py backup \
  --source "$PWD/data/ultron_operations.db" \
  --destination-dir "$BACKUP_ROOT/operations" \
  --name "ultron_operations" \
  --expected-user-version "$APPROVED_OPERATIONS_USER_VERSION"
.venv/bin/python scripts/ultron_sqlite_backup.py restore \
  --backup "$BACKUP_ROOT/cre/ultron_cre.sqlite" \
  --manifest "$BACKUP_ROOT/cre/ultron_cre.manifest.json" \
  --destination "$EMPTY_TEMP_DIRECTORY/cre-restored.sqlite" \
  --expected-user-version "$APPROVED_CRE_USER_VERSION"
```

Run the corresponding restore rehearsal for the operations backup as well.
The backup utility enforces the recorded database version/schema fingerprint
and integrity values, but those values must still be compared with the
operator-approved migration/schema specification before deployment. Preserve
the original databases and sidecars. Do not perform the backup or restoration
commands against production during a read-only audit.

The current deployment-preparation checks qualify only the bundle and backup
utilities and the source revision under test. They do not qualify a production
release: there is no approved release allowlist, no generated release archive,
and no independently stored checksum for a prior known-good revision. Preserve
the test output and exact source revision with a future approved bundle before
calling that artifact a rollback candidate.

### Preconditions and read-only preflight

1. Obtain explicit operator authorization for the outage, the exact workspace
   release, and the recovery plan. Confirm the deployment does not include
   OMEGA, Trading, voice configuration changes, publishing, or trading actions.
2. Confirm the original manager terminal is available. Record the manager PID,
   start identity, executable, working directory, command fingerprint, and
   each service PID/start identity from authenticated System Intelligence and
   the protected `services.json`. Never identify a process by PID alone.
3. Confirm each recorded process has the expected executable, project working
   directory, command identity and process group, is a child of the identified
   manager, owns its expected loopback listener, and passes its own readiness
   probe. A missing/stale record, multiple manager, unexpected parent, extra
   listener owner, failed probe, or non-loopback listener is NO-GO.
4. Verify the approved workspace files and dependencies without printing
   environment variables or credentials. Verify the Python executable,
   required files, and installed packages. Obtain a known-good prior release
   bundle and its independently recorded checksums. **If no prior release
   bundle exists, rollback is not prepared and this procedure is NO-GO.**
5. While the gateway is still serving, make SQLite-consistent backups of the
   CRE and operations databases using the SQLite online backup API into a new
   owner-only directory named for the maintenance operation. Run
   `PRAGMA integrity_check` on each source and backup, record file hashes and
   sizes, and preserve the original files. Do not copy a live SQLite database
   file without its WAL state. If either database is absent, unreadable, or
   fails integrity checks, stop before shutdown and investigate.
6. Capture the protected service-state record and sanitized manager/service
   logs in the same backup area. Do not copy credential files or raw
   environment dumps. Test that the backup can be opened and that expected CRE
   records/evidence bindings remain present.
7. Begin the durable operator journal. Its notes are operator assertions, not
   independent proof; include concise evidence locations/observations and no
   secrets:

   ```sh
   cd /home/ultron/Projects/ULTRON_INTERFACE_FINAL
   .venv/bin/python scripts/ultron_maintenance_journal.py \
     --state-dir "$HOME/.local/state/ultron/interface-launcher" \
     begin --operation-id "maintenance-YYYYMMDD-HHMM" --root "$PWD" \
     --evidence "Preflight identities and database backup manifest recorded."
   ```

   Use the same `--operation-id` for subsequent journal commands. The journal
   is owner-only, atomically replaced, serialized by an exclusive file lock,
   and retained if execution is interrupted. Duplicate identical transitions
   are idempotent. It records assertions; it does not operate or verify
   services. Check status and record each observed transition with:

   ```sh
   STATE_DIR="$HOME/.local/state/ultron/interface-launcher"
   OPERATION_ID="maintenance-YYYYMMDD-HHMM"
   .venv/bin/python scripts/ultron_maintenance_journal.py \
     --state-dir "$STATE_DIR" status --operation-id "$OPERATION_ID"
   .venv/bin/python scripts/ultron_maintenance_journal.py \
     --state-dir "$STATE_DIR" advance --operation-id "$OPERATION_ID" \
     --status STOP_REQUESTED --evidence "Operator requested Ctrl+C in owner terminal."
   ```

   For any failure after shutdown is requested, replace `advance` with `fail`
   and a concise evidence note. This records
   `OPERATOR_INTERVENTION_REQUIRED`; it does not attempt recovery.

### Bounded maintenance sequence

1. Verify `status` is `PREPARED`. If any precondition changed, use `abort`
   before requesting shutdown. Once shutdown is requested, abort is no longer
   available; failures require operator intervention.
2. Record `STOP_REQUESTED`, then return to the original manager terminal and
   press Ctrl+C once. This invokes the manager's existing shutdown path; do not
   run `stop-ultron.sh` concurrently with the live manager.
3. Within 60 seconds, verify the old manager PID/start identity is gone, all
   four captured service identities have exited, their expected ports have no
   listeners, and `services.json` has no service records. Also verify the
   manager terminal reports its normal graceful exit. Record
   `OLD_OWNER_RELEASED` only after all conditions hold. If any process remains,
   any record is ambiguous, or the manager exits abnormally, mark
   `OPERATOR_INTERVENTION_REQUIRED`; do not launch the replacement manager.
4. Record `START_REQUESTED`, then execute exactly once from the approved
   workspace: `./start-ultron.sh`. Allow up to 30 seconds for each service
   readiness check; do not launch a second copy if it is slow. The new
   manager must hold its exclusive lifetime lease and expose its protected
   `manager.sock` before it is considered active. Verify a single manager
   process, the owner-only lease/socket, and all service process identities
   against newly written records. Record `NEW_MANAGER_READY` only after those
   checks.
5. Verify the four per-service readiness results and loopback addresses,
   continued manager supervision, and absence of unexpected services. Record
   `SERVICES_READY`.
6. From the supported local dashboard origin, authenticate privately and
   refresh System Intelligence. Confirm current timestamps, evidence source,
   service state, manager lease/control socket, and truthful unknown/stale/
   unavailable states. Confirm stale prior browser data is not displayed as
   current after disconnect. Do not send credentials to chat or logs. Record
   `DASHBOARD_VERIFIED` only after observing the live authenticated response.
7. Verify existing CRE status and current persisted-preview/evidence readback
   without creating content. Confirm the original data remains readable,
   scene/audio/artifact hashes and bindings still verify, rights and human
   review state remain unchanged, approval is not granted automatically, and
   publishing remains disabled/mock-only. Do not publish. Record
   `CRE_VERIFIED` after these checks.
8. Re-run `status`; the operator may record `COMMITTED` only after all
   preceding transitions are present, in order, and backed by the recorded
   evidence. A journal status alone is not proof of deployment or health.

The normal maintenance outage is expected to include bounded service shutdown
and sequential startup. Reserve at least ten minutes including verification;
if the maintenance window expires, do not rush, kill processes, or skip
readiness. Keep operator traffic and content writes paused until commit.

### Recovery and abort rules

| Failure point | Required response |
|---|---|
| Before `STOP_REQUESTED` | `abort` the journal; leave all services running. |
| During shutdown / manager exits but a process or record remains | Record `OPERATOR_INTERVENTION_REQUIRED`; preserve records and logs. Do not start a manager or signal an ambiguous process. |
| New manager cannot acquire exclusive lease | Do not retry concurrently. Inspect the identified lease holder and process identity; operator intervention is required. |
| Manager control-channel initialization fails after service startup | The manager records the original failure and rolls back only verified children it started, in reverse order. If any process identity, shutdown, or state update remains uncertain, it records `OPERATOR_INTERVENTION_REQUIRED`, retains the lease, and stays in supervision. Do not start another manager or clear the recovery marker. |
| Gateway fails | Startup rolls back only verified services started in that attempt. Keep dashboard unavailable; inspect gateway log and records. Do not claim readiness. |
| Auth helper fails | Same bounded startup rollback; do not weaken unlock/origin policy or bypass through another endpoint. |
| Frontend fails | Same bounded rollback; do not represent the backend as dashboard-verified. |
| Voice readiness fails | Startup rolls back verified new services in reverse startup order. Do not alter voice engine/configuration to force green status. |
| Any rollback cannot verify or stop its process | Preserve the process and records; mark `OPERATOR_INTERVENTION_REQUIRED`. Never kill by PID alone. |
| Database backup or post-start integrity check fails | Keep writes paused; preserve original and backup files. Do not overwrite or delete either database. Escalate to a data-recovery operator. |
| Dashboard/System Intelligence or CRE continuity fails | Do not commit. Capture sanitized evidence, retain the journal, and use only a separately verified prior release/rollback plan. |
| Manager/process crash at any journal stage | The journal remains at the last durable operator assertion and is not auto-committed. Do not infer success from manager exit or listener presence. Re-run read-only identity/state checks; ambiguous ownership requires operator intervention. |

Rollback is not an automatic restart. Only if the old release bundle and
checksums were validated before shutdown, the new manager has fully exited,
all service identities/listeners are conclusively absent, and no new writes
occurred may an authorized operator restore the known-good release and run its
launcher once. If data changed after startup, do not restore the database
backup blindly; preserve both copies and obtain application-specific recovery
review. If any identity or ownership check is uncertain, stop and escalate.
Never delete manager locks, leases, transaction journals, state records, or
databases to force a retry.

This workspace has no systemd unit, container definition, or other managed
service configuration for ULTRON. Its application entry point is `main.py`,
which starts the loopback WebSocket server and the interactive CLI in the
foreground. For local development, provision the bearer credential in a
dedicated owner-only file outside the workspace, then read it into the launch
shell without printing it or putting it in shell history:

```sh
cd /home/ultron/Projects/ULTRON_INTERFACE_FINAL
install -d -m 700 "$HOME/.config/ultron"
chmod 700 "$HOME/.config/ultron"
umask 077
.venv/bin/python - <<'PY'
import os
import secrets
from pathlib import Path

token_path = Path.home() / ".config/ultron/interface-token"
descriptor = os.open(
    token_path,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
    0o600,
)
with os.fdopen(descriptor, "w", encoding="ascii") as token_file:
    token_file.write(secrets.token_urlsafe(32) + "\n")
PY
chmod 600 "$HOME/.config/ultron/interface-token"
```

The generator refuses to overwrite an existing credential. Keep the file
outside the project and do not display or copy its contents into chat, source
control, browser configuration, logs, or command-line arguments. Start ULTRON
from the same terminal so the credential is inherited only by that process:

```sh
IFS= read -r ULTRON_INTERFACE_AUTH_TOKEN < "$HOME/.config/ultron/interface-token"
export ULTRON_INTERFACE_AUTH_TOKEN
export ULTRON_INTERFACE_AUTH_PRINCIPAL=local-operator
export ULTRON_EXECUTION_BACKEND=simulation
exec .venv/bin/python main.py
```

Before launching, this command reports only whether the environment contains a
well-formed credential; it never prints the credential:

```sh
.venv/bin/python -c 'import os,re; value=os.environ.get("ULTRON_INTERFACE_AUTH_TOKEN", ""); print("authentication configured" if re.fullmatch(r"[A-Za-z0-9_-]{43,}", value) else "authentication not configured or invalid")'
```

This check validates the environment value's presence and shape, not that a
running server accepted it. For the first local unlock setup, enter the
passphrase directly at the hidden terminal prompt. It must be at least 20
characters; a memorable passphrase made from five randomly selected words is
recommended. The command saves only a salted scrypt verifier, not the
passphrase:

```sh
.venv/bin/python -m ultron.application.runtime.local_auth_helper setup-password
.venv/bin/python -m ultron.application.runtime.local_auth_helper serve
```

Run `serve` in its own terminal after the backend and static dashboard are
running. The helper fails closed if the token, verifier, gateway authentication,
origin, or file permissions are invalid. To change a forgotten or compromised
local passphrase, run `change-password`; this replaces the verifier atomically
without changing the gateway token or invalidating already authenticated
sessions. To clear a local lockout after confirming the OS account is secure,
run `reset-throttle`. Existing gateway sessions keep
their original expiry; stopping the helper closes its proxied connections and
revokes those sessions at the gateway. The direct-token mode remains available
at `http://127.0.0.1:8000/?auth=token`.

## Exposed token rotation

The token was printed to a terminal by a `cat` command. Treat it as exposed; do
not copy it into chat, logs, screenshots, or commands. The following procedure
is prepared but has not been run. It causes downtime and revokes existing
gateway sessions when the backend is stopped. Do not start until the operator
is ready to verify the candidate and accept that session impact.

1. Stop the local auth helper. Generate a new token into a separate file; the
    generator refuses to overwrite an existing candidate and prints no secret:

```sh
umask 077
.venv/bin/python - <<'PY'
import os
import secrets
from pathlib import Path

token_path = Path.home() / ".config/ultron/interface-token.next"
descriptor = os.open(
    token_path,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
    0o600,
)
with os.fdopen(descriptor, "w", encoding="ascii") as token_file:
    token_file.write(secrets.token_urlsafe(32) + "\n")
PY
stat -c 'candidate mode=%a owner=%U:%G bytes=%s' "$HOME/.config/ultron/interface-token.next"
```

2. At the ULTRON CLI, enter `exit` to stop the backend. Start it using the
    candidate in the process environment, keeping the token out of arguments
    and shell history:

```sh
IFS= read -r ULTRON_INTERFACE_AUTH_TOKEN < "$HOME/.config/ultron/interface-token.next"
export ULTRON_INTERFACE_AUTH_TOKEN
export ULTRON_INTERFACE_AUTH_PRINCIPAL=local-operator
export ULTRON_EXECUTION_BACKEND=simulation
.venv/bin/python main.py
```

3. From another terminal, verify the candidate against the live gateway. This
    sends only the intended authentication frame, requests no subscriptions,
    and prints only PASS or FAIL:

```sh
.venv/bin/python scripts/verify_interface_auth.py --token-file "$HOME/.config/ultron/interface-token.next"
```

4. Only after PASS, ensure no previous backup exists, back up the old file
    owner-only, and atomically promote the candidate. Verify the active path
    once more:

```sh
test ! -e "$HOME/.config/ultron/interface-token.previous" || { printf '%s\n' 'Previous-token backup already exists; stop and inspect it.'; exit 1; }
cp -p "$HOME/.config/ultron/interface-token" "$HOME/.config/ultron/interface-token.previous"
chmod 600 "$HOME/.config/ultron/interface-token.previous"
mv -f "$HOME/.config/ultron/interface-token.next" "$HOME/.config/ultron/interface-token"
.venv/bin/python scripts/verify_interface_auth.py
```

After PASS, remove the old token backup and restart the helper so it reads the
new active token:

```sh
rm -f "$HOME/.config/ultron/interface-token.previous"
.venv/bin/python -m ultron.application.runtime.local_auth_helper serve
```

If either verification fails, enter `exit` at the ULTRON CLI, restore
`interface-token.previous` to `interface-token`, and relaunch ULTRON with that
restored file using the launch steps above. Do not delete the backup until
verification succeeds.
The backend token necessarily exists in its process environment; protect the
local OS account and do not forward either WebSocket port off-host.

The session is bound to its WebSocket connection and the server-configured
principal. Sessions expire after 15 minutes by default and after five minutes
of inactivity; the configured maximum lifetime can be reduced using
`ULTRON_INTERFACE_SESSION_TTL_SECONDS` (1–900 seconds). The live browser sends
periodic PING messages to maintain the idle window. Disconnect revokes the
connection's session, expiry closes the connection, and process restart or
credential rotation revokes all sessions. Rotation requires updating the
protected environment and restarting ULTRON. This is a single-operator local
bearer boundary, not multi-user identity federation; OS users with access to
the process environment or local listener remain in the trusted host boundary.
Remote access and TLS termination are not implemented here.

## Conversational chat and voice output

The dashboard's CHAT environment sends `CHAT_MESSAGE` only over the
authenticated `/ultron/stream` WebSocket. The gateway calls the configured
conversation manager's chat handler directly, using the authenticated session
ID for conversation history. This path does not use `DISPATCH_COMMAND` and
does not create or approve an operation. Chat requires both an authenticated
session and a configured LLM provider; unavailable providers and invalid model
responses are reported as failures, not replaced with simulated responses.
Authenticated questions about system/runtime statistics include the existing
gateway runtime state and most recent host snapshot, including its source
timestamp when reported. Missing fields are reported as unreported rather than
inferred. Chat does not have a repository
inspection or arbitrary tool-execution capability, and must not claim either
was used.
Model work runs off the gateway event loop; the Ollama HTTP request has a
120-second timeout and chat reports a timeout after 125 seconds.
Conversation history is stored in the local SQLite database at
`data/conversation_history.db`, scoped to the server-authenticated session ID;
it is not written to browser storage.

Voice output uses the local speech service at
`http://127.0.0.1:8765/api/v1/speak`. It is independent of chat: a missing or
failed speech service does not prevent text requests. Responses can be played
on demand; automatic speech is opt-in and off by default. The browser stops
playback and releases generated audio URLs when stopped or when another
response begins. The dashboard does not claim a specific voice identity based
on the engine name. Microphone input is not exposed: the current
`SpeechRecognizer` has no configured transcription engine, so it cannot
produce local transcripts. Start the service from the workspace with
`.venv/bin/python -m ultron.voice.server`; it defaults to the configured XTTS
primary engine. To run the explicitly non-clone Edge fallback, use
`.venv/bin/python -m ultron.voice.server --port 8765 --engine edge --no-preload`.
The server binds to loopback. Edge synthesis requires outbound HTTPS access;
its engine metadata reports `is_clone: false`.

XTTS startup is local-only: the configured model directory must contain
`config.json`, `model.pth`, `dvae.pth`, `mel_stats.pth`, `speakers_xtts.pth`,
`vocab.json`, and `LICENSE.txt`. The service never downloads XTTS model files
during startup. Normal startup retains the Edge fallback if XTTS cannot load.
For an isolated XTTS validation, use a separate loopback port and explicitly
require XTTS so a failed load cannot silently validate as Edge:

```sh
PYTHONPATH=. HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  ultron/voice/reference_staging/xtts-cpu-inference-20261017T2247/.venv/bin/python \
  -m ultron.voice.server --port 8766 --engine xtts \
  --model-dir ultron/voice/reference_staging/xtts-cpu-inference-20261017T2247/model \
  --require-engine
```

Run this from the project root with the separately provisioned XTTS
environment; the application `.venv` does not include Coqui TTS.
Confirm `engine=xtts`, `ready=true`, and `is_clone=true` from
`/api/v1/health` and `/api/v1/engines` before treating the instance as
reference-conditioned. A missing/incomplete model directory or reference
audio is an error; use the explicit Edge command above to roll back.

Approval authorization is derived from the authenticated server-side session.
Each proposed operation records the proposing principal and session ID.
Approval is accepted only from that same live session and is bound to the
operation ID, original correlation ID, canonical request fingerprint,
approval/reconciliation purpose, unique one-use grant ID, issuance/expiry
timestamps, and authorization context. Grants expire within five minutes and
are validated immediately before execution. Grant IDs are persisted uniquely
so a grant cannot be replayed. A disconnected session cannot approve a saved
proposal after reconnecting; it must submit a new request. Operation and
command-center channels are withheld until authentication succeeds.

`COMPLETED` is reserved for completed non-mutating operations. An approved
executor action is not considered successful solely because its executor
returned success. The built-in independent verifier supports only the exact,
read-only `execute` request whose command text and terminal subject are both
exactly `pwd`: it validates the stored operation ID and request fingerprint,
then compares the command output to the independently observed process working
directory. It stores outcome, verifier ID, provenance, observation time,
operation ID, request fingerprint, and observed state.
Other successful commands remain `UNVERIFIED`. A verifier conflict, failure,
or exception remains unresolved as `UNCERTAIN`; the gateway does not fabricate
verification evidence. `VERIFIED` requires successful execution evidence and
identity-bound `CONFIRMED_SUCCESS` verification evidence.

Authenticated clients may send `RECONCILE_OPERATION` with the operation ID and
original request fingerprint. Reconciliation rechecks authoritative state
where a verifier exists, persists an auditable attempt and its authenticated
principal, and never invokes the executor. A confirmed result becomes
`VERIFIED` or `FAILED`; missing or conflicting evidence remains
`RECONCILIATION_REQUIRED` at the protocol level and retains the unresolved
lifecycle state. Reconciliation correlation IDs are idempotent; reuse with
different content is rejected. A pending attempt after a crash is not replayed
automatically. No retry operation is implemented; any future retry must be a
newly authorized action.

On gateway startup, persisted `APPROVED` or `EXECUTING` records are changed to
`UNCERTAIN`. This prevents automatic re-execution after a crash. The current
independent verifier is deliberately narrow; arbitrary terminal commands and
cognitive plans have no authoritative verifier and cannot be reconciled to
success without adding a capability-specific verifier.

The `main.py` local entry point attaches the task queue to the same
owner-only SQLite database. `TaskManager.next_task()` remains a non-claiming
compatibility lookup. Workers must use `claim_task()`, which atomically
transitions a `PENDING` or `READY` task to `CLAIMED` under a SQLite
`BEGIN IMMEDIATE` transaction and assigns an attempt ID. Duplicate submissions
with the same ID and content are idempotent; reuse with different content is
rejected. Task states and transition events are persisted. `execute_claimed_task`
persists `EXECUTING` before entering the executor and links its audit records
to the task attempt.

Recovery is explicit; startup does not automatically requeue or replay work.
A claim interrupted before the dispatch boundary can be returned to `READY`
with `recover_interrupted_task()`. An `EXECUTING` task interrupted at or after
the dispatch boundary becomes `UNCERTAIN`, remains non-runnable, and cannot be
removed until evidence resolves it. Completed, failed, and uncertain tasks
are not claimable after restart. A worker must explicitly reconcile uncertain
work; there is no automatic retry path for it. A task transitions to
`COMPLETED` only with `CONFIRMED_SUCCESS` verifier evidence. The evidence
identifier is stored as a SHA-256 fingerprint; the identifier itself is not
retained.

Executor and recovery audit rows share the operation database's
`execution_audit` table. They retain task, operation, plan, attempt, and
correlation IDs; lifecycle event/outcome; failure classification; timestamps;
and allow-listed retry/dispatch evidence. They do not persist command
parameters, raw results, or exception messages. Executor return and
independent verification are separate fields: a returned execution is
recorded as `DISPATCH_RETURNED` with verification `NOT_PERFORMED`, not as
verified success. Audit history can be read through
`SQLiteOperationStore.execution_audit()`. The independent verifier remains
limited to the exact harmless `pwd` operation described above.
