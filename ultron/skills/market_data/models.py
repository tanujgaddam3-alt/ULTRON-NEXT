from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .assets import AssetClass


@dataclass
class PricePoint:
    symbol: str
    price: float
    timestamp: datetime
    asset_class: AssetClass = AssetClass.EQUITY
    source: str = "unknown"


@dataclass
class MarketSnapshot:
    symbol: str
    current_price: float
    change_percent: float
    timestamp: datetime
    asset_class: AssetClass = AssetClass.EQUITY
    source: str = "unknown"
    freshness: str = "unknown"
    data_age_seconds: float | None = None
    currency: str | None = None
    volume: float | None = None
    instrument_type: str = "unknown"
    realtime_status: str = "unknown"

    @property
    def is_fresh(self) -> bool:
        return self.freshness == "current"
