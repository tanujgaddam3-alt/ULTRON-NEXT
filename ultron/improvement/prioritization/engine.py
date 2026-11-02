from ultron.improvement.prioritization.models import (
    ImprovementPriority,
)


class ImprovementPriorityEngine:
    """
    Determines which improvements
    should happen first.
    """
    def __init__(
    self,
    experience_memory=None,
):

     self.experience_memory = experience_memory


    def evaluate(
        self,
        capability: str,
        reason: str,
    ):

        impact = self._impact(
            capability
        )

        dependency = self._dependency(
            capability
        )

        urgency = self._urgency(
            reason
        )

        risk = self._risk(
            capability
        )
        experience_bonus = 0

        history = []


        if self.experience_memory:

          history = (
        self.experience_memory.find(
            capability
        )
    )


        for event in history:

         if event.get(
        "result"
          ) == "accepted":

          experience_bonus += 2


        score = (
    impact
    +
    dependency
    +
    urgency
    -
    risk
    +
    experience_bonus
)


        return ImprovementPriority(

            capability=capability,

            impact=impact,

            urgency=urgency,

            dependency=dependency,

            risk=risk,

            score=score,

            reason=(
                f"Priority calculated for "
                f"{capability}"
            ),
        )



    def rank(
        self,
        improvements,
    ):

        results = []


        for item in improvements:

            results.append(
                self.evaluate(
                    item["capability"],
                    item["reason"],
                )
            )


        return sorted(
            results,
            key=lambda x: x.score,
            reverse=True,
        )



    def _impact(
        self,
        capability,
    ):

        high_value = [

            "reasoning",

            "memory",

            "vision",

            "planning",

            "execution",

        ]


        if capability.lower() in high_value:

            return 10


        return 5



    def _dependency(
        self,
        capability,
    ):

        if capability.lower() in [
            "vision",
            "memory",
            "reasoning",
        ]:

            return 8


        return 4



    def _urgency(
        self,
        reason,
    ):

        if "missing" in reason.lower():

            return 7


        return 3



    def _risk(
        self,
        capability,
    ):

        dangerous = [

            "execution",

            "security",

            "self_modify",

        ]


        if capability.lower() in dangerous:

            return 8


        return 2
