import platform
import subprocess


class SystemController:
    """
    Controls operating system level actions.
    """

    def get_system_info(
        self,
    ):

        return {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        }

    def execute(
        self,
        command,
    ):

        if not command.target:
            return {"status": "missing_command"}

        try:
            output = subprocess.run(
                command.target,
                shell=True,
                capture_output=True,
                text=True,
            )

            return {
                "status": "completed",
                "output": output.stdout,
            }

        except Exception as error:
            return {
                "status": "failed",
                "error": str(error),
            }
