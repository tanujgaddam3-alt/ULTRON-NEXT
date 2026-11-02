from ultron.planning.strategy import PlanningStrategy
from ultron.planning.strategy_store import StrategyStore


class StrategyMemory:
    """
    Stores and persists strategy performance.
    """

    def __init__(
        self,
        store=None,
    ):

        self.store = store or StrategyStore()

        self.history = self.store.load()

    def record(
        self,
        strategy: PlanningStrategy,
        success: bool,
    ):

        key = strategy.value

        if key not in self.history:
            self.history[key] = {
                "success": 0,
                "total": 0,
            }

        self.history[key]["total"] += 1

        if success:
            self.history[key]["success"] += 1

        self.store.save(self.history)

    def success_rate(
        self,
        strategy: PlanningStrategy,
    ) -> float:

        data = self.history.get(
            strategy.value,
            {
                "success": 0,
                "total": 0,
            },
        )

        if data["total"] == 0:
            return 0.0

        return data["success"] / data["total"]
