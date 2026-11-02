from typing import ClassVar

from ultron.world.entity import WorldEntity


class ApplicationObserver:
    """
    Detects known applications from processes.
    """

    APPLICATION_MAP = {
        "code": "Visual Studio Code",
        "chrome": "Google Chrome",
        "firefox": "Firefox",
        "telegram": "Telegram",
        "discord": "Discord",
    }

    def observe(
        self,
        processes: list[WorldEntity],
    ):

        applications: ClassVar = []

        for process in processes:
            if process.name in self.APPLICATION_MAP:
                applications.append(
                    WorldEntity(
                        name=self.APPLICATION_MAP[process.name],
                        entity_type="application",
                        properties={
                            "process": process.name,
                            "running": True,
                        },
                    )
                )

        return applications
