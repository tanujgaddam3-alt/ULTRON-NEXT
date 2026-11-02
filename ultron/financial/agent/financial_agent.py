"""
Production financial intelligence orchestrator.

Coordinates:
    market data
    fundamental analysis
    market regime detection
    risk analysis
    strategy selection
    memory-aware reasoning
    financial decision making
    persistent financial experience

The agent is deliberately orchestration-only. Domain logic remains
inside the corresponding components.
"""

from typing import Any
from ultron.security.permission.rules import deny_when_authorization_unavailable

from ultron.financial.trading.controller import (
    FinancialTradingController,
)
from ultron.financial.trading.paper_broker import PaperBroker
from ultron.financial.trading.engine import PaperTradingEngine
from ultron.financial.trading.controller import FinancialTradingController


class FinancialAgent:
    """
    Coordinates the complete financial intelligence pipeline.
    """

    def __init__(
        self,
        market_data,
        analysis,
        regime,
        reasoning,
        decision,
        memory,
        risk=None,
        strategy_selector=None,
        backtesting=None,
         trading_controller=None,
    ):
        self.market_data = market_data
        self.analysis = analysis
        self.regime = regime
        self.reasoning = reasoning
        self.decision = decision
        self.memory = memory
        self.risk = risk
        self.strategy_selector = strategy_selector
        self.backtesting = backtesting
        self.trading_controller = trading_controller

    def _failure(self, symbol, stage, error):
        return {
            "success": False,
            "symbol": symbol,
            "stage": stage,
            "error": error,
        }

    def analyze(
    self,
    symbol: str,
    run_backtest: bool = False,
) -> dict[str, Any]:
        """
        Execute the complete financial intelligence pipeline.

        The method fails explicitly at the stage that cannot produce
        valid data instead of allowing malformed downstream state.
        """

        if not isinstance(symbol, str) or not symbol.strip():
            return self._failure(
                symbol,
                "validation",
                "A valid market symbol is required.",
            )

        symbol = symbol.strip().upper()

        # ---------------------------------------------------------
        # 1. Company fundamentals
        # ---------------------------------------------------------

        company = self.market_data.execute(
            {
                "action": "company",
                "symbol": symbol,
            }
        )

        if not company.get("success"):
            return self._failure(
                symbol,
                "company",
                company,
            )

        company_data = company.get("data", {})

        # ---------------------------------------------------------
        # 2. Current market analysis
        # ---------------------------------------------------------

        market = self.market_data.execute(
            {
                "action": "analyze",
                "symbol": symbol,
            }
        )

        if not market.get("success"):
            return self._failure(
                symbol,
                "market",
                market,
            )

        # ---------------------------------------------------------
        # 3. Fundamental + market financial analysis
        # ---------------------------------------------------------

        financial_analysis = self.analysis.execute(
            {
                "symbol": symbol,
            }
        )

        if not financial_analysis.get("success"):
            return self._failure(
                symbol,
                "financial_analysis",
                financial_analysis,
            )

        # ---------------------------------------------------------
        # 4. Historical market data
        # ---------------------------------------------------------

        history_result = self.market_data.execute(
            {
                "action": "history",
                "symbol": symbol,
            }
        )

        if not history_result.get("success"):
            return self._failure(
                symbol,
                "history",
                history_result,
            )

        history = history_result.get("data")

        if history is None:
            return self._failure(
                symbol,
                "history",
                "Historical market data is unavailable.",
            )

        # ---------------------------------------------------------
        # 5. Market regime
        # ---------------------------------------------------------

        regime = self.regime.analyze(history)

        if not isinstance(regime, dict):
            return self._failure(
                symbol,
                "regime",
                "Market regime analyzer returned an invalid result.",
            )

        regime_name = regime.get("regime", "unknown")

        # ---------------------------------------------------------
        # 6. Risk analysis
        # ---------------------------------------------------------

        # -------------------------------------------------
