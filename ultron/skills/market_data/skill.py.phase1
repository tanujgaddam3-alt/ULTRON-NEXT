"""Production market-data skill."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ultron.skills.base import Skill

from .analyzer import MarketAnalyzer
from .company_provider import CompanyDataProvider
from .provider import MarketDataProvider


class Market_dataSkill(Skill):
    """Expose market data through ULTRON's common :class:`Skill` contract.

    The dictionary-task form remains supported for existing financial-agent
    callers while the action/kwargs form is the canonical manager interface.
    """

    name = "market_data"
    description = "Retrieves prices, historical data, market analysis, and company data."
    version = "2.0"
    actions = ["price", "history", "analyze", "company"]

    def __init__(
        self,
        provider: MarketDataProvider | None = None,
        analyzer: MarketAnalyzer | None = None,
        company_provider: CompanyDataProvider | None = None,
    ) -> None:
        self.created = datetime.now()
        self.provider = provider or MarketDataProvider()
        self.analyzer = analyzer or MarketAnalyzer()
        self.company_provider = company_provider or CompanyDataProvider()

    def validate(self, action: str, **kwargs: Any) -> bool:
        if not self.supports(action):
            return False
        symbol = kwargs.get("symbol")
        return isinstance(symbol, str) and bool(symbol.strip())

    def execute(self, action: str | dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        if isinstance(action, dict):
            task = action
            action = task.get("action")
            kwargs = {**task, **kwargs}

        if not isinstance(action, str) or not action:
            return {"success": False, "reason": "Unknown market action"}

        symbol = kwargs.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            return {"success": False, "reason": "A valid market symbol is required."}
        symbol = symbol.strip().upper()

        if action == "price":
            result = self.provider.get_price(symbol)
            return {
                "success": True,
                "symbol": result.symbol,
                "price": result.price,
                "timestamp": result.timestamp.isoformat(),
            }

        if action == "history":
            history = self.provider.get_history(symbol, period=kwargs.get("period", "1y"))
            return {"success": True, "symbol": symbol, "data": history.to_dict()}

        if action == "analyze":
            history = self.provider.get_history(symbol, period=kwargs.get("period", "1y"))
            result = self.analyzer.analyze(history)
            return result if isinstance(result, dict) else {"success": True, "symbol": symbol, "data": result}

        if action == "company":
            company = self.company_provider.get_company(symbol)
            return {"success": True, "symbol": symbol, "data": company.__dict__}

        return {"success": False, "reason": "Unknown market action"}
