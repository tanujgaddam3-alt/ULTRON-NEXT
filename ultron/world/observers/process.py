import subprocess
from typing import ClassVar

from ultron.world.entity import WorldEntity


class ProcessObserver:
    def observe(self):

        entities: ClassVar = []

        try:
            result = subprocess.run(
                ["bash", "-c", "ps -e -o comm"],
                capture_output=True,
                text=True,
            )

            for process in result.stdout.splitlines()[:20]:
                if process.strip():
                    entities.append(
                        WorldEntity(
                            name=process.strip(),
                            entity_type="process",
                            properties={"running": True},
                        )
                    )

        except Exception:
            pass

        return entities
