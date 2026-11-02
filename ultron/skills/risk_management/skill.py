"""Production risk-management skill."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ultron.skills.base import Skill

from .analyzer import RiskAnalyzer


class Risk_managementSkill(Skill):
    """Expose financial risk analysis through the common Skill contract."""

    name = "risk_management"
    description = "Evaluates financial, valuation, leverage, and market risk."
    version = "2.0"
    actions = ["analyze"]

    def __init__(self, analyzer: RiskAnalyzer | None = None) -> None:
        self.created = datetime.now()
        self.analyzer = analyzer or RiskAnalyzer()

    def validate(self, action: str, **kwargs: Any) -> bool:
        return self.supports(action) and isinstance(kwargs.get("company"), dict) and isinstance(kwargs.get("market"), dict)

    def execute(self, action: str | dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        if isinstance(action, dict):
            task = action
            action = task.get("action", "analyze")
            kwargs = {**task, **kwargs}

        if action != "analyze":
            raise ValueError(f"Unsupported risk-management action: {action}")

        company = kwargs.get("company")
        market = kwargs.get("market")
        if not isinstance(company, dict):
            return {"success": False, "reason": "Company data is required."}
        if not isinstance(market, dict):
            return {"success": False, "reason": "Market data is required."}

        try:
            result = self.analyzer.analyze(company=company, market=market)
        except (TypeError, ValueError, KeyError) as exc:
            return {"success": False, "reason": f"Risk analysis failed: {exc}"}
        if not isinstance(result, dict):
            return {"success": False, "reason": "Risk analyzer returned an invalid result."}
        return {"success": True, "risk": result}
