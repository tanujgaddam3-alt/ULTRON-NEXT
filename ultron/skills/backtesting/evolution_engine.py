class StrategyEvolutionEngine:
    """
    Evolves trading strategies through repeated historical testing.

    Each generation mutates the current best strategy, evaluates the
    candidates, and keeps the strongest candidate for the next generation.
    """

    def __init__(
        self,
        evolver,
        backtester,
        evaluator,
    ):
        self.evolver = evolver
        self.backtester = backtester
        self.evaluator = evaluator

    def evolve(
        self,
        strategy,
        history,
        max_generations=5,
        min_improvement=0.25,
    ):
        if max_generations < 1:
            raise ValueError(
                "max_generations must be at least 1."
            )

        current_best = strategy
        current_score = None

        evolution_history = []
        tested_names = set()

        for generation in range(1, max_generations + 1):

            candidates = self.evolver.mutate(
                current_best
            )

            # Prevent duplicate strategy evaluation.
            candidates = [
                candidate
                for candidate in candidates
                if candidate.name not in tested_names
            ]

            if not candidates:
                break

            results = []

            for candidate in candidates:

                performance = self.backtester.run(
                    history,
                    candidate,
                )

                results.append(
                    {
                        "strategy": candidate.name,
                        "performance": performance,
                    }
                )

                tested_names.add(candidate.name)

            ranked = self.evaluator.rank(results)

            if not ranked:
                break

            best_candidate = ranked[0]

            candidate_score = float(
                best_candidate.get("score", 0.0)
            )

            improvement = (
                candidate_score - current_score
                if current_score is not None
                else None
            )

            evolution_history.append(
                {
                    "generation": generation,
                    "parent": current_best.name,
                    "candidate": best_candidate["strategy"],
                    "score": candidate_score,
                    "improvement": improvement,
                    "candidates": ranked,
                }
            )

            # First generation always establishes a baseline.
            if current_score is None:
                current_best = self._find_strategy(
                    candidates,
                    best_candidate["strategy"],
                )
                current_score = candidate_score
                continue

            # Stop if evolution no longer produces a meaningful gain.
            if improvement < min_improvement:
                break

            current_best = self._find_strategy(
                candidates,
                best_candidate["strategy"],
            )

            current_score = candidate_score

        best_performance = None

        if evolution_history:
            best_entry = max(
                evolution_history,
                key=lambda item: item["score"],
            )

            for candidate in best_entry["candidates"]:
                if candidate["strategy"] == best_entry["candidate"]:
                    best_performance = candidate["performance"]
                    break

            evolved_best = {
                "strategy": best_entry["candidate"],
                "score": best_entry["score"],
                "performance": best_performance,
                "generation": best_entry["generation"],
            }

        else:
            evolved_best = None

        return {
            "success": True,
            "parent": strategy.name,
            "evolved_best": evolved_best,
            "generations": evolution_history,
            "generations_run": len(evolution_history),
            "tested_strategies": len(tested_names),
        }

    @staticmethod
    def _find_strategy(
        candidates,
        strategy_name,
    ):
        for candidate in candidates:
            if candidate.name == strategy_name:
                return candidate

        raise ValueError(
            f"Strategy '{strategy_name}' was not found."
        )
