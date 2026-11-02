class GoalEvolutionController:
    """
    Executes capability evolution
    based on goals.
    """


    def __init__(
        self,
        goal_analyzer,
        goal_planner,
        evolution_analyzer,
        evolution_engine,
        improvement_manager,
        capability_verifier=None,
    ):

        self.goal_analyzer = goal_analyzer

        self.goal_planner = goal_planner

        self.evolution_analyzer = evolution_analyzer

        self.evolution_engine = evolution_engine

        self.improvement_manager = improvement_manager

        self.capability_verifier = capability_verifier



    def evolve_towards_goal(
        self,
        goal,
    ):

        mapping = (
            self.goal_analyzer.analyze(
                goal
            )
        )


        plan = (
            self.goal_planner.create_plan(
                mapping
            )
        )


        if not plan["next"]:

         if self.capability_verifier:

          verification = (
    self.capability_verifier
    .verify_goal(
        [
            item.capability
            for item in mapping.required
        ]
    )
)


          if verification["complete"]:

            return {

                "success": True,

                "reason":
                    "Goal fully satisfied",

                "verification":
                    verification,

            }


          return {

            "success": False,

            "reason":
                "Capabilities require improvement",

            "verification":
                verification,

        }


         return {

        "success": True,

        "reason":
            "Goal completed",

    }


        capability = (
            plan["next"]["capability"]
        )


        status = (
            self.evolution_analyzer.analyze(
                capability
            )
        )


        decision = (
            self.evolution_engine.decide(
                status
            )
        )


        if decision.action == "maintain":

            return {

                "success": False,

                "reason":
                    "Capability already sufficient",

                "decision":
                    decision,

            }


        result = (
            self.improvement_manager.improve(
                capability,
                decision.reason,
            )
        )


        return {

            "success":
                result.get(
                    "accepted",
                    False,
                ),

            "goal":
                goal,

            "capability":
                capability,

            "decision":
                decision,

            "result":
                result,

        }
