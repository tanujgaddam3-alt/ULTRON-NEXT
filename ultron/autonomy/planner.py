from __future__ import annotations

import re

from ultron.agents.prime.agent import PrimeAgent
from ultron.domain.plan import Plan, PlanStep


class AutonomousPlanner:
    """
    Converts autonomous goals into executable ULTRON plans.

    PrimeAgent determines the high-level workflow.
    This layer translates that workflow into concrete capabilities
    understood by the execution system.
    """

    SYMBOL_PATTERN = re.compile(
        r"\b[A-Z]{1,5}\b"
    )

    def __init__(
        self,
        prime_agent: PrimeAgent,
    ):
        self.prime = prime_agent

    def plan(
        self,
        goal,
    ) -> Plan:

        command = (
            f"Achieve objective: {goal.description}"
        )

        result = self.prime.process(command)

        if not isinstance(result, dict):
            raise TypeError(
                "PrimeAgent returned an invalid result."
            )

        intent = result.get("intent")
        workflow = result.get("workflow")

        description = goal.description.strip()

        symbol = self._extract_symbol(
            description
        )

        steps = []

        # Financial objectives become concrete financial actions.
        if symbol and self._is_financial_goal(
            description
        ):
            steps.append(
                PlanStep(
                    order=1,
                    action="analyze",
                    parameters={
                        "symbol": symbol,
                        "run_backtest": False,
                    },
                )
            )

        # Preserve a safe generic planning fallback.
        if not steps:
            steps.append(
                PlanStep(
                    order=1,
                    action="create_task",
                    parameters={
                        "goal": description,
                    },
                )
            )

        return Plan(
            goal=description,
            steps=steps,
        )

    @staticmethod
    def _extract_symbol(
        description: str,
    ) -> str | None:

        matches = re.findall(
            r"\b[A-Z]{1,5}\b",
            description,
        )

        ignored = {
            "A",
            "I",
            "AND",
            "THE",
            "FOR",
            "WITH",
            "PLAN",
        }

        for symbol in matches:
            if symbol not in ignored:
                return symbol

        return None

    @staticmethod
    def _is_financial_goal(
        description: str,
    ) -> bool:

        text = description.lower()

        financial_terms = (
            "market",
            "stock",
            "financial",
            "finance",
            "trading",
            "investment",
            "portfolio",
            "backtest",
            "company",
            "share",
            "price",
            "aapl",
            "nasdaq",
            "nyse",
        )

        return any(
            term in text
            for term in financial_terms
        )
