class WorkflowRecovery:
    """
    Handles failed workflow steps.
    """

    def recover(
        self,
        step,
        error,
    ):

        return {
            "step": step.agent,
            "action": "retry",
            "reason": str(error),
        }
