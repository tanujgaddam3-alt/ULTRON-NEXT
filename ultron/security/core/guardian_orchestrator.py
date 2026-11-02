class GuardianOrchestrator:
    """
    Coordinates complete ULTRON security operations.
    """

    def __init__(
        self,
        detector=None,
        response=None,
        diagnostics=None,
    ):

        self.detector = detector

        self.response = response

        self.diagnostics = diagnostics

    def process(
        self,
        event,
    ):

        result = {
            "event": event,
        }

        if self.detector:
            result["analysis"] = self.detector.detect(event)

        if self.response:
            result["response"] = self.response.respond(
                event,
                "HIGH",
            )

        return result

    def status(
        self,
    ):

        if self.diagnostics:
            return self.diagnostics.run()

        return {"status": "unknown"}
