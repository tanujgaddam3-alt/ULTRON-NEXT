import os
import shlex
import subprocess

from ultron.skills.base import Skill
from ultron.skills.security.policy import (
    CommandPermission,
    CommandPolicy,
)


class TerminalSkill(Skill):
    """
    Executes terminal commands.
    """

    name = "terminal"

    description = "Executes shell commands and returns results."

    version = "1.0"

    actions = [
        "run_command",
    ]

    def __init__(self):

        self.policy = CommandPolicy()

    def validate(
        self,
        action: str,
        **kwargs,
    ) -> bool:

        if action != "run_command":
            return False

        return bool(kwargs.get("command"))

    def execute(
        self,
        action: str,
        **kwargs,
    ):

        if action != "run_command":
            raise ValueError(f"Unsupported action: {action}")

        return self.run_command(
            kwargs.get("command"),
            kwargs.get("cwd"),
        )

    def run_command(
        self,
        command: str | None,
        cwd: str | None = None,
    ) -> dict:

        if not command:
            raise ValueError("Command is required.")
        permission = self.policy.permission(command)

        if permission == CommandPermission.DENY:
            return {
                "success": False,
                "message": ("Command denied by security policy."),
            }

        if permission == CommandPermission.CONFIRM:
            return {
                "success": False,
                "requires_confirmation": True,
                "message": ("Command requires user confirmation."),
            }

        if cwd is not None:
            return {
                "success": False,
                "requires_confirmation": True,
                "message": "A custom working directory requires confirmation.",
            }

        arguments = shlex.split(command, posix=os.name != "nt")
        try:
            result = subprocess.run(
                arguments,
                shell=False,
                cwd=None,
                capture_output=True,
                text=True,
                timeout=120,
            )

            return {
                "success": result.returncode == 0,
                "command": command,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
                "requires_confirmation": False,
                "message": (
                    "Command executed successfully."
                    if result.returncode == 0
                    else "Command exited with a non-zero status."
                ),
            }

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "command": command,
                "stdout": "",
                "stderr": "",
                "returncode": None,
                "requires_confirmation": False,
                "message": "Command timed out after 120 seconds.",
            }

        except FileNotFoundError:
            return {
                "success": False,
                "command": command,
                "stdout": "",
                "stderr": "Command executable was not found.",
                "returncode": None,
                "requires_confirmation": False,
                "not_dispatched": True,
                "message": "Command executable was not found.",
            }

        except OSError as error:
            return {
                "success": False,
                "command": command,
                "stdout": "",
                "stderr": str(error),
                "returncode": None,
                "requires_confirmation": False,
                "message": str(error),
            }
