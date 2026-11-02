class SecureImprovementController:
    """
    Complete secure self-improvement pipeline.
    """

    def __init__(
        self,
        validator,
        deployment,
        guardian=None,
    ):

        self.validator = validator

        self.deployment = deployment

        self.guardian = guardian

    def improve(
        self,
        request,
        current_state,
    ):

        validation = self.validator.validate(request)

        if validation["decision"].value == "deny":
            return {
                "status": "blocked",
                "reason": validation["reason"],
            }

        if self.guardian:
            approval = self.guardian.approve(request)

            if not approval:
                return {
                    "status": "guardian_rejected",
                }

        deployment = self.deployment.deploy(
            request,
            current_state,
        )

        return deployment
