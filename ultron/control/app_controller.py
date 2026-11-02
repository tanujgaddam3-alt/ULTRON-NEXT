import subprocess


class ApplicationController:
    """
    Controls system applications.
    """

    def __init__(
        self,
    ):

        self.running_apps = []

    def execute(
        self,
        command,
    ):

        target = command.target

        if not target:
            return {"status": "missing_target"}

        try:
            subprocess.Popen(
                target,
                shell=True,
            )

            self.running_apps.append(target)

            return {
                "status": "started",
                "application": target,
            }

        except Exception as error:
            return {
                "status": "failed",
                "error": str(error),
            }
