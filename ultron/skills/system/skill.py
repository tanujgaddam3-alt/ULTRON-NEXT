import subprocess

from ultron.config.runtime import DRY_RUN, TEST_MODE
from ultron.security.permission.rules import deny_when_authorization_unavailable
from ultron.skills.base import Skill
from ultron.skills.system.monitor import SystemMonitor
from ultron.skills.system.app_resolver import (
    ApplicationResolver,
)


class SystemSkill(Skill):
    """
    Executes operating system actions.
    """

    def __init__(self):

        self.resolver = ApplicationResolver()
        self.monitor = SystemMonitor()


    name = "system"

    description = (
        "Controls operating system applications "
        "and system-level actions."
    )

    version = "1.0"

    actions = [
        "open_application",
        "get_system_info",
        "get_disk_usage",
        "close_application",
    ]


    def execute(
        self,
        action: str,
        **kwargs,
    ):
        deny_when_authorization_unavailable(action)

        if action == "open_application":
            return self._open_application(
                kwargs.get("application")
            )


        if action == "get_system_info":
            return {
                "success": True,
                "data": self.monitor.get_info(),
            }


        if action == "get_disk_usage":
            return {
                "success": True,
                "data": self.monitor.get_disk_usage(),
            }


        if action == "close_application":
            return self._close_application(
                kwargs.get("application")
            )


        raise ValueError(
            f"Unsupported action: {action}"
        )


    def _open_application(
        self,
        application: str | None,
    ) -> dict:
        deny_when_authorization_unavailable("open_application")

        if not application:
            raise ValueError(
                "Application name is required."
            )


        if TEST_MODE or DRY_RUN:

            return {
                "success": True,
                "message": (
                    f"{application} launch simulated."
                ),
                "simulated": True,
                "application": application,
            }


        resolved = self.resolver.resolve(
            application
        )


        if resolved is None:

            return {
                "success": False,
                "message": (
                    f"{application} not found."
                ),
            }


        try:

            # Desktop application
            if (
                isinstance(resolved, dict)
                and resolved.get("type") == "desktop"
            ):

                desktop_file = resolved["path"]

                exec_command = None


                with open(
                    desktop_file,
                    "r",
                ) as file:

                    for line in file:

                        if line.startswith("Exec="):

                            exec_command = (
                                line
                                .replace(
                                    "Exec=",
                                    ""
                                )
                                .split()[0]
                            )

                            break


                if exec_command is None:

                    return {
                        "success": False,
                        "message": (
                            "Application launcher found "
                            "but no executable defined."
                        ),
                    }


                process = subprocess.Popen(
                    [
                        exec_command
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )


            # Normal executable
            else:

                executable = (
                    resolved["path"]
                    if isinstance(
                        resolved,
                        dict,
                    )
                    else resolved
                )


                process = subprocess.Popen(
                    [
                        executable
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )


            return {
                "success": True,
                "message": (
                    f"{application} launched."
                ),
                "pid": process.pid,
            }


        except OSError as error:

            return {
                "success": False,
                "message": str(error),
            }



    def validate(
        self,
        action: str,
        **kwargs,
    ) -> bool:


        if action == "open_application":

            return bool(
                kwargs.get("application")
            )


        if action in [
            "get_system_info",
            "get_disk_usage",
        ]:

            return True


        if action == "close_application":

            return bool(
                kwargs.get("application")
            )


        return False