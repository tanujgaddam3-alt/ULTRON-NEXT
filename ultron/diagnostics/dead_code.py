from pathlib import Path
from typing import ClassVar


class EmptyFileScanner:
    def scan(self, root="ultron"):

        empty: ClassVar = []

        for file in Path(root).rglob("*.py"):
            if file.stat().st_size == 0:
                empty.append(str(file))

        return empty
