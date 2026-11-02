from __future__ import annotations

from typing import Any


class MemoryAwareFinancialReasoning:
    """
    Production financial reasoning layer.

    Combines:
        - fundamental analysis
        - market context
        - market regime
        - risk analysis
        - strategy context
        - historical financial experience

    The reasoner does not make the final decision. It produces a
    structured evidence-based assessment for the decision engine.
    """

    def __init__(self, memory):
        self.memory = memory

    def analyze(
        self,
        company: str,
        current_analysis: dict[str, Any],
        market: dict[str, Any] | None = None,
        regime: dict[str, Any] | None = None,
        risk: dict[str, Any] | None = None,
        strategies: list[str] | None = None,
    ) -> dict[str, Any]:

        market = market if isinstance(market, dict) else {}
        regime = regime if isinstance(regime, dict) else {}
        risk = risk if isinstance(risk, dict) else {}
        strategies = strategies if isinstance(strategies, list) else []

        history = self.memory.recall_company(company)

        analysis = current_analysis.get("analysis", {})

        if not isinstance(analysis, dict):
            analysis = {}

        strengths = self._list_value(
            analysis.get("reasons")
        )

        risks = self._list_value(
            analysis.get("risks")
        )

        risk_data = risk.get("risk", risk)

        if not isinstance(risk_data, dict):
            risk_data = {}

        risk_warnings = self._list_value(
            risk_data.get("warnings")
        )

        for warning in risk_warnings:
            if warning not in risks:
                risks.append(warning)

        market_trend = market.get("trend")

        if market_trend == "bullish":
            strengths.append(
                "Positive technical market trend"
            )
        elif market_trend == "bearish":
            risks.append(
                "Negative technical market trend"
            )
        elif market_trend == "neutral":
            risks.append(
                "Neutral technical market trend"
            )

        regime_name = regime.get(
            "regime",
            "unknown",
        )

        regime_confidence = self._number(
            regime.get("confidence"),
            0.0,
        )

        if regime_name == "bull":
            strengths.append(
                "Broader market regime is bullish"
            )
        elif regime_name == "bear":
            risks.append(
                "Broader market regime is bearish"
            )
        elif regime_name == "high_volatility":
            risks.append(
                "Broader market is experiencing high volatility"
            )

        risk_level = risk_data.get(
            "risk_level",
            "unknown",
        )

        if risk_level == "high":
            risks.append(
                "Overall financial risk is high"
            )
        elif risk_level == "medium":
            risks.append(
                "Overall financial risk is moderate"
            )

        strengths = self._unique(strengths)
        risks = self._unique(risks)

        score = analysis.get("score", 0)

        confidence = self._confidence(
            score=score,
            history_count=len(history),
            regime_confidence=regime_confidence,
            risk_level=risk_level,
            strength_count=len(strengths),
            risk_count=len(risks),
        )

        historical_context = self._historical_context(
            history
        )

        summary = self._summary(
            strengths=strengths,
            risks=risks,
            risk_level=risk_level,
            regime_name=regime_name,
        )

        return {
            "company": company,
            "current": current_analysis,
            "strengths": strengths,
            "risks": risks,
            "risk_level": risk_level,
            "market_context": {
                "trend": market_trend,
                "volatility": market.get("volatility"),
            },
            "regime_context": {
                "regime": regime_name,
                "confidence": regime_confidence,
            },
            "historical_context": historical_context,
            "experience_count": len(history),
            "strategies": strategies,
            "summary": summary,
            "confidence": confidence,
        }

    @staticmethod
    def _list_value(value: Any) -> list[Any]:
        if not isinstance(value, list):
            return []

        return list(value)

    @staticmethod
    def _unique(values: list[Any]) -> list[Any]:
        result = []

        for value in values:
            if value not in result:
                result.append(value)

        return result

    @staticmethod
    def _number(
        value: Any,
        default: float = 0.0,
    ) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _confidence(
        score: Any,
        history_count: int,
        regime_confidence: float,
        risk_level: str,
        strength_count: int,
        risk_count: int,
    ) -> float:

        score_value = MemoryAwareFinancialReasoning._number(
            score
        )

        base = 0.40 + (
            score_value / 100.0
        ) * 0.35

        if history_count > 0:
            base += 0.05

        base += min(
            0.10,
            max(0.0, regime_confidence) * 0.10,
        )

        if risk_level == "high":
            base -= 0.15
        elif risk_level == "medium":
            base -= 0.07

        balance = strength_count - risk_count

        if balance < 0:
            base -= min(
                0.10,
                abs(balance) * 0.02,
            )
        elif balance > 0:
            base += min(
                0.05,
                balance * 0.01,
            )

        return round(
            max(0.0, min(0.95, base)),
            3,
        )

    @staticmethod
    def _historical_context(
        history: list[Any],
    ) -> list[str]:

        if not history:
            return [
                "No previous financial experience recorded."
            ]

        return [
            "Previous analysis exists for this company.",
            f"{len(history)} historical experience(s) available.",
        ]

    @staticmethod
    def _summary(
        strengths: list[Any],
        risks: list[Any],
        risk_level: str,
        regime_name: str,
    ) -> str:

        if risk_level == "high":
            return (
                "Fundamentally supported profile with "
                "material risk requiring caution."
            )

        if (
            len(strengths) > len(risks)
            and regime_name == "bull"
        ):
            return (
                "Positive financial profile supported by "
                "fundamentals and a favorable market regime."
            )

        if len(strengths) > len(risks):
            return (
                "Positive financial profile with "
                "identifiable risks."
            )

        if len(risks) > len(strengths):
            return (
                "Risk-dominated financial profile requiring caution."
            )

        return (
            "Mixed financial profile requiring further confirmation."
        )