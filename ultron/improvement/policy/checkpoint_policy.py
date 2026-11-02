class CheckpointPolicy:
    """
    Defines rules for improvement operations.
    """

    PROTECTED_AREAS = [
        "memory",
        "reasoning",
        "execution",
        "container",
        "infrastructure",
    ]

    def requires_checkpoint(
        self,
        target: str,
    ) -> bool:

        return True

    def requires_approval(
        self,
        target: str,
    ) -> bool:

        target = target.lower()

        return any(area in target for area in self.PROTECTED_AREAS)

    def validate(
        self,
        target: str,
    ) -> dict:

        return {
            "checkpoint_required": self.requires_checkpoint(target),
            "approval_required": self.requires_approval(target),
        }
