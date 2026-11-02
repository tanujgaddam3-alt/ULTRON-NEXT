class FailureListener:
    """
    Converts system failures into
    improvement requests.
    """

    def __init__(
        self,
        improvement_service,
    ):

        self.improvement = improvement_service

    def handle(
        self,
        event,
    ):

        return self.improvement.analyze_failure(
            component=event.component,
            error=event.error,
        )
