import importlib


class SkillLoader:
    """
    Dynamically loads ULTRON capabilities.
    """


    def __init__(
        self,
        registry,
    ):

        self.registry = registry

        self.loaded = {}



    def load(
        self,
        capability_name,
    ):

        capability = (
            self.registry.capabilities.get(
                capability_name
            )
        )


        if not capability:

            return {
                "success": False,
                "reason": "Capability not registered",
            }



        try:

            module = importlib.import_module(
                capability.module
            )


            skill_class = None


            for name in dir(module):

                if name.lower().endswith("skill"):

                    skill_class = getattr(
                        module,
                        name,
                    )

                    break



            if not skill_class:

                return {
                    "success": False,
                    "reason": "Skill class not found",
                }



            instance = skill_class()


            self.loaded[
                capability_name
            ] = instance


            return {
                "success": True,
                "skill": instance,
            }


        except Exception as error:

            return {
                "success": False,
                "reason": str(error),
            }