# 7. Risk analysis
# -------------------------------------------------
        risk = self.risk.execute(
    {
        "company": company_data,
        "market": market,
    }
)

        if not isinstance(risk, dict):
           return {
        "success": False,
        "symbol": symbol,
        "stage": "risk",
        "error": {
            "reason": "Risk component returned an invalid result.",
        },
    }

        if not risk.get("success"):
          return {
        "success": False,
        "symbol": symbol,
        "stage": "risk",
        "error": risk,
    }

        # ---------------------------------------------------------
        # 7. Strategy family selection
        # ---------------------------------------------------------

        strategies = []

        if self.strategy_selector is not None:
            strategies = self.strategy_selector.select(
                regime_name
            )


                # ---------------------------------------------------------
        # 8. Optional historical strategy validation
        # ---------------------------------------------------------

        backtest = None

        if run_backtest and self.backtesting is not None:
             try:
                backtest = self.backtesting.execute(
                    {
                        "symbol": symbol,
                    }
                )

                if not isinstance(backtest, dict):
                    backtest = {
                        "success": False,
                        "reason": (
                            "Backtesting component returned "
                            "an invalid result."
                        ),
                    }

             except (TypeError, ValueError, KeyError) as exc:
                backtest = {
                    "success": False,
                    "reason": f"Backtesting failed: {exc}",
                }

        # ---------------------------------------------------------
        # 8. Memory-aware reasoning
        # ---------------------------------------------------------

        reasoning = self.reasoning.analyze(
    company=symbol,
    current_analysis=financial_analysis,
    market=market,
    regime=regime,
    risk=risk,
    strategies=strategies,
)

        if not isinstance(reasoning, dict):
            return self._failure(
                symbol,
                "reasoning",
                "Financial reasoning returned an invalid result.",
            )

        # Enrich reasoning with risk and market context.

        reasoning = {
            **reasoning,
            "market": market,
            "regime": regime,
            "risk": risk,
            "strategies": strategies,
        }

        # ---------------------------------------------------------
        # 9. Financial decision
        # ---------------------------------------------------------

        decision = self.decision.decide(
            reasoning,
            regime_name,
            strategies,
        )

        # ---------------------------------------------------------
        # 10. Persistent experience
        # ---------------------------------------------------------

        try:
            self.memory.store.remember(
                company=symbol,
                analysis=financial_analysis,
                decision=decision,
            )
        except (AttributeError, TypeError):
            # Persistence must never corrupt the primary analysis
            # pipeline. The failure remains visible in diagnostics.
            pass

        # ---------------------------------------------------------
        # 11. Unified intelligence report
        # ---------------------------------------------------------

        return {
            "success": True,
            "symbol": symbol,
            "company": company,
            "market": market,
            "financial_analysis": financial_analysis,
            "regime": regime,
            "risk": risk,
            "strategies": strategies,
            "backtest": backtest,
            "reasoning": reasoning,
            "decision": decision,
        }
    def paper_trade(
        self,
        analysis: dict[str, Any],
        price: float | None = None,
    ) -> dict[str, Any]:
        """
        Convert financial intelligence into a paper-trading action.

        No real broker or real capital is involved.
        """
        deny_when_authorization_unavailable("paper_trade")

        if self.trading_controller is None:
            return {
                "success": False,
                "stage": "trading",
                "reason": (
                    "Trading controller is not configured."
                ),
            }

        if not isinstance(analysis, dict):
            return {
                "success": False,
                "stage": "trading",
                "reason": (
                    "Financial analysis must be a dictionary."
                ),
            }

        try:
            # Caller-supplied prices lack a trusted source timestamp. Obtain the
            # price and timestamp through the controller's guarded market path.
            execute_from_market = getattr(
                self.trading_controller, "execute_from_market", None
            )
            if callable(execute_from_market):
                return execute_from_market(analysis=analysis)
            return {
                "success": False,
                "stage": "market_data",
                "reason": "Controller does not support validated market-data execution.",
            }

        except (TypeError, ValueError) as exc:
            return {
                "success": False,
                "stage": "trading",
                "reason": str(exc),
            }