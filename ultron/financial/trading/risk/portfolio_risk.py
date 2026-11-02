from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping

from .correlation import (
    CorrelationEngine,
    CorrelationLimits,
)


@dataclass
class PortfolioRiskLimits:
    """
    Portfolio-level risk limits.

    Percentages are expressed as percentages of portfolio equity.
    """

    max_total_exposure_percent: float = 60.0
    max_single_position_percent: float = 25.0
    max_currency_exposure_percent: float = 50.0
    max_correlated_exposure_percent: float = 40.0

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if not isfinite(value) or not 0 < value <= 100:
                raise ValueError(
                    f"{name} must be between 0 and 100."
                )


class PortfolioRiskEngine:
    """
    Evaluates proposed trades against portfolio-level risk limits.

    Checks:

    1. Total portfolio exposure
    2. Single-position exposure
    3. Currency exposure
    4. Correlated exposure

    This layer does not execute orders.
    It only decides whether additional exposure is acceptable.
    """

    def __init__(
        self,
        limits: PortfolioRiskLimits | None = None,
    ) -> None:

        self.limits = limits or PortfolioRiskLimits()

        self.correlation_engine = CorrelationEngine(
            CorrelationLimits(
                max_correlated_exposure_percent=(
                    self.limits.max_correlated_exposure_percent
                )
            )
        )

    def evaluate(
        self,
        equity: float,
        positions: Mapping[str, Any],
        proposed_symbol: str,
        proposed_value: float,
        proposed_currency: str = "USD",
        correlations: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:

        if not isfinite(equity) or equity <= 0:
            return self._reject(
                "Portfolio equity must be positive."
            )

        if not isfinite(proposed_value) or proposed_value < 0:
            return self._reject(
                "Proposed exposure cannot be negative."
            )

        symbol = proposed_symbol.strip().upper()
        currency = proposed_currency.strip().upper()

        current_exposure = 0.0
        normalized_positions: dict[str, float] = {}
        position_currencies: dict[str, str] = {}

        # ---------------------------------------------------------
        # Normalize positions
        # ---------------------------------------------------------

        for existing_symbol, position in positions.items():

            normalized_symbol = (
                str(existing_symbol).strip().upper()
            )

            # Support:
            # {"AAPL": {"quantity": 20, "price": 100}}
            # and:
            # {"AAPL": 2000}
            if isinstance(position, Mapping):

                quantity = float(
                    position.get("quantity", 0.0)
                )

                price = float(
                    position.get("price", 0.0)
                )

                if not isfinite(quantity) or not isfinite(price):
                    return self._reject(
                        f"Position exposure for {normalized_symbol} must be finite."
                    )

                if quantity <= 0 or price <= 0:
                    continue

                value = quantity * price

                position_currency = str(
                    position.get("currency", "USD")
                ).strip().upper()

            else:

                value = float(position)

                if not isfinite(value):
                    return self._reject(
                        f"Position exposure for {normalized_symbol} must be finite."
                    )

                if value <= 0:
                    continue

                position_currency = "USD"

            current_exposure += value

            normalized_positions[
                normalized_symbol
            ] = value

            position_currencies[
                normalized_symbol
            ] = position_currency

        # ---------------------------------------------------------
        # Total exposure
        # ---------------------------------------------------------

        total_exposure = (
            current_exposure + proposed_value
        )

        total_exposure_percent = (
            total_exposure / equity
        ) * 100.0

        proposed_position_percent = (
            proposed_value / equity
        ) * 100.0

        if (
            total_exposure_percent
            > self.limits.max_total_exposure_percent
        ):
            return self._reject(
                "Maximum total portfolio exposure exceeded.",
                equity=equity,
                current_exposure=current_exposure,
                proposed_value=proposed_value,
                total_exposure=total_exposure,
                total_exposure_percent=total_exposure_percent,
                proposed_position_percent=proposed_position_percent,
            )

        # ---------------------------------------------------------
        # Single position
        # ---------------------------------------------------------

        if (
            proposed_position_percent
            > self.limits.max_single_position_percent
        ):
            return self._reject(
                "Maximum single-position exposure exceeded.",
                equity=equity,
                current_exposure=current_exposure,
                proposed_value=proposed_value,
                total_exposure=total_exposure,
                total_exposure_percent=total_exposure_percent,
                proposed_position_percent=proposed_position_percent,
            )

        # ---------------------------------------------------------
        # Currency exposure
        # ---------------------------------------------------------

        currency_exposure = proposed_value

        for existing_symbol, value in normalized_positions.items():

            if (
                position_currencies.get(
                    existing_symbol,
                    "USD",
                )
                == currency
            ):
                currency_exposure += value

        currency_exposure_percent = (
            currency_exposure / equity
        ) * 100.0

        if (
            currency_exposure_percent
            > self.limits.max_currency_exposure_percent
        ):
            return self._reject(
                "Maximum currency exposure exceeded.",
                equity=equity,
                current_exposure=current_exposure,
                proposed_value=proposed_value,
                total_exposure=total_exposure,
                total_exposure_percent=total_exposure_percent,
                proposed_position_percent=proposed_position_percent,
                currency_exposure=currency_exposure,
                currency_exposure_percent=currency_exposure_percent,
            )

        # ---------------------------------------------------------
        # Correlation exposure
        # ---------------------------------------------------------

        correlation_map: dict[
            str,
            dict[str, float],
        ] = {
            symbol: {}
        }

        if correlations:

            # Supported format:
            #
            # {"AAPL": 0.8, "MSFT": 0.7}
            #
            # and:
            #
            # {"NVDA": {"AAPL": 0.8, "MSFT": 0.7}}

            if all(
                not isinstance(value, Mapping)
                for value in correlations.values()
            ):

                correlation_map[symbol] = {
                    str(existing_symbol)
                    .strip()
                    .upper(): float(value)
                    for existing_symbol, value
                    in correlations.items()
                }

            else:

                for proposed_key, related in correlations.items():

                    if not isinstance(related, Mapping):
                        continue

                    correlation_map[
                        str(proposed_key)
                        .strip()
                        .upper()
                    ] = {
                        str(existing_symbol)
                        .strip()
                        .upper(): float(value)
                        for existing_symbol, value
                        in related.items()
                    }

        correlation_result = (
            self.correlation_engine.calculate(
                equity=equity,
                proposed_symbol=symbol,
                proposed_value=proposed_value,
                positions=normalized_positions,
                correlations=correlation_map,
            )
        )

        if not correlation_result.get(
            "approved",
            False,
        ):

            return self._reject(
                str(
                    correlation_result.get(
                        "reason",
                        "Maximum correlated exposure exceeded.",
                    )
                ),
                equity=equity,
                current_exposure=current_exposure,
                proposed_value=proposed_value,
                total_exposure=total_exposure,
                total_exposure_percent=total_exposure_percent,
                proposed_position_percent=proposed_position_percent,
                currency_exposure=currency_exposure,
                currency_exposure_percent=currency_exposure_percent,
                correlated_exposure=correlation_result.get(
                    "correlated_exposure",
                    proposed_value,
                ),
                correlated_exposure_percent=correlation_result.get(
                    "correlated_exposure_percent",
                    0.0,
                ),
                correlation_contributions=correlation_result.get(
                    "contributions",
                    [],
                ),
            )

        # ---------------------------------------------------------
        # Approved
        # ---------------------------------------------------------

        return {
            "approved": True,
            "reason": "Portfolio risk limits passed.",
            "equity": equity,
            "current_exposure": current_exposure,
            "proposed_value": proposed_value,
            "total_exposure": total_exposure,
            "total_exposure_percent": total_exposure_percent,
            "proposed_position_percent": proposed_position_percent,
            "currency_exposure": currency_exposure,
            "currency_exposure_percent": currency_exposure_percent,
            "correlated_exposure": correlation_result[
                "correlated_exposure"
            ],
            "correlated_exposure_percent": correlation_result[
                "correlated_exposure_percent"
            ],
            "correlation_contributions": correlation_result[
                "contributions"
            ],
        }

    @staticmethod
    def _reject(
        reason: str,
        **metrics: Any,
    ) -> dict[str, Any]:

        return {
            "approved": False,
            "reason": reason,
            **metrics,
        }