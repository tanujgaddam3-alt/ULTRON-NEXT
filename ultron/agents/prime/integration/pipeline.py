class PrimePipeline:
    """
    Complete ULTRON Prime mission pipeline.
    """

    def __init__(
        self,
        prime,
        team_builder,
        workflow_engine,
        workflow_coordinator,
        evaluator,
    ):

        self.prime = prime
        self.team_builder = team_builder
        self.workflow_engine = workflow_engine
        self.workflow_coordinator = workflow_coordinator
        self.evaluator = evaluator

    def run(
        self,
        command: str,
    ):

        intent_result = self.prime.process(command)

        workflow = intent_result["workflow"]

        execution = self.workflow_coordinator.execute(workflow)

        evaluation = self.evaluator.evaluate(execution)

        return {
            "command": command,
            "execution": execution,
            "evaluation": evaluation,
        }
