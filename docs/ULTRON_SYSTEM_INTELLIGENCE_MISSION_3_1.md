# ULTRON Runtime Ownership and Launch Diagnosis — Mission 3.1

## Result

- **Conclusion: `RUNTIME_ABSENT`**
- **Live integration: `UNVERIFIED`**
- **Deployment decision: `NO-GO`**

At the fresh read-only snapshot, the documented ULTRON runtime was absent from
this project environment: no process matched the canonical manager or any of
its expected service identities; no expected service listener was present;
the expected `manager.lease` and `manager.sock` paths were absent; and the
protected service state file contained no service records. The project root,
interpreter, and default launcher state directory match the documented launch
configuration. No evidence suggests a current supported runtime at this root.

This identifies the runtime as absent, but does **not** establish why it ended.
The prior ownership observation at approximately 19:42 +05:30 reported a
manager and four children, with the lease and control socket already absent.
The Mission 3 observation at 22:44 +05:30 found no expected listeners or
matching service processes. This Mission 3.1 snapshot corroborates that absence.
The observations do not establish whether the processes were stopped normally,
exited unexpectedly, or were otherwise terminated. Logs and operator history
were not inspected.

No process was started, stopped, signalled, adopted, or handed off. No manager
action, service request, secret read, database read/write, or persistent state
change was performed. The two requested report files are the only files
created for this task.

## Canonical launch and intended ownership

The documented full-stack launch command is `./start-ultron.sh` from the
project root. The script resolves its own directory, selects that directory's
`.venv/bin/python`, and `exec`s:

```text
<project>/.venv/bin/python <project>/scripts/ultron_service_manager.py start --root <project>
```

In this environment the configured interpreter resolves to
`/usr/bin/python3.14`; the project `.venv/bin/python` path is executable. The
effective launcher state directory is the default
`/home/ultron/.local/state/ultron/interface-launcher`; `XDG_STATE_HOME` is not
set in this environment.

The manager process is intended to have the project root as its working
directory, the configured project interpreter as its executable, and the
manager script path plus `start` in its argument vector. The manager does not
store its own PID/start-time tuple in `services.json`. Its supported ownership
mechanisms are instead:

- An exclusive lifetime `flock` on `manager.lease`, acquired before service
  startup and held while managing/supervising children.
- An owner-only `manager.sock` control channel created only after startup
  readiness succeeds.
- `services.json` records for child service PIDs and `/proc` start ticks.
- Per-service process validation against executable real path, project working
  directory, command identity, recorded start ticks, and, for manager-owned
  children, process group.

The manager's separate `manager.lock` file is not the lifetime manager lease;
its mere presence does not identify a manager or establish ownership.

## Expected services

The manager's production configuration starts these services in order:

| Service | Expected identity | Loopback port | Readiness evidence used by manager |
|---|---|---:|---|
| Gateway | `ultron.application.runtime.service_entrypoint` | 8080 | WebSocket greeting with the expected runtime channel and authentication-required state |
| Frontend | `http.server` serving `interface` | 8000 | HTTP 200 with the expected ULTRON operator-console title |
| Authentication helper | `ultron.application.runtime.local_auth_helper serve` | 8081 | WebSocket greeting reporting local-unlock authentication and exact dashboard origin |
| Voice | `ultron.voice.server --port 8765 --engine edge` | 8765 | HTTP health response with `ready=true` and an engine name |

Expected managed children use the project's interpreter and root working
directory; the manager records their process-start ticks and owns their
process groups. A port alone is not considered proof of process identity or
service readiness.

## Alternate launch methods

No alternate full-stack manager/launcher is documented. The runtime guide
documents `./start-ultron.sh` as the one-command full-stack startup path.
`./run_financial_dashboard.sh` is explicitly a separate Streamlit application,
not part of the managed gateway/frontend/auth/voice group. Direct commands to
run the local auth helper or voice service are documented for component setup
or standalone use; they do not establish the manager lease, manager control
socket, or managed service records and are not an alternate supported way to
own the full stack. The manager CLI supports `start` and `stop`; no adoption
or manager-handoff operation is supported.

## Fresh runtime evidence

Snapshot timestamp: **2026-10-27T22:53:41+05:30** (process and state inspection).
The final listener confirmation ran at **2026-10-27T22:54:25+05:30**.
Environment: Linux, workspace
`/home/ultron/Projects/ULTRON_INTERFACE_FINAL`.

