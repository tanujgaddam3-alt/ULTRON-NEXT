"""Production financial-analysis skill."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ultron.skills.base import Skill
from ultron.skills.market_data.skill import Market_dataSkill

from .analyzer import FinancialAnalyzer


class Financial_analysisSkill(Skill):
    """Execute fundamental and market financial analysis."""

    name = "financial_analysis"
    description = (
        "Combines company fundamentals with market analysis "
        "into financial intelligence."
    )
    version = "2.1"
    actions = ["analyze"]

    def __init__(
        self,
        analyzer: FinancialAnalyzer | None = None,
        market: Market_dataSkill | None = None,
    ) -> None:
        self.created = datetime.now()
        self.analyzer = analyzer or FinancialAnalyzer()
        self.market = market or Market_dataSkill()

    def validate(self, action: str, **kwargs: Any) -> bool:
        if not self.supports(action):
            return False

        symbol = kwargs.get("symbol")
        return isinstance(symbol, str) and bool(symbol.strip())

    @staticmethod
    def _unwrap_company(company: Any) -> dict[str, Any] | None:
        """
        Normalize company input.

        Accept both:
            {"success": True, "data": {...}}
        and:
            {...company fields...}

        This keeps callers from depending on the market-data
        skill's transport envelope.
        """
        if not isinstance(company, dict):
            return None

        data = company.get("data")

        if isinstance(data, dict):
            return data

        if "success" in company:
            return None

        return company

    @staticmethod
    def _validate_market(market: Any) -> dict[str, Any] | None:
        """
        Normalize and validate market-analysis input.
        """
        if not isinstance(market, dict):
            return None

        if market.get("success") is False:
            return None

        return market

    def execute(
        self,
        action: str | dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        if isinstance(action, dict):
            task = action
            action = task.get("action", "analyze")
            kwargs = {**task, **kwargs}

        if action != "analyze":
            raise ValueError(
                f"Unsupported financial analysis action: {action}"
            )

        symbol = kwargs.get("symbol")

        if not isinstance(symbol, str) or not symbol.strip():
            return {
                "success": False,
                "reason": "A valid market symbol is required.",
            }

        symbol = symbol.strip().upper()

        company = kwargs.get("company")
        market = kwargs.get("market")

        if company is None:
            company = self.market.execute(
                "company",
                symbol=symbol,
            )

        company_data = self._unwrap_company(company)

        if company_data is None:
            return {
                "success": False,
                "symbol": symbol,
                "reason": "Company data retrieval failed.",
            }

        if market is None:
            market = self.market.execute(
                "analyze",
                symbol=symbol,
            )

        market_data = self._validate_market(market)

        if market_data is None:
            return {
                "success": False,
                "symbol": symbol,
                "reason": "Market analysis failed.",
            }

        try:
            analysis = self.analyzer.analyze(
                company_data,
                market_data,
            )
        except (TypeError, ValueError, KeyError) as exc:
            return {
                "success": False,
                "symbol": symbol,
                "reason": f"Financial analysis failed: {exc}",
            }

        if not isinstance(analysis, dict):
            return {
                "success": False,
                "symbol": symbol,
                "reason": "Financial analyzer returned invalid data.",
            }

        return {
            "success": True,
            "symbol": symbol,
            "analysis": analysis,
            "company": company_data,
            "market": market_data,
        }
