import json
from pathlib import Path

from ultron.evaluation.result import EvaluationResult


class EvaluationStore:
    """
    Persistent storage for evaluation results.
    """

    def __init__(
        self,
        path: str = "data/evaluations.json",
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
        result: EvaluationResult,
    ):

        results = self.load()

        results.append(result.model_dump())

        self.path.write_text(
            json.dumps(
                results,
                indent=4,
                default=str,
            )
        )

    def load(self) -> list[dict]:

        try:
            return json.loads(self.path.read_text())

        except Exception:
            return []
