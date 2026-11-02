from __future__ import annotations

import hashlib
import json
import os
import getpass
from datetime import UTC, datetime
from typing import Any


def valid_verification_evidence(
    operation: dict[str, Any],
    evidence: Any,
) -> bool:
    if not isinstance(evidence, dict):
        return False
    outcome = evidence.get("outcome")
    if outcome not in {
        "CONFIRMED_SUCCESS",
        "CONFIRMED_FAILURE",
        "AMBIGUOUS",
    }:
        return False
    if (
        evidence.get("verified") is not (outcome == "CONFIRMED_SUCCESS")
        or evidence.get("operationId") != operation.get("operationId")
        or evidence.get("requestFingerprint")
        != operation.get("requestFingerprint")
        or evidence.get("expectedEffect") != operation.get("expectedEffect")
        or not isinstance(evidence.get("verifier"), str)
        or not evidence["verifier"].strip()
        or not isinstance(evidence.get("provenance"), str)
        or not evidence["provenance"].strip()
        or not isinstance(evidence.get("observedAt"), str)
        or not evidence["observedAt"].strip()
    ):
        return False
    try:
        observed_at = datetime.fromisoformat(
            evidence["observedAt"].replace("Z", "+00:00")
        )
    except ValueError:
        return False
    return observed_at.tzinfo is not None


