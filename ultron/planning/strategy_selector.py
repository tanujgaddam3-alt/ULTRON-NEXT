from ultron.planning.strategy import PlanningStrategy
from ultron.planning.strategy_memory import StrategyMemory


class StrategySelector:
    """
    Selects planning strategy.

    Uses historical performance
    when available.
    """

    def __init__(
        self,
        memory: StrategyMemory | None = None,
    ):

        self.memory = memory or StrategyMemory()

    def select(
        self,
        context=None,
    ) -> PlanningStrategy:

        if context:
            complexity = context.get("complexity")

            if complexity == "high":
                return self._best_strategy(
                    [
                        PlanningStrategy.HIERARCHICAL,
                        PlanningStrategy.SEQUENTIAL,
                    ]
                )

            if complexity == "dynamic":
                return self._best_strategy(
                    [
                        PlanningStrategy.REACTIVE,
                        PlanningStrategy.SEQUENTIAL,
                    ]
                )

        return self._best_strategy(list(PlanningStrategy))

    def _best_strategy(
        self,
        strategies,
    ) -> PlanningStrategy:

        return max(
            strategies,
            key=lambda strategy: self.memory.success_rate(strategy),
        )
