import shlex
import subprocess
import sys

from ultron.skills.security.policy import CommandPermission, CommandPolicy
from ultron.skills.terminal.skill import TerminalSkill


def test_allowed_command_returns_structured_result():
    result = TerminalSkill().execute(
        "run_command",
        command="pwd",
    )

    assert result["success"] is True
    assert result["returncode"] == 0
    assert result["requires_confirmation"] is False
    assert result["stdout"]


def test_confirmation_command_does_not_execute():
    result = TerminalSkill().execute(
        "run_command",
        command="echo hello",
    )

    assert result["success"] is False
    assert result["requires_confirmation"] is True
    assert "stdout" not in result


def test_denied_command_does_not_execute():
    result = TerminalSkill().execute(
        "run_command",
        command="dd if=/dev/zero of=/tmp/ultron_policy_test bs=1 count=1",
    )

    assert result["success"] is False
    assert result.get("requires_confirmation", False) is False
    assert "denied" in result["message"].lower()


def test_malformed_command_type_is_denied():
    result = TerminalSkill().execute("run_command", command=["pwd"])

    assert result["success"] is False
    assert result.get("requires_confirmation", False) is False
    assert "denied" in result["message"].lower()


def test_arbitrary_interpreter_commands_require_confirmation():
    result = TerminalSkill().execute(
        "run_command",
        command=f"{shlex.quote(sys.executable)} -c 'raise SystemExit(3)'",
    )

    assert result["success"] is False
    assert result["requires_confirmation"] is True
    assert "returncode" not in result


def test_command_chaining_is_not_auto_allowed():
    policy = CommandPolicy()

    assert policy.permission("pwd; ls") == CommandPermission.CONFIRM
    assert policy.permission("pwd /tmp") == CommandPermission.CONFIRM
    assert policy.permission("/tmp/pwd") == CommandPermission.CONFIRM
    for command in (
        "python -c 'print(1)'",
        "python3 -c 'print(1)'",
        "pytest -q",
        "git status",
        "pip install example",
    ):
        assert policy.permission(command) == CommandPermission.CONFIRM


def test_custom_working_directory_requires_confirmation():
    result = TerminalSkill().execute("run_command", command="pwd", cwd="/tmp")

    assert result["requires_confirmation"] is True
    assert "stdout" not in result


def test_shell_is_not_used_to_run_allowed_commands(monkeypatch):
    seen = {}

    def fake_run(arguments, **kwargs):
        seen["arguments"] = arguments
        seen["shell"] = kwargs["shell"]
        return subprocess.CompletedProcess(arguments, 3, "", "failed")

    monkeypatch.setattr("ultron.skills.terminal.skill.subprocess.run", fake_run)
    result = TerminalSkill().execute("run_command", command="pwd")

    assert seen == {"arguments": ["pwd"], "shell": False}
    assert result["success"] is False
    assert result["returncode"] == 3


def test_missing_executable_reports_failure_without_shell(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))

    result = TerminalSkill().execute("run_command", command="pwd")

    assert result["success"] is False
    assert result["returncode"] is None
    assert result["not_dispatched"] is True
    assert "not found" in result["stderr"].lower()
