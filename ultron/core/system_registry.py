from typing import ClassVar


class SystemRegistry:
    def __init__(self):
        self.systems = {}

    def register(self, name, system):
        self.systems[name] = system

    def get(self, name):
        return self.systems.get(name)

    def health(self):

        result: ClassVar = {}

        for name, system in self.systems.items():
            data = {
                "type": type(system).__name__,
                "status": "unknown",
            }

            try:
                if hasattr(system, "health_check"):
                    response = system.health_check()

                    data["status"] = "responding"
                    data["response"] = response

                else:
                    data["status"] = "exists_only"

            except (AttributeError, OSError, RuntimeError, TimeoutError) as e:
                data["status"] = "failed"
                data["error"] = str(e)

            result[name] = data

        return result
