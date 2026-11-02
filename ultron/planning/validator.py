from ultron.domain.plan import Plan


class PlanValidator:
    """
    Validates that a plan can be executed.
    """

    def validate(self, plan: Plan) -> bool:
        if not plan.steps:
            return False

        for step in plan.steps:
            if not step.action.strip():
                return False

        return True
