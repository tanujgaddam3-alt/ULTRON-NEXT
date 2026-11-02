class DeploymentController:
    """
    Controls safe improvement deployment.
    """

    def __init__(
        self,
        validator,
        sandbox,
        backup,
    ):

        self.validator = validator

        self.sandbox = sandbox

        self.backup = backup

    def deploy(
        self,
        request,
        current_state,
    ):

        validation = self.validator.validate(request)

        if validation["decision"].value == "deny":
            return {
                "status": "rejected",
                "reason": validation["reason"],
            }

        snapshot = self.backup.create(current_state)

        sandbox_result = self.sandbox.test()

        if sandbox_result["status"] != "passed":
            return {
                "status": "failed",
                "reason": "Sandbox failed",
            }

        return {
            "status": "approved",
            "backup": snapshot.id,
            "validation": validation,
        }
