from __future__ import annotations

from .models import FinancialPlan, FinancialPlanStep


class FinancialPlanValidator:
    """
    Validate financial plans before execution.

    Validation covers:
        - plan type and metadata
        - contiguous unique step ordering
        - valid actions
        - valid dependency references
        - dependency direction
        - duplicate dependencies
        - self-dependencies
        - dependency cycles
    """

    def validate(self, plan: FinancialPlan) -> bool:
        if not isinstance(plan, FinancialPlan):
            return False

        if not isinstance(plan.objective, str):
            return False

        if not plan.objective.strip():
            return False

        if not isinstance(plan.symbol, str):
            return False

        if not plan.symbol.strip():
            return False

        if plan.asset_class is not None:
            if not isinstance(plan.asset_class, str):
                return False

            if not plan.asset_class.strip():
                return False

        if not isinstance(plan.steps, list) or not plan.steps:
            return False

        if not all(
            isinstance(step, FinancialPlanStep)
            for step in plan.steps
        ):
            return False

        orders = [step.order for step in plan.steps]

        # Orders must be unique and contiguous.
        if len(set(orders)) != len(orders):
            return False

        if orders != list(range(1, len(orders) + 1)):
            return False

        by_order = {
            step.order: step
            for step in plan.steps
        }

        for step in plan.steps:
            if not isinstance(step.action, str):
                return False

            if not step.action.strip():
                return False

            if not isinstance(step.parameters, dict):
                return False

            if not isinstance(step.depends_on, list):
                return False

            # A dependency may only appear once.
            if len(step.depends_on) != len(
                set(step.depends_on)
            ):
                return False

            for dependency in step.depends_on:
                if not isinstance(dependency, int):
                    return False

                if dependency not in by_order:
                    return False

                # A step cannot depend on itself.
                if dependency == step.order:
                    return False

                # Planning is deterministic and dependency-driven.
                # Dependencies must already exist in the plan.
                if dependency >= step.order:
                    return False

        return not self._contains_cycle(by_order)

    @staticmethod
    def _contains_cycle(
        by_order: dict[int, FinancialPlanStep],
    ) -> bool:
        """
        Detect cycles using depth-first traversal.

        The normal validator already requires dependency < step.order,
        which makes cycles impossible in valid plans. Keeping explicit
        cycle detection here protects the validator if that invariant is
        relaxed in the future.
        """

        visiting: set[int] = set()
        visited: set[int] = set()

        def visit(order: int) -> bool:
            if order in visiting:
                return True

            if order in visited:
                return False

            visiting.add(order)

            step = by_order[order]

            for dependency in step.depends_on:
                if dependency not in by_order:
                    return True

                if visit(dependency):
                    return True

            visiting.remove(order)
            visited.add(order)

            return False

        return any(
            visit(order)
            for order in by_order
            if order not in visited
        )
