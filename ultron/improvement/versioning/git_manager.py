import subprocess


class GitManager:
    """
    Handles version checkpoints for
    improvement operations.
    """

    def run(
        self,
        command: list[str],
    ) -> dict:

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
            )

            return {
                "success": result.returncode == 0,
                "output": result.stdout,
                "error": result.stderr,
            }

        except Exception as error:
            return {
                "success": False,
                "error": str(error),
            }

    def create_checkpoint(
        self,
        message: str = "Before improvement",
    ):

        self.run(
            [
                "git",
                "add",
                ".",
            ]
        )

        return self.run(
            [
                "git",
                "commit",
                "-m",
                message,
            ]
        )

    def rollback(
        self,
    ):

        return self.run(
            [
                "git",
                "reset",
                "--hard",
                "HEAD~1",
            ]
        )
