class AutonomousImprovementController:
    """
    Controls ULTRON self-improvement decisions.
    """

    def __init__(
        self,
        self_awareness,
        priority_engine,
        improvement_manager,
        evaluation_engine=None,
        capability_metrics=None,
        capability_registry=None,
        evolution_analyzer=None,
        evolution_engine=None,
        evolution_memory=None
    ):

        self.self_awareness = self_awareness

        self.priority_engine = priority_engine

        self.improvement_manager = improvement_manager

        self.evaluation_engine = evaluation_engine

        self.capability_metrics = capability_metrics

        self.capability_registry = capability_registry

        self.evolution_analyzer = evolution_analyzer

        self.evolution_engine = evolution_engine

        self.evolution_memory = evolution_memory


    def analyze_and_improve(self):

        report = (
            self.self_awareness.analyze()
        )


        weaknesses = (
            report.recommendations
        )


        if not weaknesses:

            return {
                "success": False,
                "reason": "No improvements detected",
            }


        ranked = (
            self.priority_engine.rank(
                weaknesses
            )
        )


        target = ranked[0]


        evolution = None


        if (
            self.evolution_analyzer
            and self.evolution_engine
        ):

            status = (
                self.evolution_analyzer.analyze(
                    target.capability
                )
            )


            evolution = (
                self.evolution_engine.decide(
                    status
                )
            )


        else:

            return {
                "success": False,
                "reason": "Evolution engine unavailable",
            }


        before = 0

        after = 0


        if self.capability_metrics:

            before = (
                self.capability_metrics.measure(
                    target.capability
                )
            )


        if evolution.action == "create":

            result = (
                self.improvement_manager.improve(
                    target.capability,
                    self._get_original_reason(
                        weaknesses,
                        target.capability,
                    ),
                )
            )


        elif evolution.action == "improve":

            result = (
                self.improvement_manager.improve(
                    target.capability,
                    (
                        "Existing capability "
                        "requires optimization."
                    ),
                )
            )


        else:

            result = {
                "accepted": False,
                "reason": (
                    "Capability already optimal"
                ),
            }


        if self.capability_metrics:

            after = (
                self.capability_metrics.measure(
                    target.capability
                )
            )


        evaluation = None

        


        if self.evaluation_engine:

            evaluation = (
                self.evaluation_engine.evaluate(
                    target.capability,
                    before,
                    after,
                )
            )

        if (
    self.evolution_memory
    and evaluation
):

         self.evolution_memory.remember(

    capability=target.capability,

    action=evolution.action,

    before=before,

    after=after,

    success=evaluation.success,

    strategy=evolution.action,

    reason=evolution.reason,

)


        return {

            "selected": target,

            "evolution": evolution,

            "result": result,

            "evaluation": evaluation,

        }


    def _get_original_reason(
        self,
        weaknesses,
        capability,
    ):

        for item in weaknesses:

            if (
                item["capability"]
                ==
                capability
            ):

                return item["reason"]


        return (
            f"Capability gap detected: "
            f"{capability}"
        )