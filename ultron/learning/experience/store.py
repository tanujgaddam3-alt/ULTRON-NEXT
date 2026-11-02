import json
from pathlib import Path

from ultron.learning.experience.models import (
    ExperienceRecord,
)


class ExperienceStore:
    """
    Persistent storage for ULTRON experiences.
    """

    def __init__(
        self,
        path: str = "data/experiences.json",
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
        experience: ExperienceRecord,
    ):

        experiences = self.load()

        experiences.append(experience.model_dump())

        self.path.write_text(
            json.dumps(
                experiences,
                indent=4,
                default=str,
            )
        )

    def load(self) -> list[dict]:

        try:
            return json.loads(self.path.read_text())

        except Exception:
            return []

    def recent(
        self,
        limit: int = 10,
    ):

        experiences = self.load()

        return experiences[-limit:]
