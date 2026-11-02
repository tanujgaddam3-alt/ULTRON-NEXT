from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from math import isfinite


class MarketType(str, Enum):
    STOCK = "stock"
    CRYPTO = "crypto"
    FOREX = "forex"


class InstrumentType(str, Enum):
    EQUITY = "equity"
    INDEX = "index"
    ETF = "etf"
    FUTURE = "future"
    OPTION = "option"


INDIAN_INDEX_IDENTITIES = {
    "NIFTY": ("NIFTY50", "^NSEI", "NSE"),
    "NIFTY50": ("NIFTY50", "^NSEI", "NSE"),
    "NIFTY 50": ("NIFTY50", "^NSEI", "NSE"),
    "^NSEI": ("NIFTY50", "^NSEI", "NSE"),
    "SENSEX": ("SENSEX", "^BSESN", "BSE"),
    "BSE SENSEX": ("SENSEX", "^BSESN", "BSE"),
    "BSESENSEX": ("SENSEX", "^BSESN", "BSE"),
    "^BSESN": ("SENSEX", "^BSESN", "BSE"),
}


def is_indian_index_symbol(symbol: str) -> bool:
    if not isinstance(symbol, str):
        return False
    return symbol.strip().upper() in INDIAN_INDEX_IDENTITIES


@dataclass(frozen=True)
class Instrument:
    """
    Market-neutral representation of a tradable instrument.
    """

    symbol: str
    market: MarketType
    base_currency: str
    quote_currency: str
    quantity_step: float | None = 1.0
    tick_size: float | None = 0.01
    contract_size: float | None = 1.0
    min_quantity: float | None = 1.0
    min_notional: float | None = 0.0
    provider_symbol: str | None = None
    asset_class: str = "equity"
    trading_status: str = "active"
    market_session: str = "regular"
    provider_capabilities: dict[str, bool] = field(default_factory=dict)
    is_shortable: bool = False
    is_margin_eligible: bool = False
    exchange: str | None = None
    instrument_type: InstrumentType = InstrumentType.EQUITY
    underlying: str | None = None
    expiry: date | None = None
    strike: float | None = None
    option_type: str | None = None
    provider_instrument_id: str | None = None
    metadata_verified: bool = False
    metadata_observed_at: datetime | None = None
    metadata_expires_at: datetime | None = None
    orderable: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str):
            raise ValueError("Symbol must be a string.")
        if not self.symbol.strip():
            raise ValueError("Symbol is required.")

        if not isinstance(self.base_currency, str) or not self.base_currency.strip():
            raise ValueError("Base currency is required.")

        if not isinstance(self.quote_currency, str) or not self.quote_currency.strip():
            raise ValueError("Quote currency is required.")

        try:
            instrument_type = InstrumentType(self.instrument_type)
        except ValueError as exc:
            raise ValueError("Unsupported instrument type.") from exc
        object.__setattr__(self, "instrument_type", instrument_type)

        for name, value in (
            ("quantity step", self.quantity_step),
            ("tick size", self.tick_size),
            ("contract size", self.contract_size),
            ("minimum quantity", self.min_quantity),
        ):
            if value is None:
                if self.orderable:
                    raise ValueError(
                        f"Orderable instruments require a known {name}."
                    )
                continue
            if not isfinite(value) or value <= 0:
                raise ValueError(f"Instrument {name} must be finite and positive.")

        if self.min_notional is None:
            if self.orderable:
                raise ValueError("Orderable instruments require known minimum notional.")
        elif not isfinite(self.min_notional) or self.min_notional < 0:
            raise ValueError("Min notional must be finite and non-negative.")

        if self.strike is not None and (
            not isfinite(self.strike) or self.strike <= 0
        ):
            raise ValueError("Instrument strike must be finite and positive.")
        if instrument_type in {InstrumentType.FUTURE, InstrumentType.OPTION}:
            if not self.underlying or self.expiry is None:
                raise ValueError(
                    "Derivative instruments require an underlying and expiry."
                )
        if instrument_type is InstrumentType.OPTION:
            if self.strike is None or self.option_type not in {"CE", "PE"}:
                raise ValueError(
                    "Options require a positive strike and CE or PE option type."
                )
        elif self.option_type is not None or self.strike is not None:
            raise ValueError("Strike and option type are only valid for options.")
        if instrument_type is InstrumentType.INDEX and self.orderable:
            raise ValueError("Market indices are not directly orderable.")
        for name, timestamp in (
            ("metadata observation", self.metadata_observed_at),
            ("metadata expiry", self.metadata_expires_at),
        ):
            if timestamp is not None and (
                not isinstance(timestamp, datetime) or timestamp.tzinfo is None
            ):
                raise ValueError(f"Instrument {name} must be timezone-aware.")
        if self.metadata_observed_at and self.metadata_expires_at:
            if self.metadata_expires_at <= self.metadata_observed_at:
                raise ValueError("Instrument metadata expiry must follow its observation.")

        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        if self.exchange is not None:
            object.__setattr__(self, "exchange", self.exchange.strip().upper())

    def validate_quantity(self, quantity: float) -> float:
        if self.quantity_step is None or self.min_quantity is None:
            raise ValueError("Instrument quantity constraints are unavailable.")
        value = float(quantity)
        if not isfinite(value):
            raise ValueError("Instrument quantity must be finite.")
        if value <= 0:
            raise ValueError("Instrument quantity must be positive.")
        if value < self.min_quantity:
            raise ValueError(
                f"Quantity {value} is below the minimum {self.min_quantity}."
            )
        ratio = value / self.quantity_step
        if not isfinite(ratio):
            raise ValueError("Instrument quantity step produced a non-finite ratio.")
        remainder = ratio - round(ratio)
        if abs(remainder) > 1e-9:
            raise ValueError(
                "Quantity is not aligned to the instrument quantity step."
            )
        return value

    def validate_price(self, price: float) -> float:
        if self.tick_size is None:
            raise ValueError("Instrument tick size is unavailable.")
        value = float(price)
        if not isfinite(value):
            raise ValueError("Instrument price must be finite.")
        if value <= 0:
            raise ValueError("Instrument price must be positive.")
        ratio = value / self.tick_size
        if not isfinite(ratio) or abs(ratio - round(ratio)) > 1e-9:
            raise ValueError("Instrument price is not aligned to its tick size.")
        return value

    def eligible_for_order(
        self,
        quantity: float,
        notional: float,
    ) -> bool:
        if not self.orderable or self.instrument_type is InstrumentType.INDEX:
            return False
        if self.exchange in {"NSE", "BSE"} and not self.metadata_is_current():
            return False
        try:
            order_notional = float(notional)
        except (TypeError, ValueError):
            return False
        if not isfinite(order_notional) or order_notional < 0:
            return False
        try:
            self.validate_quantity(quantity)
        except ValueError:
            return False

        if str(self.trading_status).lower() != "active":
            return False

        if (
            self.min_notional is not None
            and self.min_notional > 0
            and order_notional < self.min_notional
        ):
            return False

        return True

    def metadata_is_current(self, now: datetime | None = None) -> bool:
        if not self.metadata_verified or not self.metadata_observed_at or not self.metadata_expires_at:
            return False
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("Metadata validation time must be timezone-aware.")
        return (
            self.metadata_observed_at <= current
            and current < self.metadata_expires_at
        )
