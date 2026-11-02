from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .models import Instrument


class MarketAdapter(ABC):
    """
    Common interface for all financial markets.

    Market-specific implementations handle exchange/venue mechanics.
    Trading and strategy layers remain market-agnostic.
    """

    @property
    @abstractmethod
    def market_type(self):
        """Return the type of market handled by this adapter."""
        raise NotImplementedError

    @abstractmethod
    def get_instrument(self, symbol: str) -> Instrument:
        """Return normalized instrument metadata."""
        raise NotImplementedError

    @abstractmethod
    def get_price(self, symbol: str) -> float:
        """Return the current market price."""
        raise NotImplementedError

    @abstractmethod
    def is_market_open(self, symbol: str) -> bool:
        """Return whether the instrument is currently tradable."""
        raise NotImplementedError

    @abstractmethod
    def get_capabilities(self, symbol: str) -> dict[str, Any]:
        """Return market-specific trading capabilities."""
        raise NotImplementedError
