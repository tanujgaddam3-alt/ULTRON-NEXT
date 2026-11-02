import yaml
from pathlib import Path


class CapabilityTemplateLoader:


    def load(self, capability):

        path = (
            Path(
                "ultron/improvement/templates"
            )
            /
            f"{capability}.yaml"
        )


        if not path.exists():

            return None


        with open(path) as f:

            return yaml.safe_load(f)
