from datetime import datetime

from ultron.skills.base import Skill
from ultron.skills.market_data.provider import MarketDataProvider

from .engine import BacktestEngine
from .strategy_generator import StrategyGenerator
from .evaluator import StrategyEvaluator
from .memory.strategy_memory import StrategyMemory
from .walk_forward import WalkForwardValidator
from .evolution import StrategyEvolution
from .evolution_engine import StrategyEvolutionEngine


class BacktestingSkill(Skill):
    """
    Coordinates historical testing, strategy evolution,
    and walk-forward validation.
    """

    name = "backtesting"
    description = "Backtests, evolves, evaluates, and walk-forward validates trading strategies."
    version = "2.0"
    actions = ["backtest"]

    def __init__(self, provider=None, engine=None, strategy_generator=None, evaluator=None, memory=None, validator=None):
        self.name = "backtesting"
        self.created = datetime.now()

        self.provider = provider or MarketDataProvider()
        self.engine = engine or BacktestEngine()
        self.strategy_generator = strategy_generator or StrategyGenerator()
        self.evaluator = evaluator or StrategyEvaluator()

        self.evolver = StrategyEvolution()

        self.evolution_engine = StrategyEvolutionEngine(
            evolver=self.evolver,
            backtester=self.engine,
            evaluator=self.evaluator,
        )

        self.memory = memory or StrategyMemory()
        self.validator = validator or WalkForwardValidator(self.engine)

    def validate(self, action, **kwargs):
        return self.supports(action) and isinstance(kwargs.get("symbol"), str) and bool(kwargs["symbol"].strip())

    def execute(self, action, **kwargs):
        if isinstance(action, dict):
            task = action
            action = task.get("action", "backtest")
            kwargs = {**task, **kwargs}
        if action != "backtest":
            raise ValueError(f"Unsupported backtesting action: {action}")
        symbol = kwargs.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("Backtesting requires a market symbol.")
        symbol = symbol.strip().upper()

        history = self.provider.get_history(
            symbol,
            period="5y",
        )

        strategies = self.strategy_generator.generate()

        # ---------------------------------------------------------
        # 1. Initial strategy testing
        # ---------------------------------------------------------

        results = []

        for strategy in strategies:
            performance = self.engine.run(
                history,
                strategy,
            )

            results.append(
                {
                    "strategy": strategy.name,
                    "performance": performance,
                }
            )

        ranking = self.evaluator.rank(results)

        # ---------------------------------------------------------
        # 2. Strategy evolution
        # ---------------------------------------------------------

        evolution = []
        evolved_best = None

        if ranking:
            parent_name = ranking[0]["strategy"]

            parent_strategy = next(
                (
                    strategy
                    for strategy in strategies
                    if strategy.name == parent_name
                ),
                None,
            )

            if parent_strategy is not None:
                evolution_result = self.evolution_engine.evolve(
                    parent_strategy,
                    history,
                    max_generations=5,
                    min_improvement=0.25,
                )

                evolution = evolution_result.get(
                    "evolution",
                    [],
                )

                evolved_best = evolution_result.get(
                    "evolved_best",
                )

        # ---------------------------------------------------------
        # 3. Walk-forward validation
        # ---------------------------------------------------------

        validation = None

        if evolved_best is not None:
            evolved_strategy_name = evolved_best.get(
                "strategy"
            )

            evolved_strategy = None

            if evolved_strategy_name:
                evolved_strategy = next(
                    (
                        candidate
                        for candidate in self._strategy_candidates(
                            strategies,
                            parent_strategy,
                        )
                        if candidate.name == evolved_strategy_name
                    ),
                    None,
                )

            if evolved_strategy is None:
                evolved_strategy = self._strategy_from_name(
                    evolved_strategy_name
                )

            if evolved_strategy is not None:
                validation = self.validator.validate(
                    history,
                    evolved_strategy,
                )

        # ---------------------------------------------------------
        # 4. Persist initial strategy experience
        # ---------------------------------------------------------

        for item in ranking:
            self.memory.remember(
                symbol,
                item["strategy"],
                item["performance"],
            )

        return {
            "success": True,
            "symbol": symbol,
            "strategies": ranking,
            "best": ranking[0] if ranking else None,
            "evolution": evolution,
            "evolved_best": evolved_best,
            "validation": validation,
            "memory_size": len(
                self.memory.history()
            ),
        }

    @staticmethod
    def _strategy_candidates(
        strategies,
        parent_strategy,
    ):
        """
        Return the known parent plus its evolved descendants.
        """

        candidates = list(strategies)

        if parent_strategy is not None:
            candidates.append(parent_strategy)

            # Evolution candidates are deterministic and generated
            # from the same mutation rules used by the engine.
            evolver = StrategyEvolution()

            for candidate in evolver.mutate(parent_strategy):
                candidates.append(candidate)

        return candidates

    @staticmethod
    def _strategy_from_name(name):
        """
        Reconstruct an SMA strategy from names such as SMA_13_65.
        """

        if not isinstance(name, str):
            return None

        if not name.startswith("SMA_"):
            return None

        parts = name.split("_")

        if len(parts) != 3:
            return None

        try:
            short_window = int(parts[1])
            long_window = int(parts[2])
        except ValueError:
            return None

        from .models import TradingStrategy

        try:
            return TradingStrategy(
                name=name,
                short_window=short_window,
                long_window=long_window,
            )
        except (TypeError, ValueError):
            return None
