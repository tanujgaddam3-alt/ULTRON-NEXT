"""Minimal, independent bootstrap for ULTRON Financial Mode.

This module deliberately does not import or instantiate
``ultron.infrastructure.container.Container``.  It composes only the
existing financial-domain services and defaults to the in-process paper
broker.  No language model, general cognitive runtime, voice, vision, or
autonomy service is started here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class FinancialRuntime:
    """The services needed for standalone financial analysis."""

    agent: Any
    market_data: Any
    analysis: Any
    regime: Any
    risk: Any
    strategy_selector: Any
    decision: Any
    memory: Any
    memory_store: Any
    paper_broker: Any
    trading_engine: Any
    trading_controller: Any
    backtesting: Any = None

    def close(self) -> None:
        """Close persistent resources owned by this runtime."""
        close = getattr(self.memory_store, "close", None)
        if callable(close):
            close()


def build_financial_runtime(
    *,
    memory_db: str | os.PathLike[str] | None = None,
    initial_capital: float = 100_000.0,
    enable_backtesting: bool = False,
) -> FinancialRuntime:
    """Build the financial subsystem without starting full ULTRON.

    ``memory_db`` defaults to ``data/financial/financial_memory.db`` under
    the current project directory. ``:memory:`` can be passed for an
    ephemeral SQLite store in tests. The broker is always the local paper
    broker; this bootstrap intentionally ignores live-broker configuration.
    """
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")

    # Imports are intentionally local: ``--help`` and basic CLI validation
    # do not load the financial dependency graph.
    from ultron.financial.agent.financial_agent import FinancialAgent
    from ultron.financial.decision.engine import FinancialDecisionEngine
    from ultron.financial.market.analyzer import MarketRegimeAnalyzer
    from ultron.financial.memory.manager import FinancialMemoryManager
    from ultron.financial.reasoning.memory_reasoner import (
        MemoryAwareFinancialReasoning,
    )
    from ultron.financial.storage.experience_store import FinancialExperienceStore
    from ultron.financial.strategies.selector import StrategySelector
    from ultron.financial.trading.controller import FinancialTradingController
    from ultron.financial.trading.market_data.service import MarketDataService
    from ultron.financial.trading.market_data.alpaca_adapter import AlpacaMarketAdapter
    from ultron.financial.trading.market_data.groww_adapter import GrowwMarketAdapter
    from ultron.financial.trading.market.adapters.yahoo import YahooMarketAdapter
    from ultron.financial.trading.engine import PaperTradingEngine
    from ultron.financial.trading.paper_broker import PaperBroker
    from ultron.skills.financial_analysis.skill import Financial_analysisSkill
    from ultron.skills.market_data.skill import Market_dataSkill
    from ultron.skills.risk_management.skill import Risk_managementSkill

    backtesting = None
    if enable_backtesting:
        from ultron.skills.backtesting.skill import BacktestingSkill
        backtesting = BacktestingSkill()

    if memory_db is None:
        memory_path = Path.cwd() / "data" / "financial" / "financial_memory.db"
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        memory_db = memory_path
    elif str(memory_db) != ":memory:":
        memory_path = Path(memory_db).expanduser().resolve()
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        memory_db = memory_path

    store = FinancialExperienceStore(path=str(memory_db))
    memory = FinancialMemoryManager(store)
    market_data = Market_dataSkill()
    analysis = Financial_analysisSkill(market=market_data)
    regime = MarketRegimeAnalyzer()
    risk = Risk_managementSkill()
    strategy_selector = StrategySelector()
    reasoning = MemoryAwareFinancialReasoning(memory)
    decision = FinancialDecisionEngine()

    # Standalone mode is paper-only regardless of ULTRON_EXECUTION_BACKEND.
    paper_broker = PaperBroker(initial_capital=float(initial_capital))
    trading_engine = PaperTradingEngine(broker=paper_broker)
    # Alpaca market data is opt-in; orders remain on the local PaperBroker.
    provider_choice = os.getenv("ULTRON_FINANCIAL_MARKET_PROVIDER", "yahoo").strip().lower()
    market_service = None
    if provider_choice == "alpaca":
        market_service = MarketDataService(AlpacaMarketAdapter())
    elif provider_choice == "groww":
        market_service = MarketDataService(GrowwMarketAdapter())
    elif provider_choice == "india_yahoo":
        market_service = MarketDataService(YahooMarketAdapter())
    elif provider_choice != "yahoo":
        raise ValueError(
            "ULTRON_FINANCIAL_MARKET_PROVIDER must be 'yahoo', 'india_yahoo', 'alpaca', or 'groww'."
        )

    trading_controller = FinancialTradingController(
        trading_engine=trading_engine,
        market_data=market_service,
    )

    agent = FinancialAgent(
        market_data=market_data,
        analysis=analysis,
        regime=regime,
        reasoning=reasoning,
        decision=decision,
        memory=memory,
        risk=risk,
        strategy_selector=strategy_selector,
        backtesting=backtesting,
        trading_controller=trading_controller,
    )

    return FinancialRuntime(
        agent=agent,
        market_data=market_data,
        analysis=analysis,
        regime=regime,
        risk=risk,
        strategy_selector=strategy_selector,
        decision=decision,
        memory=memory,
        memory_store=store,
        paper_broker=paper_broker,
        trading_engine=trading_engine,
        trading_controller=trading_controller,
        backtesting=backtesting,
    )