| Evidence | Observation |
|---|---|
| Project root and interpreter | Root exists; `.venv/bin/python` is executable and resolves to `/usr/bin/python3.14`. |
| `XDG_STATE_HOME` | Not set; state path resolves to the documented default. |
| Manager/service processes | None matched the expected project interpreter, project working directory, and exact manager/service argument identities. The scan emitted no raw arguments or environment values. |
| `services.json` | Present, owner UID 1000, mode `0600`; valid version-1 state with an empty `services` mapping and no `startupRecovery` status. No service PID/start-tick records were present. |
| `manager.lease` | Path absent. With no lease inode present, its file-backed lock could not be independently inspected; no matching manager process was found. |
| `manager.sock` | Absent. No connection or control request was attempted. |
| `manager.lock` | Present as an owner-only regular file, mode `0600`. Its presence alone is not evidence of manager ownership. |
| Expected listener ports | `ss` showed no listeners on 8000, 8080, 8081, or 8765. |
| Prior observation | Mission 3 recorded connection failures on those ports and no exact matching service processes at approximately 22:44 +05:30. |

These are point-in-time observations. They do not prove that no differently
configured or unrelated application exists elsewhere on the host. They do
show that the documented project-root runtime, its expected listeners, and its
supported ownership artifacts were absent at the observed time.

## Commands and outcomes

| Check | Result |
|---|---|
| `date --iso-8601=seconds` | Exit **0**; `2026-10-27T22:54:25+05:30` |
| Bounded `/proc` scan for exact canonical manager and service identities; reports only PID, PPID, start ticks/time, executable-match booleans, and role | Exit **0**; no matching processes |
| Read-only metadata inspection of `services.json`, `manager.lease`, `manager.sock`, and `manager.lock` under the effective state directory | Completed in the bounded Python inspection (exit **0**); state record empty, lease/socket absent |
| Selective read of the owner-only `services.json` identity fields | Completed (exit **0**); valid version 1, zero service records; no logs or other state values read |
| `ss -ltnp '( sport = :8000 or sport = :8080 or sport = :8081 or sport = :8765 )'` | Exit **0**; no matching listeners |
| Read-only check of `XDG_STATE_HOME`, project root, and interpreter executable/realpath | Exit **0**; no override; documented root and interpreter available |
| Authentication, HTTP/WebSocket readiness probes, control-socket connection, database access, or manager actions | **Not run**; unnecessary for ownership classification and outside this mission's read-only boundaries |

## Source references

- [start-ultron.sh](../start-ultron.sh#L4) resolves the root/interpreter and
  [executes the canonical manager start command](../start-ultron.sh#L12).
- [ultron_service_manager.py](../scripts/ultron_service_manager.py#L278)
  defines the state paths;
  [lease acquisition](../scripts/ultron_service_manager.py#L302) uses a
  nonblocking exclusive lease and rejects a detected legacy manager;
  [service definitions](../scripts/ultron_service_manager.py#L532) establish
  child commands and ports;
  [process matching](../scripts/ultron_service_manager.py#L677) validates
  executable, cwd, service identity, start ticks, and managed process group;
  [CLI parsing](../scripts/ultron_service_manager.py#L1856) supports only
  `start` and `stop`.
- [RUNTIME_OPERATIONS.md](./RUNTIME_OPERATIONS.md#L104) documents canonical
  full-stack startup; [its standalone-app section](./RUNTIME_OPERATIONS.md#L144)
  distinguishes the financial dashboard; helper and voice direct commands are
  documented as component-level procedures.
- [Mission 3 report](./ULTRON_SYSTEM_INTELLIGENCE_MISSION_3.md) records the
  earlier 22:44 +05:30 unavailable-listener observation.
- [Mission 00 ownership report](./ULTRON_RUNTIME_OWNERSHIP_MISSION_00.md)
  records the historical 19:42 +05:30 manager/child observation and its
  missing lease/control-channel anomaly.

## Unresolved questions

1. What caused the previously observed manager and child processes to disappear
   between the 19:42 and 22:44 observations?
2. Was there an operator-initiated shutdown, a manager/service failure, or
   another termination event? No logs or operator history were examined.
3. Was any service launched from a different project root or through an
   undocumented mechanism? The expected ports and this project's canonical
   process identities were absent, but that cannot exclude all host-level
   alternatives.
4. What current source revision would a future manager load? Git metadata is
   unavailable from this workspace, and no backend is running to attest a
   loaded revision.

No recovery or launch is performed or recommended as an action in this
diagnostic. Preserve the current state and obtain operator direction before
any runtime change.
