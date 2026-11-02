import json
from pathlib import Path

from ultron.learning.patterns.models import (
    BehaviorPattern,
)


class PatternStore:
    """
    Persistent storage for learned patterns.
    """

    def __init__(
        self,
        path: str = "data/patterns.json",
    ):

        self.path = Path(path)

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        if not self.path.exists():
            self.path.write_text("[]")

    def save(
        self,
        patterns: list[BehaviorPattern],
    ):

        data = [pattern.model_dump() for pattern in patterns]

        self.path.write_text(
            json.dumps(
                data,
                indent=4,
                default=str,
            )
        )

    def load(self) -> list[dict]:

        try:
            return json.loads(self.path.read_text())

        except Exception:
            return []

    def clear(self):

        self.path.write_text("[]")
