from ultron.decision.result import DecisionResult
from ultron.planning.optimizer import PlanOptimizer
from ultron.planning.planner import Planner
from ultron.planning.result import PlanningResult
from ultron.planning.strategy_selector import StrategySelector
from ultron.planning.validator import PlanValidator


class PlanningEngine:
    """
    Converts decisions into validated execution plans.
    """

    def __init__(
        self,
        strategy_memory=None,
    ):

        self.strategy_selector = StrategySelector(
            memory=strategy_memory,
        )

        self.planner = Planner()

        self.validator = PlanValidator()

        self.optimizer = PlanOptimizer()

    def plan(
        self,
        decision,
    ) -> PlanningResult:

        if isinstance(
            decision,
            DecisionResult,
        ):
            decision = decision.decision

        strategy = self.strategy_selector.select()

        plan = self.planner.create_plan(
            decision,
            strategy,
        )

        plan = self.optimizer.optimize(plan)

        valid = self.validator.validate(plan)

        return PlanningResult(
            plan=plan,
            valid=valid,
        )
