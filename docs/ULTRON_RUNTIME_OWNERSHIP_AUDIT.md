# ULTRON Runtime Ownership Audit

## Scope and evidence labels

Read-only inspection of the current service manager, runtime state record,
process metadata, and loopback listeners. No service or database was changed.
Observation time: 2026-10-27, approximately 17:51 +05:30. Process values below
are a point-in-time snapshot and can change.

- **Verified fact**: directly observed from source, state, process metadata, or
  an executed test.
- **Test result**: observed output from a test command.
- **Inference**: consistent with evidence but not proved.
- **Unknown**: unavailable without additional evidence or operator access.
- **Operator action**: requires an authorized operator; not performed here.

## Manager ownership invariants in current source

Source inspected: `scripts/ultron_service_manager.py`, SHA-256
`b9a02815bf1d25679fa360fe04c0abcb8979b67dba99bab889af035878d1de97`.

1. `acquire_manager_lease()` creates/opens owner-only `manager.lease` and takes
   a non-blocking exclusive `flock`. Failure to acquire it rejects a competing
   modern manager. It also scans `/proc` for a legacy manager command in this
   project and rejects startup when found.
2. Service state is protected separately by `manager.lock`. `services.json`
   is owner-only and records each service PID and process-start ticks.
3. `_process_matches()` compares executable real path, working directory,
   service-specific command identity, recorded process-start ticks, and (for
   managed processes) process group. A matching PID alone is not sufficient.
4. The manager keeps `Popen` handles for processes it started. A new manager
   refuses to adopt a live recorded child without a validated ownership
   transfer. Stale or mismatched identities are not signalled.
5. The manager starts configured services in order, waits for service-specific
   readiness, then creates the local control socket. The lifetime lease is
   acquired before startup and is retained while the manager supervises
   children.
6. `close_control_channel()` closes the server, removes its socket path, joins
   its thread, and releases the manager lease. Normal shutdown reaches this
   cleanup through the manager's `finally` path.
7. An unexpected exit of a tracked service retains existing stack recovery
   behavior. An intentional helper restart is an explicit recorded control
   operation; its replacement is identity/readiness checked and the supervisor
   continues. The regression test distinguishes that path from an unexpected
   helper crash.

These are source facts; they do not prove which source code is loaded in the
currently running manager.

## Current runtime observation

| Item | Observation at approximately 17:51 +05:30 | Evidence and limitation |
|---|---|---|
| Manager | PID 6943; PPID 5868; process group 6943; executable `/usr/bin/python3.14`; working directory is this project; command contains the manager script path and `start`. | Read-only `ps`, `/proc` executable/cwd/cmdline checks. This identifies the process command, not the Python source already loaded into memory. |
| Manager lease | Path absent; no open descriptor resolving to the lease path was found. | State path is `~/.local/state/ultron/interface-launcher/manager.lease`. Absence is not proof of relinquished ownership. |
| Manager control socket | Path absent. | `~/.local/state/ultron/interface-launcher/manager.sock` did not exist. No authenticated control operation was attempted. |
| Service state | `services.json` is mode 0600, owner `ultron`; mtime 2026-10-27 09:07:50 +05:30. It records gateway 6970/start ticks 13117, frontend 7065/13349, auth 7071/13380, and voice 7075/13411. | Only PID, start ticks, and log paths were selected from the JSON for inspection. Logs were not read. |
| Service identity | Current source `_process_matches(..., managed=True)` returned true for each of the four recorded services. | The check compares process start identity, executable, cwd, command identity, and process group. It is not a source-version attestation. |
| Parentage | Each recorded service process has PPID 6943; each has its own process group. | Read-only `ps`. |
| Listeners | Gateway 127.0.0.1:8080/PID 6970; frontend 127.0.0.1:8000/PID 7065; helper 127.0.0.1:8081/PID 7071; voice 127.0.0.1:8765/PID 7075. | Read-only `ss`. Listener presence is not a fresh application-level readiness probe. |
| Readiness | Earlier read-only probes reported gateway authentication-required readiness, frontend reachable, helper ready, and voice Edge ready. | Historical observation, not repeated in this snapshot. No authenticated browser verification was performed. |

The manager process remains the recorded services' parent while both expected
ownership artifacts are absent. This is an ownership anomaly. It is not safe to
interpret the absent paths as permission to start another manager.

## Timeline: facts versus inference

1. **2026-10-27T14:27:19+05:30 — historical inventory observation.** The
   inventory recorded manager PID 6943 and the four children, with no lease or
   control socket. It explicitly marked the loaded source revision unknown.
2. **Approximately 2026-10-27 17:43 +05:30 — prior read-only check.** The
   manager was still parent of the four services; service process identities
   and probes matched; the lease/socket paths and corresponding descriptors
   were absent. This was reported in the preceding mission handoff.
3. **Approximately 2026-10-27 17:51 +05:30 — current read-only check.** The
   manager command/executable/cwd, service identities, recorded start ticks,
   parentage, loopback listener owners, state-file permissions, and absent
   lease/socket paths were rechecked. The current source checksum was captured
   independently, but the live process's loaded source remains unknown.

**Inference, not proof:** a manager running older in-memory code or removal of
the lease/socket by another actor could each explain the mismatch. The current
observations cannot distinguish them. No manager log was read because logs
could contain sensitive material; no startup history or operator audit record
establishing the cause was available.

## Recovery and maintenance boundary

There is no supported live adoption or manager-to-manager handoff in the
inspected source/runbook. The runbook in `RUNTIME_OPERATIONS.md` requires an
approved maintenance window and access to the original manager terminal. It
does not authorize PID-only signals or a competing manager. The terminal's
availability was not established.

**Operator action required:** keep the stack unchanged; establish the original
manager terminal and its owner; obtain an explicit maintenance authorization;
then follow a separately reviewed outage, backup, rollback, and recovery
procedure. If the owner/terminal cannot be identified, stop and require
operator intervention. Do not remove state records or create the lease/socket
manually.

For a future implementation, use a cooperative, versioned handoff protocol:
the existing owner must authenticate and persist a transaction, quiesce only
after durable intent, transfer the exclusive lease, and acknowledge adoption
only after each process identity and service readiness is revalidated. On any
ambiguous state, the old owner must retain supervision; otherwise block both
adoption and signaling and require operator intervention. This protocol is a
design proposal only; it was not implemented.
