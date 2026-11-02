"""Read-only, point-in-time evidence collection for CRE writer readiness."""

from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ultron_service_manager import ServiceManager


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WRITER_ENTRYPOINTS = (
    {
        "id": "gateway",
        "kind": "runtime",
        "source": "ultron/application/runtime/service_entrypoint.py",
        "effect": "Gateway configures the CRE runtime when authenticated and enabled.",
        "canWrite": True,
    },
    {
        "id": "schema-migration-v7-v9",
        "kind": "callable-migration-api",
        "source": "ultron/application/content/store.py",
        "effect": "Explicit v7-to-v9 publishing-attempt schema migration method.",
        "canWrite": True,
    },
    {
        "id": "schema-migration",
        "kind": "operator-invoked-maintenance",
        "source": "tools/migrate_cre_private_render_operations.py",
        "effect": "Explicit v9-to-v10 database migration.",
        "canWrite": True,
    },
    {
        "id": "fixture-schema-validator",
        "kind": "validator",
        "source": "scripts/cre_schema_validator.py",
        "effect": "Source manifest generation uses an in-memory ContentStore; "
        "supplied fixture is opened read-only.",
        "canWrite": False,
    },
    {
        "id": "sqlite-backup",
        "kind": "backup-tool",
        "source": "scripts/ultron_sqlite_backup.py",
        "effect": "Reads source for backup and writes separate output; not a "
        "source-database writer under documented use.",
        "canWrite": False,
    },
)
RESTART_LINE = re.compile(r"^\s*Restart\s*=\s*(.*?)\s*$", re.IGNORECASE)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _canonical_database_targets(database: Path) -> set[str]:
    absolute = Path(os.path.abspath(database))
    try:
        resolved = database.resolve(strict=False)
    except OSError:
        resolved = absolute
    bases = {str(absolute), str(resolved)}
    return {
        target
        for base in bases
        for target in (
            base,
            f"{base}-wal",
            f"{base}-shm",
        )
    }


def _process_stat(pid: int, proc_root: Path) -> tuple[int, int] | None:
    try:
        text = (proc_root / str(pid) / "stat").read_text(encoding="ascii")
        tail = text[text.rfind(")") + 2 :].split()
        return int(tail[2]), int(tail[19])
    except (OSError, ValueError, IndexError):
        return None


