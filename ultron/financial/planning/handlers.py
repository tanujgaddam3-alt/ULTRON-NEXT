from __future__ import annotations

from typing import Any, Callable

from .executor import FinancialExecutionContext, StepHandler
from .models import FinancialPlanStep


class FinancialPlanningHandlers:
    """
    Bridges FinancialPlan actions to ULTRON's existing financial
    intelligence components.

    Handlers communicate exclusively through FinancialExecutionContext,
    allowing the planner/executor to remain provider-agnostic.
    """

    def __init__(
        self,
        *,
        market_data: Any,
        analysis: Any,
        regime: Any,
        risk: Any,
        strategy_selector: Any,
        backtesting: Any,
        reasoning: Any,
        decision: Any,
        memory: Any,
    ) -> None:
        self.market_data = market_data
        self.analysis = analysis
        self.regime = regime
        self.risk = risk
        self.strategy_selector = strategy_selector
        self.backtesting = backtesting
        self.reasoning = reasoning
        self.decision = decision
        self.memory = memory

    def registry(self) -> dict[str, StepHandler]:
        return {
            "get_company_data": self.get_company_data,
            "get_market_data": self.get_market_data,
            "get_financial_analysis": self.get_financial_analysis,
            "get_historical_data": self.get_historical_data,
            "analyze_regime": self.analyze_regime,
            "assess_risk": self.assess_risk,
            "generate_strategy": self.generate_strategy,
            "backtest_strategy": self.backtest_strategy,
            "reason": self.reason,
            "make_decision": self.make_decision,
            "persist_experience": self.persist_experience,
            "evaluate_plan": self.evaluate_plan,
        }

    @staticmethod
    def _symbol(step: FinancialPlanStep) -> str:
        symbol = step.parameters.get("symbol")

        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("Financial step requires a valid symbol.")

        return symbol.strip().upper()

    @staticmethod
    def _require_success(
        result: Any,
        stage: str,
    ) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise TypeError(
                f"{stage} returned an invalid result."
            )

        if not result.get("success"):
            raise RuntimeError(
                f"{stage} failed: "
                f"{result.get('reason', result)}"
            )

        return result

    def get_company_data(
        self,
        step: FinancialPlanStep,
        context: FinancialExecutionContext,
    ) -> dict[str, Any]:
        result = self.market_data.execute(
            {
                "action": "company",
                "symbol": self._symbol(step),
            }
        )

        return self._require_success(result, "Company data")

    def get_market_data(
        self,
        step: FinancialPlanStep,
        context: FinancialExecutionContext,
    ) -> dict[str, Any]:
        result = self.market_data.execute(
            {
                "action": "analyze",
                "symbol": self._symbol(step),
                "period": step.parameters.get("period", "1y"),
            }
        )

        return self._require_success(result, "Market analysis")

    def get_financial_analysis(
        self,
        step: FinancialPlanStep,
        context: FinancialExecutionContext,
    ) -> dict[str, Any]:
        company = context.require("action:get_company_data")
        market = context.require("action:get_market_data")

        company_data = company.get("data")

        result = self.analysis.execute(
            {
                "action": "analyze",
                "symbol": self._symbol(step),
                "company": company_data,
                "market": market,
            }
        )

        return self._require_success(
            result,
            "Financial analysis",
        )

    def get_historical_data(
        self,
        step: FinancialPlanStep,
        context: FinancialExecutionContext,
    ) -> dict[str, Any]:
        result = self.market_data.execute(
            {
                "action": "history",
                "symbol": self._symbol(step),
                "period": step.parameters.get(
                    "period",
                    "1y",
                ),
            }
        )

        return self._require_success(
            result,
            "Historical data",
        )

    def analyze_regime(
        self,
        step: FinancialPlanStep,
        context: FinancialExecutionContext,
    ) -> dict[str, Any]:
        history_result = context.require("action:get_historical_data")
        history = history_result.get("data")

        if history is None:
            raise ValueError(
                "Historical data is required for regime analysis."
            )

        result = self.regime.analyze(history)

        if not isinstance(result, dict):
            raise TypeError(
                "Market regime analyzer returned an invalid result."
            )

        return result

    def assess_risk(
        self,
        step: FinancialPlanStep,
        context: FinancialExecutionContext,
    ) -> dict[str, Any]:
        company_result = context.require("action:get_company_data")
        market = context.require("action:get_market_data")
        company = company_result.get("data")

        result = self.risk.execute(
            {
                "action": "analyze",
                "company": company,
                "market": market,
            }
        )

        return self._require_success(
            result,
            "Risk analysis",
        )

    def generate_strategy(
        self,
        step: FinancialPlanStep,
        context: FinancialExecutionContext,
    ) -> list[str]:
        regime = context.require("action:analyze_regime")

        regime_name = regime.get(
            "regime",
            "unknown",
        )

        result = self.strategy_selector.select(
            regime_name
        )

        if result is None:
            return []

        if isinstance(result, str):
            return [result]

        if isinstance(result, (list, tuple)):
            return list(result)

        raise TypeError(
            "Strategy selector returned an invalid result."
        )

    def backtest_strategy(
        self,
        step: FinancialPlanStep,
        context: FinancialExecutionContext,
    ) -> Any:
        result = self.backtesting.execute(
            {
                "action": "backtest",
                "symbol": self._symbol(step),
        "historical_period": step.parameters.get(
                    "historical_period",
                    "1y",
                ),
            }
        )

        if not isinstance(result, dict):
            raise TypeError(
                "Backtesting component returned an invalid result."
            )

        return result

    def reason(
        self,
        step: FinancialPlanStep,
        context: FinancialExecutionContext,
    ) -> dict[str, Any]:
        company = context.require("action:get_company_data")
        financial_analysis = context.require("action:get_financial_analysis")
        market = context.require("action:get_market_data")
        regime = context.require("action:analyze_regime")
        risk = context.require("action:assess_risk")
        strategies = context.require("action:generate_strategy")

        company_data = company.get("data")

        result = self.reasoning.analyze(
            company=self._symbol(step),
            current_analysis=financial_analysis,
            market=market,
            regime=regime,
            risk=risk,
            strategies=strategies,
        )

        if not isinstance(result, dict):
            raise TypeError(
                "Financial reasoning returned an invalid result."
            )

        return result

    def make_decision(
        self,
        step: FinancialPlanStep,
        context: FinancialExecutionContext,
    ) -> dict[str, Any]:
        reasoning = context.require("action:reason")
        regime = context.require("action:analyze_regime")
        strategies = context.require("action:generate_strategy")

        result = self.decision.decide(
            reasoning,
            regime.get("regime", "unknown"),
            strategies,
        )

        if not isinstance(result, dict):
            raise TypeError(
                "Financial decision engine returned an invalid result."
            )

        return result

    def persist_experience(
        self,
        step: FinancialPlanStep,
        context: FinancialExecutionContext,
    ) -> dict[str, Any]:
        symbol = self._symbol(step)
        analysis = context.require("action:get_financial_analysis")
        decision = context.require("action:make_decision")

        self.memory.store.remember(
            company=symbol,
            analysis=analysis,
            decision=decision,
        )

        return {
            "success": True,
            "symbol": symbol,
            "persisted": True,
        }

    def evaluate_plan(
        self,
        step: FinancialPlanStep,
        context: FinancialExecutionContext,
    ) -> dict[str, Any]:
        decision = context.get("action:make_decision")
        reasoning = context.get("action:reason")
        risk = context.get("action:assess_risk")
        backtest = context.get("action:backtest_strategy")

        return {
            "success": True,
            "objective": step.parameters.get("objective"),
            "symbol": self._symbol(step),
            "decision": decision,
            "reasoning": reasoning,
            "risk": risk,
            "backtest": backtest,
        }


def build_financial_plan_handlers(
    *,
    market_data: Any,
    analysis: Any,
    regime: Any,
    risk: Any,
    strategy_selector: Any,
    backtesting: Any,
    reasoning: Any,
    decision: Any,
    memory: Any,
) -> dict[str, StepHandler]:
    """Build the production financial planning handler registry."""

    handlers = FinancialPlanningHandlers(
        market_data=market_data,
        analysis=analysis,
        regime=regime,
        risk=risk,
        strategy_selector=strategy_selector,
        backtesting=backtesting,
        reasoning=reasoning,
        decision=decision,
        memory=memory,
    )

    return handlers.registry()
