class ExecutionService:
    """
    Coordinates execution.
    """

    def __init__(self, execution_engine):
        self.engine = execution_engine

    def execute(self, plan):
        return self.engine.execute(plan)
