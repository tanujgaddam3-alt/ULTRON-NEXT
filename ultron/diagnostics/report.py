class HealthReport:
    def generate(self, data):

        return {
            "modules": len(data["modules"]),
            "tests": data["tests"],
            "security": data["security"],
            "dead_code": data["dead_code"],
        }
