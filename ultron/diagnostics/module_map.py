from pathlib import Path
from typing import ClassVar


class ModuleScanner:
    def scan(self, root="ultron"):

        modules: ClassVar = []

        for file in Path(root).rglob("*.py"):
            modules.append({"file": str(file), "size": file.stat().st_size})

        return modules
