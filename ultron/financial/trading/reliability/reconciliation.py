from __future__ import annotations

from math import isfinite
from typing import Any


class PortfolioReconciler:
    """
    Compares the locally known portfolio against broker state.

    Trading must not resume when portfolio state cannot be
    reconciled safely.
    """

    def reconcile(
        self,
        internal: dict[str, Any],
        broker: dict[str, Any],
        cash_tolerance: float = 0.01,
        quantity_tolerance: float = 0.000001,
    ) -> dict[str, Any]:
        if not isinstance(internal, dict) or not isinstance(broker, dict):
            return {
                "valid": False,
                "reconciled": False,
                "reason": "Internal and broker portfolios must be dictionaries.",
                "differences": [],
            }

        try:
            cash_tolerance = self._finite_number(
                cash_tolerance,
                "cash tolerance",
            )
            quantity_tolerance = self._finite_number(
                quantity_tolerance,
                "quantity tolerance",
            )
        except (TypeError, ValueError):
            return {
                "valid": False,
                "reconciled": False,
                "reason": "Reconciliation tolerances must be finite and non-negative.",
                "differences": [],
            }
        if cash_tolerance < 0 or quantity_tolerance < 0:
            return {
                "valid": False,
                "reconciled": False,
                "reason": "Reconciliation tolerances must be finite and non-negative.",
                "differences": [],
            }

        try:
            internal_cash = self._finite_number(internal["cash"], "internal cash")
            broker_cash = self._finite_number(broker["cash"], "broker cash")
            internal_positions = self._positions(internal["positions"], "internal")
            broker_positions = self._positions(broker["positions"], "broker")
        except (KeyError, TypeError, ValueError) as exc:
            return {
                "valid": False,
                "reconciled": False,
                "reason": str(exc),
                "cash_match": False,
                "positions_match": False,
                "differences": [],
            }

        differences: list[dict[str, Any]] = []

        if abs(internal_cash - broker_cash) > cash_tolerance:
            differences.append({
                "type": "cash",
                "internal": internal_cash,
                "broker": broker_cash,
                "difference": internal_cash - broker_cash,
            })

        symbols = (
            set(internal_positions)
            | set(broker_positions)
        )

        for symbol in sorted(symbols):
            internal_position = internal_positions.get(
                symbol,
                {},
            )

            broker_position = broker_positions.get(
                symbol,
                {},
            )

            internal_quantity = internal_position.get("quantity", 0.0)
            broker_quantity = broker_position.get("quantity", 0.0)

            if (
                abs(
                    internal_quantity
                    - broker_quantity
                )
                > quantity_tolerance
            ):
                differences.append({
                    "type": "position_quantity",
                    "symbol": symbol,
                    "internal": internal_quantity,
                    "broker": broker_quantity,
                    "difference": (
                        internal_quantity
                        - broker_quantity
                    ),
                })

        reconciled = not differences

        return {
            "valid": reconciled,
            "reconciled": reconciled,
            "reason": (
                "Portfolio state reconciled."
                if reconciled
                else "Portfolio state mismatch."
            ),
            "cash_match": not any(
                item["type"] == "cash"
                for item in differences
            ),
            "positions_match": not any(
                item["type"] == "position_quantity"
                for item in differences
            ),
            "differences": differences,
        }

    @staticmethod
    def _finite_number(value: Any, label: str) -> float:
        if isinstance(value, bool):
            raise ValueError(f"{label.capitalize()} must be a finite number.")
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"{label.capitalize()} must be a finite number.") from None
        if not isfinite(number):
            raise ValueError(f"{label.capitalize()} must be a finite number.")
        return number

    @classmethod
    def _positions(
        cls,
        value: Any,
        portfolio_name: str,
    ) -> dict[str, dict[str, float]]:
        if not isinstance(value, dict):
            raise ValueError(f"{portfolio_name.capitalize()} positions must be a dictionary.")

        normalized: dict[str, dict[str, float]] = {}
        for symbol, position in value.items():
            if not isinstance(symbol, str) or not symbol.strip():
                raise ValueError(
                    f"{portfolio_name.capitalize()} position symbols must be non-empty strings."
                )
            if not isinstance(position, dict) or "quantity" not in position:
                raise ValueError(
                    f"{portfolio_name.capitalize()} position {symbol} has no valid quantity."
                )
            normalized[symbol] = {
                "quantity": cls._finite_number(
                    position["quantity"],
                    f"{portfolio_name} position quantity for {symbol}",
                )
            }
        return normalized
