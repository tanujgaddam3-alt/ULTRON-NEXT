# ULTRON Runtime Ownership — Mission 00

**Disposition:** `RECOVERY_GATE_NOT_PASSED`  
**Deployment decision:** **NO-GO**  
**Observation time:** 2026-10-27 19:42:27.674 +05:30  
**Method:** read-only `/proc` inspection, exact-path metadata checks, selective read of recorded service identity fields, passive `/proc/net/tcp` and `/proc/net/unix` inspection, source/test/document review. Listener/artifact metadata was sampled at approximately 19:42:01 +05:30; process tuples were sampled twice approximately 500 ms apart beginning at 19:42:27.674 +05:30. No application tests, service requests, authenticated requests, or lifecycle operations were run.

## Executive result

The historical manager and child PIDs are still present. The manager process is PID 6943; its four recorded service processes remain its direct children. Their executable, working directory, command identity, process group, start ticks, and service-state PID/start-tick/cwd records match the identity checks implemented in the current manager source. The expected loopback listeners are present and their socket inodes map to those four child PIDs.

This confirms the observed process topology and service process identities at this snapshot; it does **not** establish supervisory ownership. The expected `manager.lease` and `manager.sock` paths are absent, no matching descriptor was found on the manager, and no Unix listener exists at the expected socket path. The current manager's stdin/stdout/stderr point to `/dev/pts/1`, but the terminal's owner, availability, and original startup history were not established. No authenticated control relationship was tested.

The on-disk manager source currently has the same SHA-256 recorded by the prior ownership audit. The running process command points to that source path, but the exact source revision already loaded in Python memory remains **UNVERIFIED**. Current application-level readiness is also **UNVERIFIED** because the mission prohibits service requests; listeners are passive evidence only.

**No supported live manager handoff or process-adoption procedure exists in the inspected implementation and runbook. Do not start a competing manager, signal, stop, restart, adopt, repair state artifacts, or infer authority from the matching records.**

## Scope and safeguards

- **VERIFIED:** Only read-only process metadata, selected service identity fields, exact manager artifact metadata, passive listener ownership, source, tests, and runbooks were inspected.
- **VERIFIED:** No start/stop script, service API, WebSocket, HTTP endpoint, database, credential file, log, or OMEGA interface was accessed.
- **VERIFIED:** No process was signalled, attached to, or modified. No file, permission, service, or database was changed.
- **VERIFIED:** Raw process arguments and environment were not reported. The command-line check emitted only allowlisted script/module/service identity tokens. The only environment field checked was the non-secret `XDG_STATE_HOME` key; it was absent for PID 6943.
- **VERIFIED:** No tests were run, as required.
- **VERIFIED:** Two probe-formatting mistakes were corrected before relying on output: an initial stability summary omitted candidate labels, and a combined state comparison keyed records by PID instead of service name. The corrected stability and service-name keyed comparison confirmed all five process tuples stable and all four service records matched. No files or services were affected by those read-only probes.

## Current process identity findings

The default state directory is `/home/ultron/.local/state/ultron/interface-launcher`. PID 6943 had no `XDG_STATE_HOME` override. The current source's production service commands resolve to `/usr/bin/python3.14` in this environment.

| Finding | Status | Current observation |
|---|---|---|
| Manager process | `VERIFIED` | PID 6943; PPID 5868; process group 6943; start ticks 13097; executable `/usr/bin/python3.14`; cwd `/home/ultron/Projects/ULTRON_INTERFACE_FINAL`; sanitized identity matches `scripts/ultron_service_manager.py start`. It remained present with the same identity tuple on the second sample. No manager PID/start-time record exists in `services.json`. |
| Gateway process | `VERIFIED` | PID 6970; PPID 6943; group 6970; start ticks 13117; executable `/usr/bin/python3.14`; project cwd; sanitized module identity `ultron.application.runtime.service_entrypoint`. PID, start ticks, and cwd match its `services.json` record; the manager source's executable, cwd, command-identity and managed-process-group predicates also match. Present and unchanged on second sample. |
| Frontend process | `VERIFIED` | PID 7065; PPID 6943; group 7065; start ticks 13349; executable `/usr/bin/python3.14`; project cwd; sanitized `http.server` identity with port 8000, bind 127.0.0.1 and interface directory. Its record and source identity predicates match. Present and unchanged on second sample. |
| Authentication helper | `VERIFIED` | PID 7071; PPID 6943; group 7071; start ticks 13380; executable `/usr/bin/python3.14`; project cwd; sanitized `local_auth_helper serve` identity. Its record and source identity predicates match. Present and unchanged on second sample. |
| Voice service | `VERIFIED` | PID 7075; PPID 6943; group 7075; start ticks 13411; executable `/usr/bin/python3.14`; project cwd; sanitized `ultron.voice.server --port 8765` identity. Its record and source identity predicates match. Present and unchanged on second sample. |
| Expected topology | `PARTIALLY_VERIFIED` | The four recorded services are direct children of PID 6943 and each is in its own process group. This agrees with the expected launcher topology but does not prove that the manager still holds Python `Popen` handles or has authority to supervise them. |
| Original terminal/operator | `PARTIALLY_VERIFIED` | Manager standard input/output/error resolve to `/dev/pts/1`. This identifies a terminal device only; the user, shell/session history, and operator availability are unknown. |

