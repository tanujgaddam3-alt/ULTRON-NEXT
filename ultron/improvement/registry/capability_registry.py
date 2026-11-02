import json
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class Capability:

    name: str

    module: str

    description: str

    status: str = "active"



class CapabilityRegistry:
    """
    Persistent capability memory for ULTRON.
    """

    def __init__(
        self,
        storage_path="data/capabilities.json",
    ):

        self.storage_path = Path(
            storage_path
        )

        self.capabilities = {}

        self._load()



    def register(
        self,
        name: str,
        module: str,
        description: str,
    ):

        capability = Capability(
            name=name,
            module=module,
            description=description,
        )


        self.capabilities[name] = capability


        self._save()


        return capability



    def exists(
        self,
        name,
    ):

        return name in self.capabilities



    def list_capabilities(
        self,
    ):

        return list(
            self.capabilities.values()
        )



    def _save(
        self,
    ):

        self.storage_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )


        data = {

            name: asdict(capability)

            for name, capability

            in self.capabilities.items()

        }


        self.storage_path.write_text(
            json.dumps(
                data,
                indent=4,
            )
        )



    def _load(
        self,
    ):

        if not self.storage_path.exists():

            return


        data = json.loads(
            self.storage_path.read_text()
        )


        for name, value in data.items():

            self.capabilities[name] = Capability(
                **value
            )