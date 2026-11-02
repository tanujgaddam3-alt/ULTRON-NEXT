from pathlib import Path
from typing import ClassVar


class CodeLocator:
    """
    Finds source files related to a component.
    """

    def __init__(
        self,
        root=".",
    ):

        self.root = Path(root)

    def locate(
        self,
        component: str,
        problem: str = "",
    ):

        matches: ClassVar = []

        keywords = set(
            (component + " " + problem)
            .lower()
            .replace(".", "")
            .replace("(", "")
            .replace(")", "")
            .split()
        )

        for file in self.root.rglob("*.py"):
            path = str(file)

            if ".venv" in path:
                continue

            if "__pycache__" in path:
                continue

            score = 0

            filename = file.stem.lower()

            for keyword in keywords:
                if len(keyword) > 4 and keyword in filename:
                    score += 5

            try:
                content = file.read_text(encoding="utf-8").lower()

            except Exception:
                continue

            for keyword in keywords:
                if len(keyword) > 4 and keyword in content:
                    score += 2

            if score > 0:
                matches.append(
                    (
                        score,
                        path,
                    )
                )

        matches.sort(reverse=True)

        return [path for score, path in matches]
