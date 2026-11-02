class CognitiveService:
    """
    Runs ULTRON's complete cognitive pipeline.
    """

    def __init__(
        self,
        reasoning,
        decision,
        planning,
        execution,
        reflection,
        learning,
    ):
        self.reasoning = reasoning
        self.decision = decision
        self.planning = planning
        self.execution = execution
        self.reflection = reflection
        self.learning = learning

    def think(self, context):
        """
        Execute one complete cognitive cycle.
        """

        reasoning = self.reasoning.think(context)

        decision = self.decision.decide(reasoning)

        plan = self.planning.plan(decision)

        from ultron.execution.result import ExecutionResult
        from ultron.domain.execution import ExecutionRecord


        if plan.plan.steps:
           execution = self.execution.execute(plan)

        else:
         execution = ExecutionResult(
        record=ExecutionRecord(
            success=True,
            completed_steps=0,
            message="No execution required."
        )
    )

        reflection = self.reflection.reflect(execution)

        experience = self.learning.learn(
            execution,
            reflection,
        )

        return {
            "reasoning": reasoning,
            "decision": decision,
            "plan": plan,
            "execution": execution,
            "reflection": reflection,
            "experience": experience,
        }
