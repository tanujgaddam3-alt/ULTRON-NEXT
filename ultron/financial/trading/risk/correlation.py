from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Mapping


@dataclass(frozen=True)
class CorrelationLimits:
    max_correlated_exposure_percent: float = 40.0


class CorrelationEngine:
    """
    Measures effective exposure created by positions that
    are correlated with a proposed position.

    Correlation values must be between -1.0 and 1.0.
    Positive correlation increases effective exposure.
    Negative correlation reduces it.
    """

    def __init__(
        self,
        limits: CorrelationLimits | None = None,
    ) -> None:
        self.limits = limits or CorrelationLimits()

        if (
            not isfinite(self.limits.max_correlated_exposure_percent)
            or not 0 < self.limits.max_correlated_exposure_percent <= 100
        ):
            raise ValueError(
                "max_correlated_exposure_percent must be between 0 and 100."
            )

    @staticmethod
    def validate_matrix(
        correlations: Mapping[str, Mapping[str, float]],
    ) -> None:
        for symbol, related in correlations.items():
            for other, value in related.items():
                correlation = float(value)

                if not isfinite(correlation) or not -1.0 <= correlation <= 1.0:
                    raise ValueError(
                        f"Correlation for {symbol}/{other} must be between -1 and 1."
                    )

    def calculate(
        self,
        equity: float,
        proposed_symbol: str,
        proposed_value: float,
        positions: Mapping[str, float],
        correlations: Mapping[str, Mapping[str, float]],
    ) -> dict[str, object]:

        if not isfinite(equity) or equity <= 0:
            return {
                "approved": False,
                "reason": "Equity must be positive.",
            }

        if not isfinite(proposed_value) or proposed_value < 0:
            return {
                "approved": False,
                "reason": "Proposed value cannot be negative.",
            }

        proposed_symbol = proposed_symbol.strip().upper()

        self.validate_matrix(correlations)

        correlated_exposure = float(proposed_value)

        contributions: list[dict[str, float | str]] = []

        for symbol, position_value in positions.items():
            symbol = symbol.strip().upper()
            position_value = float(position_value)

            if not isfinite(position_value):
                return {
                    "approved": False,
                    "reason": f"Position exposure for {symbol} must be finite.",
                }

            if position_value <= 0:
                continue

            correlation = float(
                correlations
                .get(proposed_symbol, {})
                .get(
                    symbol,
                    correlations
                    .get(symbol, {})
                    .get(proposed_symbol, 0.0),
                )
            )

            contribution = position_value * max(
                correlation,
                0.0,
            )

            correlated_exposure += contribution

            if correlation != 0:
                contributions.append(
                    {
                        "symbol": symbol,
                        "correlation": correlation,
                        "position_value": position_value,
                        "contribution": contribution,
                    }
                )

        correlated_percent = (
            correlated_exposure / equity
        ) * 100.0

        approved = (
            correlated_percent
            <= self.limits.max_correlated_exposure_percent
        )

        return {
            "approved": approved,
            "reason": (
                "Correlated exposure limit passed."
                if approved
                else "Maximum correlated exposure exceeded."
            ),
            "equity": equity,
            "proposed_symbol": proposed_symbol,
            "proposed_value": proposed_value,
            "correlated_exposure": correlated_exposure,
            "correlated_exposure_percent": correlated_percent,
            "max_correlated_exposure_percent": (
                self.limits.max_correlated_exposure_percent
            ),
            "contributions": contributions,
        }
