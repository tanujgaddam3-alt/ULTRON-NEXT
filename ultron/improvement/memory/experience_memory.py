import json
from pathlib import Path
from datetime import datetime


class ExperienceMemory:
    """
    Stores ULTRON improvement history.
    """


    def __init__(
        self,
        path="data/improvement_history.json",
    ):

        self.path = Path(path)

        self.events = []

        self._load()



    def record(
        self,
        event: dict,
    ):

        event["timestamp"] = (
            datetime.now().isoformat()
        )

        self.events.append(
            event
        )

        self._save()



    def history(
        self,
    ):

        return self.events



    def find(
        self,
        capability,
    ):

        return [

            event

            for event in self.events

            if event.get(
                "capability"
            ) == capability

        ]



    def _save(
        self,
    ):

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.path.write_text(
            json.dumps(
                self.events,
                indent=4,
            )
        )



    def _load(
        self,
    ):

        if not self.path.exists():

            return


        self.events = json.loads(
            self.path.read_text()
        )
