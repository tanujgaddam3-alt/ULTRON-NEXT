import os
import shlex
import subprocess
import sys
from collections.abc import Mapping, Sequence


class TestRunner:
    """
    Controlled subprocess runner for validation commands.

    No shell is used by default.
    """

    def __init__(
        self,
        default_timeout: int = 300,
    ):
        if default_timeout <= 0:
            raise ValueError(
                "default_timeout must be positive"
            )

        self.default_timeout = default_timeout

    def run(
        self,
        command="pytest",
        *,
        cwd: str | None = None,
        timeout: int | None = None,
        env: Mapping[str, str] | None = None,
    ) -> dict:
        if timeout is None:
            timeout = self.default_timeout

        if timeout <= 0:
            raise ValueError(
                "timeout must be positive"
            )

        argv = self._normalize_command(
            command
        )

        process_env = None

        if env is not None:
            process_env = os.environ.copy()
            process_env.update(env)

        try:
            result = subprocess.run(
                argv,
                shell=False,
                cwd=cwd,
                env=process_env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )

            return {
                "success": result.returncode == 0,
                "return_code": result.returncode,
                "command": argv,
                "cwd": cwd,
                "timeout": timeout,
                "output": result.stdout,
                "errors": result.stderr,
            }

        except subprocess.TimeoutExpired as exc:
            stdout = self._decode_output(
                exc.stdout
            )
            stderr = self._decode_output(
                exc.stderr
            )

            return {
                "success": False,
                "return_code": None,
                "command": argv,
                "cwd": cwd,
                "timeout": timeout,
                "output": stdout,
                "errors": stderr,
                "error": "Test execution timeout",
            }

        except OSError as exc:
            return {
                "success": False,
                "return_code": None,
                "command": argv,
                "cwd": cwd,
                "timeout": timeout,
                "output": "",
                "errors": str(exc),
                "error": type(exc).__name__,
            }

    @staticmethod
    def _normalize_command(
        command,
    ) -> list[str]:
        if isinstance(command, str):
            argv = shlex.split(
                command
            )
        elif isinstance(command, Sequence):
            argv = [
                str(part)
                for part in command
            ]
        else:
            raise TypeError(
                "command must be a string or sequence"
            )

        if not argv:
            raise ValueError(
                "command cannot be empty"
            )

        if argv[0].lower() in {"python", "python3", "python.exe", "python3.exe"}:
            argv[0] = sys.executable

        return argv

    @staticmethod
    def _decode_output(
        output,
    ) -> str:
        if output is None:
            return ""

        if isinstance(output, bytes):
            return output.decode(
                "utf-8",
                errors="replace",
            )

        return str(output)