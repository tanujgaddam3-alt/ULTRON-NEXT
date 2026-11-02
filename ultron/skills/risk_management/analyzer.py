from __future__ import annotations

from typing import Any


class RiskAnalyzer:
    """
    Evaluates financial, valuation, leverage, and market risk.

    Risk score:
        0-29   = low
        30-59  = medium
        60+    = high
    """

    def analyze(
        self,
        company: dict[str, Any],
        market: dict[str, Any],
    ) -> dict[str, Any]:

        risk_score = 0.0
        warnings: list[str] = []

        volatility = float(
            market.get("volatility") or 0
        )

        debt = float(
            company.get("debt_to_equity") or 0
        )

        pe = float(
            company.get("pe_ratio") or 0
        )

        margin = float(
            company.get("profit_margin") or 0
        )

        growth = float(
            company.get("revenue_growth") or 0
        )

        trend = market.get("trend", "neutral")

        # -----------------------------
        # Volatility
        # -----------------------------

        if volatility > 0.04:
            risk_score += 35
            warnings.append("Very high price volatility")
        elif volatility > 0.03:
            risk_score += 25
            warnings.append("High price volatility")
        elif volatility > 0.02:
            risk_score += 15
            warnings.append("Elevated price volatility")
        else:
            risk_score += 5

        # -----------------------------
        # Leverage
        # -----------------------------

        if debt > 200:
            risk_score += 35
            warnings.append("Very high debt exposure")
        elif debt > 150:
            risk_score += 25
            warnings.append("High debt exposure")
        elif debt > 75:
            risk_score += 12
            warnings.append("Moderate debt exposure")

        # -----------------------------
        # Valuation
        # -----------------------------

        if pe > 60:
            risk_score += 30
            warnings.append("Extreme valuation risk")
        elif pe > 40:
            risk_score += 20
            warnings.append("High valuation risk")
        elif pe > 30:
            risk_score += 10
            warnings.append("Elevated valuation")

        # -----------------------------
        # Profitability
        # -----------------------------

        if margin <= 0:
            risk_score += 15
            warnings.append("Negative profitability")
        elif margin < 0.05:
            risk_score += 10
            warnings.append("Low profit margin")

        # -----------------------------
        # Growth
        # -----------------------------

        if growth < 0:
            risk_score += 15
            warnings.append("Negative revenue growth")

        # -----------------------------
        # Technical trend
        # -----------------------------

        if trend == "bearish":
            risk_score += 10
            warnings.append("Bearish technical trend")

        risk_score = round(
            min(100.0, risk_score),
            2,
        )

        return {
            "risk_score": risk_score,
            "risk_level": self.level(risk_score),
            "warnings": warnings,
            "components": {
                "volatility": volatility,
                "debt_to_equity": debt,
                "pe_ratio": pe,
                "profit_margin": margin,
                "revenue_growth": growth,
                "trend": trend,
            },
        }

    @staticmethod
    def level(score: float) -> str:
        if score >= 60:
            return "high"

        if score >= 30:
            return "medium"

        return "low"