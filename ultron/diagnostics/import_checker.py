import ast
from pathlib import Path
from typing import ClassVar


class ImportChecker:
    def scan(self, root="ultron"):

        failures: ClassVar = []

        for file in Path(root).rglob("*.py"):
            try:
                ast.parse(file.read_text())

            except Exception as e:
                failures.append({"file": str(file), "error": str(e)})

        return failures
