from ultron.skills.base import Skill


class SelfAwarenessSkill(Skill):
    """
    Provides ULTRON internal self-analysis capability.
    """

    name = "self_awareness"

    description = (
        "Analyzes ULTRON internal capabilities, "
        "schemas and experience."
    )

    version = "1.0"

    actions = [
        "self_analyze",
    ]


    def __init__(
        self,
        engine,
    ):
        self.engine = engine


    def execute(
        self,
        action: str,
        **kwargs,
    ):

        if action == "self_analyze":

            report = self.engine.analyze()

            return {
                "success": True,
                "report": report,
            }


        raise ValueError(
            f"Unsupported action: {action}"
        )


    def validate(
        self,
        action: str,
        **kwargs,
    ) -> bool:

        return action == "self_analyze"
