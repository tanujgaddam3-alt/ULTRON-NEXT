class SecurityBootManager:
    """
    Controls ULTRON security startup.
    """

    def __init__(
        self,
        initializer=None,
        validator=None,
    ):

        self.initializer = initializer

        self.validator = validator

    def boot(
        self,
    ):

        result = {
            "status": "starting",
        }

        if self.initializer:
            result["initialization"] = self.initializer.initialize()

        if self.validator:
            result["validation"] = self.validator.validate()

        result["status"] = "ready"

        return result
