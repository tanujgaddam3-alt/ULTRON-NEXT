from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BrokerAdapter(ABC):
    """Unified broker adapter contract for simulation and provider-backed execution."""

    def __init__(self, *, mode: str = "simulation") -> None:
        self.mode = mode

    @abstractmethod
    def snapshot(self, prices: dict[str, float]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def submit(self, order: Any) -> dict[str, Any]:
        raise NotImplementedError

    def get_account_snapshot(
        self,
        prices: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        return self.snapshot(prices or {})

    def get_order_status(self, order_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def reconcile_order(self, order_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @property
    def supports_live_orders(self) -> bool:
        return False
