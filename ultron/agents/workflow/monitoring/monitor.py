class WorkflowMonitor:
    """
    Monitors workflow execution.
    """

    def __init__(self):

        self.history = []

    def record(
        self,
        step,
        result,
    ):

        entry = {
            "agent": step.agent,
            "objective": step.objective,
            "result": result,
        }

        self.history.append(entry)

        return entry

    def status(
        self,
    ):

        return self.history