class WriterEvidenceCollector:
    """Collect repository, process, manager-state, and restart-policy evidence.

    The collector intentionally does not open SQLite databases, read process
    environments, contact service managers, probe listeners, or invoke process
    control.
    """

    def __init__(
        self,
        *,
        project_root: Path = PROJECT_ROOT,
        state_directory: Path | None = None,
        proc_root: Path = Path("/proc"),
        systemd_roots: tuple[Path, ...] | None = None,
        cron_roots: tuple[Path, ...] | None = None,
        clock: Callable[[], str] = _timestamp,
    ) -> None:
        self.project_root = project_root.resolve()
        self.database = self.project_root / "data" / "ultron_cre.db"
        if state_directory is None:
            raise ValueError("state_directory must be explicitly supplied")
        self.state_directory = state_directory
        self.proc_root = proc_root
        self.systemd_roots = systemd_roots or (
            Path("/etc/systemd/system"),
            Path("/run/systemd/system"),
            Path("/usr/lib/systemd/system"),
            Path("/lib/systemd/system"),
            Path(pwd.getpwuid(os.getuid()).pw_dir) / ".config/systemd/user",
        )
        self.cron_roots = cron_roots or (
            Path("/etc/crontab"),
            Path("/etc/cron.d"),
            Path("/etc/cron.hourly"),
            Path("/etc/cron.daily"),
            Path("/etc/cron.weekly"),
            Path("/etc/cron.monthly"),
            Path("/var/spool/cron"),
            Path("/var/spool/cron/crontabs"),
        )
        self.clock = clock
        self.manager = ServiceManager(
            self.project_root,
            state_directory=self.state_directory,
            validate_project=False,
        )
        self.services = {service.name: service for service in self.manager.services}

    def _source_inventory(self) -> dict[str, Any]:
        writers = []
        for entrypoint in WRITER_ENTRYPOINTS:
            path = self.project_root / entrypoint["source"]
            writers.append(
                {
                    **entrypoint,
                    "evidenceStatus": "CHECKED" if path.is_file() else "COULD_NOT_VERIFY",
                }
            )
        return {
            "status": "CHECKED" if all(
                item["evidenceStatus"] == "CHECKED" for item in writers
            ) else "COULD_NOT_VERIFY",
            "knownEntryPoints": writers,
            "limitations": [
                "Repository source does not enumerate deployment-specific scripts, "
                "scheduled jobs, or external applications.",
                "A source entry point is not evidence that it is currently running.",
            ],
        }

    def _read_manager_state(self) -> dict[str, Any]:
        state_path = self.state_directory / "services.json"
        try:
            directory_info = self.state_directory.lstat()
            state_info = state_path.lstat()
            if (
                not stat.S_ISDIR(directory_info.st_mode)
                or directory_info.st_uid != os.getuid()
                or directory_info.st_mode & 0o077
                or not stat.S_ISREG(state_info.st_mode)
                or state_info.st_uid != os.getuid()
                or state_info.st_mode & 0o077
            ):
                return {
                    "status": "COULD_NOT_VERIFY",
                    "reason": "manager state ownership, type, or permissions are unsafe",
                    "services": [],
                    "gatewayQuiesce": "UNKNOWN",
                }
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {
                "status": "COULD_NOT_VERIFY",
                "reason": "manager state file is missing",
                "services": [],
                "gatewayQuiesce": "UNKNOWN",
            }
        except (OSError, ValueError, TypeError):
            return {
                "status": "COULD_NOT_VERIFY",
                "reason": "manager state is unreadable or malformed",
                "services": [],
                "gatewayQuiesce": "UNKNOWN",
            }
        if (
            not isinstance(state, dict)
            or state.get("version") != 1
            or not isinstance(state.get("services"), dict)
        ):
            return {
                "status": "COULD_NOT_VERIFY",
                "reason": "manager state shape is unsupported",
                "services": [],
                "gatewayQuiesce": "UNKNOWN",
            }

        services = []
        names = sorted(set(self.services) | set(state["services"]))
        for name in names:
            record = state["services"].get(name)
            service = self.services.get(name)
            if name not in state["services"]:
                services.append(
                    {
                        "service": name,
                        "expectedExecutable": (
                            str(Path(os.path.realpath(service.command[0])))
                            if service is not None
                            else None
                        ),
                        "workingDirectory": (
                            str(service.cwd.resolve()) if service is not None else None
                        ),
                        "expectedListener": (
                            f"{service.host}:{service.port}"
                            if service is not None
                            else None
                        ),
                        "recordStatus": "NOT_RECORDED",
                        "identityStatus": "NOT_VERIFIED",
                    }
                )
                continue
            if not isinstance(record, dict):
                services.append(
                    {
                        "service": name,
                        "recordStatus": "AMBIGUOUS",
                        "identityStatus": "AMBIGUOUS",
                    }
                )
                continue
            pid = record.get("pid")
            start_ticks = record.get("start_ticks")
            status = "UNVERIFIED"
            if service is None or type(pid) is not int or type(start_ticks) is not int:
                status = "AMBIGUOUS"
            else:
                stat_record = _process_stat(pid, self.proc_root)
                proc_root = self.proc_root / str(pid)
                try:
                    executable = Path(os.path.realpath(proc_root / "exe"))
                    cwd = Path(os.path.realpath(proc_root / "cwd"))
                except OSError:
                    executable = cwd = None
                if stat_record is None or executable is None or cwd is None:
                    status = "COULD_NOT_VERIFY"
                else:
                    process_group, actual_start_ticks = stat_record
                    matches = (
                        executable == Path(os.path.realpath(service.command[0]))
                        and cwd == service.cwd.resolve()
                        and actual_start_ticks == start_ticks
                        and process_group == pid
                    )
                    status = (
                        "PARTIAL_MATCH_COMMAND_IDENTITY_NOT_INSPECTED"
                        if matches
                        else "MISMATCH"
                    )
            services.append(
                {
                    "source": str(state_path),
                    "service": name,
                    "pid": pid if type(pid) is int else None,
                    "startTicks": start_ticks if type(start_ticks) is int else None,
                    "expectedExecutable": str(Path(os.path.realpath(service.command[0])))
                    if service is not None
                    else None,
                    "workingDirectory": str(service.cwd.resolve())
                    if service is not None
                    else None,
                    "expectedListener": f"{service.host}:{service.port}"
                    if service is not None
                    else None,
                    "recordStatus": "CHECKED",
                    "identityStatus": status,
                    "commandIdentity": "NOT_CHECKED_TO_AVOID_PROCESS_ARGUMENTS",
                }
            )
        quiesce = state.get("gatewayQuiesce")
        quiesce_status = (
            quiesce.get("status", "UNKNOWN")
            if isinstance(quiesce, dict)
            else "NOT_RECORDED"
        )
        return {
            "status": "CHECKED",
            "services": services,
            "gatewayQuiesce": quiesce_status,
            "managerOwnership": "NOT_VERIFIED",
            "writersStatus": "NOT_VERIFIED",
        }

    def _process_inventory(self) -> dict[str, Any]:
        targets = _canonical_database_targets(self.database)
        descriptor_matches: list[dict[str, Any]] = []
        inaccessible_processes = 0
        try:
            entries = sorted(
                (entry for entry in self.proc_root.iterdir() if entry.name.isdecimal()),
                key=lambda item: int(item.name),
            )
        except OSError:
            return {
                "status": "COULD_NOT_VERIFY",
                "knownProcesses": [],
                "databaseDescriptorMatches": [],
                "unreadableProcessCount": None,
            }

        for entry in entries:
            pid = int(entry.name)
            proc_stat = _process_stat(pid, self.proc_root)
            proc_root = self.proc_root / str(pid)
            try:
                executable = Path(os.path.realpath(proc_root / "exe"))
                cwd = Path(os.path.realpath(proc_root / "cwd"))
            except OSError:
                executable = cwd = None
            if executable is None or cwd is None or proc_stat is None:
                inaccessible_processes += 1
                continue
            _process_group, start_ticks = proc_stat

            descriptor_root = proc_root / "fd"
            try:
                descriptors = tuple(descriptor_root.iterdir())
            except FileNotFoundError:
                descriptors = ()
            except OSError:
                inaccessible_processes += 1
                continue
            for descriptor in descriptors:
                try:
                    target = os.readlink(descriptor)
                except OSError:
                    inaccessible_processes += 1
                    continue
                target_without_deleted = target.removesuffix(" (deleted)")
                if target in targets or target_without_deleted in targets:
                    descriptor_matches.append(
                        {
                            "pid": pid,
                            "startTicks": start_ticks,
                            "executable": str(executable),
                            "descriptorTarget": target,
                        }
                    )
        return {
            "status": "CHECKED" if inaccessible_processes == 0 else "INCOMPLETE",
            "databaseDescriptorMatches": sorted(
                descriptor_matches,
                key=lambda item: (item["pid"], item["descriptorTarget"]),
            ),
            "unreadableProcessCount": inaccessible_processes,
            "snapshotOnly": True,
        }

    def _restart_policy_inventory(self) -> dict[str, Any]:
        discovered: list[dict[str, Any]] = []
        checked_roots: list[str] = []
        unavailable_roots: list[str] = []
        for root in self.systemd_roots:
            if not root.exists():
                checked_roots.append(str(root))
                continue
            if not root.is_dir():
                unavailable_roots.append(str(root))
                continue
            checked_roots.append(str(root))
            try:
                units = sorted(
                    unit
                    for unit in root.glob("*.service")
                    if unit.is_file() and not unit.is_symlink()
                )
            except OSError:
                unavailable_roots.append(str(root))
                continue
            for unit in units:
                policies: list[str] = []
                section = ""
                try:
                    with unit.open(encoding="utf-8", errors="replace") as stream:
                        for line in stream:
                            stripped = line.strip()
                            if stripped.startswith("[") and stripped.endswith("]"):
                                section = stripped[1:-1]
                                continue
                            match = RESTART_LINE.match(line) if section == "Service" else None
                            if match is not None:
                                policies.append(match.group(1))
                except OSError:
                    discovered.append(
                        {
                            "unit": unit.name,
                            "evidenceStatus": "COULD_NOT_VERIFY",
                            "restartPolicy": "COULD_NOT_VERIFY",
                            "source": str(unit),
                        }
                    )
                    continue
                discovered.append(
                    {
                        "unit": unit.name,
                        "evidenceStatus": "CHECKED",
                        "restartPolicy": (
                            policies[-1] if policies else "NOT_CONFIGURED_IN_FILE"
                        ),
                        "source": str(unit),
                        "runningState": "NOT_CHECKED",
                        "writerAssociation": "NOT_CHECKED",
                    }
                )
        return {
            "status": (
                "COULD_NOT_VERIFY" if unavailable_roots
                else "CHECKED"
            ),
            "checkedRoots": checked_roots,
            "unavailableRoots": unavailable_roots,
            "systemdServiceFiles": discovered,
            "runtimeManagerPolicy": {
                "status": "CHECKED",
                "description": (
                    "Repository manager supervises its child services; "
                    "unexpected managed-service failure can trigger managed-stack "
                    "teardown. This does not establish an external manager restart policy."
                ),
                "externalManagerRestart": "NOT_CHECKED",
            },
            "repositoryMechanisms": [
                {
                    "source": "start-ultron.sh",
                    "status": (
                        "CHECKED"
                        if (self.project_root / "start-ultron.sh").is_file()
                        else "COULD_NOT_VERIFY"
                    ),
                    "behavior": "Invokes the service manager start action, which can launch managed services.",
                },
                {
                    "source": "scripts/ultron_service_manager.py",
                    "status": "CHECKED",
                    "behavior": (
                        "Unexpected managed child exit triggers stop_managed() for the managed stack; "
                        "it is not an individual automatic restart policy. Explicit manager start can launch services."
                    ),
                },
            ],
            "limitations": [
                "Only top-level service files are read; drop-ins, generated units, symlink aliases, timers, loaded manager state, and other supervisor types are not resolved."
            ],
        }

    def _scheduled_source_inventory(self) -> dict[str, Any]:
        sources = []
        for path in self.cron_roots:
            if path.exists():
                sources.append(
                    {
                        "source": str(path),
                        "status": "NOT_CHECKED",
                        "reason": "cron contents and host scheduler state are outside this collector's safe inspection scope",
                    }
                )
            else:
                sources.append(
                    {
                        "source": str(path),
                        "status": "COULD_NOT_VERIFY",
                        "reason": "source path is absent or not observable; this does not prove no scheduled writers",
                    }
                )
        return {
            "status": "NOT_CHECKED",
            "sources": sources,
            "otherSupervisors": "NOT_CHECKED",
        }

    def collect(self) -> dict[str, Any]:
        source_inventory = self._source_inventory()
        manager_state = self._read_manager_state()
        process_inventory = self._process_inventory()
        restart_policies = self._restart_policy_inventory()
        scheduled_sources = self._scheduled_source_inventory()

        incomplete = (
            source_inventory["status"] != "CHECKED"
            or manager_state["status"] != "CHECKED"
            or process_inventory["status"] != "CHECKED"
            or restart_policies["status"] != "CHECKED"
            or any(
                item["status"] == "COULD_NOT_VERIFY"
                for item in scheduled_sources["sources"]
            )
        )
        return {
            "generatedAt": self.clock(),
            "overallStatus": "INCOMPLETE" if incomplete else "NOT VERIFIED",
            "quiescenceStatus": "NOT VERIFIED",
            "databaseTarget": {
                "configuredBySource": str(self.database),
                "accessed": False,
            },
            "configuredServices": [
                {
                    "service": service.name,
                    "executable": str(Path(os.path.realpath(service.command[0]))),
                    "workingDirectory": str(service.cwd.resolve()),
                    "expectedListener": f"{service.host}:{service.port}",
                    "definitionSource": "scripts/ultron_service_manager.py",
                }
                for service in sorted(self.manager.services, key=lambda item: item.name)
            ],
            "knownWriterInventory": source_inventory,
            "managerState": manager_state,
            "processSnapshot": process_inventory,
            "restartPolicies": restart_policies,
            "scheduledAndExternalSources": scheduled_sources,
            "limitations": [
                "This is a point-in-time observation; process absence or no matching open descriptor does not prove quiescence.",
                "True quiescence requires an operator-controlled maintenance hold for every writer and restart-capable supervisor, plus ongoing observation for the entire maintenance window.",
                "The collector does not read process environments, inspect database contents, probe listeners, contact service managers, or perform process/service control.",
                "Host cron and external supervisor configuration is not fully inventoried; paths marked NOT_CHECKED or COULD_NOT_VERIFY require separate operator evidence.",
                "VERIFIED is never returned because this collector cannot prove continuous quiescence or prevent future restarts.",
            ],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect read-only, point-in-time CRE writer evidence."
    )
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument(
        "--state-directory",
        type=Path,
        required=True,
        help="verified launcher-manager state directory; no environment default is read",
    )
    args = parser.parse_args(argv)
    report = WriterEvidenceCollector(
        project_root=args.root,
        state_directory=args.state_directory,
    ).collect()
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["overallStatus"] == "NOT VERIFIED" else 2


if __name__ == "__main__":
    sys.exit(main())
