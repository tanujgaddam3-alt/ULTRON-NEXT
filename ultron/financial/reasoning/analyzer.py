from __future__ import annotations

from typing import Any


class FinancialReasoningEngine:
    """
    Converts financial, market, regime, and risk signals into
    structured financial reasoning.

    This component does not execute trades. It evaluates evidence,
    identifies strengths and risks, and produces an explainable
    confidence score.
    """

    def analyze(
        self,
        company: dict[str, Any],
        market: dict[str, Any],
        regime: dict[str, Any] | Any,
        risk: dict[str, Any] | None = None,
    ) -> dict[str, Any]:

        reasons: list[str] = []
        risks: list[str] = []

        margin = float(company.get("profit_margin") or 0)
        growth = float(company.get("revenue_growth") or 0)
        debt = float(company.get("debt_to_equity") or 0)
        pe = float(company.get("pe_ratio") or 0)

        trend = market.get("trend", "neutral")
        volatility = float(market.get("volatility") or 0)

        regime_name = self._regime_name(regime)

        # -----------------------------
        # Fundamental analysis
        # -----------------------------

        if margin > 0.20:
            reasons.append("Strong profitability")
        elif margin > 0:
            reasons.append("Positive profitability")
        else:
            risks.append("Weak or negative profitability")

        if growth > 0.05:
            reasons.append("Strong revenue growth")
        elif growth > 0:
            reasons.append("Positive revenue growth")
        else:
            risks.append("Negative revenue growth")

        if debt < 75:
            reasons.append("Low debt exposure")
        elif debt <= 150:
            reasons.append("Moderate debt exposure")
        else:
            risks.append("High debt exposure")

        if pe > 50:
            risks.append("Very high valuation")
        elif pe > 40:
            risks.append("High valuation")

        # -----------------------------
        # Technical / market analysis
        # -----------------------------

        if trend == "bullish":
            reasons.append("Positive technical trend")
        elif trend == "bearish":
            risks.append("Negative technical trend")
        else:
            reasons.append("Neutral technical trend")

        if volatility > 0.03:
            risks.append("High market volatility")
        elif volatility > 0.02:
            risks.append("Elevated market volatility")

        # -----------------------------
        # Regime
        # -----------------------------

        if regime_name == "bull":
            reasons.append("Market regime is bullish")
        elif regime_name == "bear":
            risks.append("Market regime is bearish")
        elif regime_name == "high_volatility":
            risks.append("Market regime indicates high volatility")

        # -----------------------------
        # Risk engine
        # -----------------------------

        if risk:
            risk_level = risk.get("risk_level")

            if risk_level == "high":
                risks.append("Overall financial risk is high")
            elif risk_level == "medium":
                risks.append("Overall financial risk is moderate")

        confidence = self._confidence(
            reasons=reasons,
            risks=risks,
            risk=risk,
        )

        return {
            "summary": self._summary(reasons, risks),
            "strengths": reasons,
            "risks": risks,
            "confidence": confidence,
            "regime": regime_name,
        }

    @staticmethod
    def _regime_name(regime: dict[str, Any] | Any) -> str:
        if isinstance(regime, dict):
            return str(regime.get("regime", "unknown"))

        return str(
            getattr(regime, "name", regime)
        )

    @staticmethod
    def _summary(
        reasons: list[str],
        risks: list[str],
    ) -> str:

        if len(reasons) >= len(risks) + 2:
            return "Strong profile with favorable supporting evidence."

        if len(reasons) > len(risks):
            return "Positive profile with manageable risks."

        if len(risks) > len(reasons):
            return "Risk-heavy profile requiring caution."

        return "Mixed profile requiring balanced evaluation."

    @staticmethod
    def _confidence(
        reasons: list[str],
        risks: list[str],
        risk: dict[str, Any] | None,
    ) -> float:

        score = 0.50

        score += min(len(reasons), 6) * 0.06
        score -= min(len(risks), 6) * 0.06

        if risk:
            risk_level = risk.get("risk_level")

            if risk_level == "high":
                score -= 0.10
            elif risk_level == "low":
                score += 0.05

        return round(
            max(0.0, min(1.0, score)),
            3,
        )