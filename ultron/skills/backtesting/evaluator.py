class StrategyEvaluator:
    """
    Ranks tested strategies while preserving experiment metadata.
    """

    def rank(
        self,
        results,
    ):
        ranked = []

        for item in results:

            performance = item["performance"]

            score = self.calculate_score(
                performance
            )

            ranked_item = {
                "strategy": item["strategy"],
                "score": score,
                "performance": performance,
            }

            # Preserve evolution lineage when available.
            if "parent" in item:
                ranked_item["parent"] = item["parent"]

            if "generation" in item:
                ranked_item["generation"] = item["generation"]

            ranked.append(ranked_item)

        ranked.sort(
            key=lambda x: x["score"],
            reverse=True,
        )

        return ranked

    def calculate_score(
        self,
        performance,
    ):
        score = 0

        # Reward return.
        score += performance.get(
            "return_percent",
            0
        )

        # Penalize drawdown.
        score -= (
            performance.get(
                "max_drawdown",
                0
            )
            * 0.5
        )

        # Reward risk-adjusted return.
        score += (
            performance.get(
                "sharpe_ratio",
                0
            )
            * 10
        )

        return score