class LocalReadOnlyOperationVerifier:
    """Independently checks the narrow, read-only `pwd` gateway operation."""

    verifier_id = "ultron.local-working-directory"

    def verify(self, operation: dict[str, Any]) -> dict[str, Any] | None:
        command_text = operation.get("commandText")
        if (
            operation.get("verb") != "execute"
            or not isinstance(command_text, str)
            or command_text.strip() != "pwd"
            or operation.get("expectedEffect")
            != "Return the current working directory without changing state."
            or not isinstance(operation.get("parameters"), dict)
            or operation["parameters"].get("subject") != "pwd"
        ):
            return None

        operation_id = operation.get("operationId")
        request_fingerprint = operation.get("requestFingerprint")
        expected_fingerprint = self._request_fingerprint(operation)
        if (
            not isinstance(operation_id, str)
            or not isinstance(request_fingerprint, str)
            or request_fingerprint != expected_fingerprint
        ):
            return self._evidence(
                operation,
                "AMBIGUOUS",
                None,
                "Operation identity or request fingerprint did not validate.",
            )

        receipt = operation.get("executionResult")
        raw_result = receipt.get("result") if isinstance(receipt, dict) else None
        steps = raw_result.get("results") if isinstance(raw_result, dict) else None
        step = (
            steps[0]
            if isinstance(steps, list) and len(steps) == 1
            and isinstance(steps[0], dict)
            else None
        )
        command_result = (
            step.get("result")
            if isinstance(step, dict) and isinstance(step.get("result"), dict)
            else None
        )
        if command_result is None or command_result.get("command") != "pwd":
            return self._evidence(
                operation,
                "AMBIGUOUS",
                None,
                "The persisted receipt does not identify the exact read-only command.",
            )

        observed_directory = os.getcwd()
        returncode = command_result.get("returncode")
        stdout = command_result.get("stdout")
        if (
            returncode == 0
            and isinstance(stdout, str)
            and stdout.strip() == observed_directory
        ):
            return self._evidence(
                operation,
                "CONFIRMED_SUCCESS",
                {"workingDirectory": observed_directory},
                "Command output matches the independently observed process directory.",
            )
        if isinstance(returncode, int) and returncode != 0:
            return self._evidence(
                operation,
                "CONFIRMED_FAILURE",
                {"workingDirectory": observed_directory},
                "The read-only command returned a non-zero exit status.",
            )
        return self._evidence(
            operation,
            "AMBIGUOUS",
            {"workingDirectory": observed_directory},
            "The command receipt conflicts with the independently observed directory.",
        )

    @classmethod
    def _request_fingerprint(cls, operation: dict[str, Any]) -> str:
        canonical = json.dumps(
            {
                "verb": operation.get("verb"),
                "commandText": str(operation.get("commandText", "")).strip(),
                "parameters": operation.get("parameters"),
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _evidence(
        operation: dict[str, Any],
        outcome: str,
        observed_state: dict[str, Any] | None,
        detail: str,
    ) -> dict[str, Any]:
        return {
            "verified": outcome == "CONFIRMED_SUCCESS",
            "outcome": outcome,
            "operationId": operation.get("operationId"),
            "requestFingerprint": operation.get("requestFingerprint"),
            "verifier": LocalReadOnlyOperationVerifier.verifier_id,
            "provenance": "LOCAL_PROCESS_STATE",
            "observedAt": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "expectedEffect": operation.get("expectedEffect"),
            "observedState": observed_state,
            "detail": detail,
        }


class LocalReadOnlyTaskVerifier:
    """Verifies a bounded task containing only `pwd` and `whoami` steps."""

    verifier_id = "ultron.local-read-only-task"
    _EXPECTED_EFFECT = (
        "Run the approved read-only local inspection steps and verify each result."
    )

    def verify(self, operation: dict[str, Any]) -> dict[str, Any] | None:
        if (
            operation.get("verb") != "execute_task"
            or operation.get("expectedEffect") != self._EXPECTED_EFFECT
        ):
            return None
        parameters = operation.get("parameters")
        if not isinstance(parameters, dict):
            return None
        steps = parameters.get("steps")
        receipt = operation.get("executionResult")
        execution = receipt.get("result") if isinstance(receipt, dict) else None
        results = execution.get("results") if isinstance(execution, dict) else None
        if (
            not isinstance(steps, list)
            or not isinstance(results, list)
            or len(steps) != len(results)
            or not steps
        ):
            return self._evidence(
                operation, "AMBIGUOUS", None,
                "The persisted task receipt does not match its approved steps.",
            )

        expected_identity = {
            "pwd": os.getcwd(),
            "whoami": getpass.getuser(),
        }
        observed = []
        for step, result in zip(steps, results):
            command = (
                step.get("parameters", {}).get("command")
                if isinstance(step, dict)
                and isinstance(step.get("parameters"), dict)
                else None
            )
            terminal_result = (
                result.get("result")
                if isinstance(result, dict)
                and isinstance(result.get("result"), dict)
                else None
            )
            if (
                command not in expected_identity
                or terminal_result is None
                or terminal_result.get("command") != command
            ):
                return self._evidence(
                    operation, "AMBIGUOUS", None,
                    "The task receipt does not identify an approved read-only command.",
                )
            if result.get("status") != "COMPLETED":
                return self._evidence(
                    operation, "AMBIGUOUS", None,
                    "A task step did not report completed execution.",
                )
            if terminal_result.get("returncode") != 0:
                observed.append({"command": command, "returncode": terminal_result.get("returncode")})
                return self._evidence(
                    operation, "CONFIRMED_FAILURE", {"steps": observed},
                    "A read-only inspection command returned a non-zero status.",
                )
            stdout = terminal_result.get("stdout")
            if not isinstance(stdout, str) or stdout.strip() != expected_identity[command]:
                return self._evidence(
                    operation, "AMBIGUOUS", None,
                    "A step result conflicts with independently observed local state.",
                )
            observed.append({"command": command, "value": expected_identity[command]})

        return self._evidence(
            operation, "CONFIRMED_SUCCESS", {"steps": observed},
            "Every approved read-only task step matches independently observed local state.",
        )

    @classmethod
    def _request_fingerprint(cls, operation: dict[str, Any]) -> str:
        canonical = json.dumps(
            {
                "verb": operation.get("verb"),
                "commandText": str(operation.get("commandText", "")).strip(),
                "parameters": operation.get("parameters"),
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def _evidence(
        cls,
        operation: dict[str, Any],
        outcome: str,
        observed_state: dict[str, Any] | None,
        detail: str,
    ) -> dict[str, Any]:
        valid_fingerprint = (
            operation.get("requestFingerprint")
            == cls._request_fingerprint(operation)
        )
        if not valid_fingerprint:
            outcome = "AMBIGUOUS"
            observed_state = None
            detail = "The task request fingerprint did not validate."
        return {
            "verified": outcome == "CONFIRMED_SUCCESS",
            "outcome": outcome,
            "operationId": operation.get("operationId"),
            "requestFingerprint": operation.get("requestFingerprint"),
            "verifier": cls.verifier_id,
            "provenance": "LOCAL_PROCESS_STATE",
            "observedAt": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "expectedEffect": operation.get("expectedEffect"),
            "observedState": observed_state,
            "detail": detail,
        }
