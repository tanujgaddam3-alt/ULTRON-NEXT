from dataclasses import dataclass, field


@dataclass
class ComponentBlueprint:

    name: str

    files: list[dict] = field(
        default_factory=list
    )

    registrations: list[str] = field(
        default_factory=list
    )

    tests: list[str] = field(
        default_factory=list
    )

    description: str = ""



class ComponentBuilder:


    def __init__(
        self,
        template_loader=None,
    ):

        self.template_loader = template_loader



    def build(
        self,
        capability: str,
        description: str = "",
    ):


        capability = capability.lower()


        template = None


        if self.template_loader:

            template = (
                self.template_loader
                .load(capability)
            )


        if template:

            files = []


            for component in template.get(
                "components",
                []
            ):

                files.append(

                    {
                        "path":
                        f"ultron/skills/{capability}/{component['file']}",

                        "purpose":
                        component["name"],

                    }

                )


            return ComponentBlueprint(

                name=capability,

                files=files,

                registrations=[
                    f"Register {capability} capability"
                ],

                tests=[
                    f"tests/test_{capability}.py"
                ],

                description=description,

            )


        return ComponentBlueprint(

            name=capability,

            files=[

                {
                    "path":
                    f"ultron/skills/{capability}/skill.py",

                    "purpose":
                    f"{capability} capability skill",

                }

            ],

            registrations=[
                f"Register {capability} skill"
            ],

            tests=[
                f"tests/test_{capability}.py"
            ],

            description=description,

        )