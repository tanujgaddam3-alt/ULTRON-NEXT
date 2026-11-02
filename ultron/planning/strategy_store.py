import json
from pathlib import Path


class StrategyStore:
    """
    Persistent storage for strategy performance.
    """

    def __init__(
        self,
        path: str = "data/strategy_memory.json",
    ):

        self.path = Path(path)

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        if not self.path.exists():
            self.path.write_text("{}")

    def save(
        self,
        data: dict,
    ):

        self.path.write_text(
            json.dumps(
                data,
                indent=4,
            )
        )

    def load(self) -> dict:

        try:
            return json.loads(self.path.read_text())

        except Exception:
            return {}