The exact historical PIDs all still exist. **No previously recorded PID was found to have disappeared or been replaced.** The current observed identities agree with the historical service records. Historical application-readiness probes were not repeated and are not upgraded to current readiness evidence.

## Lease and control-channel findings

| Finding | Status | Evidence |
|---|---|---|
| Expected state directory | `VERIFIED` | `/home/ultron/.local/state/ultron/interface-launcher` exists, directory mode 0700, owner UID 1000. |
| Manager lease | `VERIFIED` | `/home/ultron/.local/state/ultron/interface-launcher/manager.lease` is absent (`ENOENT`). No manager descriptor points to that path or its deleted form. |
| Manager control socket | `VERIFIED` | `/home/ultron/.local/state/ultron/interface-launcher/manager.sock` is absent (`ENOENT`). No manager descriptor or `/proc/net/unix` listener for that exact path was found. No control request was attempted. |
| Service state record | `VERIFIED` | `services.json` exists mode 0600, owner UID 1000. Only its four records' `pid`, `start_ticks`, and `cwd` fields were considered; no log paths or other record values are included here. |
| Lease and socket ownership meaning | `VERIFIED` | Current source expects an owner-only `manager.lease` protected by nonblocking exclusive `flock`; it opens the control server only after the lease is held and configured services pass startup readiness. |
| Competing manager protection | `VERIFIED` | The lease fences modern managers. Startup also scans `/proc` for a legacy manager command in this project and refuses ownership when found. A new manager refuses to adopt a live recorded child unless it has an accepted ownership transfer; the code has no such transfer protocol. |
| Stale/PID-reuse handling | `VERIFIED` | `_process_matches` checks executable real path, cwd, service command identity, optional recorded start ticks, and managed process group. Stale or ambiguous identities are not permission to signal/adopt. |
| Live control relationship | `BLOCKED` | Lease and control channel are absent. Parentage/state-record matches alone cannot establish control authority; manager-held `Popen` handles are not observable through this read-only check. |

### Expected paths and protocol in source

- State directory: `$XDG_STATE_HOME/ultron/interface-launcher`, or `~/.local/state/ultron/interface-launcher` when `XDG_STATE_HOME` is unset.
- Lease: `manager.lease`, opened as a regular owner-only file and guarded by `flock(LOCK_EX | LOCK_NB)`. The launcher acquires it before startup and releases it in cleanup after closing the control channel.
- Control socket: `manager.sock`, a mode-0600 Unix stream socket created only after service startup/readiness and lease acquisition. Shutdown closes the server and removes its socket path.
- Accepted control request: one bounded JSON line with exactly `operation` and `operationId`, where operation is only `restart-auth` and the ID matches `[A-Za-z0-9._:-]{1,128}`. The peer UID is checked using `SO_PEERCRED`. This is not a stop, adoption, or manager-handoff API.
- State record: `services.json` stores versioned service records including PID, process start ticks, cwd, and log path. This report discloses only PID/start ticks/cwd.

## Running source identity

| Finding | Status | Evidence |
|---|---|---|
| Current manager executable/cwd/command | `VERIFIED` | `/usr/bin/python3.14`, project cwd, sanitized command identity matching the current manager script path and `start` action. |
| Current manager source file hash | `VERIFIED` | SHA-256 of current `scripts/ultron_service_manager.py`: `b9a02815bf1d25679fa360fe04c0abcb8979b67dba99bab889af035878d1de97`; it matches the hash in the prior ownership audit. |
| Process-to-path association | `PARTIALLY_VERIFIED` | Process command line references the manager script path; this proves invocation identity, not which source bytes the interpreter loaded or whether code changed after loading. |
| Exact loaded source/release | `UNVERIFIED` | No supported read-only build/version identity or launch-time immutable release attestation was available. No debugger, attach, injection, or restart was used. |

## Service listeners and passive readiness

