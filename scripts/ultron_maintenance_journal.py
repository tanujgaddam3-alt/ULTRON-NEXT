#!/usr/bin/env python3
"""Durable operator journal for a controlled local ULTRON maintenance window.

This module records operator-observed milestones only. It never starts, stops,
signals, adopts, or probes a service process.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import stat
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence


_OPERATION_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_NEXT = {
    "PREPARED": "STOP_REQUESTED",
    "STOP_REQUESTED": "OLD_OWNER_RELEASED",
    "OLD_OWNER_RELEASED": "START_REQUESTED",
    "START_REQUESTED": "NEW_MANAGER_READY",
    "NEW_MANAGER_READY": "SERVICES_READY",
    "SERVICES_READY": "DASHBOARD_VERIFIED",
    "DASHBOARD_VERIFIED": "CRE_VERIFIED",
    "CRE_VERIFIED": "COMMITTED",
}
_TERMINAL = {"COMMITTED", "ABORTED", "OPERATOR_INTERVENTION_REQUIRED"}


class JournalError(RuntimeError):
    """A maintenance journal operation was rejected or could not be persisted."""


class MaintenanceJournal:
    def __init__(self, state_directory: Path):
        self.state_directory = state_directory
        self.path = state_directory / "maintenance.json"
        self.lock_path = state_directory / "maintenance.lock"

    def begin(self, operation_id: str, root: Path, evidence: str) -> dict[str, Any]:
        self._validate(operation_id, evidence)
        root = root.resolve()
        with self._locked():
            state = self._read()
            transactions = state.setdefault("transactions", {})
            existing = transactions.get(operation_id)
            if existing is not None:
                if (
                    existing.get("root") == str(root)
                    and existing.get("events", [{}])[0].get("evidence") == evidence
                ):
                    return existing
                raise JournalError("Operation ID already belongs to another transaction.")
            if any(
                isinstance(item, dict) and item.get("status") not in _TERMINAL
                for item in transactions.values()
            ):
                raise JournalError("Another maintenance transaction is still active.")
            now = time.time()
            transaction = {
                "operationId": operation_id,
                "root": str(root),
                "status": "PREPARED",
                "createdAt": now,
                "updatedAt": now,
                "events": [{
                    "status": "PREPARED",
                    "at": now,
                    "evidence": evidence,
                }],
            }
            transactions[operation_id] = transaction
            self._write(state)
            return transaction

    def advance(self, operation_id: str, status: str, evidence: str) -> dict[str, Any]:
        self._validate(operation_id, evidence)
        with self._locked():
            state, transaction = self._transaction(operation_id)
            if (
                transaction["status"] == status
                and transaction["events"][-1].get("evidence") == evidence
            ):
                return transaction
            expected = _NEXT.get(transaction["status"])
            if status != expected:
                raise JournalError(
                    f"Invalid transition from {transaction['status']}; expected {expected or 'terminal'}."
                )
            self._append(transaction, status, evidence)
            self._write(state)
            return transaction

    def fail(self, operation_id: str, evidence: str) -> dict[str, Any]:
        self._validate(operation_id, evidence)
        with self._locked():
            state, transaction = self._transaction(operation_id)
            if transaction["status"] == "OPERATOR_INTERVENTION_REQUIRED":
                if transaction["events"][-1].get("evidence") == evidence:
                    return transaction
            if transaction["status"] in _TERMINAL:
                raise JournalError("A terminal maintenance transaction cannot be changed.")
            self._append(transaction, "OPERATOR_INTERVENTION_REQUIRED", evidence)
            self._write(state)
            return transaction

    def abort(self, operation_id: str, evidence: str) -> dict[str, Any]:
        self._validate(operation_id, evidence)
        with self._locked():
            state, transaction = self._transaction(operation_id)
            if transaction["status"] == "ABORTED":
                if transaction["events"][-1].get("evidence") == evidence:
                    return transaction
            if transaction["status"] != "PREPARED":
                raise JournalError("Abort is permitted only before shutdown is requested.")
            self._append(transaction, "ABORTED", evidence)
            self._write(state)
            return transaction

    def status(self, operation_id: str) -> dict[str, Any]:
        with self._locked():
            _, transaction = self._transaction(operation_id)
            return transaction

    def _transaction(self, operation_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        state = self._read()
        transactions = state.get("transactions")
        transaction = transactions.get(operation_id) if isinstance(transactions, dict) else None
        if not isinstance(transaction, dict):
            raise JournalError("Maintenance transaction was not found.")
        if not isinstance(transaction.get("events"), list) or not transaction["events"]:
            raise JournalError("Maintenance transaction is malformed.")
        return state, transaction

    @staticmethod
    def _append(transaction: dict[str, Any], status: str, evidence: str) -> None:
        now = time.time()
        transaction["status"] = status
        transaction["updatedAt"] = now
        transaction["events"].append({
            "status": status,
            "at": now,
            "evidence": evidence,
        })

    @staticmethod
    def _validate(operation_id: str, evidence: str) -> None:
        if not isinstance(operation_id, str) or not _OPERATION_ID.fullmatch(operation_id):
            raise JournalError("A valid operation ID is required.")
        if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 1000:
            raise JournalError("A concise, non-secret evidence note is required.")
        if re.search(r"(?i)(token|password|secret|credential)\s*[:=]\s*\S+", evidence):
            raise JournalError("Evidence notes must not contain credentials.")

    def _locked(self):
        self.state_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory = self.state_directory.lstat()
        if (
            not stat.S_ISDIR(directory.st_mode)
            or directory.st_uid != os.getuid()
            or stat.S_IMODE(directory.st_mode) & 0o077
        ):
            raise JournalError("Maintenance state directory has unsafe ownership or permissions.")
        descriptor = os.open(
            self.lock_path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            os.close(descriptor)
            raise JournalError("Maintenance lock has unsafe ownership or permissions.")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return _Lock(descriptor)

    def _read(self) -> dict[str, Any]:
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return {"version": 1, "transactions": {}}
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise JournalError("Maintenance journal has unsafe ownership or permissions.")
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            raise JournalError("Maintenance journal is unreadable or malformed.") from None
        if (
            not isinstance(state, dict)
            or state.get("version") != 1
            or not isinstance(state.get("transactions"), dict)
        ):
            raise JournalError("Maintenance journal has an unsupported shape.")
        return state

    def _write(self, state: dict[str, Any]) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".maintenance-", suffix=".tmp", dir=self.state_directory
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(state, output, separators=(",", ":"), sort_keys=True)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_name, self.path)
            os.chmod(self.path, 0o600)
            directory_fd = os.open(self.state_directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


class _Lock:
    def __init__(self, descriptor: int):
        self.descriptor = descriptor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        fcntl.flock(self.descriptor, fcntl.LOCK_UN)
        os.close(self.descriptor)


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="action", required=True)
    begin = subparsers.add_parser("begin")
    begin.add_argument("--operation-id", required=True)
    begin.add_argument("--root", type=Path, required=True)
    begin.add_argument("--evidence", required=True)
    advance = subparsers.add_parser("advance")
    advance.add_argument("--operation-id", required=True)
    advance.add_argument("--status", choices=tuple(_NEXT.values()), required=True)
    advance.add_argument("--evidence", required=True)
    fail = subparsers.add_parser("fail")
    fail.add_argument("--operation-id", required=True)
    fail.add_argument("--evidence", required=True)
    abort = subparsers.add_parser("abort")
    abort.add_argument("--operation-id", required=True)
    abort.add_argument("--evidence", required=True)
    status = subparsers.add_parser("status")
    status.add_argument("--operation-id", required=True)
    arguments = parser.parse_args(argv)
    journal = MaintenanceJournal(arguments.state_dir)
    try:
        if arguments.action == "begin":
            result = journal.begin(
                arguments.operation_id, arguments.root, arguments.evidence
            )
        elif arguments.action == "advance":
            result = journal.advance(
                arguments.operation_id, arguments.status, arguments.evidence
            )
        elif arguments.action == "fail":
            result = journal.fail(arguments.operation_id, arguments.evidence)
        elif arguments.action == "abort":
            result = journal.abort(arguments.operation_id, arguments.evidence)
        else:
            result = journal.status(arguments.operation_id)
    except JournalError as exc:
        parser.exit(2, f"maintenance journal: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
