from __future__ import annotations

from dataclasses import dataclass

from .models import FinancialPlan, FinancialPlanStep


@dataclass(frozen=True)
class FinancialPlanningConfig:
    """Configuration for construction of a financial intelligence plan."""

    historical_period: str = "1y"
    timeframe: str = "1d"

    include_company_data: bool = True
    include_market_data: bool = True
    include_financial_analysis: bool = True
    include_historical_data: bool = True
    include_regime_analysis: bool = True
    include_risk_assessment: bool = True
    include_strategy: bool = True
    include_backtest: bool = True
    include_reasoning: bool = True
    include_decision: bool = True
    include_memory: bool = True
    include_evaluation: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.historical_period, str):
            raise TypeError("historical_period must be a string.")

        if not self.historical_period.strip():
            raise ValueError("Historical period is required.")

        if not isinstance(self.timeframe, str):
            raise TypeError("timeframe must be a string.")

        if not self.timeframe.strip():
            raise ValueError("Timeframe is required.")

        if not any(
            (
                self.include_company_data,
                self.include_market_data,
                self.include_financial_analysis,
                self.include_historical_data,
                self.include_regime_analysis,
                self.include_risk_assessment,
                self.include_strategy,
                self.include_backtest,
                self.include_reasoning,
                self.include_decision,
                self.include_memory,
                self.include_evaluation,
            )
        ):
            raise ValueError(
                "At least one planning capability must be enabled."
            )


class FinancialPlanner:
    """
    Builds deterministic dependency-aware financial intelligence plans.

    The planner describes WHAT must happen. The executor determines HOW
    each capability is performed.

    Pipeline:

        company
          + market
          + financial analysis
          + history
                ↓
             regime
                ↓
              risk
                ↓
            strategy
                ↓
             backtest
                ↓
            reasoning
                ↓
             decision
                ↓
             memory
                ↓
            evaluation
    """

    def __init__(
        self,
        config: FinancialPlanningConfig | None = None,
    ) -> None:
        self.config = config or FinancialPlanningConfig()

    def create_plan(
        self,
        objective: str,
        symbol: str,
        *,
        asset_class: str | None = None,
        run_backtest: bool | None = None,
        historical_period: str | None = None,
        timeframe: str | None = None,
    ) -> FinancialPlan:
        if not isinstance(objective, str):
            raise TypeError("Financial objective must be a string.")

        if not isinstance(symbol, str):
            raise TypeError("Financial symbol must be a string.")

        objective = objective.strip()
        symbol = symbol.strip().upper()

        if not objective:
            raise ValueError("Financial objective is required.")

        if not symbol:
            raise ValueError("Financial symbol is required.")

        period = (
            historical_period.strip()
            if historical_period is not None
            else self.config.historical_period
        )

        selected_timeframe = (
            timeframe.strip()
            if timeframe is not None
            else self.config.timeframe
        )

        if not period:
            raise ValueError("Historical period is required.")

        if not selected_timeframe:
            raise ValueError("Timeframe is required.")

        include_backtest = (
            self.config.include_backtest
            if run_backtest is None
            else bool(run_backtest)
        )

        steps: list[FinancialPlanStep] = []
        order_by_action: dict[str, int] = {}

        def add_step(
            action: str,
            parameters: dict[str, object],
            dependencies: list[str] | None = None,
        ) -> int:
            order = len(steps) + 1

            dependency_orders = [
                order_by_action[name]
                for name in dependencies or []
                if name in order_by_action
            ]

            steps.append(
                FinancialPlanStep(
                    order=order,
                    action=action,
                    parameters=parameters,
                    depends_on=dependency_orders,
                )
            )
            order_by_action[action] = order
            return order

        if self.config.include_company_data:
            add_step(
                "get_company_data",
                {"symbol": symbol},
            )

        if self.config.include_market_data:
            add_step(
                "get_market_data",
                {
                    "symbol": symbol,
                    "timeframe": selected_timeframe,
                },
            )

        if self.config.include_financial_analysis:
            add_step(
                "get_financial_analysis",
                {
                    "symbol": symbol,
                    "objective": objective,
                },
                dependencies=[
                    "get_company_data",
                    "get_market_data",
                ],
            )

        if self.config.include_historical_data:
            add_step(
                "get_historical_data",
                {
                    "symbol": symbol,
                    "period": period,
                    "timeframe": selected_timeframe,
                },
                dependencies=["get_market_data"],
            )

        if self.config.include_regime_analysis:
            add_step(
                "analyze_regime",
                {
                    "symbol": symbol,
                    "timeframe": selected_timeframe,
                },
                dependencies=["get_historical_data"],
            )

        if self.config.include_risk_assessment:
            add_step(
                "assess_risk",
                {
                    "symbol": symbol,
                    "asset_class": asset_class,
                    "objective": objective,
                },
                dependencies=[
                    "get_company_data",
                    "get_market_data",
                    "get_financial_analysis",
                    "analyze_regime",
                ],
            )

        if self.config.include_strategy:
            add_step(
                "generate_strategy",
                {
                    "symbol": symbol,
                    "objective": objective,
                    "asset_class": asset_class,
                    "timeframe": selected_timeframe,
                },
                dependencies=[
                    "analyze_regime",
                    "assess_risk",
                ],
            )

        if include_backtest:
            add_step(
                "backtest_strategy",
                {
                    "symbol": symbol,
                    "timeframe": selected_timeframe,
                    "historical_period": period,
                },
                dependencies=[
                    "get_historical_data",
                    "generate_strategy",
                ],
            )

        if self.config.include_reasoning:
            add_step(
                "reason",
                {
                    "symbol": symbol,
                    "objective": objective,
                },
                dependencies=[
                    "get_company_data",
                    "get_market_data",
                    "get_financial_analysis",
                    "analyze_regime",
                    "assess_risk",
                    "generate_strategy",
                    "backtest_strategy",
                ],
            )

        if self.config.include_decision:
            add_step(
                "make_decision",
                {
                    "symbol": symbol,
                    "objective": objective,
                },
                dependencies=[
                    "reason",
                    "analyze_regime",
                    "generate_strategy",
                ],
            )

        if self.config.include_memory:
            add_step(
                "persist_experience",
                {
                    "symbol": symbol,
                },
                dependencies=[
                    "get_financial_analysis",
                    "make_decision",
                ],
            )

        if self.config.include_evaluation:
            add_step(
                "evaluate_plan",
                {
                    "symbol": symbol,
                    "objective": objective,
                    "asset_class": asset_class,
                },
                dependencies=[
                    action
                    for action in (
                        "get_company_data",
                        "get_market_data",
                        "get_financial_analysis",
                        "analyze_regime",
                        "assess_risk",
                        "generate_strategy",
                        "backtest_strategy",
                        "reason",
                        "make_decision",
                        "persist_experience",
                    )
                    if action in order_by_action
                ],
            )

        if not steps:
            raise ValueError(
                "Financial planning configuration produced no steps."
            )

        return FinancialPlan(
            objective=objective,
            symbol=symbol,
            asset_class=asset_class,
            steps=steps,
        )