Passive `/proc/net/tcp` inspection found loopback TCP listeners at `127.0.0.1:8080`, `:8000`, `:8081`, and `:8765`. Socket inode ownership maps them respectively to PIDs 6970, 7065, 7071, and 7075. The corresponding process command identities and state records match the configured services.

| Service | Status | Passive finding | Application readiness |
|---|---|---|---|
| Gateway, 127.0.0.1:8080 | `PARTIALLY_VERIFIED` | Listener owner is the recorded gateway PID 6970; process identity and parent match the expected topology. | `UNVERIFIED`; no WebSocket greeting/authentication-required readiness probe was made. |
| Frontend, 127.0.0.1:8000 | `PARTIALLY_VERIFIED` | Listener owner is the recorded frontend PID 7065; process identity and parent match. | `UNVERIFIED`; no HTTP request or browser page inspection was made. |
| Auth helper, 127.0.0.1:8081 | `PARTIALLY_VERIFIED` | Listener owner is the recorded helper PID 7071; process identity and parent match. | `UNVERIFIED`; no local-unlock WebSocket greeting was requested. |
| Voice, 127.0.0.1:8765 | `PARTIALLY_VERIFIED` | Listener owner is the recorded voice PID 7075; process identity and parent match. | `UNVERIFIED`; no `/api/v1/health` request was made. |

Listener ownership plus process identity distinguishes these listeners from an unrelated process at this snapshot, but does not prove functional readiness, authentication success, or manager control.

## Existing test evidence and limits

Tests were inspected but **not run**. Historical pass counts below come from [ULTRON_RECOVERY_READINESS.json](./ULTRON_RECOVERY_READINESS.json) and are not fresh evidence for this mission.

| Topic | Inspected tests and coverage | Status / gap |
|---|---|---|
| Lease exclusivity and socket timing | `test_manager_lease_is_exclusive_and_control_schema_is_bounded` at `tests/runtime/test_service_manager.py:1043`; `test_control_channel_success_opens_socket_after_services_are_ready` at `:186`. Tests lease exclusivity, socket absent before startup readiness, and protocol bounds. | `VERIFIED` as source coverage; isolated temporary fixtures only. |
| Missing lease/socket and no-adoption | `test_legacy_manager_without_lease_or_socket_blocks_new_owner` at `tests/runtime/test_service_manager.py:477`; `test_separate_manager_refuses_to_adopt_recorded_process_without_transfer` at `:733`. | `VERIFIED` as source coverage; these do not diagnose the live anomaly. |
| PID reuse/stale records | `test_stale_pid_record_is_removed_without_signalling_pid` at `tests/runtime/test_service_manager.py:766`; `test_stale_helper_start_identity_is_rejected_without_signal` at `:1012`. | `VERIFIED` as source coverage; no live process is signalled by this mission. |
| Interrupted/ambiguous recovery | `test_interrupted_candidate_is_identity_checked_and_safely_rolled_back` at `tests/runtime/test_service_manager.py:1199`; `test_interrupted_ambiguous_candidate_is_never_signalled_or_adopted` at `:1260`; `test_control_rollback_never_signals_ambiguous_child_and_blocks_next_owner` at `:234`. | `VERIFIED` as source coverage; test cases use temporary state/fixture children. |
| Crash recovery | `test_unexpected_auth_crash_keeps_existing_stack_recovery_behavior` at `tests/runtime/test_service_manager.py:987`. | `VERIFIED` as source coverage; does not establish current process ownership. |
| Maintenance ordering/failure | `test_maintenance_journal_requires_ordered_evidence_and_retains_interruption` and `test_maintenance_journal_blocks_commit_after_failure_and_pre_stop_abort` at `tests/runtime/test_maintenance_transition.py:109` and `:134`. | `VERIFIED` as source coverage. The journal records operator-observed milestones; inspected tests do not independently authenticate human authorization. |
| Isolated maintenance/rollback | `test_isolated_maintenance_stops_then_starts_exactly_one_manager_and_preserves_cre_data` at `tests/runtime/test_maintenance_transition.py:149`; `test_failed_new_manager_readiness_rolls_back_only_new_fixture_services` at `:233`. | `VERIFIED` as fixture coverage; not a live handoff or production recovery rehearsal. |
| Release artifacts | Release bundle tests at `tests/runtime/test_release_bundle.py:14`, `:63`, and `:73` cover reproducibility/restore, unsafe paths, and tamper rejection. | `VERIFIED` as source coverage; no candidate or rollback archive is qualified by this mission. |
| Handoff | No handoff implementation or handoff test was found in the inspected manager, tests, and runbook. | `VERIFIED` as documented/source boundary; live adoption/handoff is unsupported. |
| Fresh test execution | No tests were run in this mission. | `NOT_APPLICABLE` by mission constraint; historical results remain historical. |

