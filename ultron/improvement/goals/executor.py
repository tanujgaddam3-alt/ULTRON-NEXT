class GoalEvolutionExecutor:
    """
    Executes complete capability evolution plans.
    """


    def __init__(
        self,
        goal_analyzer,
        goal_planner,
        evolution_controller,
        capability_registry,
    ):

        self.goal_analyzer = goal_analyzer

        self.goal_planner = goal_planner

        self.evolution_controller = (
            evolution_controller
        )

        self.registry = capability_registry



    def execute(
        self,
        goal,
        max_steps=10,
    ):

        results = []


        for step in range(max_steps):

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


            next_capability = (
                plan.get("next")
            )


            if not next_capability:

                return {

                    "success": True,

                    "goal": goal,

                    "message":
                    "Goal capabilities completed",

                    "results": results,

                }


            capability = (
                next_capability["capability"]
            )


            result = (
                self.evolution_controller
                .evolve_towards_goal(
                    goal
                )
            )


            results.append(
                {
                    "capability": capability,
                    "result": result,
                }
            )


            if not result.get(
                "success",
                False
            ):

                break


        return {

            "success": False,

            "goal": goal,

            "results": results,

        }
