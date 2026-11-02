from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite


@dataclass(frozen=True)
class PriceSnapshot:
    """
    Market price together with the time it was observed.
    """

    symbol: str
    price: float
    timestamp: datetime

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("Symbol is required.")

        if not isfinite(self.price) or self.price <= 0:
            raise ValueError("Price must be finite and positive.")

        if self.timestamp.tzinfo is None:
            raise ValueError(
                "Price timestamp must be timezone-aware."
            )


class PriceGuard:
    """
    Prevents trading decisions from using stale market prices.
    """

    def __init__(
        self,
        max_age_seconds: float = 30.0,
    ) -> None:
        if not isfinite(max_age_seconds) or max_age_seconds <= 0:
            raise ValueError(
                "max_age_seconds must be finite and positive."
            )

        self.max_age_seconds = float(
            max_age_seconds
        )

    def validate(
        self,
        snapshot: PriceSnapshot,
        now: datetime | None = None,
    ) -> dict[str, object]:

        if not isinstance(snapshot, PriceSnapshot):
            return {
                "valid": False,
                "reason": "Invalid price snapshot.",
            }

        current_time = now or datetime.now(UTC)

        if current_time.tzinfo is None:
            raise ValueError(
                "Current time must be timezone-aware."
            )

        age = (
            current_time - snapshot.timestamp
        ).total_seconds()

        if age < 0:
            return {
                "valid": False,
                "reason": "Price timestamp is in the future.",
                "age_seconds": age,
            }

        if age > self.max_age_seconds:
            return {
                "valid": False,
                "reason": (
                    "Market price is stale."
                ),
                "age_seconds": age,
                "max_age_seconds": (
                    self.max_age_seconds
                ),
            }

        return {
            "valid": True,
            "reason": "Market price is fresh.",
            "age_seconds": age,
        }