The recorded historical runs include 33 manager tests plus four subtests, and 19 maintenance/release/SQLite tests; they were not repeated here.

## Recovery options

| Option | Status | Preconditions and required human authorization | Evidence, risks, and stop conditions |
|---|---|---|---|
| 1. Recover control through the existing manager | `BLOCKED` | The expected live manager must hold its lease and expose the owner-only control socket; manager/service identities must be verified. Helper restart, if ever requested, is limited to the supported authenticated `restart-auth` operation. A shutdown still requires the original manager terminal's normal Ctrl+C path. Operator authorization is required for any live lifecycle action. | Currently unavailable: lease/socket absent. No request was attempted. Do not treat parentage or records as control. Stop if artifacts, source identity, or ownership are ambiguous. |
| 2. Documented manager handoff | `VERIFIED` — unsupported | No preconditions can make the absent implementation supported. A future handoff would require a separately implemented, reviewed, and tested cooperative protocol. | Runbook and code state that manager-to-manager handoff is not implemented. Do not adopt processes or start a competing manager. |
| 3. Authorized maintenance window | `PARTIALLY_VERIFIED` | Runbook requires the original `start-ultron.sh` terminal, exact approved release and rollback, protected live database backup/restore evidence, explicit operator authorization, durable journal, and a fresh identity preflight. | A controlled interruption has outage risk; incorrect identity, release, or recovery point risks service/data loss. Existing transition tests use fixture managers and a temporary database only. Stop on any unknown owner, mismatched PID/start identity, failed backup/restore, missing authorization, or unavailable original terminal. Not executed. |
| 4. Manual operator intervention | `VERIFIED` — required now | Responsible operator/system owner identifies the original manager owner and terminal/session and supplies an authenticated startup/ownership record. No service action is authorized by this report. | Preserve all processes and records. If the owner or original terminal cannot be established, leave services untouched and escalate to the system owner. No automatic adoption workaround. |

## Explicit operator action required next

The responsible operator must identify who owns the manager attached to `/dev/pts/1`, locate the original startup terminal/session, and confirm the startup/ownership history through an authenticated operator record. This is a read-only ownership clarification only; it is **not** permission to send Ctrl+C, stop, start, adopt, restart, alter artifacts, or access databases. If ownership or terminal access cannot be established, require system-owner intervention and keep the stack unchanged.

After that read-only clarification, a separate authorized maintenance plan may be prepared only after the exact release, rollback, protected backup/restore, and test prerequisites are evidenced. The recovery gate remains **not passed**.

## Evidence paths and exact locations

- [Service manager source](../scripts/ultron_service_manager.py): `_read_proc` lines 76–89; manager paths 278–286; lease acquisition/release 302–390; control socket/protocol 393–530; configured services 532–585; state read 635–653; process identity check 677 onward; record creation 826 onward; startup/control channel 1504 onward; supervision 1786 onward; CLI lifecycle 1856 onward.
- [Service-manager tests](../tests/runtime/test_service_manager.py): test locations listed above.
- [Maintenance transition tests](../tests/runtime/test_maintenance_transition.py): test locations listed above.
- [Release bundle tests](../tests/runtime/test_release_bundle.py): test locations listed above.
- [Maintenance and recovery runbook](./RUNTIME_OPERATIONS.md): helper restart 158 onward; handoff boundary 217 onward; controlled maintenance 264 onward; lifecycle and recovery details 273 onward.
- [System Intelligence source](../ultron/application/runtime/system_intelligence.py): manager/control availability projection around lines 402–480; read-only handoff status source reviewed without calling the live interface.
- [Prior runtime ownership audit](./ULTRON_RUNTIME_OWNERSHIP_AUDIT.md) and [prior recovery readiness record](./ULTRON_RECOVERY_READINESS.json): historical observations and test results, not current proofs beyond the specific rechecks above.
- [Closure plan](./ULTRON_BLOCKER_CLOSURE_PLAN.md): this report addresses `ULTRON-CLOSE-00`; no gate is passed by this report.

## Final disposition

**`RECOVERY_GATE_NOT_PASSED` — manager ownership is not established; loaded source identity is unverified; no supported safe handoff exists.** Current process identities, recorded service identities, topology, and passive loopback listener ownership were verified. Functional readiness, authenticated control, source bytes already loaded in memory, and original operator ownership remain unverified. No prohibited action or live mutation occurred.
