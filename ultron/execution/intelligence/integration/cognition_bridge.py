class ExecutionCognitionBridge:
    """
    Connects execution results
    with cognition monitoring.
    """

    def __init__(
        self,
        feedback=None,
        cognition_agent=None,
    ):

        self.feedback = feedback

        self.cognition = cognition_agent

    def process_result(
        self,
        action: str,
        success: bool,
    ):

        record = None

        if self.feedback:
            record = self.feedback.record(
                action,
                success,
            )

        if self.cognition:
            self.cognition.observe(
                "execution",
                action,
                1.0 if success else 0.0,
            )

        return record
